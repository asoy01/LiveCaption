#!/usr/bin/env python3
"""字幕1文ぶんの翻訳にかかる時間を測る。

    pixi run python scripts/translate_latency.py

まとめて訳したときの値（1.7〜2.6秒）は本番の条件と違う。本番は1文ずつ送るので、
実際の長さで測る。

入力は、ストリーミング認識が実際に返した日本語（KAGRA朝礼）。

**プロンプトと用語表の読み込みは本体（src/live_caption/）を使う。**
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from live_caption import config, glossary  # noqa: E402
from live_caption.translator import build_system, chat  # noqa: E402

MODELS = ["gpt-4.1-mini", "gpt-4.1-nano"]

# gpt-live-transcribe が実際に返した文。1文ずつ送る想定。
SENTENCES = [
    "干渉計の方は先ほど言った通りというか、書いてある通りというところですが、藤本くんなんか追加コメントなりディスカッションなりありますか？",
    "その一昨日の時点ではなんかビートで見たときにS偏光しか入れてないのになんかピークが2つあるってことで、それは機材による複屈折によってこういう偏光が斜めの直線偏光になってるんじゃないかって思われていて。",
    "昨日そのOMMT2の反射のところでそのハーフウェーブプレートとPBSを置いて、できた偏光を傾けてPBSの反射と透過でその2つの固有偏光のそれぞれのキャビティスキャンを見れるようにしました。",
    "ビートじゃなくて透過光量を確認したんですけど、やっぱりキャビティスキャンのピークはずれていました。",
    "Okay, so let's start the morning briefing.",
    "Also, Woza-san prepared the infrared camera to monitor the beam spot position on the OMT1.",
]


def main() -> int:
    config.load_env()
    entries = glossary.load()
    system = build_system(entries)
    print(f"用語対訳表: {len(entries)} 語")
    print()

    times: dict[str, list[float]] = {m: [] for m in MODELS}
    for i, ja in enumerate(SENTENCES, 1):
        print(f"--- {i}. ({len(ja)}字) {ja[:60]}...")
        for model in MODELS:
            out, dt = chat(model, system, ja)
            times[model].append(dt)
            lines = out.splitlines() or [""]
            print(f"  [{model:<14} {dt:4.1f}s]  {lines[0][:110]}")
            for line in lines[1:]:
                print(f"  {'':<21}  {line[:110]}")
        print()

    print("=" * 66)
    for model in MODELS:
        ts = times[model]
        print(f"{model:<14} 最小 {min(ts):.1f}s  中央 {sorted(ts)[len(ts)//2]:.1f}s  最大 {max(ts):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
