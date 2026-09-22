"""Join and leave a Zoom meeting. **Linux (container) only.**

The Windows version is in `zoom_join.py`. **That one is frozen. Do not touch
it.** The shared parts (`parse_meeting` / `join_url` / `JoinError`) are
borrowed from there.

There are two large differences from Windows.

1. **A preview window appears.** `enableShowPreviewWndToJoin=false` has no
   effect (confirmed in stage 0). So a step that presses "Join" is needed.
2. **Joining the meeting and joining the audio are separate.** After Join,
   not a single byte of audio arrives until "Join with Computer Audio" is
   pressed.

**Buttons are pressed at a distance from the corner of the window.** Holding
absolute screen coordinates would break silently on the day the window size
or the screen resolution changes. The distances were measured (2026-09-21, on
a 1600x1200 screen).

    With a 638x532 preview window, Join is at screen (1073, 1016)
      -> 47 from the right, 49 from the bottom
    With a 570x400 audio dialog, Join with Computer Audio is at (799, 563)
      -> centered horizontally, 143 from the top
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config
from .zoom_join import JoinError, join_url, parse_meeting  # noqa: F401

#: The names of the meeting window. **The container's locale is fixed, so
#: they are in English.** The class name `zoom.zoom` is the same on every
#: window, so windows cannot be told apart by class as they are on Windows
#: (confirmed on 2026-09-21).
MEETING_WINDOW_NAMES = frozenset({"Meeting", "Zoom Meeting"})
#: The resident home window. It has nothing to do with a meeting.
HOME_WINDOW_NAMES = frozenset({"Zoom Workplace", "Zoom"})
#: How the audio dialog is recognized (substring match).
AUDIO_DIALOG_HINT = "audio conference options"

#: The smallest size that counts as the preview window.
#: **Zoom shows a thin notification window** (a 850x74 one named `zoom` was
#: measured). Selecting by name alone would press that one instead. The real
#: preview window was 638x532.
MIN_PREVIEW_W = 300
MIN_PREVIEW_H = 300

#: Button positions (the measurements above).
JOIN_FROM_RIGHT = 47
JOIN_FROM_BOTTOM = 49
AUDIO_FROM_TOP = 143

#: How many seconds to wait at each step.
#: **When it is fast, it is fast.** With a warm profile, the preview window
#: appears in 4 seconds, and audio arrives 2 to 5 seconds after joining
#: (measured on 2026-09-21). These waits are not shortened because of cold
#: starts and busy networks.
PREVIEW_WAIT_SEC = 45.0
AUDIO_DIALOG_WAIT_SEC = 45.0
AUDIO_READY_WAIT_SEC = 20.0
_POLL_SEC = 0.5

#: Where crash reports are kept. **Empty it before starting Zoom.**
#: If the last run ended badly, "Zoom quit unexpectedly" comes to the front
#: and the steps that press by coordinates stop there.
CRASH_REPORT_DIR = Path.home() / ".zoom" / "reports"

#: How many seconds to watch which windows are present after joining.
#: **This is for diagnosis only.**
#:
#: The "AI Companion is on" dialog appears **almost every time** (reported by
#: a user, 2026-09-21). Captions appear without closing it by hand, so **it
#: is not in the way.** But its name and size are not known, so there is no
#: guarantee that `_preview_window()` will not grab it. **The safety we have
#: now rests on luck in the ordering.** It is only because the preview window
#: appears first that the preview window is picked up first; if Zoom is
#: already running and a dialog from the previous meeting is still there,
#: that one gets pressed.
#:
#: **This dialog appears after the meeting is joined, so it is not visible
#: before `join()` returns.** That is why the windows are watched for a while
#: after joining as well.
#: **Once the name is known, add it to the exclusions in
#: `_preview_window()` and this watcher can be removed entirely.**
WATCH_AFTER_JOIN_SEC = 20.0
WATCH_STEP_SEC = 5.0


def _run(args: list[str], timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


def _pids() -> set[int]:
    """The Zoom processes themselves. **Do not use `pgrep -f`.**

    The command line of the shell that started this program also contains the
    word `zoom`, so it would be picked up too.
    """
    pids = set()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")[0].decode(
                "utf-8", "replace")
        except OSError:
            continue
        if cmd == "/opt/zoom/zoom":
            pids.add(int(p.name))
    return pids


def running() -> bool:
    """Whether Zoom is running.

    **Do not use this to decide whether we are in a meeting.** Zoom leaves
    the resident home window behind after leaving a meeting. Use
    `in_meeting()` to see whether a meeting is in progress.
    """
    return bool(_pids())


def _windows() -> list[tuple[str, int, str]]:
    """A list of `(window id, PID, name)`. Zoom's windows only."""
    pids = _pids()
    out = []
    for line in _run(["wmctrl", "-lp"]).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5 or not parts[2].isdigit():
            continue
        if int(parts[2]) in pids:
            out.append((parts[0], int(parts[2]), parts[4].strip()))
    return out


