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
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable

import websockets

from . import config


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
        self.reconnects = 0

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
        """
        self._stop.clear()

    async def run(self, capture, on_delta: Callable[[str], None]) -> None:
        """Keep running, reconnecting whenever the connection drops."""
        headers = {"Authorization": f"Bearer {config.openai_key()}"}
        backoff = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    config.ASR_URL, additional_headers=headers, max_size=None
                ) as ws:
                    await ws.send(self._session())
                    # Throw away the audio that piled up while disconnected.
                    # Resume from the present.
                    capture.drain()
                    backoff = 1.0
                    print("  [文字起こし] 接続した")

                    sender = asyncio.create_task(self._send_audio(ws, capture))
                    try:
                        await self._receive(ws, on_delta)
                    finally:
                        sender.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - never crash in a meeting
                if self._stop.is_set():
                    break
                self.reconnects += 1
                print(f"  [文字起こし] 切断した（{type(e).__name__}: {e}）。{backoff:.0f} 秒後に繋ぎ直す")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

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
                print(f"  [文字起こしのエラー] {json.dumps(ev, ensure_ascii=False)[:300]}")
