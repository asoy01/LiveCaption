"""End condition 4: is the meeting being closed right now?

An LLM reads the last few minutes of the transcript and says whether the chair or
the participants are ending the meeting ("これで終わります", "お疲れ様でした",
"thanks, bye").

**This never ends the meeting by itself.** Small talk and "one more thing" often
follow the goodbyes. The scheduler asks only after meaningful speech has stopped
for `config.SCHEDULE_CLOSING_PAUSE_SEC`, and a yes ends the meeting there instead
of waiting for the full silence limit. A wrong yes therefore cuts off at most a
pause that was already long; a wrong no only means the normal wait.

Checked on 32 recorded meetings (2026-11-19): it found the closing in 25. The
other 7 stopped recording before the closing, or went on chatting after it. At
128 points in the middle of meetings it said yes 3 times, each where a meeting
inside a morning briefing ended.

The model is `gpt-5.6-luna` with low reasoning. Set `LIVECAPTION_CLOSING_MODEL`
in `.env` to use another one. **Only the container uses this** (`schedule.py`).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from . import translator

MODEL = "gpt-5.6-luna"
MODEL_ENV = "LIVECAPTION_CLOSING_MODEL"
REASONING = "low"
# How much of the end of the transcript to read.
WINDOW_SEC = 180.0

SYSTEM = """\
You read the last few minutes of a meeting transcript made by speech recognition
(Japanese, English or both). There are no speaker names.

Decide whether the whole meeting is being closed right now: the chair ends it, or the
participants say goodbye to each other ("これで終わります", "以上です、お疲れ様でした",
"ありがとうございました、失礼します", "OK, let's stop here", "thank you all, bye").
A morning meeting often ends by sending people to work: after "他に何かありますか",
"じゃあ今日の作業を始めてください" / "今日の作業をお願いします" / "以上です、よろしくお願いします".
That is a closing too.

These are NOT the closing of the meeting:
- the end of one agenda item, or moving to the next item or the next speaker
- thanks to one speaker after a talk ("ありがとうございました" followed by questions)
- someone leaving early while the meeting goes on
- a question "anything else?" that is followed by more discussion

Judge by the last lines most of all. If the closing words came earlier and the
discussion started again after them, it is not closing.

Return JSON: {"closing": true or false, "quote": "the words that show it, copied", "reason": "a few words in Japanese"}"""


@dataclass
class Verdict:
    closing: bool
    quote: str = ""
    reason: str = ""
    error: str = ""


_JSON = re.compile(r"\{.*\}", re.S)


def judge(lines: list[str], context: str = "") -> Verdict:
    """lines: the transcript of the last WINDOW_SEC, oldest first.

    **Never raises.** It runs on another thread (`asyncio.to_thread`); an error
    comes back in `error`, and the answer is then no.
    """
    if not lines:
        return Verdict(False, reason="発話なし")
    head = f"Meeting: {context}\n\n" if context else ""
    user = head + "Transcript (last minutes):\n" + "\n".join(lines)
    model = os.environ.get(MODEL_ENV, "").strip() or MODEL
    try:
        body, _took = translator.chat(model, SYSTEM, user, timeout=60.0, reasoning=REASONING)
        found = _JSON.search(body)
        out = json.loads(found.group(0) if found else body)
    except Exception as exc:  # noqa: BLE001
        return Verdict(False, error=f"{type(exc).__name__}: {exc}")
    return Verdict(out.get("closing") is True, str(out.get("quote", ""))[:200],
                   str(out.get("reason", ""))[:200])
