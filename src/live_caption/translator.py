"""Translation. **The caption direction is chosen for each meeting.**

- `ja2en`: Japanese is translated into English, English is passed through
- `en2ja`: English is translated into Japanese, Japanese is passed through

In both directions, text in the other language is passed through as it is. The
language that is mainly spoken can change in the middle of a meeting, so neither
direction can assume one language only.

The glossary and the replacement rules are embedded in the prompt. Even when
speech recognition picks the wrong kanji, they are restored here. **Not
everything can be saved, though.** When a misrecognition lands on another
plausible technical term, the result is a fluent mistranslation. The quality of
the recognition side matters as well.

Translating a single sentence on its own breaks the words that point back
(pronouns and the like), so the preceding sentences are passed as context. Only
the last sentence is translated.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

from . import config, glossary

SYSTEM_JA2EN = """\
あなたは技術的な会議の同時通訳者である。音声認識の出力を、英語の字幕に変換する。

**この会議は日本語と英語が混ざる。** 前半と後半で主に話される言語が変わることもある。

- 入力が**日本語**なら、英語に訳す。
- 入力が**既に英語**なら、訳さずにそのまま出す。認識の誤りと言い直しだけを整え、
  語彙や言い回しは変えない。話者の多くは英語を母語としないので、
  文法の細かな誤りは、意味が通るなら直さずに残す。

入力は音声認識の生の出力である。次の特徴がある。

- 漢字の変換を誤っていることが多い。音は合っているので、読みから正しい専門用語を推測すること。
- アルファベットの略語が小文字になっている。正しい大文字に直すこと。
- 句読点が無い、または誤っている。

{meeting}
専門用語はこの分野のものとして解釈すること。

{glossary}

出力の規則:

- 英語の字幕だけを出力する。説明や注釈は付けない。
- **1行は40〜{max_chars}文字にする。** これより長い場合だけ改行する。
  **極端に短い行を作らない。** 1〜3語だけの行は読みにくい。
  改行は、句読点や接続詞など、意味の切れ目で入れること。
- **多くても3行に収めること。** 字幕の表示領域は狭く、送りすぎると先頭が流れて消える。
  入力が長い場合は、細部を削ってでも短くする。
- 会議で口頭で話される英語にする。文語的な表現は使わない。
- 意味が取れない部分は、無理に訳さず、そのまま音写する。
"""

SYSTEM_EN2JA = """\
あなたは技術的な会議の同時通訳者である。音声認識の出力を、日本語の字幕に変換する。

**この会議は日本語と英語が混ざる。** 前半と後半で主に話される言語が変わることもある。

- 入力が**英語**なら、日本語に訳す。
- 入力が**既に日本語**なら、そのまま日本語で出す。**英語に訳してはいけない。**
  認識の誤りと言い直しだけを整え、語彙や言い回しは変えない。
  **入力が日本語のときも、下の置換規則は適用する。** 誤認識はそこで直す。

入力は音声認識の生の出力である。次の特徴がある。

- 専門用語を、音の近い普通の語に取り違えていることが多い。下の置換規則で直すこと。
- アルファベットの略語が小文字になっている。正しい大文字に直すこと。
- 句読点が無い、または誤っている。
- 話者の多くは英語を母語としない。文法が崩れていても、意味を取って訳すこと。

{meeting}
専門用語はこの分野のものとして解釈すること。

{glossary}

出力の規則:

- 日本語の字幕だけを出力する。説明や注釈は付けない。
- **出力は必ず日本語である。英語の文を出力してはいけない。**
  入力が日本語だったときは、その日本語を出す。訳し返さない。
- **1行は20〜{max_chars}文字にする。** これより長い場合だけ改行する。
  **極端に短い行を作らない。** 改行は、読点や助詞の切れ目など、意味の切れ目で入れること。
- **多くても3行に収めること。** 字幕の表示領域は狭く、送りすぎると先頭が流れて消える。
  入力が長い場合は、細部を削ってでも短くする。
- 会議で口頭で話される日本語にする。文語的な表現は使わない。「です・ます」で揃える。
- **専門用語を日本語に訳しすぎない。** 上の表にある語は表の日本語を使う。
  表に無い略語や装置名は、英字のまま残すこと。
  現場で英語のまま呼んでいる語（ロック、アライメントなど）も、そのまま使う。
- 意味が取れない部分は、無理に訳さず、そのまま音写する。
"""

# Look up the system prompt to use from the caption direction.
SYSTEMS = {"ja2en": SYSTEM_JA2EN, "en2ja": SYSTEM_EN2JA}

USER = """\
直前の文脈（訳さなくてよい。指示語を解釈するための参考）:
{context}

---

