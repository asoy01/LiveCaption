#!/usr/bin/env python3
"""音声認識の誤りを、翻訳段の用語対訳表で復元できるかを試す。

    pixi run python scripts/translate_test.py

Deepgram が返した実際の誤認識テキストを入力に使う。日本語の漢字が誤っていても、
音さえ合っていれば、用語対訳表を持ったLLMが正しい英語を出せるはず、という仮説を試す。

**プロンプトと用語表の読み込みは本体（src/live_caption/）を使う。**
ここに複製しないこと。複製すると必ずずれる。
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config, glossary  # noqa: E402
from live_caption.translator import build_system, chat  # noqa: E402

MODELS = ["gpt-5-mini", "gpt-4.1-mini"]

# Deepgram が実際に返したテキスト。正しくは:
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
    print(f"用語対訳表: {len(entries)} 語")
    print()

    for i, text in enumerate(SAMPLES, 1):
        print(f"--- 入力 {i} (音声認識の生出力) ---")
        print(f"  {text}")
        for model in MODELS:
            out, dt = chat(model, system, text)
            print(f"  [{model}]  {dt:.1f} 秒")
            for line in out.splitlines():
                print(f"    {line}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
