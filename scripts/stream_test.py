#!/usr/bin/env python3
"""Feed audio to gpt-live-transcribe in real time, and measure the delay and
the way it splits the text.

    pixi run python scripts/stream_test.py <24kHz mono wav> [seconds] [delay]

    Example: pixi run python scripts/stream_test.py local/SampleRecordings/_wav24k/mix.wav 180 low

It sends the audio file at the speed of a real meeting, 100 ms at a time. So
it takes as much real time as the number of seconds you ask for. The default
is 180 seconds.

`delay` is the knob for the delay and the accuracy of the API:
minimal / low / medium / high / xhigh. The stated time to the first partial
string is 0.70 / 1.19 / 1.39 / 2.09 / 2.91 seconds.

What it measures:
    1. The seconds from the end of the speech to the final string (this is the
       caption delay)
    2. How many characters one segment holds (one POST to the Zoom captions
       should be short)
    3. Recognition when the terms (keywords) are passed in
    4. The gap between deltas while a person keeps talking (the lower limit
       for `IDLE_FLUSH_SEC`)

The audio must be 24 kHz, 16 bit, mono WAV. Make it with ffmpeg:
    ffmpeg -i in.m4a -ac 1 -ar 24000 -c:a pcm_s16le out.wav
"""

import asyncio
import base64
import json
import os
import sys
import time
import wave
from pathlib import Path

import websockets

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config as config_mod  # noqa: E402
from live_caption import glossary as glossary_mod  # noqa: E402


def config_idle() -> float:
    """The silence timeout as it is set now. Printed so you can compare."""
    return config_mod.IDLE_FLUSH_SEC

URL = "wss://api.openai.com/v1/realtime?intent=transcription"
CHUNK_MS = 100
RATE = 24000


def keywords(limit: int = 100) -> list[str]:
    """Terms passed to the recognizer. It uses the engine's glossary; do not
    copy the list here."""
    return glossary_mod.keywords(glossary_mod.load(), limit)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(
                f"{path.name} は {w.getframerate()} Hz / {w.getnchannels()} ch / "
                f"{w.getsampwidth()*8} bit。24000 Hz・モノラル・16 bit にすること。"
            )
        return w.readframes(w.getnframes())