def _geometry(win_id: str) -> tuple[int, int, int, int] | None:
    """`(x, y, width, height)`. None if it cannot be read."""
    vals = {}
    for line in _run(["xdotool", "getwindowgeometry", "--shell", win_id]).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if v.strip().lstrip("-").isdigit():
                vals[k.strip()] = int(v)
    try:
        return vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"]
    except KeyError:
        return None


def _audio_attached() -> bool:
    """Whether Zoom is writing out audio. **Judged by whether a sink-input
    exists.**

    In PulseAudio terms, `meeting` and `mic` are **sinks** (outputs), and a
    stream of audio that an application has connected to one is a
    **sink-input**. When Zoom joins the meeting audio a sink-input appears,
    and when it leaves the sink-input goes away.

    **This is the most reliable sign that a meeting is in progress.** Window
    names change with the display language; this does not. Zoom is the only
    thing that plays audio in this container.
    """
    return bool(_run(["pactl", "list", "short", "sink-inputs"]).strip())


def in_meeting() -> bool:
    """Whether we are in a meeting right now.

    **The presence of the process does not tell you.** Zoom leaves the
    resident home window behind after leaving a meeting, so on a machine that
    runs all the time it is running almost always. Reading that as "in a
    meeting" means **we never leave the meeting we joined.**

    Look at the audio (sink-input) first, and at the window names when there
    is none. There is an in-between state where the audio is not connected
    yet but the meeting window is already up.
    """
    if not running():
        return False
    if _audio_attached():
        return True
    known = {n.lower() for n in MEETING_WINDOW_NAMES}
    return any(name.lower() in known for _id, _pid, name in _windows())


def _click(x: int, y: int) -> None:
    _run(["xdotool", "mousemove", str(x), str(y), "click", "1"])


