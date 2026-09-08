#!/usr/bin/env python3
"""各音声認識の書き起こしを、同じ翻訳段に通して英語を比べる。

    pixi run python scripts/compare_translation.py [先頭何文字を使うか] [対象のパターン]

    例: pixi run python scripts/compare_translation.py 1100 "mix.*.txt"
        pixi run python scripts/compare_translation.py 900 "en.openai-gpt-transcribe.txt"

`local/compare/` の書き起こしを読み、それぞれの先頭 N 文字を同じプロンプト・
同じ用語対訳表で英訳して並べる。

**判定はここで行う。** 日本語の書き起こしを並べても意味がない。
認識は音を当てて漢字を外すので、最終的な英語が正しいかだけが問題になる。

**プロンプトと用語表の読み込みは本体（src/live_caption/）を使う。**
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config, glossary  # noqa: E402
from live_caption.translator import build_system, chat  # noqa: E402

COMPARE_DIR = PROJECT_ROOT / "local" / "compare"
MODEL = "gpt-4.1-mini"

# 2026-09-03 朝礼の冒頭（先頭1100文字前後）で、英語に出るべき語と、出てはいけない語。
# 目で読むだけだと改善したか分からないので、機械的に数えられるようにしておく。
# 別の素材を使うときは、この2つを書き換えること。
EXPECTED = [
    "interferometer", "birefringence", "polarization", "half-wave plate",
    "PBS", "OMMT", "cavity scan", "eigenpolarization", "beat note",
    "transmitted power", "Saito",
]
# 「干渉計」を「防振系/懸架系」と取り違えた徴候。実際に起きた誤訳。
FORBIDDEN = ["suspension system", "vibration isolation"]
# Whisper が捏造した、その場にいない人名。
HALLUCINATED = ["Terada", "Odash"]


def check(text: str) -> tuple[list[str], list[str]]:
    low = text.lower()
    missing = [t for t in EXPECTED if t.lower() not in low]
    bad = [t for t in FORBIDDEN + HALLUCINATED if t.lower() in low]
    return missing, bad


def main() -> int:
    config.load_env()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    pattern = sys.argv[2] if len(sys.argv) > 2 else "mix.*.txt"

    # 期待語のリストは 2026-09-03 朝礼の日本語部分に合わせてある。
    # 別の素材では意味を成さないので、判定を出さない。
    do_check = pattern.startswith("mix.")

    files = sorted(COMPARE_DIR.glob(pattern))
    if not files:
        print(f"書き起こしが無い: {COMPARE_DIR / pattern}")
        return 1

    entries = glossary.load()
    system = build_system(entries)
    print(f"用語対訳表: {len(entries)} 語")
    print(f"各書き起こしの先頭 {n} 文字を {MODEL} で英訳する")
    print()

    summary = []
    for path in files:
        text = path.read_text(encoding="utf-8")[:n]
        out, dt = chat(MODEL, system, text)
        missing, bad = check(out)
        summary.append((path.stem.replace("mix.", ""), missing, bad))

        print("=" * 72)
        print(path.stem.replace("mix.", ""))
        print("=" * 72)
        print(f"[入力 {len(text)} 文字 / 翻訳 {dt:.1f} 秒]")
        print()
        for line in out.splitlines():
            print(f"  {line}")
        print()

    if not do_check:
        return 0

    print("=" * 72)
    print(f"判定（期待語 {len(EXPECTED)} 個）")
    print("=" * 72)
    for name, missing, bad in summary:
        hit = len(EXPECTED) - len(missing)
        print(f"  {name}")
        print(f"    命中 {hit}/{len(EXPECTED)}")
        if missing:
            print(f"    出なかった: {', '.join(missing)}")
        if bad:
            print(f"    出てはいけない語: {', '.join(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
