"""音声認識。gpt-live-transcribe に WebSocket で流し込む。

このモデルの性質（local/HANDOFF.md の「実測結果: ストリーミング」）:

- **turn detection に対応しない。** 指定すると拒否される。`turn_detection: null` にする。
- **`completed` を返さない。`delta` を連続で流すだけ。** 文の切り出しは segmenter が行う。
- `keywords` は日本語でも機能する。用語表を渡す。
- `delay` は `low`。`minimal` と `high` はどちらも用語を外した。

回線は切れる前提で書く。切れたら繋ぎ直し、溜まった音声は捨てて現在から再開する。
溜め込んで一気に流すと、遅れた字幕が会話と噛み合わなくなる。
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
                            "prompt": config.ASR_PROMPT,
                            "keywords": self.keywords,
                            "languages": list(self.languages),
                            "delay": self.delay,
                        },
                        # このモデルは turn detection に対応しない。
                        "turn_detection": None,
                    }
                },
            },
        })

    def stop(self) -> None:
        self._stop.set()

    def resume(self) -> None:
        """停止した後にもう一度使えるようにする。

        操作画面から字幕の生成を止めて、また開始したときに呼ぶ。
        `stop()` を立てたままだと、接続してもすぐ抜けてしまう。
        """
        self._stop.clear()

    async def run(self, capture, on_delta: Callable[[str], None]) -> None:
        """接続が切れても繋ぎ直しながら回り続ける。"""
        headers = {"Authorization": f"Bearer {config.openai_key()}"}
        backoff = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    config.ASR_URL, additional_headers=headers, max_size=None
                ) as ws:
                    await ws.send(self._session())
                    # 切断中に溜まった音声は捨てる。現在から再開する。
                    capture.drain()
                    backoff = 1.0
                    print("  [認識] 接続した")

                    sender = asyncio.create_task(self._send_audio(ws, capture))
                    try:
                        await self._receive(ws, on_delta)
                    finally:
                        sender.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 会議中に落とさない
                if self._stop.is_set():
                    break
                self.reconnects += 1
                print(f"  [認識] 切断した（{type(e).__name__}: {e}）。{backoff:.0f} 秒後に繋ぎ直す")
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
                print(f"  [認識のエラー] {json.dumps(ev, ensure_ascii=False)[:300]}")
