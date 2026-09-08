#!/usr/bin/env python3
"""gpt-live-transcribe に音声を実時間で流し込み、遅延と区切り方を測る。

    pixi run python scripts/stream_test.py <24kHz mono wav> [秒数] [delay]

    例: pixi run python scripts/stream_test.py local/SampleRecordings/_wav24k/mix.wav 180 low

音声ファイルを、実際の会議と同じ速さ（100 ms ずつ）で送る。したがって
「秒数」に指定しただけ実時間がかかる。既定は 180 秒。

delay は API の遅延と精度の調整つまみ。minimal / low / medium / high / xhigh。
公称の初回部分文字列までの時間は 0.70 / 1.19 / 1.39 / 2.09 / 2.91 秒。

測るもの:
    1. 発話が止まってから、確定した文字列が返るまでの秒数（これが字幕の遅延）
    2. 1つの区切りが何文字になるか（Zoom字幕は1回のPOSTを短くしたい）
    3. 用語（keywords）を渡したときの認識

音声は 24 kHz・16 bit・モノラルの WAV であること。ffmpeg で作る:
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

from live_caption import glossary as glossary_mod  # noqa: E402

URL = "wss://api.openai.com/v1/realtime?intent=transcription"
CHUNK_MS = 100
RATE = 24000


def load_env() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # .env の値を優先する。環境変数に同じ名前があっても上書きする。
        if value:
            os.environ[key] = value


def keywords(limit: int = 100) -> list[str]:
    """認識側に渡す語。本体の用語表を使う（ここに複製しない）。"""
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
                    # gpt-live-transcribe は turn detection に対応しない。
                    # 「Turn detection is not supported for this transcription model」
                    # モデルが自分の判断で区切りを返す。
                    "turn_detection": None,
                }
            },
        },
    }

    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    segments: list[tuple[float, float, str]] = []   # (発話終了の音声位置, 遅延, 文字列)
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
                # 実時間に合わせる。送り終えた分と経過時間の差を待つ。
                target = t0 + (i + 1) * CHUNK_MS / 1000
                await asyncio.sleep(max(0.0, target - time.perf_counter()))

        debug = bool(os.environ.get("DEBUG"))
        seen: dict[str, int] = {}
        # gpt-live-transcribe は completed を返さない。delta を item_id ごとに束ねる。
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
                        items[item_id] = {"text": "", "first": now, "last": now}
                        order.append(item_id)
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

        # 送り終えてから delta が止まるまでが、実時間からの遅れ。
        quiet_since = time.perf_counter()
        while time.perf_counter() - quiet_since < 3.0:
            before = last_delta_at[0]
            await asyncio.sleep(0.25)
            if last_delta_at[0] != before:
                quiet_since = time.perf_counter()
        # 最後に commit を送ると completed が来るかを見る
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
    print("item ごとの中身:")
    for i in order:
        it = items[i]
        print(f"  [{it['first']:6.1f}s → {it['last']:6.1f}s / {len(it['text']):4d}字]")
        print(f"    {it['text'].strip()}")
    return 0


def main() -> int:
    load_env()
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
