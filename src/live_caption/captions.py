"""Sending to the Zoom caption API.

We POST plain UTF-8 text to the URL that the host obtains during the meeting
through Captions -> the caret -> Manual Captions settings -> Copy the API
token.

**seq must increase monotonically across the whole meeting session.**
If it goes backwards, Zoom drops the caption silently, without an error. When
the caption application crashes and restarts, resetting seq to 1 makes the
captions stop without a word. We save seq per meeting ID and start from the
larger of the saved value and a value built from the clock. (Skipping ahead is
allowed; jumps of 200 million were measured and worked.)

All of this was measured against the live Zoom caption API.

**The token can be replaced after startup.** It is entered through the control
page in the browser (`web.py`). The token cannot be obtained until the meeting
has started, so it cannot be fixed at startup. Sending can be started and
stopped for the same reason.

Replacing the token and sending come from different threads (the main event
loop and the HTTP server thread). `_lock` guards the URL, seq and the state
together.
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
    """A string that cannot be accepted as a token URL."""


def parse_token(url: str) -> tuple[str, str]:
    """Check the token URL and return (cleaned URL, meeting ID).

    Accepting a wrong one silently leaves you with no way to see why the
    captions are missing. Zoom drops a rewound seq without an error, so reject
    here everything that can be rejected.
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
        # **Do not capture `config.CAPTION_LANG` in a default argument.** A
        # default argument is evaluated once at import time, so setting the
        # caption direction to `en2ja` would still send `en-US`. Read it here,
        # each time an instance is created.
        self.lang = config.CAPTION_LANG if lang is None else lang
        # dry_run means "never send to Zoom, whatever happens". It is for
        # testing and cannot be changed while running.
        self.dry_run = dry_run
        self._lock = threading.Lock()
        self.base_url: str | None = None
        self.meeting_key = ""
        self.seq = 0
        self.sent = 0
        self.failed = 0
        # Send if there is a token. If not, the user enters one in the browser.
        self.enabled = False
        if base_url:
            self.set_token(base_url)
            self.enabled = not dry_run

    # --- State --------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Whether we send to Zoom right now."""
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
        """Set (or replace) the token. Return the meeting ID.

        When the meeting changes, seq is taken again. For the same meeting, it
        continues from the saved value.
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

    # --- Managing seq -------------------------------------------------------

    def _state(self) -> dict:
        if config.SEQ_STATE_PATH.exists():
            try:
                return json.loads(config.SEQ_STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _initial_seq_locked(self) -> int:
        saved = self._state().get(self.meeting_key, 0) + 1
        # So that we can recover even if the saved state is lost, compare with
        # a value built from the clock and use the larger one. The factor is
        # taken larger than the measured send rate (3.5 per second).
        clock = int((time.time() - config.SEQ_EPOCH) * config.SEQ_TIME_SCALE)
        return max(saved, clock)

    def _save_seq_locked(self) -> None:
        state = self._state()
        state[self.meeting_key] = self.seq - 1
        config.SEQ_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.SEQ_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # --- Sending ------------------------------------------------------------

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
        """Send one caption line. seq advances only on success (a retry does
        not increase it).

        When sending is turned off, return True without doing anything.
        Counting failures here would mean nothing when only the browser
        captions are in use.
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
        """Throwaway captions sent when the caption stream starts.

        On the receiving side, "Show Captions" cannot be turned on until
        captions start flowing. So the first few reach nobody. This keeps the
        first real utterance from being lost.

        **Go through here as well when captions are started from the browser in
        the middle of a meeting.** That is the moment the stream starts for the
        receiving side.
        """
        if not self.active:
            return
        for line in config.WARMUP_CAPTIONS:
            await self.send(line)
            await asyncio.sleep(0.5)
