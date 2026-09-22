"""The viewer URL for each meeting. Create, select, delete.

A viewer URL looks like `https://<host name>/v/<path>`. This module holds the
`<path>` part.

**Each meeting uses a different path.** The people who join differ from meeting
to meeting, so the URL of last week's meeting must not show today's captions.

**The URL can be made in advance.** The host name of Tailscale Funnel does not
change, so the URL is already fixed the day before the meeting. This is what
lets you put it in the invitation next to the Zoom link. The host name of a
Cloudflare temporary tunnel changes every time, so even when the path is ready,
the whole URL is not known until the day itself.

**Only the one selected meeting is delivered.** On that day, the URLs of the
other meetings return 404. The viewer server in `web.py` looks at a single
`viewer_path`, so this holds by itself.

**There is always at least one meeting.** With zero meetings the viewer URL is
undefined, and screen sharing is not possible either. When the last one is
deleted, a replacement is created with today's date.

The file is `local/meetings.json`. It is not in git, because it holds the names
of the meetings.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from . import config

# Upper limit for the length of a name. It is there so the list on the control
# page stays readable. It is not a limit on the content.
NAME_MAX = 60
# How a scheduled time is written. **No seconds and no time zone.** This runs on
# one machine, and a person types the value by hand.
TIME_FMT = "%Y-%m-%d %H:%M"
# These two are the only repeats. No cron, no RRULE.
REPEATS = ("", "weekly")


@dataclass(frozen=True)
class Meeting:
    """One meeting. The `id` appears in the URL.

    **The schedule fields below were added later.** They are not in an old
    `local/meetings.json`, so they have default values (`_meeting_from` fills
    them in).
    """

    id: str
    name: str
    created: str
    # --- Schedule (the fields that let it run unattended) ---
    start: str = ""            # "2026-09-25 09:30". Empty means no schedule
    repeat: str = ""           # "" or "weekly"
    zoom: str = ""             # Zoom invitation URL or meeting number. Empty
                               # means it does not join by itself
    lead_min: int = 2          # How many minutes before the start it wakes up
    silence_min: float = 10.0  # Stop after this much silence
    max_min: int = 180         # Safety limit. It always stops here, even
                               # without silence
    # **The flag for running automatically. The dataclass default is off.** This
    # field was added to meetings that were already saved, so when it is not
    # written, it is read as off. **`create` turns it on for a meeting you added
    # yourself.** A meeting created only to keep the count above zero stays off,
    # with this default.
    auto: bool = False
    # **The flag for posting the caption URL to the Zoom chat.** It is handled
    # the same way as `auto`. When it posts, every participant of the meeting
    # sees the URL. Turn it off for a meeting that must not leave the room.
    chat: bool = False
    last_fired: str = ""       # The `start` of the occurrence that is done.
                               # **It records the occurrence, not a clock time**
    host_id: str = ""          # The path of the URL where the host pastes the
                               # token (Phase 6)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "created": self.created,
            "start": self.start, "repeat": self.repeat, "zoom": self.zoom,
            "lead_min": self.lead_min, "silence_min": self.silence_min,
            "max_min": self.max_min, "auto": self.auto, "chat": self.chat,
            "last_fired": self.last_fired, "host_id": self.host_id,
        }

    @property
    def scheduled(self) -> bool:
        return bool(self.start) and self.auto


def _new_id() -> str:
    return secrets.token_urlsafe(config.VIEWER_SECRET_BYTES)


def _new_host_id() -> str:
    """The path of the URL for the host.

    **Make it longer than the viewer `id`.** If the viewer path is broken, only
    the captions leak, and that path is handed to tens of people anyway. This
    one decides what is sent to Zoom, so it has more digits. **It must not be
    derivable from the viewer path.**
    """
    return secrets.token_urlsafe(config.HOST_SECRET_BYTES)


# --- Repair on load -----------------------------------------------------------
# **Do not crash on a `meetings.json` that was edited by hand.** A value that
# cannot be read falls back to the default. Raising here would make the
# scheduler trip over the same meeting every 5 seconds.


def _clean_start(raw) -> str:  # noqa: ANN001
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, TIME_FMT).strftime(TIME_FMT)
    except (ValueError, TypeError):
        return ""


def _num(raw, lo: float, hi: float, default: float) -> float:  # noqa: ANN001
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value < lo or value > hi:  # NaN is caught here too
        return default
    return value


def _meeting_from(m: dict) -> Meeting:
    """Read one entry. **Missing fields are filled with the defaults.**"""
    return Meeting(
        id=str(m["id"]),
        name=str(m.get("name", "")),
        created=str(m.get("created", "")),
        start=_clean_start(m.get("start")),
        repeat=str(m.get("repeat", "")) if m.get("repeat") in REPEATS else "",
        zoom=str(m.get("zoom", "")).strip(),
        lead_min=int(_num(m.get("lead_min"), 0, 60, 2)),
        silence_min=_num(m.get("silence_min"), 0.5, 240, 10.0),
        max_min=int(_num(m.get("max_min"), 5, 24 * 60, 180)),
        auto=bool(m.get("auto", False)),
        chat=bool(m.get("chat", False)),
        last_fired=str(m.get("last_fired", "")),
        host_id=str(m.get("host_id", "")),
    )


class Store:
    """The list of meetings, and the meeting that is being delivered now.

    **It is touched from the control page (a thread of the HTTP server).**
    `_lock` protects it. A failed write does not stop the program. The meeting
    must be able to go on.
    """

    def __init__(self, path=None) -> None:  # noqa: ANN001
        # **Do not capture MEETINGS_STATE_PATH in a default argument.** A
        # default argument is evaluated once at import time, so a test could no
        # longer replace it.
        self.path = config.MEETINGS_STATE_PATH if path is None else path
        self._lock = threading.Lock()
        self._items: list[Meeting] = []
        self._active = ""
        self._error = ""
        # Called when the path changes, so that the viewer server can follow
        # the new `viewer_path`.
        self.on_change = None
        self._load()

    # --- Reading and writing the file ---------------------------------------

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            items = [
                _meeting_from(m)
                for m in saved.get("items", [])
                if isinstance(m, dict) and m.get("id")
            ]
            active = str(saved.get("active", ""))
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            items, active = [], ""
        self._items = items
        self._active = active if any(m.id == active for m in items) else ""
        if not self._items:
            # **Never leave the list empty.** Without a viewer URL there is no
            # page and no QR code. Create one with today's date.
            # **Unlike `create`, no flag is set.** Nobody asked for this
            # meeting, so it does not join Zoom and does not post to the chat.
            self._items = [Meeting(_new_id(), str(date.today()), str(date.today()))]
            self._active = self._items[0].id
            self._save_locked()
        elif not self._active:
            self._active = self._items[0].id

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"active": self._active,
                       "items": [m.as_dict() for m in self._items]}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
            self._error = ""
        except OSError as exc:
            self._error = f"Cannot save the meeting list: {exc}"
            print(f"  [meetings] {self._error}")

    # --- Reading -------------------------------------------------------------

    @property
    def active_id(self) -> str:
        with self._lock:
            return self._active

    @property
    def viewer_path(self) -> str:
        """The path of the meeting that is being delivered now."""
        return f"/v/{self.active_id}"

    def path_of(self, meeting_id: str) -> str:
        return f"/v/{meeting_id}"

    def items(self) -> list[Meeting]:
        with self._lock:
            return list(self._items)

    def status(self) -> dict:
        with self._lock:
            return {
                "active": self._active,
                "error": self._error,
                "items": [dict(m.as_dict(), path=f"/v/{m.id}") for m in self._items],
            }

    # --- Changing ------------------------------------------------------------

    def create(self, name: str) -> dict:
        """Add one meeting. **It only adds. It does not change what is
        delivered.**

        While you prepare the URL of a future meeting, today's delivery must not
        switch over.

        **A meeting you add yourself is created with `auto` and `chat` on.** If
        you go as far as typing a name, you mean to run that meeting. Turn them
        off if you do not need them. **The meetings created only to keep the
        count above zero (the replacement in `_load_locked` and `remove`) do not
        get them.** Nobody asked for those. They exist only so that a viewer URL
        always exists, so they must not go outside on their own.
        """
        name = str(name).strip()[:NAME_MAX]
        if not name:
            raise ValueError("会議の名前を入れること。")
        with self._lock:
            if any(m.name == name for m in self._items):
                raise ValueError(f"A meeting with this name already exists: {name}")
            self._items.append(Meeting(_new_id(), name, str(date.today()),
                                       auto=True, chat=True))
            self._save_locked()
            return self._status_locked()

    def select(self, meeting_id: str) -> dict:
        """Select the meeting to deliver. **It takes effect at once.**

        **A viewer page that is open now loses its connection**, because the
        path changes. This is not something to touch during a meeting.
        """
        meeting_id = str(meeting_id)
        with self._lock:
            if not any(m.id == meeting_id for m in self._items):
                raise ValueError("There is no such meeting.")
            changed = meeting_id != self._active
            self._active = meeting_id
            self._save_locked()
            out = self._status_locked()
        if changed:
            self._changed()
        return out

    def delete(self, meeting_id: str) -> dict:
        """Delete a meeting. The URL dies at once.

        **When the last one is deleted, a replacement is created.** Without a
        viewer URL, screen sharing is not possible either. Instead of refusing
        with "this one cannot be deleted", a new one is made with today's date.
        Being able to clear away every meeting that is over feels more natural
        to the person using it.
        """
        meeting_id = str(meeting_id)
        with self._lock:
            if not any(m.id == meeting_id for m in self._items):
                raise ValueError("There is no such meeting.")
            self._items = [m for m in self._items if m.id != meeting_id]
            changed = meeting_id == self._active
            if not self._items:
                # Same as above. **The replacement gets no flags.**
                self._items = [Meeting(_new_id(), str(date.today()), str(date.today()))]
            if changed:
                self._active = self._items[0].id
            self._save_locked()
            out = self._status_locked()
        if changed:
            self._changed()
        return out

    # --- Schedule ------------------------------------------------------------

    def set_schedule(self, meeting_id: str, **fields) -> dict:  # noqa: ANN003
        """Rewrite the schedule fields. A value that cannot be read is refused
        with `ValueError`.

        **This is the entrance for values a person typed, so refuse here.** The
        loading side (`_meeting_from`) does the opposite: whatever arrives, it
        falls back to the default and lets it through. A broken file that makes
        the scheduler trip over and over is the worse outcome.
        """
        meeting_id = str(meeting_id)
        clean: dict = {}

        if "start" in fields:
            raw = str(fields["start"] or "").strip().replace("T", " ")
            # The browser's datetime-local returns "2026-09-25T09:30".
            if raw and _clean_start(raw) == "":
                raise ValueError(
                    f"Bad date and time: {raw}. Use the form {TIME_FMT}.")
            clean["start"] = _clean_start(raw)
        if "repeat" in fields:
            rep = str(fields["repeat"] or "")
            if rep not in REPEATS:
                raise ValueError(
                    f"Bad repeat: {rep}. Use an empty value or weekly.")
            clean["repeat"] = rep
        if "zoom" in fields:
            clean["zoom"] = str(fields["zoom"] or "").strip()[:500]
        if "lead_min" in fields:
            clean["lead_min"] = int(_num(fields["lead_min"], 0, 60, -1))
            if clean["lead_min"] < 0:
                raise ValueError("Minutes before the start must be 0 to 60.")
        if "silence_min" in fields:
            value = _num(fields["silence_min"], 0.5, 240, -1)
            if value < 0:
                raise ValueError("Minutes of silence before stopping must be "
                                 "0.5 to 240.")
            clean["silence_min"] = value
        if "max_min" in fields:
            value = int(_num(fields["max_min"], 5, 24 * 60, -1))
            if value < 0:
                raise ValueError("The hard cap must be 5 to 1440 minutes.")
            clean["max_min"] = value
        if "auto" in fields:
            clean["auto"] = bool(fields["auto"])
        if "chat" in fields:
            clean["chat"] = bool(fields["chat"])

        with self._lock:
            found = [m for m in self._items if m.id == meeting_id]
            if not found:
                raise ValueError("There is no such meeting.")
            old = found[0]
            # When the schedule changes, clear the "done" mark. After moving the
            # time, the occurrence must not still count as finished.
            if clean.get("start", old.start) != old.start:
                clean["last_fired"] = ""
            new = replace(old, **clean)
            self._items = [new if m.id == meeting_id else m for m in self._items]
            self._save_locked()
            return self._status_locked()

    def ensure_host_id(self, meeting_id: str, renew: bool = False) -> str:
        """The path of the URL for the host. If there is none, make one and save
        it."""
        meeting_id = str(meeting_id)
        with self._lock:
            found = [m for m in self._items if m.id == meeting_id]
            if not found:
                raise ValueError("There is no such meeting.")
            if found[0].host_id and not renew:
                return found[0].host_id
            new = replace(found[0], host_id=_new_host_id())
            self._items = [new if m.id == meeting_id else m for m in self._items]
            self._save_locked()
            return new.host_id

    def mark_fired(self, meeting_id: str, occurrence: str) -> None:
        """Mark that occurrence as done. **It never raises, even on failure.**

        The scheduler calls this. An exception here would trip the scheduler,
        and it would pick up the same occurrence again and again.
        """
        try:
            with self._lock:
                found = [m for m in self._items if m.id == meeting_id]
                if not found:
                    return
                new = replace(found[0], last_fired=str(occurrence))
                self._items = [new if m.id == meeting_id else m for m in self._items]
                self._save_locked()
        except Exception as exc:  # noqa: BLE001
            print(f"  [meetings] Cannot mark the occurrence as done: {exc}")

    def next_occurrence(self, m: Meeting, now: datetime) -> datetime | None:
        """The next occurrence. None if there is none.

        **`weekly` adds 7 days to the date and rebuilds the value.** It does not
        add the number of seconds in 7 days. Even if the machine is moved to a
        place that uses summer time, the clock time stays as it was set.
        """
        if not m.start:
            return None
        try:
            when = datetime.strptime(m.start, TIME_FMT)
        except ValueError:
            return None
        if not m.repeat:
            return when
        # If it is in the past, move forward to the next same weekday and same
        # clock time.
        guard = 0
        while when < now and guard < 520:  # Give up after 10 years
            when = datetime.combine(
                when.date() + timedelta(days=7), when.time())
            guard += 1
        return when

    def occurrence_key(self, when: datetime) -> str:
        return when.strftime(TIME_FMT)

    def due(self, now: datetime) -> list[tuple[Meeting, datetime]]:
        """The meetings that should wake up now. **They are returned by earliest
        start time, not newest first.**

        All of them are returned, not just one, so that overlaps are visible.
        The scheduler is the one that chooses.
        """
        out = []
        for m in self.items():
            if not m.scheduled:
                continue
            when = self.next_occurrence(m, now)
            if when is None:
                continue
            key = self.occurrence_key(when)
            if m.last_fired == key:
                continue
            # Pick it up from `lead_min` minutes before the start. An occurrence
            # that is long past is not picked up, so the occurrences that went
            # by while the program was stopped do not all start at once.
            begin = when - timedelta(minutes=m.lead_min)
            if begin <= now <= when + timedelta(minutes=config.SCHEDULE_GRACE_MIN):
                out.append((m, when))
        out.sort(key=lambda pair: pair[1])
        return out

    def upcoming(self, now: datetime, limit: int = 3) -> list[dict]:
        """The coming occurrences, earliest first. Shown on the control page."""
        out = []
        for m in self.items():
            if not m.scheduled:
                continue
            when = self.next_occurrence(m, now)
            if when is None:
                continue
            key = self.occurrence_key(when)
            if m.last_fired == key and not m.repeat:
                continue
            if m.last_fired == key and m.repeat:
                when = datetime.combine(when.date() + timedelta(days=7), when.time())
            out.append({
                "id": m.id, "name": m.name,
                "at": when.strftime(TIME_FMT),
                "in_sec": int((when - now).total_seconds()),
                "zoom": bool(m.zoom),
            })
        out.sort(key=lambda d: d["at"])
        return out[:limit]

    # --- Internal ------------------------------------------------------------

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _status_locked(self) -> dict:
        return {
            "active": self._active,
            "error": self._error,
            "items": [dict(m.as_dict(), path=f"/v/{m.id}") for m in self._items],
        }
