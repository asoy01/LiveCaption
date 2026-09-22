#!/usr/bin/env python3
"""Run the same audio through several speech recognizers and save the
transcripts.

    pixi run python scripts/transcribe_all.py <16k mono wav> [smaller file for OpenAI]

Leave out the second argument and the first one goes to OpenAI too. The
OpenAI transcription API takes **at most 25 MB per file**, so for long audio
pass the original m4a as the second argument.

What it runs:
    1. Deepgram nova-3  language=multi   the main candidate
    2. Deepgram whisper-large            a stand-in for the quality of a local
                                         setup (whisper.cpp)
    3. OpenAI gpt-transcribe             without a prompt
    4. OpenAI gpt-transcribe             with a prompt of terms

The language is not pinned to ja. The morning meeting used here is in English
in the first half and in Japanese in the second, so it switches inside one
meeting.

The results are saved to local/compare/<audio name>.<part name>.txt.
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
        # .env wins. It overwrites an environment variable of the same name.
        if value:
            os.environ[key] = value


def glossary_terms() -> list[str]:
    """Terms for the OpenAI prompt. It uses the engine's glossary; do not copy
    the list here."""
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
    """Build a multipart/form-data body, so that the standard library is
    enough."""
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
            f"{path.name} is {len(content)/1024/1024:.1f} MB, over the OpenAI limit of 25 MB."
            " Pass a compressed file as the second argument."
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
            print(f"{key} is not set.")
            return 1

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    wav = Path(sys.argv[1])
    small = Path(sys.argv[2]) if len(sys.argv) > 2 else wav
    if not wav.exists():
        print(f"No audio file: {wav}")
        return 1

    audio = wav.read_bytes()
    terms = glossary_terms()
    prompt = "専門用語: " + "、".join(terms)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = wav.stem
    print(f"Audio: {wav.name} ({len(audio)/1024/1024:.1f} MB)")
    print(f"For OpenAI: {small.name} ({small.stat().st_size/1024/1024:.1f} MB)")
    print(f"Term prompt: {len(terms)} terms")
    print()

    runs = [
        ("deepgram-nova3-multi", lambda: deepgram(
            audio,
            [("model", "nova-3"), ("language", "multi"),
             ("punctuate", "true"), ("smart_format", "true")],
            "audio/wav")),
        # Leave out language and Whisper translates into English on its own
        # (it becomes task=translate). That translation is poor and the
        # glossary does not reach it, so it cannot be used. Always give
        # language.
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
        print(f"ONLY={only} narrowed it down to {len(runs)} runs")
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
        except Exception as e:  # noqa: BLE001 - one failure must not stop the rest
            print(f"Failed: {type(e).__name__}: {e}")
            continue
        dt = time.perf_counter() - t0
        path = OUT_DIR / f"{stem}.{name}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"{dt:.0f} s  {len(text):,} chars  -> {path.name}")

    print()
    print(f"Saved to: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
