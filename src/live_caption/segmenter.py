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

from . import config

# 日本語の文末。英語は「. 」のように後ろに空白が続く場合だけ文末とみなす
# （OMMT2. のような略語の途中で切らないため）。
JA_ENDINGS = "。！？"
EN_ENDINGS = ".!?"


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

    def feed(self, delta: str) -> list[str]:
        """認識の delta を足して、確定した文のリストを返す。"""
        if not delta:
            return []
        self.buffer += delta
        self.last_delta_at = time.monotonic()

        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            sentence, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:].lstrip()
            if has_content(sentence):
                out.append(sentence)
        return out

    def flush_if_idle(self) -> list[str]:
        """発話が途切れたら、文末記号が無くても確定させる。"""
        if not self.buffer.strip():
            return []
        if time.monotonic() - self.last_delta_at < self.idle_sec:
            return []
        sentence, self.buffer = self.buffer.strip(), ""
        return [sentence] if has_content(sentence) else []

    def reset(self) -> None:
        """途中まで溜まっている文字を捨てる。字幕の生成を止めたときに呼ぶ。

        **出さずに捨てる。** 止めた後に字幕が1行出てくると、読み手は
        何が起きたのか分からない。再開したときに古い断片が混ざるのも避ける。
        """
        self.buffer = ""
        self.last_delta_at = time.monotonic()

    def flush(self) -> list[str]:
        """残りを全部出す。終了時に使う。"""
        sentence, self.buffer = self.buffer.strip(), ""
        return [sentence] if has_content(sentence) else []

    def _find_cut(self) -> int | None:
        for i, ch in enumerate(self.buffer):
            if ch in JA_ENDINGS:
                return i + 1
            if ch in EN_ENDINGS:
                # 後ろに空白が続くときだけ文末とみなす。
                nxt = self.buffer[i + 1: i + 2]
                if nxt in (" ", "\n"):
                    return i + 2
        if len(self.buffer) >= self.force_cut:
            return self._soft_cut()
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
