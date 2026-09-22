#!/usr/bin/env python3
"""Send each transcript through the same translation stage and compare the
English.

    pixi run python scripts/compare_translation.py [leading characters] [pattern]

    Example: pixi run python scripts/compare_translation.py 1100 "mix.*.txt"
             pixi run python scripts/compare_translation.py 900 "en.openai-gpt-transcribe.txt"

It reads the transcripts in `local/compare/`, translates the first N
characters of each into English with the same prompt and the same glossary,
and prints them one after another.

**The judgement is made here.** Putting the Japanese transcripts side by side
tells you nothing. Speech recognition matches the sound and picks the wrong
kanji, so the only question is whether the final English is right.

**The prompt and the glossary are loaded through the engine
(src/live_caption/).**
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config, glossary  # noqa: E402
from live_caption.translator import build_system, chat  # noqa: E402

COMPARE_DIR = PROJECT_ROOT / "local" / "compare"
MODEL = "gpt-4.1-mini"

# Words that must appear in the English, and words that must not, for the
# opening of the 2026-09-03 morning meeting (the first 1100 characters or so).
# Reading it by eye does not tell you whether it improved, so keep it
# countable by machine.
# Rewrite these two lists when you use different material.
EXPECTED = [
    "interferometer", "birefringence", "polarization", "half-wave plate",
    "PBS", "OMMT", "cavity scan", "eigenpolarization", "beat note",
    "transmitted power", "Saito",
]
# Signs that "干渉計" (interferometer) was taken for the vibration isolation
# or suspension system. This mistranslation really happened.
FORBIDDEN = ["suspension system", "vibration isolation"]
# Names of people who were not there, invented by Whisper.
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

    # The list of expected words matches the Japanese part of the 2026-09-03
    # morning meeting. It means nothing for other material, so no judgement is
    # printed then.
    do_check = pattern.startswith("mix.")

    files = sorted(COMPARE_DIR.glob(pattern))
    if not files:
        print(f"No transcript found: {COMPARE_DIR / pattern}")
        return 1

    entries = glossary.load()
    system = build_system(entries)
    print(f"Glossary: {len(entries)} entries")
    print(f"Translating the first {n} characters of each transcript with {MODEL}")
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
        print(f"[input {len(text)} chars / translated in {dt:.1f} s]")
        print()
        for line in out.splitlines():
            print(f"  {line}")
        print()

    if not do_check:
        return 0

    print("=" * 72)
    print(f"Result ({len(EXPECTED)} expected words)")
    print("=" * 72)
    for name, missing, bad in summary:
        hit = len(EXPECTED) - len(missing)
        print(f"  {name}")
        print(f"    hit {hit}/{len(EXPECTED)}")
        if missing:
            print(f"    missing: {', '.join(missing)}")
        if bad:
            print(f"    must not appear: {', '.join(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
