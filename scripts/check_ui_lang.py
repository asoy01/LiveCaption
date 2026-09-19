#!/usr/bin/env python3
"""操作画面を日本語と英語で組み立てて、2つ調べる。

    pixi run python scripts/check_ui_lang.py

1. **スクリプトが壊れていないか。** 訳した文字列は JavaScript の文字列リテラルの
   中にも入る。訳文に `"` が混ざるとリテラルがそこで閉じ、**操作画面の
   スクリプト全体が構文エラーになって、どのボタンも効かなくなる。**
   日本語では何ともないので、英語で使っている人にだけ起きる。
   2026-09-19 にこれで操作画面が丸ごと死んだ。訳表の `「」` が `"` に化けていた。
2. **訳し残した日本語が無いか。** 訳表（`src/live_caption/i18n.py`）に無い
   文字列は、英語にしても日本語のまま出る。壊れはしないが、画面が日英混在になる。

操作画面の文言を足したら、これを走らせること。

1 には `node` が要る。無ければその検査だけ飛ばす。
コメント（`/* */`・`//`・`<!-- -->`）は画面に出ないので、2 では数えない。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
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


def build_page() -> str:
    return web._head("Live Captions ・ 操作", 8) + web.CONTROL_BODY.replace(
        "__FEED_JS__",
        web.FEED_JS.replace("__FEED__", "/api/lines")
        .replace("__HISTORY__", str(HISTORY))
        .replace("__SOURCE_DEFAULT__", "false"),
    )


def scripts_of(page: str) -> str:
    return "\n".join(m.group(1) for m in re.finditer(r"<script>(.*?)</script>",
                                                     page, flags=re.S))


def check_syntax(page: str) -> list[str]:
    """それぞれの言語で、スクリプトが構文として通るかを見る。"""
    node = shutil.which("node")
    if node is None:
        print("node が無いので、スクリプトの構文検査は飛ばした。")
        return []

    bad = []
    with tempfile.TemporaryDirectory() as tmp:
        for lang in i18n.LANGS:
            js = Path(tmp) / f"control-{lang}.js"
            js.write_text(scripts_of(i18n.apply(page, lang)), encoding="utf-8")
            out = subprocess.run([node, "--check", str(js)], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace")
            if out.returncode != 0:
                bad.append(f"[{lang}] {(out.stderr or out.stdout).strip()}")
    return bad


def main() -> int:
    page = build_page()

    broken = check_syntax(page)
    if broken:
        print("操作画面のスクリプトが構文エラーになる。このままでは全部のボタンが効かない。")
        for item in broken:
            print()
            print(item)
        print()
        print("訳文に \" を入れていないか、JavaScript の文字列に 「」 を書いていないか"
              "（訳表で \" に化ける）を見ること。")
        return 1
    print("スクリプトの構文: 日本語・英語ともに通る。")

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
