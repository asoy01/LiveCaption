#!/usr/bin/env python3
"""Check the Deepgram API key and the nova-3 parameter combinations.

    pixi run python scripts/deepgram_check.py

The key is read from DEEPGRAM_API_KEY in .env, or from the environment.

What it checks:
    1. Is the key valid (through /v1/projects, which is not billed)
    2. Does nova-3 accept language=ja
    3. **Does nova-3 accept language=ja and keyterm at the same time**
    4. What happens with language=multi

3 is the one that matters. If it fails, the reason to choose Deepgram
(naming the terms) is gone.

The audio is Deepgram's public sample, in English. The transcript itself is
not examined. It only looks at whether the parameter combination is accepted.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_AUDIO_URL = "https://dpgr.am/spacewalk.wav"
TIMEOUT = 60.0

# Terms from the field of the meeting, as they would really be used. This also
# shows whether Japanese goes through as a keyterm.
KEYTERMS = ["サスペンション", "防振系", "PRM", "干渉計", "type-A"]


def load_env() -> None:
    """Read .env if it exists. **.env wins and overwrites the environment.**"""
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


def request(url: str, key: str, body: dict | None = None):
    """Return (status, response). status is None when the connection fails."""
    headers = {"Authorization": f"Token {key}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:300]}
    except urllib.error.URLError as e:
        return None, {"error": str(e.reason)}


def check_auth(key: str) -> bool:
    print("1. Check the key (/v1/projects, not billed)")
    status, body = request("https://api.deepgram.com/v1/projects", key)
    if status == 200:
        names = [p.get("name", "?") for p in body.get("projects", [])]
        print(f"   OK   Projects: {', '.join(names) or '(none)'}\n")
        return True
    print(f"   NG   status={status}  {json.dumps(body, ensure_ascii=False)[:200]}\n")
    return False


def check_combo(key: str, label: str, params: list[tuple[str, str]]) -> None:
    url = "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)
    status, body = request(url, key, {"url": SAMPLE_AUDIO_URL})

    if status == 200:
        # The content is the English sample, so it is not examined. Only that
        # the request went through.
        alts = body["results"]["channels"][0]["alternatives"][0]
        n = len(alts.get("transcript", ""))
        print(f"   OK   {label}  (transcript {n} chars)")
    else:
        msg = body.get("err_msg") or body.get("reason") or json.dumps(body, ensure_ascii=False)
        print(f"   NG   {label}  status={status}")
        print(f"        {msg[:250]}")


def main() -> int:
    load_env()
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        print("DEEPGRAM_API_KEY is not set.")
        print(f"Copy .env.example to .env and write the key there: {PROJECT_ROOT / '.env'}")
        return 1

    if not check_auth(key):
        return 1

    kt = [("keyterm", t) for t in KEYTERMS]

    print("2. nova-3 parameter combinations")
    check_combo(key, "language=ja                     ", [("model", "nova-3"), ("language", "ja")])
    check_combo(key, "language=ja   + keyterm (main)  ", [("model", "nova-3"), ("language", "ja")] + kt)
    check_combo(key, "language=multi                  ", [("model", "nova-3"), ("language", "multi")])
    check_combo(key, "language=multi + keyterm        ", [("model", "nova-3"), ("language", "multi")] + kt)

    print("\nIf the main line is NG, the reason to choose Deepgram (naming the terms) is gone.")
    print("In that case, review the comparison of designs in HANDOFF.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
