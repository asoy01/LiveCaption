"""Zoom字幕APIへの送信。

ホストが会議中に「字幕」→「∧」→「手動字幕の設定」→「APIトークンをコピー」で
得たURLに、UTF-8のプレーンテキストをPOSTする。

**seq はミーティングのセッション全体で単調増加していないといけない。**
巻き戻すと Zoom はエラーを返さずに黙って捨てる。字幕アプリが落ちて再起動したとき、
seq を 1 に戻すと字幕が無言で止まる。会議IDごとに保存し、時刻から作った値との
大きい方から始める（飛ばして送るのは可。2億の飛びまで実測済み）。

根拠は local/HANDOFF.md の「実測結果: Zoom 字幕API」。

**トークンは起動後に差し替えられる。** ブラウザの操作画面（`web.py`）から
入れる使い方があるためである。会議が始まってからでないとトークンは取れないので、
起動時に決め打ちにはできない。送信の開始・停止も同じ理由で切り替えられる。

トークンの差し替えと送信は別のスレッドから来る（本体のイベントループと、
HTTPサーバのスレッド）。`_lock` で URL・seq・状態をまとめて守る。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class TokenError(ValueError):
    """トークンURLとして受け付けられない文字列。"""


def parse_token(url: str) -> tuple[str, str]:
    """トークンURLを検査して、(整えたURL, 会議ID) を返す。

    間違ったものを黙って受け取ると、字幕が出ない理由が分からなくなる。
    Zoomは巻き戻した seq をエラー無しで捨てるので、ここで弾けるものは弾く。
    """
    url = (url or "").strip().strip('"').strip("'")
    if not url:
        raise TokenError("トークンが空である。")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise TokenError("http/https のURLではない。")
    if "closedcaption" not in parsed.path:
        raise TokenError(
            "字幕トークンのURLではない。"
            "「字幕」→「∧」→「手動字幕の設定」→「APIトークンをコピー」で得たものを貼ること。"
        )
    query = urllib.parse.parse_qs(parsed.query)
    meeting = query.get("id", [""])[0]
    if not meeting:
        raise TokenError("URLに会議ID（id=）が入っていない。")
    return url.rstrip("&"), meeting


class CaptionSender:
    def __init__(
        self,
        base_url: str | None = None,
        lang: str | None = None,
        dry_run: bool = False,
    ) -> None:
        # **既定値引数で `config.CAPTION_LANG` を捕まえてはいけない。** 既定値引数は
        # import のときに1回だけ評価されるので、字幕の向きを `en2ja` にしても
        # `en-US` のまま送ることになる。ここで、作られるたびに読む。
        self.lang = config.CAPTION_LANG if lang is None else lang
        # dry_run は「何があってもZoomへ送らない」。試験用で、実行中は変えられない。
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self.base_url: str | None = None
        self.meeting_key = ""
        self.seq = 0
        self.sent = 0
        self.failed = 0
        # トークンがあれば送る。無ければブラウザから入れてもらう。
        self.enabled = False
        if base_url:
            self.set_token(base_url)
            self.enabled = not dry_run

    # --- 状態 ---------------------------------------------------------------

    @property
    def active(self) -> bool:
        """いまZoomへ送るか。"""
        return bool(self.base_url) and self.enabled and not self.dry_run

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled,
                "has_token": bool(self.base_url),
                "active": bool(self.base_url) and self.enabled and not self.dry_run,
                "dry_run": self.dry_run,
                "meeting": self.meeting_key,
                "seq": self.seq,
                "sent": self.sent,
                "failed": self.failed,
            }

    def set_token(self, url: str) -> str:
        """トークンを入れる（差し替える）。会議IDを返す。

        会議が変われば seq も取り直す。同じ会議なら保存した続きから始める。
        """
        clean, meeting = parse_token(url)
        with self._lock:
            self.base_url = clean
            self.meeting_key = meeting
            self.seq = self._initial_seq_locked()
            self.failed = 0
        return meeting

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self.enabled = bool(on)

    # --- seq の管理 ---------------------------------------------------------

    def _state(self) -> dict:
        if config.SEQ_STATE_PATH.exists():
            try:
                return json.loads(config.SEQ_STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _initial_seq_locked(self) -> int:
        saved = self._state().get(self.meeting_key, 0) + 1
        # 保存を失っても復帰できるように、時刻から作った値と比べて大きい方を使う。
        # 係数は送信レートの実測（3.5 回/秒）より大きく取る。
        clock = int((time.time() - config.SEQ_EPOCH) * config.SEQ_TIME_SCALE)
        return max(saved, clock)

    def _save_seq_locked(self) -> None:
        state = self._state()
        state[self.meeting_key] = self.seq - 1
        config.SEQ_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SEQ_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # --- 送信 ---------------------------------------------------------------

    def _post(self, text: str) -> tuple[int | None, str]:
        with self._lock:
            url = f"{self.base_url}&seq={self.seq}&lang={self.lang}"
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=config.CAPTION_TIMEOUT) as res:
                return res.status, res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            return None, str(e.reason)

    async def send(self, text: str) -> bool:
        """字幕を1行送る。seq は成功したときだけ進める（リトライでは増やさない）。

        送らない設定のときは、何もせずに True を返す。ブラウザ字幕だけで
        使っているときに、ここで失敗を数えても意味がない。
        """
        text = text.strip()
        if not text:
            return True
        if not self.active:
            return False

        status, body = await asyncio.to_thread(self._post, text)
        if status == 200:
            with self._lock:
                self.seq += 1
                self.sent += 1
                await asyncio.to_thread(self._save_seq_locked)
            return True

        with self._lock:
            self.failed += 1
        print(f"  [字幕の送信に失敗] status={status} {body.strip()[:120]}")
        return False

    async def warmup(self) -> None:
        """字幕を流し始めるときの捨て字幕。

        受信側は、字幕が流れ始めるまで「字幕を表示」を有効にできない。
        そのため最初の数個は誰にも届かない。本番の最初の発言が消えるのを避ける。

        **会議の途中でブラウザから開始したときも、ここを通ること。**
        そのときが受信側にとっての「流れ始め」である。
        """
        if not self.active:
            return
        for line in config.WARMUP_CAPTIONS:
            await self.send(line)
            await asyncio.sleep(0.5)
