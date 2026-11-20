"""Which recognized sentences count as speech that keeps a meeting going.

The scheduler shuts a meeting down after a long silence (`schedule.py`). Until
this module existed, **any** sentence reset that timer. But recognition also
returns sentences when nobody is really talking:

- fillers alone ("はい", "yeah")
- text the model makes up on near-silent audio ("ご視聴ありがとうございました")
- a lone glossary word ("アライメント。"): when the model cannot make out
  the speech, it often returns one of the keywords it was given
- the same sentence again

With those counted, a meeting where two people say "はい" now and then never
reaches its silence limit. `meaningful()` drops them.

The lone glossary word was seen in a real Zoom meeting (2026-11-20): four times
in a few minutes of muffled English.

**Only the container uses this.** The Windows native version is frozen and
keeps counting every sentence (`schedule.py`).
"""

from __future__ import annotations

import re

# What is left after the fillers must be at least this many characters.
MEANINGFUL_CHARS = 6

FILLERS = frozenset({
    "はい", "うん", "ええ", "えー", "えっと", "あー", "あの", "そうですね", "そうですか",
    "なるほど", "ok", "okay", "yeah", "yes", "yep", "right", "sure", "uh", "um",
    "mm", "hmm", "oh", "so", "thank you", "thanks",
    "ありがとうございます", "ありがとうございました",
})
# Text the model makes up on near-silent audio. Only short sentences are
# checked: a long sentence that contains one of these is real speech.
HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録", "字幕は",
                  "thanks for watching", "thank you for watching")
_SPLIT = re.compile(r"[\s、。，,.!?！？…・「」『』()（）]+")


def bare(text: str) -> str:
    """The text without spaces and punctuation, in lower case."""
    return _SPLIT.sub("", text).lower()


def keyword_set(keywords) -> frozenset[str]:  # noqa: ANN001
    """The recognition keywords, in the form `meaningful()` compares with."""
    return frozenset(bare(k) for k in keywords or [] if k)


def meaningful(text: str, previous: str = "",
               keywords: frozenset[str] = frozenset()) -> bool:
    """Whether a recognized sentence is speech that keeps the meeting going.

    previous: the sentence before this one.
    keywords: `keyword_set()` of the recognition keywords.
    """
    t = text.strip().lower()
    if not t or t == previous.strip().lower():
        return False
    if bare(t) in keywords:
        return False
    if len(t) < 40 and any(h in t for h in HALLUCINATIONS):
        return False
    rest = [w for w in _SPLIT.split(t) if w and w not in FILLERS]
    return len("".join(rest)) >= MEANINGFUL_CHARS
