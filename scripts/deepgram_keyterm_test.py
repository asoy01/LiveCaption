#!/usr/bin/env python3
"""See whether the keyterm option of Deepgram nova-3 really works in Japanese.

    pixi run python scripts/deepgram_keyterm_test.py [audio file]

It sends the same audio four ways (language=ja / multi, with and without
keyterm) and prints the outputs side by side.
The default audio is data/recordings/tts_jargon.wav, made with the Windows
speech synthesizer.

What Deepgram announced was "keyterm support on the multilingual model".
It may not work with language=ja (a single language) and work only with
language=multi, so both are compared.

Note: synthetic speech is too clean. Make the real decision with a recording
of a real meeting. The only thing to see here is whether keyterm changes the
output.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIO = PROJECT_ROOT / "data" / "recordings" / "tts_jargon.wav"
TIMEOUT = 120.0

# The spelling we want to see. The same strings are passed as keyterm.
KEYTERMS = [
    "タイプA",
    "防振系",
    "サスペンション",
    "ダンピング",
    "PRM",
    "アライメント",
    "ビームスプリッター",
    "OMC",
    "リサイクリングゲイン",
]

CONFIGS = [("ja", False), ("ja", True), ("multi", False), ("multi", True)]


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


def transcribe(key: str, audio: bytes, params: list[tuple[str, str]]) -> str:
    url = "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=audio,
        headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            body = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return f"[HTTP {e.code}] {e.read().decode('utf-8', 'replace')[:200]}"
    except urllib.error.URLError as e:
        return f"[接続失敗] {e.reason}"
    return body["results"]["channels"][0]["alternatives"][0]["transcript"]


def main() -> int:
    load_env()
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        print("DEEPGRAM_API_KEY が無い。.env に書くこと。")
        return 1

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_AUDIO
    if not path.exists():
        print(f"音声ファイルが無い: {path}")
        return 1
    audio = path.read_bytes()
    print(f"音声: {path.name}  ({len(audio):,} bytes)")
    print()

    kt = [("keyterm", t) for t in KEYTERMS]
    results: dict[tuple[str, bool], str] = {}

    for lang, use_kt in CONFIGS:
        params = [("model", "nova-3"), ("language", lang), ("punctuate", "true")]
        if use_kt:
            params = params + kt
        text = transcribe(key, audio, params)
        results[(lang, use_kt)] = text
        print(f"language={lang}  keyterm={'あり' if use_kt else 'なし'}")
        print(f"  {text or '(空)'}")
        print()

    print("用語ごとの命中")
    print(f"  {'用語':<20}  ja  ja+kt  multi  multi+kt")
    for term in KEYTERMS:
        marks = ["○" if term in results[c] else "×" for c in CONFIGS]
        print(f"  {term:<20}  {marks[0]}    {marks[1]}      {marks[2]}       {marks[3]}")
    print()

    for lang in ("ja", "multi"):
        same = results[(lang, False)] == results[(lang, True)]
        verdict = "出力が同一 → keyterm は作用していない" if same else "出力が変わった → keyterm は作用している"
        print(f"  language={lang:<6}: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
