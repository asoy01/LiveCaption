"""Sentence splitting.

gpt-live-transcribe does not return boundaries. It only streams `delta`
continuously (measured on real meeting audio). So we decide on our own where a
sentence ends.

There are three conditions for a cut:

1. an end-of-sentence mark arrived (。！？ / . ! ?)
2. it grew too long without any such mark
3. no new characters arrived for a while (the speech paused)

Condition 3 works because the caller calls `flush_if_idle()` at intervals.

**Throw away fragments that are punctuation only.** Speech recognition
sometimes streams a delta that is nothing but "。". Settling that as a sentence
gives a caption line holding only ".". The caption window is only four lines
tall at minimum.
"""

from __future__ import annotations

import time
from typing import NamedTuple

from . import config

# Japanese end-of-sentence marks. In English, a mark counts as the end of a
# sentence only when a space follows it, as in ". " (so that we do not cut in
# the middle of an abbreviation such as OMMT2.).
JA_ENDINGS = "。！？"
EN_ENDINGS = ".!?"


class Cut(NamedTuple):
    """One settled sentence, and the reason it was settled.

    **The reason is needed to tune the latency.** Of the three ways of cutting,
    only `idle` pays a waiting time. You cannot tell what share of the total it
    is unless it is recorded.

    `waited` is meaningful for `idle` only. It is the number of seconds
    actually waited, kept so that the effect of changing `IDLE_FLUSH_SEC` can
    be checked afterwards.
    """

    text: str
    # punct (end mark) / force (length) / idle (silence) / flush (at shutdown)
    reason: str
    waited: float = 0.0


def has_content(text: str) -> bool:
    """Whether this is worth sending as a caption. Drop it when it holds no
    letter and no digit.

    This rejects fragments made only of punctuation, such as "。" or "...".
    isalnum() is true for Japanese kana and kanji.
    """
    return any(ch.isalnum() for ch in text)


class Segmenter:
    def __init__(
        self,
        force_cut: int | None = None,
        idle_sec: float | None = None,
    ) -> None:
        self.buffer = ""
        # **Do not capture config values in default arguments.** A default
        # argument is evaluated once at import time, so the replacement from
        # `.env` (done by `load_env()`) would stop working. Read them here,
        # each time an instance is created.
        self.force_cut = config.FORCE_CUT_CHARS if force_cut is None else force_cut
        self.idle_sec = config.IDLE_FLUSH_SEC if idle_sec is None else idle_sec
        self.last_delta_at = time.monotonic()

    def feed(self, delta: str) -> list[Cut]:
        """Add a recognition delta and return the list of settled sentences."""
        if not delta:
            return []
        self.buffer += delta
        self.last_delta_at = time.monotonic()

        out: list[Cut] = []
        while True:
            found = self._find_cut()
            if found is None:
                break
            cut, reason = found
            sentence, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:].lstrip()
            if has_content(sentence):
                out.append(Cut(sentence, reason))
        return out

    def silent_for(self) -> float:
        """How many seconds since the last delta. 0 when there is nothing
        buffered.
        """
        if not self.buffer.strip():
            return 0.0
        return time.monotonic() - self.last_delta_at

    def flush_if_idle(self) -> list[Cut]:
        """When the speech pauses, settle the sentence even without an
        end-of-sentence mark.
        """
        if not self.buffer.strip():
            return []
        waited = time.monotonic() - self.last_delta_at
        if waited < self.idle_sec:
            return []
        sentence, self.buffer = self.buffer.strip(), ""
        return [Cut(sentence, "idle", waited)] if has_content(sentence) else []

    def reset(self) -> None:
        """Throw away the characters buffered so far. Called when caption
        generation is stopped.

        **Throw them away instead of emitting them.** A caption line appearing
        after the stop leaves the readers wondering what happened. It also
        keeps an old fragment from being mixed in when generation restarts.
        """
        self.buffer = ""
        self.last_delta_at = time.monotonic()

    def flush(self) -> list[Cut]:
        """Emit everything that is left. Used at shutdown."""
        sentence, self.buffer = self.buffer.strip(), ""
        return [Cut(sentence, "flush")] if has_content(sentence) else []

    def _find_cut(self) -> tuple[int, str] | None:
        for i, ch in enumerate(self.buffer):
            if ch in JA_ENDINGS:
                return i + 1, "punct"
            if ch in EN_ENDINGS:
                # Count it as the end of a sentence only when a space follows.
                nxt = self.buffer[i + 1: i + 2]
                if nxt in (" ", "\n"):
                    return i + 2, "punct"
        if len(self.buffer) >= self.force_cut:
            return self._soft_cut(), "force"
        return None

    def _soft_cut(self) -> int:
        """When the text grows without an end-of-sentence mark, cut at a comma
        or a space.
        """
        window = self.buffer[: self.force_cut]
        for mark in ("、", "，", ", ", " "):
            pos = window.rfind(mark)
            # Cutting too early gives tiny pieces, so use only separators that
            # sit in the second half.
            if pos > self.force_cut // 2:
                return pos + len(mark)
        return self.force_cut