次の1文だけを英語の字幕にすること:
{target}
"""


def build_system(
    entries: list[glossary.Entry],
    max_chars: int | None = None,
    direction: str | None = None,
) -> str:
    """Build the system prompt.

    **This is the core of the system.** Both the main program and the
    experiments in `scripts/` use it. Separate copies of the prompt always drift
    apart, so do not copy it.

    If `direction` is None, the caption direction that is selected now
    (`config.DIRECTION`) is used. If `max_chars` is None, the characters per
    line of that direction is used (80 for English, 40 for Japanese).

    **Do not capture a value of `config` in a default argument.** A default
    argument is evaluated once at import time, so neither switching the caption
    direction nor replacing `.env` would take effect. The values are read here,
    every time the function is called.
    """
    name = config.DIRECTION if direction is None else direction
    if max_chars is None:
        max_chars = config.DIRECTIONS[name].max_caption_chars
    template = SYSTEMS.get(name, SYSTEM_JA2EN)
    return template.format(
        meeting=config.meeting_context(),
        glossary=glossary.prompt_block(entries, direction=name),
        max_chars=max_chars,
    )


def chat(model: str, system: str, user: str, timeout: float = 180.0) -> tuple[str, float]:
    """Make one request. Return (body, seconds taken). Synchronous."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        config.TRANSLATE_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.openai_key()}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip(), time.perf_counter() - t0


class Translator:
    def __init__(self, entries: list[glossary.Entry], model: str = config.TRANSLATE_MODEL) -> None:
        self.model = model
        self.system = build_system(entries)
        self.history: list[str] = []

    def _request(self, target: str) -> tuple[str, float]:
        context = "\n".join(self.history[-config.CONTEXT_SENTENCES:]) or "(なし)"
        return chat(
            self.model, self.system, USER.format(context=context, target=target), timeout=60.0
        )

    def remember(self, target: str) -> None:
        """Add a translated sentence to the context. The caller calls this when
        an early translation is accepted."""
        self.history.append(target)
        del self.history[:-config.CONTEXT_SENTENCES]

    async def translate(self, target: str, *, remember: bool = True) -> tuple[list[str], float]:
        """Translate one sentence and return (caption lines, seconds taken). On
        failure, an empty list.

        **The seconds taken are returned, not stored in an attribute.** Up to 4
        translations run at the same time, so a shared attribute would already
        hold the value of another sentence by the time it is read.

        `remember=False` exists for the early translation. **A sentence that may
        be thrown away must not be mixed into the context.** The early
        translation runs several times a minute and most of its results are
        discarded, so mixing them in would fill the 3-sentence context with
        half-spoken fragments. Call `remember()` once the result is accepted.
        """
        try:
            text, took = await asyncio.to_thread(self._request, target)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            print(f"  [翻訳の失敗] HTTP {e.code} {detail}")
            return [], 0.0
        except Exception as e:  # noqa: BLE001 - never crash during a meeting
            print(f"  [翻訳の失敗] {type(e).__name__}: {e}")
            return [], 0.0

        # Only a translated sentence is added to the context. A sentence that
        # failed is not added.
        if remember:
            self.remember(target)

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [ln for line in lines for ln in _wrap(line, config.MAX_CAPTION_CHARS)], took


def _wrap(line: str, limit: int) -> list[str]:
    """A safety net for when the model returns a long line. It wraps at word
    boundaries.

    **Japanese has no spaces.** Wrapping at spaces alone would let a long
    Japanese line through untouched, which is no safety net at all. A line with
    no spaces is wrapped at punctuation, and when there is none, by character
    count.
    """
    if len(line) <= limit:
        return [line]
    if " " not in line:
        return _wrap_ja(line, limit)
    out, current = [], ""
    for word in line.split(" "):
        if current and len(current) + 1 + len(word) > limit:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def _wrap_ja(line: str, limit: int) -> list[str]:
    """Wrap a line that has no spaces. Japanese commas and periods come first,
    and when there are none, it cuts by character count.

    **Decide the number of lines first, then even out the width.** Cutting at
    `limit` from the start would split a 42-character line into one of 40
    characters and one of 2. A line of 1 or 2 characters cannot be read as a
    caption.
    """
    out = []
    while len(line) > limit:
        # **Divide again from the remaining length every time.** Cutting on with
        # the width decided at the start makes the remainder drift at each
        # break, and the last line ends up holding 2 characters.
        pieces = -(-len(line) // limit)      # How many lines to divide into
                                             # (rounded up)
        width = -(-len(line) // pieces)      # The evened-out width
        window = line[:width]
        cut = max(window.rfind(mark) + len(mark) for mark in ("、", "。", "，", "・"))
        # Cutting too early makes the lines tiny. When there is no break in the
        # second half, cut by character count.
        if cut <= width // 2:
            cut = width
            # **Do not put punctuation or a closing bracket at the head of a
            # line.** Move it back to the previous line, one character at a
            # time.
            while line[cut: cut + 1] in ("、", "。", "，", "．", "・", "」", "）", "』"):
                cut += 1
        out.append(line[:cut].strip())
        line = line[cut:].strip()
    if line:
        out.append(line)
    return out
