"""Speech recognition. Streams into gpt-live-transcribe over a WebSocket.

What this model does, measured on real meeting audio:

- **It does not support turn detection.** Setting it is rejected. Use
  `turn_detection: null`.
- **It never returns `completed`. It only streams `delta` continuously.**
  Sentence splitting is done by segmenter.
- `keywords` works for Japanese too. We pass the term list.
- `delay` is `low`. Both `minimal` and `high` missed the terms.

Write the code assuming the connection will drop. When it drops, reconnect,
throw away the audio that piled up, and resume from the present. Holding audio
back and sending it all at once makes the captions late, and they no longer
match the conversation.

**The connection can also go silent without dropping.** Seen on 2026-10-01
and 2026-10-07: in the middle of a sentence the deltas stopped, with no
`error` event and no close. The keepalive pings kept passing (61 seconds
went by without a disconnect), so the socket was alive and the server simply
stopped answering. Audio kept arriving, so the meter on the control page
looked normal. Stopping and starting fixed it.
So we watch for it: **audio with a voice in it is coming in, but no delta
has arrived for `ASR_STALL_SEC`.** Then we reconnect, the same as after a
drop. Only this WebSocket is reopened. The audio device, delivery and the
Zoom captions are not touched.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable

import websockets

from . import config


class _Stalled(Exception):
    """Raised inside `run()` to reconnect a connection that went silent."""


class _Kicked(_Stalled):
    """Raised inside `run()` when the control page asks for a reconnect.
    **Not counted in `stalls`**, which counts what the watchdog caught."""


class Asr:
    def __init__(
        self,
        keywords: list[str],
        delay: str = config.ASR_DELAY,
        languages: tuple[str, ...] = config.ASR_LANGUAGES,
    ) -> None:
        self.keywords = keywords
        self.delay = delay
        self.languages = languages
        self._stop = asyncio.Event()
        # Set to reconnect now (the button on the control page).
        self._kick = asyncio.Event()
        self.reconnects = 0
        # Reconnects made because the connection went silent (`_Stalled`).
        # Counted separately from `reconnects` so we can tell how often it
        # happens.
        self.stalls = 0
        # When the last delta arrived (`time.monotonic`). 0 = not yet on
        # this connection.
        self.last_delta_at = 0.0
        self._connected_at = 0.0
        # The wait before calling a connection silent. It doubles after a
        # reconnect that brought no delta either (up to ASR_STALL_MAX_SEC),
        # and goes back to ASR_STALL_SEC on the next delta.
        # **Zoom's mixed audio can carry noise above VOICE_PEAK**, and noise
        # alone gives no delta. Without the doubling, a long pause would
        # reconnect every 15 seconds.
        self._stall_after = config.ASR_STALL_SEC

    def _session(self) -> str:
        return json.dumps({
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": config.ASR_RATE},
                        "transcription": {
                            "model": config.ASR_MODEL,
                            "prompt": config.asr_prompt(),
                            "keywords": self.keywords,
                            "languages": list(self.languages),
                            "delay": self.delay,
                        },
                        # This model does not support turn detection.
                        "turn_detection": None,
                    }
                },
            },
        })

    def stop(self) -> None:
        self._stop.set()

    def resume(self) -> None:
        """Make this usable again after a stop.

        Called when caption generation is stopped from the control page and
        then started again. While the `stop()` flag stays set, the loop leaves
        right after connecting.

        `stalls` starts again from 0 here. The control page shows it, and a
        count from an earlier meeting would mean nothing there.
        """
        self._stop.clear()
        self.stalls = 0
        self._stall_after = config.ASR_STALL_SEC

    def kick(self) -> None:
        """Reconnect now, without waiting for the watchdog.

        **Call it on the event loop thread** (`call_soon_threadsafe` from the
        HTTP server). Does nothing while not connected.
        """
        self._kick.set()

    async def run(self, capture, on_delta: Callable[[str], None]) -> None:
        """Keep running, reconnecting whenever the connection drops or goes
        silent."""
        headers = {"Authorization": f"Bearer {config.openai_key()}"}
        backoff = 1.0

        def got(delta: str) -> None:
            self.last_delta_at = time.monotonic()
            self._stall_after = config.ASR_STALL_SEC
            on_delta(delta)

        while not self._stop.is_set():
            self.last_delta_at = 0.0
            try:
                async with websockets.connect(
                    config.ASR_URL, additional_headers=headers, max_size=None
                ) as ws:
                    await ws.send(self._session())
                    # Throw away the audio that piled up while disconnected.
                    # Resume from the present.
                    capture.drain()
                    self._connected_at = time.monotonic()
                    self._kick.clear()
                    print("  [transcription] connected")

                    # **Wait on all three.** Before 2026-10-07 we waited on
                    # the receiver alone, so a sender that died (or a
                    # connection that went silent) left us waiting forever.
                    tasks = [
                        asyncio.create_task(self._send_audio(ws, capture)),
                        asyncio.create_task(self._receive(ws, got)),
                        asyncio.create_task(self._watch(capture)),
                    ]
                    try:
                        done, _ = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    # Whatever finished first decides why we leave. An
                    # exception goes to the handler below and reconnects.
                    for t in done:
                        exc = t.exception()
                        if exc is not None:
                            raise exc
                    if not self._stop.is_set():
                        # The server closed the stream cleanly, or the
                        # sender ran out. Either way, reconnect.
                        raise ConnectionError("the stream ended")
            except asyncio.CancelledError:
                raise
            except _Stalled as e:
                if self._stop.is_set():
                    break
                if isinstance(e, _Kicked):
                    print(f"  [transcription] {e} Reconnecting now")
                    continue
                self.stalls += 1
                print(f"  [transcription] {e} Reconnecting now "
                      f"(stall #{self.stalls})")
                if not self.last_delta_at:
                    # Not one delta on this connection either. Probably
                    # noise, not a voice. Wait longer next time.
                    self._stall_after = min(self._stall_after * 2,
                                            config.ASR_STALL_MAX_SEC)
                # Reconnect at once. Every second here is a second of the
                # meeting with no captions.
            except Exception as e:  # noqa: BLE001 - never crash in a meeting
                if self._stop.is_set():
                    break
                self.reconnects += 1
                # A connection that worked resets the wait. **Only one that
                # gave us a delta.** Resetting it on connect would make a
                # server that accepts and then fails retry every second.
                if self.last_delta_at:
                    backoff = 1.0
                print(f"  [transcription] disconnected ({type(e).__name__}: {e}). "
                      f"Reconnecting in {backoff:.0f} s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _watch(self, capture) -> None:
        """Raise `_Stalled` when the connection goes silent, or when asked to
        reconnect (`kick`).

        Silent means: **somebody is speaking now** (a voice within the last
        `ASR_STALL_VOICE_SEC`), and no delta has arrived for `_stall_after`
        seconds (counted from the connection, if no delta came yet). During
        real silence nothing happens, however long it lasts.
        """
        quiet_for = getattr(capture, "quiet_for", None)
        while True:
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=1.0)
                raise _Kicked("reconnect asked for from the control page.")
            except asyncio.TimeoutError:
                pass
            if not config.ASR_STALL_SEC or quiet_for is None:
                continue
            since = max(self.last_delta_at, self._connected_at)
            mute = time.monotonic() - since
            if mute >= self._stall_after and quiet_for() < config.ASR_STALL_VOICE_SEC:
                raise _Stalled(
                    f"no transcript for {mute:.0f} s while audio with a voice "
                    "is coming in. The connection went silent.")

    async def _send_audio(self, ws, capture) -> None:
        async for chunk in capture.chunks():
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode(),
            }))

    async def _receive(self, ws, on_delta: Callable[[str], None]) -> None:
        async for raw in ws:
            if self._stop.is_set():
                return
            ev = json.loads(raw)
            kind = ev.get("type", "")
            if kind.endswith("input_audio_transcription.delta"):
                on_delta(ev.get("delta", ""))
            elif kind == "error":
                print(f"  [transcription error] {json.dumps(ev, ensure_ascii=False)[:300]}")