def _wait(predicate, limit: float):  # noqa: ANN001
    """Wait until `predicate()` returns something true, and return that value
    as it is. None when the time runs out."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(_POLL_SEC)
    return None


def _preview_window() -> tuple[str, int, str] | None:
    """The preview window. **Its name is the meeting title, so it is not
    known in advance.**

    So it is picked up as "the window that is not the resident home window,
    not the meeting window, and not the audio dialog".
    """
    known = {n.lower() for n in HOME_WINDOW_NAMES | MEETING_WINDOW_NAMES}
    for win in _windows():
        name = win[2].lower()
        if name in known or AUDIO_DIALOG_HINT in name:
            continue
        geo = _geometry(win[0])
        if geo is None or geo[2] < MIN_PREVIEW_W or geo[3] < MIN_PREVIEW_H:
            continue
        return win
    return None


def _audio_dialog() -> tuple[str, int, str] | None:
    for win in _windows():
        if AUDIO_DIALOG_HINT in win[2].lower():
            return win
    return None


def _label(name: str, geo: tuple[int, int, int, int] | None) -> str:
    """What that window is. **Judge it by the same conditions as
    `_preview_window()`.**

    If the conditions are written separately, a day will come when
    `_preview_window()` picks up a window that the log says is not a
    candidate.
    """
    low = name.lower()
    if low in {n.lower() for n in HOME_WINDOW_NAMES}:
        return "常駐の窓口"
    if low in {n.lower() for n in MEETING_WINDOW_NAMES}:
        return "会議の窓"
    if AUDIO_DIALOG_HINT in low:
        return "音声ダイアログ"
    if geo is None:
        return "大きさが取れない（候補外）"
    if geo[2] < MIN_PREVIEW_W or geo[3] < MIN_PREVIEW_H:
        return f"小さいので候補外（{MIN_PREVIEW_W}x{MIN_PREVIEW_H} 未満）"
    return "★プレビュー窓の候補"


def _log_windows(stage: str) -> None:
    """Record the visible Zoom windows by name and size.

    **When the app runs without a display, `docker compose logs` is the only
    clue.** Printing them here makes the next real meeting a record in
    itself. Nobody has to sit and watch VNC.
    """
    try:
        wins = _windows()
    except Exception as exc:  # noqa: BLE001
        print(f"  [参加] 窓を見られない（{stage}）: {exc}")
        return
    if not wins:
        print(f"  [参加] 窓（{stage}）: 無し")
        return
    print(f"  [参加] 窓（{stage}）: {len(wins)}個")
    for win_id, _pid, name in wins:
        geo = _geometry(win_id)
        size = f"{geo[2]}x{geo[3]}+{geo[0]}+{geo[1]}" if geo else "大きさ不明"
        print(f"  [参加]   {name!r} {size}  {_label(name, geo)}")


def _watch_windows_later() -> None:
    """Record which windows are present for a while after joining. **Run it
    on a separate thread.**

    **`join()` must not be made to wait.** Making it wait delays the captions
    and the chat posts by the same amount. Do not make the main work wait for
    something that exists only for diagnosis.

    The thread is a daemon. It holds no blocking wait, so it does not get in
    the way of shutdown (unlike a thread that sits in `queue.get()`).
    """
    def run() -> None:
        watched = 0.0
        while watched < WATCH_AFTER_JOIN_SEC:
            time.sleep(WATCH_STEP_SEC)
            watched += WATCH_STEP_SEC
            _log_windows(f"参加の{int(watched)}秒後")

    threading.Thread(
        target=run, name="zoom-window-watch", daemon=True).start()


def join(text: str, name: str = "") -> str:
    """Start Zoom and make it join the meeting. Return the URL that was
    passed.

    **Whether it got in is not known here.** Zoom returns nothing, whether it
    lands in the waiting room, on a wrong passcode, or on an update dialog.
    **Confirm by the audio** (`in_meeting()` looks at the sink-input).
    """
    confno, pwd = parse_meeting(text)
    url = join_url(confno, pwd, name)

    exe = shutil.which(config.ZOOM_CMD)
    if exe is None:
        raise JoinError(
            f"Zoom が見つからない（{config.ZOOM_CMD}）。\n"
            "  コンテナに Zoom が入っているか確かめること。")

    # **Throw away the crash reports first.** If the last run ended badly,
    # "Zoom quit unexpectedly" comes to the front the moment Zoom starts, and
    # the coordinate clicks below stop there.
    try:
        for f in CRASH_REPORT_DIR.iterdir():
            if f.is_file():
                f.unlink()
    except OSError:
        pass

    try:
        subprocess.Popen(
            [exe, url], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise JoinError(f"Zoom を起こせない: {exc}") from exc

    # --- The "Join" on the preview window ------------------------------------
    # **`enableShowPreviewWndToJoin=false` has no effect** (stage 0).
    win = _wait(_preview_window, PREVIEW_WAIT_SEC)
    _log_windows("プレビュー窓を待った後")
    if win is None:
        print("  [参加] プレビュー窓が出てこなかった。Joinは押していない。")
    else:
        print(f"  [参加] プレビュー窓として {win[2]!r} を押す")
        geo = _geometry(win[0])
        if geo is not None:
            x, y, w, h = geo
            _click(x + w - JOIN_FROM_RIGHT, y + h - JOIN_FROM_BOTTOM)

    # --- The "Join with Computer Audio" on the audio dialog ------------------
    # **Joining the meeting and joining the audio are separate actions.** No
    # audio arrives until this is pressed.
    dlg = _wait(_audio_dialog, AUDIO_DIALOG_WAIT_SEC)
    if dlg is None:
        print("  [参加] 音声ダイアログが出てこなかった。音は来ない見込み。")
    else:
        geo = _geometry(dlg[0])
        if geo is not None:
            x, y, w, h = geo
            _click(x + w // 2, y + AUDIO_FROM_TOP)

    # Wait until audio arrives. **Do not raise when it does not.** We may
    # just be held in the waiting room. The scheduler (schedule.py) closes
    # things down on silence.
    _wait(_audio_attached, AUDIO_READY_WAIT_SEC)
    _log_windows("音に入った後")

    # **The AI Companion dialog appears after this point.** Hand it to
    # another thread and do not make the main work wait (read the note on
    # `WATCH_AFTER_JOIN_SEC`).
    _watch_windows_later()
    return url


def leave() -> bool:
    """Quit Zoom. True if it was quit.

    **There is no way to leave a meeting.** Quitting is the only option.
    **Call this only when we were the one who joined the meeting**
    (`schedule.py` remembers that).
    """
    if not in_meeting():
        return False
    pids = _pids()
    if not pids:
        return False
    _run(["pkill", "-TERM", "-f", "/opt/zoom/zoom"], timeout=15)
    # Kill it if it does not go down quietly. **If it stays, the next meeting
    # cannot be joined.**
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _pids():
        time.sleep(_POLL_SEC)
    if _pids():
        _run(["pkill", "-KILL", "-f", "/opt/zoom/zoom"], timeout=15)
    return True
