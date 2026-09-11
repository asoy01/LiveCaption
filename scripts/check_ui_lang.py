#!/usr/bin/env python3
"""操作画面を英語で組み立てて、訳し残した日本語を報告する。

    pixi run python scripts/check_ui_lang.py

**訳表（`src/live_caption/i18n.py`）に無い文字列は、英語にしても日本語のまま出る。**
壊れはしないが、画面が日英混在になる。操作画面の文言を足したら、これを走らせること。

コメント（`/* */`・`//`・`<!-- -->`）は画面に出ないので数えない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from live_caption import i18n, web  # noqa: E402

HISTORY = 300


def strip_comments(text: str) -> str:
    """画面に出ない部分と、訳さない部分を落とす。"""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)      # CSS と JS のブロック
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)     # HTML
    # 行末コメント。`https://` を巻き込まないよう、空白か行頭に続くものだけ。
    text = re.sub(r"(?m)(^|\s)//.*$", " ", text)
    # 言語を選ぶ欄。**言語の名前は訳さない。** 日本語は日本語で書いてあるほうが探せる。
    text = re.sub(r'<select id="uiLang".*?</select>', " ", text, flags=re.S)
    return text


def main() -> int:
    page = web._head("Live Captions ・ 操作", 8) + web.CONTROL_BODY.replace(
        "__FEED_JS__",
        web.FEED_JS.replace("__FEED__", "/api/lines")
        .replace("__HISTORY__", str(HISTORY))
        .replace("__SOURCE_DEFAULT__", "false"),
    )
    left = i18n.remaining_japanese(strip_comments(i18n.apply(page, "en")))

    if not left:
        print("訳し残しは無い。")
        return 0

    print(f"訳し残し: {len(left)} 件")
    for word in left:
        print(f"    {word}")
    print()
    print("src/live_caption/i18n.py の EN に足すこと。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
