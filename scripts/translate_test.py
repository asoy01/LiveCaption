#!/usr/bin/env python3
"""Try to recover misrecognitions with the glossary in the translation stage.

    pixi run python scripts/translate_test.py

The input is real misrecognized text that Deepgram returned. The idea under
test is this: even when the Japanese kanji are wrong, an LLM that holds the
glossary should still produce the right English as long as the sounds match.

**The prompt and the glossary are loaded through the engine
(src/live_caption/).**
Do not copy them here. A copy always drifts.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config, glossary  # noqa: E402
from live_caption.translator import build_system, chat  # noqa: E402

MODELS = ["gpt-5-mini", "gpt-4.1-mini"]

# Text that Deepgram actually returned. The correct form is:
#   「タイプAの防振系のダンパーについて報告します。防振系のアライメントを確認してください。」
SAMPLES = [
    "タイプaの防寒系のダンパーについて報告します。防寒系のアライメントを確認してください。",
    "タイプaの棒神経のダンパーについて報告します。棒神経のアライメントを確認してください。",
    "次に、タイプaの防腐系について報告します。サスペンションのダンピングが不安定で、"
    "prmのアライメントがずれていました。ビームスプリッターとomcも確認しましたが、"
    "リサイクリングゲインは下がったままです。",
]


def main() -> int:
    config.load_env()
    entries = glossary.load()
    system = build_system(entries)
    print(f"Glossary: {len(entries)} entries")
    print()

    for i, text in enumerate(SAMPLES, 1):
        print(f"--- Input {i} (raw output of speech recognition) ---")
        print(f"  {text}")
        for model in MODELS:
            out, dt = chat(model, system, text)
            print(f"  [{model}]  {dt:.1f} s")
            for line in out.splitlines():
                print(f"    {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
