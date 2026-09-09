"""文の切り出し。

gpt-live-transcribe は区切りを返さない。`delta` を連続で流すだけである
（local/HANDOFF.md の「実測結果: ストリーミング」）。したがって、
どこで1文を確定させるかは自前で決める。

切る条件は3つ:

1. 文末の記号が来た（。！？ / . ! ?）
2. 記号が来ないまま長くなりすぎた
3. 一定時間、新しい文字が来なくなった（発話が途切れた）

3 は呼び出し側が定期的に `flush_if_idle()` を呼ぶことで働く。

**記号だけの断片は捨てる。** 認識は「。」だけを delta として流してくることがある。
そのまま確定させると「.」だけの字幕が1行を占める。字幕の窓は最小4行しかない。
"""

from __future__ import annotations

import time
from typing import NamedTuple

from . import config

# 日本語の文末。英語は「. 」のように後ろに空白が続く場合だけ文末とみなす
# （OMMT2. のような略語の途中で切らないため）。
JA_ENDINGS = "。！？"
EN_ENDINGS = ".!?"


class Cut(NamedTuple):
    """確定した1文と、確定した理由。

    **理由は遅延の調整に要る。** 3つの切り方のうち、待ち時間を払っているのは
    `idle` だけである。それが全体の何割かは、記録に残さないと分からない。

    `waited` は `idle` のときだけ意味を持つ。実際に待った秒数で、
    `IDLE_FLUSH_SEC` を触った効果を後から確かめるために残す。
    """

    text: str
    reason: str          # punct（文末記号）/ force（長さ）/ idle（無音）/ flush（終了時）
    waited: float = 0.0


def has_content(text: str) -> bool:
    """字幕として送る価値があるか。文字か数字が1つも無ければ捨てる。

    「。」「...」のような記号だけの断片を弾く。日本語の仮名・漢字は isalnum() が真。
    """
    return any(ch.isalnum() for ch in text)


class Segmenter:
    def __init__(
        self,
        force_cut: int = config.FORCE_CUT_CHARS,
        idle_sec: float = config.IDLE_FLUSH_SEC,
    ) -> None:
        self.buffer = ""
        self.force_cut = force_cut
        self.idle_sec = idle_sec
        self.last_delta_at = time.monotonic()

    def feed(self, delta: str) -> list[Cut]:
        """認識の delta を足して、確定した文のリストを返す。"""
        if not delta:
            return []
        self.buffer += delta
        self.last_delta_at = time.monotonic()

        out: list[Cut] = []
        while True:
            found = self._find_cut()
            if found is None:
                break
            cut, reason = found
            sentence, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:].lstrip()
            if has_content(sentence):
                out.append(Cut(sentence, reason))
        return out

    def silent_for(self) -> float:
        """最後の delta から何秒たったか。中身が無ければ 0。"""
        if not self.buffer.strip():
            return 0.0
        return time.monotonic() - self.last_delta_at

    def flush_if_idle(self) -> list[Cut]:
        """発話が途切れたら、文末記号が無くても確定させる。"""
        if not self.buffer.strip():
            return []
        waited = time.monotonic() - self.last_delta_at
        if waited < self.idle_sec:
            return []
        sentence, self.buffer = self.buffer.strip(), ""
        return [Cut(sentence, "idle", waited)] if has_content(sentence) else []

    def reset(self) -> None:
        """途中まで溜まっている文字を捨てる。字幕の生成を止めたときに呼ぶ。

        **出さずに捨てる。** 止めた後に字幕が1行出てくると、読み手は
        何が起きたのか分からない。再開したときに古い断片が混ざるのも避ける。
        """
        self.buffer = ""
        self.last_delta_at = time.monotonic()

    def flush(self) -> list[Cut]:
        """残りを全部出す。終了時に使う。"""
        sentence, self.buffer = self.buffer.strip(), ""
        return [Cut(sentence, "flush")] if has_content(sentence) else []

    def _find_cut(self) -> tuple[int, str] | None:
        for i, ch in enumerate(self.buffer):
            if ch in JA_ENDINGS:
                return i + 1, "punct"
            if ch in EN_ENDINGS:
                # 後ろに空白が続くときだけ文末とみなす。
                nxt = self.buffer[i + 1: i + 2]
                if nxt in (" ", "\n"):
                    return i + 2, "punct"
        if len(self.buffer) >= self.force_cut:
            return self._soft_cut(), "force"
        return None

    def _soft_cut(self) -> int:
        """文末記号が来ないまま伸びたときに、読点や空白で切る。"""
        window = self.buffer[: self.force_cut]
        for mark in ("、", "，", ", ", " "):
            pos = window.rfind(mark)
            # あまり手前で切ると細切れになるので、後半にある区切りだけ使う。
            if pos > self.force_cut // 2:
                return pos + len(mark)
        return self.force_cut
