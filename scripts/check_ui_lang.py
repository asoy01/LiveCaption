#!/usr/bin/env python3
"""Checks to run before release. It inspects the control page and the
launcher files.

    pixi run python scripts/check_ui_lang.py

1. **Is the script still valid?** Translated strings also go inside
   JavaScript string literals. A `"` in a translation closes the literal
   there, and **the whole control page script becomes a syntax error, so no
   button works at all.** Nothing goes wrong in Japanese, so it only hits
   people who use English.
   On 2026-09-19 the whole control page died this way. A `「」` in the
   translation table had turned into `"`.
2. **Is any Japanese left untranslated?** A string that is not in the
   translation table (`src/live_caption/i18n.py`) stays Japanese even in
   English. Nothing breaks, but the page mixes the two languages.

3. **Is the launcher `.vbs` pure ASCII?** Windows Script Host reads a `.vbs`
   in the system ANSI code page. Write a Japanese comment in UTF-8 and
   **nothing happens. No error. No window.**
   On 2026-09-19 we hit this: a double-click left nothing resident.

Run this after you add wording to the control page.

Check 1 needs `node`. If it is missing, only that check is skipped.
Comments (`/* */`, `//`, `<!-- -->`) never reach the screen, so check 2 does
not count them.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from live_caption import i18n, meetings_page, schedule, web  # noqa: E402

HISTORY = 300


def strip_comments(text: str) -> str:
    """Drop what never reaches the screen, and what we do not translate."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)      # CSS and JS blocks
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)     # HTML
    # End-of-line comments. Only ones after a space or at the start of a line,
    # so that `https://` is not caught.
    text = re.sub(r"(?m)(^|\s)//.*$", " ", text)
    # The language selector. **Do not translate the language names.** Japanese
    # is easier to find when it is written in Japanese.
    text = re.sub(r'<select id="uiLang".*?</select>', " ", text, flags=re.S)
    return text


def build_page() -> str:
    return web._head("Live Captions ・ 操作", 8) + web.CONTROL_BODY.replace(
        "__FEED_JS__",
        web.FEED_JS.replace("__FEED__", "/api/lines")
        .replace("__HISTORY__", str(HISTORY))
        .replace("__SOURCE_DEFAULT__", "false"),
    )


def build_meetings_page() -> str:
    """The meeting management page. **Check it apart from the control page.**

    This one is not part of `web.CONTROL_BODY`, so if you look only at the
    control page, both missing translations and syntax errors slip through.
    """
    return web._head("Live Captions ・ 会議の管理", 8) + meetings_page.BODY


def scripts_of(page: str) -> str:
    return "\n".join(m.group(1) for m in re.finditer(r"<script>(.*?)</script>",
                                                     page, flags=re.S))


def check_syntax(page: str) -> list[str]:
    """Check that the scripts parse in each language."""
    node = shutil.which("node")
    if node is None:
        print("node is not installed, so the script syntax check was skipped.")
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


def check_scheduler_strings() -> list[str]:
    """Missing translations in the notes the scheduler puts in the status.

    **These never appear in the page markup.** They travel to the screen in
    `/api/status`, so if you look only at the page, Japanese shows up in the
    English display alone.
    On 2026-09-19 "本体を組み立てているところ" got through this way.
    """
    left = []
    for text in schedule.UI_STRINGS:
        if i18n.remaining_japanese(i18n.apply(text, "en")):
            left.append(text)
    return left


# Files that WSH reads as ANSI. **They have to be pure ASCII.**
ASCII_ONLY = ("StartLiveCaptionTray.vbs",)


def check_ascii_launchers() -> list[str]:
    """Look for non-ASCII bytes in the launcher `.vbs`.

    If there are any, Windows Script Host **silently does nothing.** There is
    no error either, so it takes a long time to find the cause.
    """
    root = Path(__file__).resolve().parent.parent
    bad = []
    for name in ASCII_ONLY:
        path = root / name
        if not path.exists():
            continue
        raw = path.read_bytes()
        offenders = [i for i, b in enumerate(raw) if b > 0x7F]
        if offenders:
            where = offenders[0]
            line = raw[:where].count(b"\n") + 1
            bad.append(f"{name}: {len(offenders)} bytes are not ASCII (the first is on line {line})")
    return bad


def main() -> int:
    page = build_page()

    launchers = check_ascii_launchers()
    if launchers:
        print("The launcher .vbs contains non-ASCII characters. "
              "As it is, a double-click does nothing at all.")
        for item in launchers:
            print(f"    {item}")
        print()
        print("Windows Script Host reads .vbs as ANSI. You cannot write Japanese "
              "comments in it. Put the explanation in docs/manual.ja.md.")
        return 1

    meets = build_meetings_page()

    broken = check_syntax(page) + check_syntax(meets)
    if broken:
        print("The control page script has a syntax error. As it is, no button works.")
        for item in broken:
            print()
            print(item)
        print()
        print("Check that no translation contains a \" , and that no JavaScript "
              "string contains 「」 (the translation table turns them into \").")
        return 1
    print("Script syntax: both Japanese and English parse.")

    left = i18n.remaining_japanese(strip_comments(i18n.apply(page, "en")))
    left += [w for w in i18n.remaining_japanese(
        strip_comments(i18n.apply(meets, "en"))) if w not in left]
    notes = check_scheduler_strings()
    if not left and not notes:
        print("Nothing is left untranslated.")
        return 0

    if left:
        print(f"Untranslated on the page: {len(left)} items")
        for word in left:
            print(f"    {word}")
    if notes:
        print(f"Untranslated scheduler notes: {len(notes)} items"
              " (schedule.UI_STRINGS)")
        for word in notes:
            print(f"    {word}")
    print()
    print("Add them to EN in src/live_caption/i18n.py.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
