#!/usr/bin/env python3
"""Deepgram の API キーと、nova-3 のパラメータの組み合わせを確認する。

    pixi run python scripts/deepgram_check.py

キーは .env の DEEPGRAM_API_KEY、または環境変数から読む。

確認すること:
    1. キーが有効か（課金の発生しない /v1/projects で確認）
    2. nova-3 で language=ja が使えるか
    3. **nova-3 で language=ja と keyterm を同時に指定できるか**
    4. language=multi ではどうか

3 が本命。ここが通らないと、Deepgram を選ぶ理由（用語指定）が消える。

音声は Deepgram の公開サンプル（英語）を使う。文字起こしの中身は見ない。
パラメータの組み合わせが受け付けられるかだけを見る。
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

# 実際に使う予定の KAGRA 用語。日本語が keyterm に通るかも同時に見る。
KEYTERMS = ["サスペンション", "防振系", "PRM", "干渉計", "type-A"]


def load_env() -> None:
    """.env があれば読む。**.env の値を優先し、環境変数を上書きする。**"""
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


def request(url: str, key: str, body: dict | None = None):
    """(status, 応答) を返す。status は接続失敗時 None。"""
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
    print("1. キーの確認 (/v1/projects, 課金なし)")
    status, body = request("https://api.deepgram.com/v1/projects", key)
    if status == 200:
        names = [p.get("name", "?") for p in body.get("projects", [])]
        print(f"   OK   プロジェクト: {', '.join(names) or '(なし)'}\n")
        return True
    print(f"   NG   status={status}  {json.dumps(body, ensure_ascii=False)[:200]}\n")
    return False


def check_combo(key: str, label: str, params: list[tuple[str, str]]) -> None:
    url = "https://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params)
    status, body = request(url, key, {"url": SAMPLE_AUDIO_URL})

    if status == 200:
        # 中身は英語サンプルなので見ない。通ったことだけ確認する。
        alts = body["results"]["channels"][0]["alternatives"][0]
        n = len(alts.get("transcript", ""))
        print(f"   OK   {label}  (transcript {n} 文字)")
    else:
        msg = body.get("err_msg") or body.get("reason") or json.dumps(body, ensure_ascii=False)
        print(f"   NG   {label}  status={status}")
        print(f"        {msg[:250]}")


def main() -> int:
    load_env()
    key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if not key:
        print("DEEPGRAM_API_KEY が無い。")
        print(f".env.example を .env にコピーして、キーを書くこと: {PROJECT_ROOT / '.env'}")
        return 1

    if not check_auth(key):
        return 1

    kt = [("keyterm", t) for t in KEYTERMS]

    print("2. nova-3 のパラメータの組み合わせ")
    check_combo(key, "language=ja                     ", [("model", "nova-3"), ("language", "ja")])
    check_combo(key, "language=ja   + keyterm (本命)  ", [("model", "nova-3"), ("language", "ja")] + kt)
    check_combo(key, "language=multi                  ", [("model", "nova-3"), ("language", "multi")])
    check_combo(key, "language=multi + keyterm        ", [("model", "nova-3"), ("language", "multi")] + kt)

    print("\n本命の行が NG なら、Deepgram を選ぶ理由（用語指定）が消える。")
    print("その場合は HANDOFF.md の構成比較を見直すこと。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
