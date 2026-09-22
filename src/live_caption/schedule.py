"""A scheduler that runs a scheduled meeting with nobody present.

The caption machine runs all the time. When the time comes, this module does
the work step by step.

    lead_min before start  select the meeting -> start the delivery ->
                           (join Zoom)
    start time             switch the record to this meeting -> start captions
    a few seconds later    **check that captions really started**
    after a long silence   stop captions -> leave Zoom -> stop the delivery ->
                           close the record

**This never returns, and it never lets an exception out.**
`app.App.run()` waits with `asyncio.wait(..., FIRST_COMPLETED)`, so the whole
app is shut down when any task finishes. LiveCaption must not go down because
of the schedule.

**Check the status to see whether captions started.**
`EngineControl.set_running(True)` returns at once. When the input device could
not be opened, `_pipeline` catches that and only returns to the stopped state.
Nothing reaches the caller, and there is no retry. `generating` being False
means both "not yet" and "it failed", so look at `audio.status()["error"]`
first.

**Decide to shut down by "did a sentence come out", not by the sound level.**
The Zoom mix always carries background noise. If you decide that the room is
quiet from the amplitude, the app stays connected to an empty room and never
stops.

**Always keep a hard cap.** Transcription is billed for the audio you send,
and silence costs the same ($0.017/min, about 1 USD/hour). A room with an open
microphone never goes quiet, so keep a hard limit separate from the silence
test.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta

from . import config
from . import meetings

# **Driving the meeting client works differently on each machine.**
# Windows uses window class names and the registry. Linux (the container) uses
# windows on Xvfb and `xdotool`. Only the URL building could be shared, so the
# two implementations are kept apart. **`zoom_join.py` (the Windows one) is
# frozen.** The interface is the same on both sides
# (`running` / `in_meeting` / `join` / `leave` / `JoinError`).
if os.name == "nt":
    from . import zoom_join
else:
    from . import zoom_join_linux as zoom_join

# States. The control page shows these same names.
IDLE = "idle"
JOINING = "joining"
ARMING = "arming"
RUNNING = "running"
STOPPING = "stopping"
# **There is no "failed" state.** A state you cannot leave would mean that one
# failure stops every later meeting from ever starting. A failure is kept as
# text in `failure`, and the state goes back to `idle`.


# **Every short note that appears on the page is listed here.** These notes
# ride on `/api/status` to the control page, so a note that is missing from the
# translation table shows up in Japanese only in the English display. They do
# not appear in the page markup, so `scripts/check_ui_lang.py` reads this list
# to find notes that were not translated. When you add a sentence, add it here
# as well.
UI_STRINGS = (
    "会議を選んだ",
    "配信を始めた",
    "配信を始められなかった",
    "Zoomに入った",
    "Zoomに入れなかった（手で入れば字幕は出る）",
    "Zoomに入れない: ",
    "Zoomから音が来ない。パスコード違いか、待機室で止まっているか、"
    "更新のダイアログが出ている可能性がある。画面を見ること。",
    "字幕を出している",
    "生成が止められた",
    "操作画面から止めた",
    "例外が出たので片付けた",
    "始まらなかったので片付けた",
    "安全上限で止めた",
    "無音が続いたので止めた",
    "飛ばす予定が無い。",
    "次の予定を飛ばした。",
    "生成が始まらない。",
    "音声の入力を開けない: ",
    "会議を選べない: ",
    "スケジューラで例外: ",
)


def now_str() -> str:
    return time.strftime("%H:%M:%S")


class Scheduler:
    """Read the schedule and run one meeting at a time.

    **Only one meeting runs at a time.** When two overlap, the later one is
    skipped. The running one is not cut off: meetings run long all the time,
    and cutting captions in the middle for the sake of an older entry is worse.

    `app.App` owns this object. It is called from the main event loop only.
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app
        self.state = IDLE
        # The meeting that is running now, and which occurrence it is.
        self.meeting_id = ""
        self.meeting_name = ""
        self.occurrence = ""
        # When caption generation started (`time.monotonic`). The hard cap is
        # counted from here.
        self.started_at = 0.0
        # The last failure. **It is not cleared by time.** It stays on the page
        # until someone presses the button to clear it.
        self.failure = ""
        self.failure_at = ""
        # What was done just before. It is shown on the page.
        self.note = ""
        # How to shut down the meeting that is running. It differs per meeting.
        self._silence_min = 10.0
        self._max_min = 180
        # Whether we started the delivery. A delivery a person started is not
        # stopped here.
        self._we_started_tunnel = False
        # Whether we launched Zoom. **A meeting a person had open is never
        # killed.**
        self._we_launched_zoom = False
        # Whether we tried to join Zoom for this occurrence. It is used to
        # judge the case where no sound arrives.
        self._joined_zoom = False
        # Whether sound has arrived even once. Once it has, the waiting room is
        # no longer suspected.
        self._saw_audio = False
        # `last_sentence_at` at the start of this occurrence. **When it moves,
        # somebody spoke.** The amplitude (`_saw_audio`) cannot stand in for
        # this, because background noise while waiting for people also raises
        # the amplitude.
        self._sentence_mark = 0.0
        # The times at which to post to the chat. **Posting is retried every
        # time this is checked, until the list is empty.**
        self._chat_todo: list[datetime] = []
        self._chat_total = 0
        self._chat_next = 0.0

    # --- What goes back to the control page ---------------------------------

    def status(self) -> dict:
        left_silence = -1.0
        left_max = -1.0
        if self.state == RUNNING:
            # **The time left on the page must come from the timer that is
            # really in effect.** At the start of a meeting the app waits
            # longer, so a short number there would make people think that
            # captions are about to stop.
            quiet, limit, _why = self._quiet_state()
            left_silence = max(0.0, limit - quiet)
            left_max = max(0.0, self._max_limit() - (time.monotonic() - self.started_at))
        return {
            "state": self.state,
            "meeting": self.meeting_name,
            "occurrence": self.occurrence,
            "note": self.note,
            "failure": self.failure,
            "failure_at": self.failure_at,
            "silence_left_sec": round(left_silence, 1),
            "max_left_sec": round(left_max, 1),
            "upcoming": self._upcoming(),
        }

    def _upcoming(self) -> list[dict]:
        store = getattr(self.app.web, "meetings", None) if self.app.web else None
        if store is None:
            return []
        try:
            return store.upcoming(datetime.now(), limit=3)
        except Exception:  # noqa: BLE001
            return []

    # --- Commands from the control page -------------------------------------

    def ack(self) -> dict:
        """Clear the failure shown on the page. **It stays until pressed.**"""
        self.failure = ""
        self.failure_at = ""
        return self.status()

    def stop_now(self) -> dict:
        """Shut down the meeting that is running now.

        **This is not `App.request_stop`.** That one ends the whole process,
        which breaks a machine that is meant to stay up.
        """
        if self.state == IDLE:
            return self.status()
        self._teardown("操作画面から止めた")
        return self.status()

    def start_now(self, meeting_id: str = "") -> dict:
        """**Start this meeting right now.** It does not wait for the set time.

        The order of the steps is exactly the same as for a scheduled
        occurrence (`_begin`): start the delivery, switch the record to the
        meeting name, join Zoom, start caption generation, post to the chat.
        **Both go down the same path.** A separate order written for the manual
        case would mean that one day only one of the two gets fixed.

        This is for running a meeting that has no schedule entry. If the
        meeting has the `chat` box ticked, the URL is posted to the chat **when
        the button is pressed and again three minutes later** (for a scheduled
        occurrence it is the set time and three minutes later). If the Zoom URL
        is empty, it does not join.

        **It returns without waiting.** `_begin` includes launching Zoom and
        bringing the delivery up, which takes tens of seconds. The HTTP reply
        is not held that long. The progress appears in `state` and `note` of
        `status()`.

        It is called from the HTTP server thread.
        """
        if self.state != IDLE:
            raise ValueError("いま会議を回している。先に「いま止める」を押すこと。")
        store = self.app.web.meetings if self.app.web else None
        if store is None:
            raise ValueError("会議の一覧が用意できていない。")
        want = str(meeting_id) or store.status()["active"]
        found = [m for m in store.items() if m.id == want]
        if not found:
            raise ValueError("先に、配信する会議を選ぶこと。")
        loop = self.app.zoom.loop
        if loop is None:
            raise ValueError("本体がまだ動いていない。")
        meeting = found[0]
        # **Take the slot here.** `_begin` sets the state inside the event
        # loop, so during the few seconds before that the scheduler could start
        # another occurrence.
        self.state = JOINING
        self.note = f"Starting now: {meeting.name}"
        occurrence = store.occurrence_key(datetime.now())
        asyncio.run_coroutine_threadsafe(
            self._begin_guarded(meeting, occurrence), loop)
        print(f"[{now_str()}] schedule    starting {meeting.name} "
              "from the control page")
        return self.status()

    def skip_next(self) -> dict:
        """Mark the next occurrence as done and skip it.

        Use it when you will not attend, for example tomorrow.
        """
        store = self.app.web.meetings if self.app.web else None
        if store is None:
            return self.status()
        nxt = store.upcoming(datetime.now(), limit=1)
        if not nxt:
            self.note = "飛ばす予定が無い。"
            return self.status()
        store.mark_fired(nxt[0]["id"], nxt[0]["at"])
        self.note = "次の予定を飛ばした。"
        print(f"[{now_str()}] schedule    skipped {nxt[0]['name']} "
              f"at {nxt[0]['at']}")
        return self.status()

    async def _begin_guarded(self, meeting, occurrence: str) -> None:  # noqa: ANN001
        """`_begin` as thrown from `start_now`. **No exception gets out.**

        Nobody awaits the coroutine sent with `run_coroutine_threadsafe`, so an
        exception would show up nowhere. **And the state would stay stuck at
        JOINING.** After that, no meeting would ever start again, because the
        scheduler reads JOINING as "a meeting is running". This does the same
        cleanup that `run_forever` does for `_tick`.
        """
        try:
            await self._begin(meeting, occurrence)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(f"Cannot start now: {type(exc).__name__}: {exc}")
            try:
                self._teardown("Could not start, so the meeting was shut down")
            except Exception:  # noqa: BLE001
                self.state = IDLE

    # --- The main loop --------------------------------------------------------

    async def run_forever(self) -> None:
        """**It never returns. It never lets an exception out.**

        The `asyncio.wait(..., FIRST_COMPLETED)` in `app.App.run()` shuts the
        whole app down as soon as any task finishes. If this returned even
        once, LiveCaption would go down because of the schedule.
        """
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # **Do not hold the slot.** If the state stops halfway, no
                # meeting will start again. Keep the reason and go back to
                # idle.
                self._fail(f"スケジューラで例外: {type(exc).__name__}: {exc}")
                if self.state != IDLE:
                    try:
                        self._teardown("例外が出たので片付けた")
                    except Exception:  # noqa: BLE001
                        self.state = IDLE
            await asyncio.sleep(config.SCHEDULE_TICK_SEC)

    async def _tick(self) -> None:
        if self.state in (RUNNING, ARMING, JOINING):
            await self._watch_running()
            return
        if self.app.web is None:
            return
        due = self.app.web.meetings.due(datetime.now())
        if not due:
            return
        meeting, when = due[0]
        key = self.app.web.meetings.occurrence_key(when)
        for other, other_when in due[1:]:
            # Skip an overlapping occurrence. If it were kept, the app would
            # join an empty room 40 minutes late.
            other_key = self.app.web.meetings.occurrence_key(other_when)
            self.app.web.meetings.mark_fired(other.id, other_key)
            print(f"[{now_str()}] schedule    skipped {other.name} at {other_key} "
                  f"(it overlaps {meeting.name})")
        await self._begin(meeting, key)

    # --- Starting -------------------------------------------------------------

    async def _begin(self, meeting, occurrence: str) -> None:  # noqa: ANN001
        """Start one meeting.

        **Mark the occurrence as done first of all.** If slow work ran before
        the mark, the loop five seconds later would pick up the same occurrence
        again and launch Zoom over and over. An occurrence that failed to start
        is dropped. **Losing one meeting is better than going wild.**
        """
        store = self.app.web.meetings
        store.mark_fired(meeting.id, occurrence)

        self.state = JOINING
        self.meeting_id = meeting.id
        self.meeting_name = meeting.name
        self.occurrence = occurrence
        self._silence_min = meeting.silence_min
        self._max_min = meeting.max_min
        self._we_started_tunnel = False
        self._joined_zoom = bool(meeting.zoom)
        self._saw_audio = False
        self._chat_todo = []
        print(f"[{now_str()}] schedule    starting {meeting.name} ({occurrence})")

        # 1. Switch the meeting that is delivered.
        try:
            store.select(meeting.id)
        except ValueError as exc:
            # **Go back to idle.** If this returned with JOINING still set, no
            # meeting would ever start again.
            self._fail(f"会議を選べない: {exc}")
            self.state = IDLE
            self.meeting_id = self.meeting_name = self.occurrence = ""
            return
        self.note = "会議を選んだ"

        # 2. Start the delivery. **If a person already started it, leave it.**
        tunnel = self.app.web.tunnel
        if tunnel is not None:
            st = tunnel.status()
            if st["state"] in ("off", "error"):
                st = await asyncio.to_thread(tunnel.start)
                self._we_started_tunnel = True
                if st["state"] == "error":
                    # Carry on even without a delivery. Screen sharing and the
                    # Zoom captions still work.
                    print(f"[{now_str()}] schedule    cannot start the delivery: "
                          f"{st['error']}")
                    self.note = "配信を始められなかった"
                else:
                    self.note = "配信を始めた"

        # 3. Switch the record so that each meeting gets its own file.
        self.app.roll_transcript(label=meeting.name)

        # 4. Join Zoom.
        await self._join_zoom(meeting)

        # 5. Start caption generation and check that it really started.
        self.state = ARMING
        self.app.engine.set_running(True)
        self.started_at = time.monotonic()
        self._sentence_mark = self.app.last_sentence_at
        why = await self._arm()
        if why:
            self._fail(why)
            self._teardown("始まらなかったので片付けた")
            return
        self.state = RUNNING
        self.note = "字幕を出している"
        print(f"[{now_str()}] schedule    captions are running for {meeting.name}")

        # 6. Posting to the Zoom chat comes later. **Nothing is posted here.**
        #
        #    **Post only after the scheduled start time.** The Zoom chat does
        #    not show messages that were sent before you joined. LiveCaption
        #    starts lead_min minutes before the meeting, so a message posted
        #    here would be missed by everyone who joins on time (reported by a
        #    user, 2026-09-20).
        #
        #    Also, Zoom takes tens of seconds to a few minutes to show the
        #    meeting window after `zoommtg:` is handed over (measured: it was
        #    still not there five seconds after launch).
        #
        #    `_watch_running` waits until both the time and the window are
        #    ready.
        self._chat_next = 0.0
        self._chat_todo = []
        if meeting.chat:
            try:
                start_at = datetime.strptime(occurrence, meetings.TIME_FMT)
            except ValueError:
                start_at = datetime.now()
            self._chat_todo = [start_at + timedelta(minutes=m)
                               for m in config.SCHEDULE_CHAT_AT_MIN]
        self._chat_total = len(self._chat_todo)

    async def _try_chat(self) -> None:
        """Post to the chat once the meeting window is up. Retry until it is.

        **When captions start, Zoom has not finished joining the meeting yet.**
        Trying once and giving up there means that a meeting with the box
        ticked never gets a post at all (seen in a real meeting, 2026-09-20).

        **Post from another thread.** Posting takes about eight seconds.
        Waiting for it on the main event loop would stop both the audio capture
        and the captions for that time.
        """
        if not self._chat_todo:
            return
        # **Do not post before the time of this round.** A message posted early
        # leaves nothing for the people who join later. The Zoom chat does not
        # show messages that were sent before you joined.
        wall = datetime.now()
        if wall < self._chat_todo[0]:
            return
        now = time.monotonic()
        if now < self._chat_next:
            return
        self._chat_next = now + config.SCHEDULE_CHAT_RETRY_SEC
        late = wall > self._chat_todo[0] + timedelta(
            seconds=config.SCHEDULE_CHAT_WAIT_SEC)
        # Which round this is. **Keep it in the log.** There are two posts, so
        # you need to know which one failed.
        which = self._chat_total - len(self._chat_todo) + 1
        round_ = f" ({which}/{self._chat_total})"

        # **The implementation differs per machine.** Windows uses window
        # classes and the clipboard API. Linux (the container) uses windows on
        # Xvfb and `xdotool`. The interface is the same on both sides
        # (`meeting_window` / `compose` / `qr_file` / `post`).
        if os.name == "nt":
            from . import zoom_chat
        else:
            from . import zoom_chat_linux as zoom_chat

        if not zoom_chat.meeting_window():
            if late:
                self._chat_todo.pop(0)
                print(f"[{now_str()}] schedule    cannot post to the chat{round_}: "
                      "the Zoom meeting window never appeared")
            return

        web = self.app.web
        url = (web.public_url() or web.viewer_url()) if web else ""
        if not url:
            if late:
                self._chat_todo.pop(0)
                print(f"[{now_str()}] schedule    no URL to post to the chat{round_}")
            return

        name = self.meeting_name
        try:
            done = await asyncio.to_thread(self._post_chat_now, url, name)
        except Exception as exc:  # noqa: BLE001
            self._chat_todo.pop(0)
            print(f"[{now_str()}] schedule    cannot post to the chat{round_}: "
                  f"{type(exc).__name__}: {exc}")
            return

        if done["text"]:
            self._chat_todo.pop(0)
            self._space_out_next()
            extra = " (with the QR code)" if done["files"] else ""
            print(f"[{now_str()}] schedule    posted the URL to the chat"
                  f"{round_}{extra}")
            if not done["files"]:
                # This happens when the host has turned file sending off.
                # **The URL did arrive.**
                print(f"[{now_str()}] schedule    the QR code did not go. "
                      "Only the URL arrived.")
            return
        # Another window came to the front while pasting, and so on.
        # **Come back and try once more.**
        if late:
            self._chat_todo.pop(0)
            why = done["why"]
            print(f"[{now_str()}] schedule    cannot post to the chat{round_}: {why}")

    def _space_out_next(self) -> None:
        """Move the next round at least `SCHEDULE_CHAT_GAP_SEC` into the future.

        **When joining Zoom is late, the first post is pushed back.** If the
        time of the second post has already passed by then, the same message
        appears twice a few seconds apart. It looks broken.
        """
        if not self._chat_todo:
            return
        floor = datetime.now() + timedelta(seconds=config.SCHEDULE_CHAT_GAP_SEC)
        if self._chat_todo[0] < floor:
            self._chat_todo[0] = floor

    @staticmethod
    def _post_chat_now(url: str, name: str) -> dict:
        """**Runs on another thread.** Do not touch the app state from here."""
        # **The implementation differs per machine.** Windows uses window
        # classes and the clipboard API. Linux (the container) uses windows on
        # Xvfb and `xdotool`. The interface is the same on both sides
        # (`meeting_window` / `compose` / `qr_file` / `post`).
        if os.name == "nt":
            from . import zoom_chat
        else:
            from . import zoom_chat_linux as zoom_chat

        shot = zoom_chat.qr_file(url, name)
        return zoom_chat.post(zoom_chat.compose(url), [shot] if shot else [])

    async def _join_zoom(self, meeting) -> None:  # noqa: ANN001
        """Join Zoom. **Whether it worked cannot be known here.**

        `zoommtg:` is only handed to the handler. Nothing comes back, whether
        the app lands in the waiting room, hits a wrong passcode, or stops at
        an update dialog. **The check is done through the sound**:
        `_watch_running` watches for time passing with no transcript.
        """
        if not meeting.zoom:
            return
        # **If a person was already in a meeting, do not cut it later.** Only
        # remember the case where we joined, so that a meeting somebody had
        # open is not taken down with ours.
        #
        # **Do not test this with `running()`.** Zoom keeps a resident window
        # after you leave a meeting, so on a machine that stays up it is True
        # almost all the time. Reading that as "in a meeting" means we never
        # leave the meeting we joined (hit on the real machine, 2026-09-19).
        already = zoom_join.in_meeting()
        try:
            url = await asyncio.to_thread(
                zoom_join.join, meeting.zoom, config.ZOOM_DISPLAY_NAME)
        except zoom_join.JoinError as exc:
            # **Do not shut the meeting down here.** Captions still work if
            # somebody joins Zoom by hand.
            self._fail(f"Zoomに入れない: {exc}")
            self.note = "Zoomに入れなかった（手で入れば字幕は出る）"
            return
        self._we_launched_zoom = not already
        self.note = "Zoomに入った"
        print(f"[{now_str()}] schedule    joining Zoom: {url[:90]}")
        if already:
            print(f"[{now_str()}] schedule    Zoom was already in a meeting. "
                  "It will not be quit at the end")

    async def _arm(self) -> str:
        """Check that captions really started. Return the reason if they did not.

        **Do not look at `generating` alone.** When the input device could not
        be opened, `_pipeline` quietly sets it back to False as well. False
        means both "not yet" and "it failed", so look at
        `audio.status()["error"]` first.
        """
        deadline = time.monotonic() + config.SCHEDULE_ARM_SEC
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            err = self.app.audio.status().get("error", "")
            if err:
                return f"音声の入力を開けない: {err}"
            if self.app.engine.status()["generating"]:
                return ""
        return "生成が始まらない。"

    # --- While it is running ------------------------------------------------

    def _heard_sound(self) -> bool:
        """Whether any sound arrived at all since the meeting started.

        **This one looks at the amplitude**, not at whether a sentence came
        out. The question here is whether the audio path from Zoom is
        connected, not whether somebody spoke. If the app is stuck in the
        waiting room, not even background noise arrives.
        """
        capture = self.app.capture
        quiet_for = getattr(capture, "quiet_for", None)
        if quiet_for is None:
            return True     # when we cannot tell, do not suspect anything
        return quiet_for() < config.SCHEDULE_JOIN_AUDIO_SEC

    def _silence_limit(self) -> float:
        return self._silence_min * 60.0

    def _quiet_state(self) -> tuple[float, float, str]:
        """`(seconds of silence, the limit, the note shown when stopping)`.

        **"Nobody has spoken yet" and "the meeting is over" are two different
        things.** At the start of a meeting, a few minutes of silence while
        waiting for people is normal (reported by a user, 2026-09-21). Running
        the silence timer there **shuts down a meeting before it begins.**

        So a different timer is used until the first sentence comes out. What
        tells the two apart is **whether a sentence came out**, not the
        amplitude. Background noise while waiting for people raises the
        amplitude too, so the amplitude cannot tell them apart.

        A meeting that never starts is not left alone either. It is shut down
        past the limit. The speech recognition connection is billed even while
        nobody speaks, so the app does not sit in an empty meeting for hours.
        """
        now = time.monotonic()
        if self.app.last_sentence_at != self._sentence_mark:
            # Somebody spoke at least once. From here on, look at the time
            # since speech stopped.
            return now - self.app.last_sentence_at, self._silence_limit(), \
                "無音が続いたので止めた"
        # Not a single sentence yet. Look at the time since joining.
        # **If the per-meeting setting is longer, use that one.**
        limit = max(self._silence_limit(), config.SCHEDULE_OPENING_SEC)
        return now - self.started_at, limit, "会議が始まらないので止めた"

    def _max_limit(self) -> float:
        return self._max_min * 60.0

    async def _watch_running(self) -> None:
        if self.state != RUNNING:
            return
        # If a person stopped it from the control page, clean up here too.
        if not self.app.engine.status()["generating"]:
            self._teardown("生成が止められた")
            return
        # The chat post waits for the Zoom window. Retry it here as often as
        # needed.
        await self._try_chat()
        elapsed = time.monotonic() - self.started_at
        # **If time passes with no sound at all, we probably did not get in.**
        # Zoom reports nothing, whether it stopped in the waiting room, on a
        # wrong passcode, or at an update dialog. All we can see from here is
        # that no sound arrives. Meetings do start late, so do not judge this
        # in a hurry.
        if not self._saw_audio and self._heard_sound():
            self._saw_audio = True
        if (self._joined_zoom and not self._saw_audio
                and elapsed > config.SCHEDULE_JOIN_AUDIO_SEC):
            self._fail("Zoomから音が来ない。パスコード違いか、待機室で止まっているか、"
                       "更新のダイアログが出ている可能性がある。画面を見ること。")
            self._joined_zoom = False   # report it once, not again
        if elapsed > self._max_limit():
            # **Stop even when it is not silent.** This is the last guard
            # against a bill that never stops.
            self._teardown("安全上限で止めた", detail=f"{self._max_min} min")
            return
        quiet, limit, why = self._quiet_state()
        if quiet > limit:
            self._teardown(why, detail=f"{limit / 60:g} min")

    # --- Cleaning up --------------------------------------------------------

    def _teardown(self, why: str, detail: str = "") -> None:
        """Shut one meeting down. **The process is not ended.**

        `why` is the short note shown on the page, and it **must be a fixed
        sentence with no number in it.** The translation table can only replace
        fixed sentences. Put numbers in `detail`, which is kept in the terminal
        log only. That is enough for diagnosis.
        """
        self.state = STOPPING
        name = self.meeting_name
        extra = f" ({detail})" if detail else ""
        print(f"[{now_str()}] schedule    shutting {name} down: {why}{extra}")
        try:
            self.app.engine.set_running(False)
        except Exception as exc:  # noqa: BLE001
            print(f"[{now_str()}] schedule    cannot stop the captions: {exc}")
        self._leave_zoom()
        # Drop the Zoom caption token. **Each meeting has its own token.**
        # Keeping it would send the next meeting's captions to the old meeting.
        try:
            self.app.zoom.set_enabled(False)
        except Exception:  # noqa: BLE001
            pass
        tunnel = self.app.web.tunnel if self.app.web else None
        if tunnel is not None and self._we_started_tunnel:
            try:
                tunnel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"[{now_str()}] schedule    cannot stop the delivery: {exc}")
        self.app.roll_transcript()
        self.state = IDLE
        self.note = why
        self.meeting_id = ""
        self.meeting_name = ""
        self.occurrence = ""
        self._we_started_tunnel = False
        self._joined_zoom = False
        self._saw_audio = False
        # The chat posts that were still due. **They are dropped on teardown.**
        self._chat_todo = []
        self._chat_total = 0
        self._chat_next = 0.0

    def _leave_zoom(self) -> None:
        """Leave Zoom. **Only when we launched it.**

        There is no way to leave a meeting, so Zoom has to be quit. That kills
        every `Zoom.exe`, so a meeting a person had open must never be taken
        down with it.
        """
        if not self._we_launched_zoom:
            return
        self._we_launched_zoom = False
        try:
            if zoom_join.leave():
                print(f"[{now_str()}] schedule    Zoom was quit")
        except Exception as exc:  # noqa: BLE001
            print(f"[{now_str()}] schedule    cannot quit Zoom: {exc}")

    # --- Failures -----------------------------------------------------------

    def _fail(self, why: str) -> None:
        """**Keep it on the page until it is cleared by hand.**

        On a machine with nobody in front of it, a failure that scrolls past is
        never seen.
        """
        self.failure = why
        self.failure_at = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now_str()}] schedule    failed: {why}")
