#!/usr/bin/env python3
"""同じ音声を複数の音声認識に通して、書き起こしを保存する。

    pixi run python scripts/transcribe_all.py <16k mono wav> [OpenAI用の小さいファイル]

第2引数を省くと、OpenAI にも第1引数を送る。OpenAI の文字起こしAPIは
**1ファイル25 MBまで**なので、長い音声では元の m4a を第2引数に渡すこと。

走らせるもの:
    1. Deepgram nova-3  language=multi   本命候補
    2. Deepgram whisper-large            ローカル構成(whisper.cpp)の品質の代理
    3. OpenAI gpt-transcribe             プロンプトなし
    4. OpenAI gpt-transcribe             用語プロンプトあり

language は ja に固定しない。KAGRAの朝礼は前半が英語、後半が日本語で、
1つの会議の中で切り替わるため。

結果は local/compare/<音声名>.<部品名>.txt に保存する。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import glossary as glossary_mod  # noqa: E402

OUT_DIR = PROJECT_ROOT / "local" / "compare"
TIMEOUT = 1800.0
OPENAI_MAX_BYTES = 25 * 1024 * 1024


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


def glossary_terms() -> list[str]:
    """OpenAI の prompt に渡す語。本体の用語表を使う（ここに複製しない）。"""
    return [e.ja for e in glossary_mod.load()]


# ---------------------------------------------------------------- Deepgram


def deepgram(audio: bytes, params: list[tuple[str, str]], mime: str) -> str:
    url = "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=audio,
        headers={
            "Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}",
            "Content-Type": mime,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        body = json.loads(res.read().decode("utf-8"))
    return body["results"]["channels"][0]["alternatives"][0]["transcript"]


# ------------------------------------------------------------------ OpenAI


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    """multipart/form-data の本体を組み立てる。標準ライブラリだけで済ませるため。"""
    boundary = uuid.uuid4().hex
    sep = f"--{boundary}".encode()
    chunks = []
    for key, value in fields.items():
        chunks.append(sep)
        chunks.append(f'Content-Disposition: form-data; name="{key}"'.encode())
        chunks.append(b"")
        chunks.append(value.encode("utf-8"))
    chunks.append(sep)
    chunks.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    chunks.append(b"Content-Type: application/octet-stream")
    chunks.append(b"")
    chunks.append(content)
    chunks.append(f"--{boundary}--".encode())
    return b"\r\n".join(chunks), f"multipart/form-data; boundary={boundary}"


def openai_transcribe(path: Path, model: str, prompt: str | None) -> str:
    content = path.read_bytes()
    if len(content) > OPENAI_MAX_BYTES:
        raise ValueError(
            f"{path.name} は {len(content)/1024/1024:.1f} MB で、OpenAI の上限 25 MB を超える。"
            " 圧縮済みのファイルを第2引数に渡すこと。"
        )
    fields = {"model": model, "response_format": "json"}
    if prompt:
        fields["prompt"] = prompt
    body, content_type = multipart(fields, path.name, content)
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "Content-Type": content_type,
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8"))["text"]


# -------------------------------------------------------------------- main


def main() -> int:
    load_env()
    for key in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
        if not os.environ.get(key):
            print(f"{key} が無い。")
            return 1

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    wav = Path(sys.argv[1])
    small = Path(sys.argv[2]) if len(sys.argv) > 2 else wav
    if not wav.exists():
        print(f"音声ファイルが無い: {wav}")
        return 1

    audio = wav.read_bytes()
    terms = glossary_terms()
    prompt = "専門用語: " + "、".join(terms)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = wav.stem
    print(f"音声: {wav.name} ({len(audio)/1024/1024:.1f} MB)")
    print(f"OpenAI用: {small.name} ({small.stat().st_size/1024/1024:.1f} MB)")
    print(f"用語プロンプト: {len(terms)} 語")
    print()

    runs = [
        ("deepgram-nova3-multi", lambda: deepgram(
            audio,
            [("model", "nova-3"), ("language", "multi"),
             ("punctuate", "true"), ("smart_format", "true")],
            "audio/wav")),
        # language を省くと Whisper は勝手に英訳して返す（task=translate になる）。
        # その英訳は品質が低く、用語表も通らないので使えない。必ず language を指定する。
        ("deepgram-whisper-large", lambda: deepgram(
            audio,
            [("model", "whisper-large"), ("language", "ja"), ("punctuate", "true")],
            "audio/wav")),
        ("deepgram-whisper-large-autotranslate", lambda: deepgram(
            audio,
            [("model", "whisper-large"), ("punctuate", "true")],
            "audio/wav")),
        ("openai-gpt-transcribe", lambda: openai_transcribe(
            small, "gpt-transcribe", None)),
        ("openai-gpt-transcribe-prompt", lambda: openai_transcribe(
            small, "gpt-transcribe", prompt)),
    ]

    only = os.environ.get("ONLY", "").strip()
    if only:
        runs = [(n, r) for n, r in runs if only in n]
        print(f"ONLY={only} で {len(runs)} 件に絞った")
        print()

    for name, run in runs:
        print(f"{name} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            text = run()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            print(f"HTTP {e.code}")
            print(f"   {detail}")
            continue
        except Exception as e:  # noqa: BLE001 - 1つ失敗しても残りは回す
            print(f"失敗: {type(e).__name__}: {e}")
            continue
        dt = time.perf_counter() - t0
        path = OUT_DIR / f"{stem}.{name}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"{dt:.0f} 秒  {len(text):,} 文字  -> {path.name}")

    print()
    print(f"保存先: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
