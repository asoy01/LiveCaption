"""Loading the glossary.

**There is more than one glossary.** The `.tsv` files in `etc/glossary/` are
selected to match the meeting and stacked together. For example, `Optics` +
`Control`. The selection can be changed from the control page.

They are kept apart because the vocabulary differs between subsystems. Using one
large glossary for every meeting lets unrelated terms eat up the `keywords` of
the speech recognition, and the terms that are really needed are dropped at the
limit (`config.ASR_KEYWORD_LIMIT`).

The format of `etc/glossary/*.tsv` is:

    Japanese (correct spelling) <TAB> English <TAB> common misrecognitions
    (separated by commas)

The third column is the important one. Collecting the misrecognitions that
actually appeared is what makes it work. Put English misrecognitions in as well
(for example, the acronym "PID" heard as the ordinary word "peed").

The glossary is used in two places.

1. The `keywords` of the speech recognition (so the transcription stage picks up
   the sounds)
2. The translation prompt (so the English is restored even when the recognition
   picks the wrong kanji)

The second one is the important one. Simply listing the terms as reference
information does not work, so they are passed as replacement rules. This was
confirmed by measurement.

**Changing the caption direction does not mean rebuilding the glossary.** It is
a `Japanese / English` pair, so `prompt_block()` only swaps the two sides and
the same glossary works for `en2ja`. The `keywords()` passed to the speech
recognition do not depend on the direction at all, because both languages are
always passed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Entry:
    ja: str
    en: str
    wrong: tuple[str, ...]


#: The characters allowed in the name of a glossary. **The control page has no
#: authentication.** The name becomes the file name as it is, so loosening this
#: would let someone walk up the directory tree.
_NAME_OK = re.compile(r"\A[A-Za-z0-9_\-. ぁ-んァ-ヶ一-龠々ー]{1,64}\Z")

#: The upper limit for an upload. A glossary is a few hundred lines of text, so
#: 1 MB is more than enough.
MAX_UPLOAD_BYTES = 1 << 20


def check_name(name: str) -> str:
    """Turn the input into a usable glossary name. `ValueError` if it cannot be
    used.

    **It handles the "name" only, with `.tsv` removed.** Neither a path
    separator nor `..` gets through.
    """
    stem = name[:-4] if name.lower().endswith(".tsv") else name
    stem = stem.strip()
    if not stem or not _NAME_OK.match(stem) or stem in {".", ".."}:
        raise ValueError(
            f'Not usable as a glossary name: "{name}"\n'
            "  Use letters, digits, kana, kanji and ` _ - . ` only, up to 64 characters."
        )
    return stem


def path_of(name: str) -> Path:
    """Build the path of the glossary file from a name. The extension may be
    given or left out."""
    return config.glossary_dir() / f"{check_name(name)}.tsv"


def available() -> list[dict]:
    """The list of glossaries in `etc/glossary/`, by name.

    The number of terms is returned as well. **Without seeing how many terms a
    selection adds up to, you cannot choose.**
    """
    out = []
    root = config.glossary_dir()
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.tsv"), key=lambda q: q.name.lower()):
        out.append({"name": p.stem, "terms": len(load_file(p))})
    return out


def read_text(name: str) -> str:
    """Return the content of a glossary as it is. Used for downloading."""
    path = path_of(name)
    if not path.is_file():
        raise ValueError(f'No glossary with that name: "{name}"')
    return path.read_text(encoding="utf-8")


def save_text(name: str, text: str) -> str:
    """Write a glossary. An existing one with the same name is replaced. Return
    the name that was saved.

    **Check the content before writing.** This is the only way in from the
    control page, so it must not be possible to store a broken glossary and find
    out on the day of the meeting.
    """
    stem = check_name(name)
    raw = text.encode("utf-8")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"The glossary is too large ({len(raw)} bytes). "
            f"The limit is {MAX_UPLOAD_BYTES} bytes."
        )
    if "\x00" in text:
        raise ValueError("This is not text. Give a .tsv file.")

    # **Do not accept a glossary in the wrong format.** A `.csv` saved with
    # commas reads as one Japanese term per whole line. It becomes a broken
    # glossary without a word, and you find out on the day of the meeting, in
    # the form of "the terms are not working".
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.startswith("#")]
    entries = parse_text(text)
    if not entries or not any("\t" in ln for ln in lines):
        raise ValueError(
            "This cannot be read as a glossary. Check the format:\n"
            "  Japanese (correct spelling) <TAB> English <TAB> "
            "common misrecognitions (separated by commas)\n"
            "  **The separator is a tab, not a comma.**\n"
            '  When you export from Excel, choose "Text (Tab delimited)".'
        )

    root = config.glossary_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stem}.tsv"
    # Normalize the line endings to LF. A glossary edited on Windows arrives as
    # it is.
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    # **Never leave a half-written file.** A glossary is sometimes replaced
    # during a meeting.
    tmp = path.with_suffix(".tsv.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return stem


def delete_file(name: str) -> str:
    """Delete a glossary. Return the name that was deleted."""
    path = path_of(name)
    if not path.is_file():
        raise ValueError(f'No glossary with that name: "{name}"')
    path.unlink()
    return path.stem


def parse_text(text: str) -> list[Entry]:
    """Parse the content of a glossary. **It is kept separate from the file,
    because it is also used to check an upload before it is accepted.**"""
    entries: list[Entry] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        ja = cols[0].strip()
        en = cols[1].strip() if len(cols) > 1 else ""
        wrong = tuple(
            w.strip() for w in (cols[2] if len(cols) > 2 else "").split(",") if w.strip()
        )
        if ja:
            entries.append(Entry(ja, en, wrong))
    return entries


def load_file(path: Path) -> list[Entry]:
    """Read one glossary."""
    return parse_text(path.read_text(encoding="utf-8"))


def load(names: list[str] | tuple[str, ...] | None = None) -> list[Entry]:
    """Read the selected glossaries, stacked together. If `names` is None, the
    default selection is used.

    **When the same Japanese term appears in several glossaries, they are merged
    into one.** The English of the first one is kept, and the misrecognition
    column is collected from every glossary. More misrecognitions is better, so
    none are thrown away.

    When the English differs, it is printed. **Dropping one of them without a
    word would leave you puzzling during the meeting over why the English you
    wrote in the glossary does not appear.**
    """
    if names is None:
        names = selection()

    merged: dict[str, Entry] = {}
    for name in names:
        try:
            path = path_of(name)
        except ValueError:
            # **Do not crash while loading.** Even when the remembered
            # selection is broken, the meeting has to be able to start.
            print(f'  [glossary] "{name}" is not a usable name. Skipped')
            continue
        if not path.exists():
            print(f"  [glossary] {path.name} is missing. Skipped")
            continue
        for e in load_file(path):
            old = merged.get(e.ja)
            if old is None:
                merged[e.ja] = e
                continue
            if old.en and e.en and old.en != e.en:
                print(f'  [glossary] "{e.ja}" has two English terms: '
                      f"using {old.en!r}, not {e.en!r} (from {name})")
            wrong = tuple(dict.fromkeys(old.wrong + e.wrong))
            merged[e.ja] = Entry(old.ja, old.en or e.en, wrong)
    return list(merged.values())


def selection() -> tuple[str, ...]:
    """The names of the glossaries selected now. The last selection is
    remembered.

    **It is remembered so that you do not have to choose again for every
    meeting.** Meetings of the same kind usually come one after another. The
    selection is shown both on the startup screen and on the control page, so
    there is no risk of not noticing that it is still the last one.
    """
    try:
        saved = json.loads(config.GLOSSARY_STATE_PATH.read_text(encoding="utf-8"))
        names = tuple(str(n) for n in saved.get("names", []))
    except (OSError, ValueError, AttributeError):
        names = ()
    # Do not keep remembering a glossary that is gone. A broken name is dropped
    # as well.
    names = tuple(n for n in names if _exists(n))
    if names:
        return names
    return tuple(n for n in config.GLOSSARY_DEFAULT if _exists(n))


def _exists(name: str) -> bool:
    try:
        return path_of(name).exists()
    except ValueError:
        return False


def remember(names: list[str] | tuple[str, ...]) -> None:
    """Remember the selection for the next start. It does not crash when the
    file cannot be written."""
    try:
        config.GLOSSARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.GLOSSARY_STATE_PATH.write_text(
            json.dumps({"names": list(names)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  [glossary] cannot remember the selection: {exc}")


def keywords(
    entries: list[Entry], limit: int | None = config.ASR_KEYWORD_LIMIT
) -> list[str]:
    """The terms passed to the speech recognition. Both the correct Japanese
    spelling and the English are passed.

    With `limit=None` nothing is cut off. That is used to count how many terms
    the limit drops.
    """
    out: list[str] = []
    for e in entries:
        out.append(e.ja)
        if e.en and e.en != e.ja:
            out.append(e.en)
    return out if limit is None else out[:limit]


def _examples(entries: list[Entry], direction: str) -> list[str]:
    """Build the worked examples from the glossary itself.

    **Do not write examples into the prompt by hand.** Hand-written ones name
    the terms of one field, and every user of this program then gets that
    field's vocabulary pushed into the model, whatever their own meetings are
    about. Taking the examples from the glossary that is loaded keeps them
    relevant to the meeting at hand.

    Returns an empty list when no entry carries a misrecognition. The
    instructions above the examples stand on their own, so an empty glossary
    simply gets no examples.
    """
    picked = [e for e in entries if e.en and e.wrong][:2]
    out = []
    for e in picked:
        wrong = e.wrong[0]
        right = e.ja if direction == "en2ja" else f"「{e.ja}」= {e.en}"
        out.append(f"  例:「{wrong}」は「{e.ja}」の誤認識であることが多い。"
                   if direction == "en2ja"
                   else f"  例:「{wrong}」が現れたら、{right} の誤認識と考える。")
    return out


def prompt_block(entries: list[Entry], direction: str | None = None) -> str:
    """Build the two sections embedded in the translation prompt.

    **The two sides are swapped by the caption direction.** The glossary is a
    `Japanese / English` pair, so which of them is the caption language decides
    the order of the glossary and where the replacement rules land. The glossary
    itself is not rebuilt. The same `.tsv` is used as it is for `en2ja`.

    If `direction` is None, the caption direction that is selected now
    (`config.DIRECTION`) is used.
    """
    name = config.DIRECTION if direction is None else direction

    if name == "en2ja":
        # The captions are Japanese. List them as English = Japanese, and let
        # the replacement rules land on Japanese as well.
        header = "## 用語対訳表（この日本語を必ず使う）"
        terms = [f"- {e.en} = {e.ja}" for e in entries if e.en]
        rules = [f"- 「{w}」 → 「{e.en}」 = {e.ja}"
                 for e in entries if e.en for w in e.wrong]
        examples = [
            "- **左の語がこの分野で意味を成さないなら、必ず右の語として訳すこと。**",
            "- **日本語の誤認識も含まれる。** 日本語で話している部分にも同じ規則を適用すること。",
            "- **音が近いだけの別の語と取り違えてはいけない。**",
            *_examples(entries, "en2ja"),
        ]
    else:
        header = "## 用語対訳表（この英語を必ず使う）"
        terms = [f"- {e.ja} = {e.en}" for e in entries if e.en]
        rules = [f"- 「{w}」 → 「{e.ja}」 = {e.en}" for e in entries for w in e.wrong]
        examples = [
            "- **左の語がこの分野で意味を成さないなら、必ず右の語として訳すこと。**",
            "- **音が近いだけの別の語と取り違えてはいけない。**",
            "- **英語の誤認識も含まれる。** 英語で話している部分にも同じ規則を適用すること。",
            *_examples(entries, "ja2en"),
        ]

    return "\n".join([
        header,
        "",
        *terms,
        "",
        "## 誤認識の置換規則（重要）",
        "",
        "下の「誤 → 正」は、実際に音声認識が出した誤りである。",
        "左の語が入力に現れたら、右の語の誤認識だと考えること。",
        "",
        *examples,
        "- 左の語が普通の語としても成立する場合は、文脈で判断する。",
        "  この分野の話をしている最中なら、右の語を優先する。",
        "  関係のない話をしているなら、そのままの意味で訳す。",
        "",
        *rules,
    ])