async def run(path: Path, seconds: int, delay: str) -> int:
    audio = read_wav(path)
    bytes_per_chunk = RATE * 2 * CHUNK_MS // 1000
    total_chunks = min(len(audio) // bytes_per_chunk, seconds * 1000 // CHUNK_MS)

    kw = keywords()
    print(f"音声: {path.name}  送る長さ {total_chunks * CHUNK_MS / 1000:.0f} 秒")
    print(f"delay={delay}  keywords={len(kw)} 語  languages=['ja','en']")
    print("実時間で送るので、送る長さと同じだけかかる。")
    print()

    session = {
        "type": "session.update",
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": RATE},
                    "transcription": {
                        "model": "gpt-live-transcribe",
                        "prompt": "重力波望遠鏡 KAGRA の定例会議。干渉計の光学と制御の話題。日本語と英語が混ざる。",
                        "keywords": kw,
                        "languages": ["ja", "en"],
                        "delay": delay,
                    },
                    # gpt-live-transcribe does not support turn detection:
                    # "Turn detection is not supported for this transcription
                    # model". The model decides where to split by itself.
                    "turn_detection": None,
                }
            },
        },
    }

    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    segments: list[tuple[float, float, str]] = []   # (audio position where the speech ended, delay, text)
    pending_ends: list[float] = []
    first_delta: list[float] = []

    async with websockets.connect(URL, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps(session))
        t0 = time.perf_counter()

        async def send_audio() -> None:
            for i in range(total_chunks):
                chunk = audio[i * bytes_per_chunk:(i + 1) * bytes_per_chunk]
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode(),
                }))
                # Keep to real time. Wait for the difference between what has
                # been sent and the time that has passed.
                target = t0 + (i + 1) * CHUNK_MS / 1000
                await asyncio.sleep(max(0.0, target - time.perf_counter()))

        debug = bool(os.environ.get("DEBUG"))
        seen: dict[str, int] = {}
        # gpt-live-transcribe does not send completed. Group the deltas by
        # item_id.
        items: dict[str, dict] = {}
        order: list[str] = []
        last_delta_at = [0.0]

        async def receive() -> None:
            async for raw in ws:
                ev = json.loads(raw)
                kind = ev.get("type", "")
                now = time.perf_counter() - t0
                seen[kind] = seen.get(kind, 0) + 1

                if debug and seen[kind] <= 2:
                    print(f"  [{now:6.2f}s] {kind}")
                    print(f"           {json.dumps(ev, ensure_ascii=False)[:280]}")

                if kind.endswith("input_audio_transcription.delta"):
                    item_id = ev.get("item_id", "?")
                    if item_id not in items:
                        items[item_id] = {"text": "", "first": now, "last": now, "gaps": []}
                        order.append(item_id)
                    else:
                        # A gap inside one item is a gap while the person
                        # keeps talking. A gap across the break between two
                        # items holds real silence, so it is left out.
                        items[item_id]["gaps"].append(now - items[item_id]["last"])
                    items[item_id]["text"] += ev.get("delta", "")
                    items[item_id]["last"] = now
                    last_delta_at[0] = now
                elif kind.endswith("input_audio_transcription.completed"):
                    text = (ev.get("transcript") or "").strip()
                    print(f"  [completed {now:6.1f}s / {len(text):3d}字]  {text[:80]}")
                elif kind == "error":
                    print(f"  エラー: {json.dumps(ev, ensure_ascii=False)[:400]}")

        sender = asyncio.create_task(send_audio())
        receiver = asyncio.create_task(receive())
        await sender
        audio_done = time.perf_counter() - t0
        print(f"  音声の送信を終えた: {audio_done:.1f}s")

        # The time from the end of the sending to the last delta is how far
        # behind real time it runs.
        quiet_since = time.perf_counter()
        while time.perf_counter() - quiet_since < 3.0:
            before = last_delta_at[0]
            await asyncio.sleep(0.25)
            if last_delta_at[0] != before:
                quiet_since = time.perf_counter()
        # See whether a final commit brings a completed event
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await asyncio.sleep(3)
        receiver.cancel()

    lag = last_delta_at[0] - audio_done

    print()
    print("=" * 66)
    print("受け取ったイベントの種類:")
    for kind, count in sorted(seen.items(), key=lambda x: -x[1]):
        print(f"  {count:5d}  {kind}")
    print()

    if not order:
        print("文字列が1つも返らなかった。session の設定を疑うこと。")
        return 1

    print(f"実時間からの遅れ: **{lag:.2f} 秒**")
    print(f"  （音声を送り終えた {audio_done:.1f}s から、最後の delta が届いた {last_delta_at[0]:.1f}s まで）")
    print()

    lens = [len(items[i]["text"]) for i in order]
    print(f"item の数: {len(order)}")
    print(f"1 item の長さ: 最小 {min(lens)}字  中央 {sorted(lens)[len(lens)//2]}字  最大 {max(lens)}字")
    print()

    # **This is what sets the lower limit for IDLE_FLUSH_SEC.**
    # The silence timer measures the time since the last delta. Set it shorter
    # than the gaps that occur while a person keeps talking, and it finishes a
    # sentence while they are still speaking.
    gaps = sorted(g for i in order for g in items[i]["gaps"])
    if gaps:
        def pct(p: float) -> float:
            return gaps[min(len(gaps) - 1, int(p * len(gaps)))]

        print("同じ item の中での delta の間隔（＝話し続けている間の間隔）:")
        print(
            f"  件数 {len(gaps)}  中央 {pct(0.5):.2f}s  p95 {pct(0.95):.2f}s"
            f"  p99 {pct(0.99):.2f}s  最大 {gaps[-1]:.2f}s"
        )
        print()
        # How often a sentence is finished on silence for each threshold.
        # **Whatever you take off the threshold becomes that many more cuts in
        # the middle of a sentence.**
        minutes = max(audio_done, 1.0) / 60.0
        print("  閾値ごとの、無音で確定させる回数:")
        print("    閾値     件数   1分あたり")
        for th in (1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0):
            n = sum(1 for g in gaps if g >= th)
            mark = "  <- いまの設定" if abs(th - config_idle()) < 0.01 else ""
            print(f"    {th:4.1f}s  {n:5d}   {n / minutes:6.1f}{mark}")
        print()
        print("  **IDLE_FLUSH_SEC は、この p99 より上に置くこと。** 下げると、")
        print("  まだ話している途中で文を確定させてしまう。")
        print()
        print("  注意: item が長いと、この間隔には話者の交代や本当の沈黙も混ざる。")
        print("  「話し続けている間の間隔」の上限としては、やや大きめに出る。")
        print()
    print("item ごとの中身:")
    for i in order:
        it = items[i]
        print(f"  [{it['first']:6.1f}s → {it['last']:6.1f}s / {len(it['text']):4d}字]")
        print(f"    {it['text'].strip()}")
    return 0


def main() -> int:
    # **Use the engine's load_env.** With a copy of its own, a threshold
    # replaced through .env would not take effect here, and the IDLE_FLUSH_SEC
    # it prints would differ from the real one.
    config_mod.load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY が無い。")
        return 1
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    delay = sys.argv[3] if len(sys.argv) > 3 else "low"
    return asyncio.run(run(path, seconds, delay))


if __name__ == "__main__":
    sys.exit(main())
