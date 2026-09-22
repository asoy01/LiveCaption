"""Post the caption URL into the meeting chat. **Linux (container) only.**

The Windows version is in `zoom_chat.py`. **That one is frozen. Do not touch
it.** Building the message text and the QR code (`compose` / `qr_file`) does
not depend on the machine, so we borrow those from there. **Do not duplicate
them.**

**The API cannot do this.** The Zoom REST API has no interface for posting to
the chat of a running meeting. So we drive the screen instead. This is the same
reason as in the Windows version.

## Differences from the Windows version

**The chat is not a separate window; it is a panel inside the meeting window.**
On Windows a standalone window with the class
`ZConfChatPopupContainerWndClass` appears, but Zoom on Linux inserts the chat
on the right side of the meeting window. **So the window class cannot tell us
whether the chat is open.**

We decide from **the width of the meeting window**. Opening the chat widens the
window (measured: 1188 to about 1600). Alt+H toggles open and closed, so it
must not be pressed without checking the state first. Pressing it while the
chat is open closes it, and the paste and Enter that follow land in the meeting
window. That does no damage, but **we would record "sent" while nothing was
posted. For unattended operation, that is the worst outcome.**

## Use the clipboard

We send by pasting. Typing one character at a time drops characters in URLs and
in Japanese text. Put the text in with `xclip` and paste it with `ctrl+v`.
**Save the previous contents and put them back.**

## Be aware that this is a risky thing to do

**This is a tool that types keys into another window.** If it types during the
moment the front window changes, there is no telling where the input goes.
Before each step, check that the intended window is in front.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import zoom_join_linux as zoom_join
# **Borrow the message text and the QR code.** `zoom_chat.py` is written so
# that it can be imported on Linux too (on a machine without `ctypes.windll`,
# only the window functions are dead).
from .zoom_chat import DIRECTION_EN, MESSAGE, compose, qr_file  # noqa: F401

#: The difference at which we treat the width as "changed". Measured, it moves
#: a lot: **1188 to 1629** (2026-09-21, on a 1600x1200 screen). Keep it large
#: enough not to pick up jitter.
CHAT_WIDTH_DELTA = 50

#: Position of the chat input box. **Held as a distance from the bottom right
#: of the window.** The chat panel sticks to the right edge of the window, so
#: the distance from the right does not move.
#: Measured (2026-09-21: screen position (1400, 890) when the meeting window
#: was 1629x800).
INPUT_FROM_RIGHT = 229
INPUT_FROM_BOTTOM = 110

#: Waits for each step.
FOCUS_SEC = 0.4         # after raising the window, until it settles
OPEN_CHAT_SEC = 6.0     # after Alt+H, the limit for waiting for the widening
PASTE_SEC = 0.9         # from the paste until Enter
SENT_SEC = 1.5          # after Enter, until moving on
IMAGE_PASTE_SEC = 2.0   # after pasting an image, until the preview appears


def _run(args: list[str], timeout: float = 10.0, text_in: str | None = None) -> str:
    try:
        out = subprocess.run(
            args, input=text_in, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            stdin=None if text_in is not None else subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


# --- Clipboard --------------------------------------------------------------


def clip_get_text() -> str | None:
    """The current clipboard. None if it cannot be read."""
    out = _run(["xclip", "-o", "-selection", "clipboard"], timeout=5)
    return out if out else None


def clip_set_text(text: str) -> bool:
    """Put text in the clipboard.

    **`xclip` stays resident after putting the text in.** It has to remain the
    owner of the selection, or the contents disappear before we paste. So we
    fire it off without waiting for it.
    """
    try:
        subprocess.Popen(
            ["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).communicate(text.encode("utf-8"), timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


# --- Windows ----------------------------------------------------------------


def meeting_window() -> int:
    """The ID of the meeting window. 0 if there is none."""
    known = {n.lower() for n in zoom_join.MEETING_WINDOW_NAMES}
    for wid, _pid, name in zoom_join._windows():
        if name.lower() in known:
            try:
                return int(wid, 16)
            except ValueError:
                return 0
    return 0


def _meeting_id() -> str:
    """The ID of the meeting window, in the form `xdotool` takes. Empty if
    there is none.
    """
    known = {n.lower() for n in zoom_join.MEETING_WINDOW_NAMES}
    for wid, _pid, name in zoom_join._windows():
        if name.lower() in known:
            return wid
    return ""


def _width(win_id: str) -> int:
    geo = zoom_join._geometry(win_id)
    return geo[2] if geo else 0


def _raise(win_id: str) -> bool:
    """Raise the window to the front. True if it could be raised."""
    _run(["xdotool", "windowactivate", "--sync", win_id], timeout=8)
    time.sleep(FOCUS_SEC)
    return _focused(win_id)


def _focused(win_id: str) -> bool:
    """Whether this window is the one in front right now.

    **Always check before typing.**
    """
    now = _run(["xdotool", "getactivewindow"], timeout=5).strip()
    if not now:
        return False
    try:
        return int(now) == int(win_id, 16)
    except ValueError:
        return False


def _toggle(win_id: str, before: int) -> int:
    """Press Alt+H and wait until the width changes.

    Returns the width after the change (0 if it did not change).
    """
    _run(["xdotool", "key", "--window", win_id, "alt+h"], timeout=8)
    deadline = time.monotonic() + OPEN_CHAT_SEC
    while time.monotonic() < deadline:
        time.sleep(0.25)
        now = _width(win_id)
        if abs(now - before) >= CHAT_WIDTH_DELTA:
            return now
    return 0


def open_chat() -> str:
    """Open the chat panel and return the ID of the meeting window. Empty if it
    cannot be opened.

    **Do not decide "is it open" from an absolute value.** Zoom on Linux
    inserts the chat inside the meeting window, so we cannot tell from a
    separate window class the way the Windows version does. That leaves the
    width, but **the closed width depends on the screen size and the Zoom
    version.** A hard-coded value will quietly stop matching one day.

    So we press the key and judge from **the direction the width moved**. If it
    shrank, we closed a chat that was open, so we press again. No reference
    value is needed.

    Measured (2026-09-21, on a 1600x1200 screen): closed 1188, open 1629.
    """
    win_id = _meeting_id()
    if not win_id:
        return ""
    if not _raise(win_id):
        return ""

    w0 = _width(win_id)
    w1 = _toggle(win_id, w0)
    if not w1:
        return ""           # no reaction. the meeting may have chat disabled
    if w1 > w0:
        return win_id       # it opened

    # It shrank, so we closed a chat that was open. **Press again to restore.**
    w2 = _toggle(win_id, w1)
    return win_id if w2 and w2 > w1 else ""


# --- Attachments ------------------------------------------------------------


def _clip_set_image(path: Path) -> bool:
    """Put an image on the clipboard. `xclip` stays resident as the owner of
    the selection.
    """
    try:
        subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-t", "image/png",
             "-i", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except OSError:
        return False
    time.sleep(0.8)
    return True


def _click_input(win_id: str) -> bool:
    """Click the chat input box to give it the focus."""
    geo = zoom_join._geometry(win_id)
    if geo is None:
        return False
    x, y, w, h = geo
    _run(["xdotool", "mousemove",
          str(x + w - INPUT_FROM_RIGHT), str(y + h - INPUT_FROM_BOTTOM)],
         timeout=8)
    time.sleep(0.3)
    _run(["xdotool", "click", "1"], timeout=8)
    time.sleep(0.5)
    return True


def attach(win_id: str, path: Path) -> bool:
    """Put the QR code in the chat. True if it could be put there.

    **Do not use the attach button. Paste from the clipboard.**
    Pressing the attach button does open a Qt file dialog, but **it opened once
    and after that no amount of pressing produced any reaction** (2026-09-21).
    We do not put something whose cause we do not understand on an unattended
    path.

    Pasting also gives a better result. **The image appears in the chat
    directly**, so the people who receive it can read it on the spot without
    downloading. The problem seen in the Windows version, where the file name
    is a random string and nobody can tell what the file is, does not happen
    either.
    """
    if not _clip_set_image(path):
        return False
    if not _focused(win_id):
        return False
    _click_input(win_id)
    _run(["xdotool", "key", "--window", win_id, "ctrl+v"], timeout=8)
    time.sleep(IMAGE_PASTE_SEC)
    _run(["xdotool", "key", "--window", win_id, "Return"], timeout=8)
    time.sleep(SENT_SEC)
    return True


# --- Posting ----------------------------------------------------------------


def post(text: str, files: list[Path] | None = None) -> dict:
    """Post to the chat. Return what could be posted. **Never raises.**

    This is a tool that runs unattended, so a failure must not drag the caller
    down with it.
    """
    done = {"text": False, "files": 0, "error": ""}
    win_id = open_chat()
    if not win_id:
        done["error"] = "会議のチャットを開けない。会議に入っているか確かめること。"
        return done

    saved = clip_get_text()
    try:
        if not _raise(win_id):
            done["error"] = "会議の窓を前面に出せない。"
            return done
        if not clip_set_text(text):
            done["error"] = "クリップボードに入れられない。"
            return done
        time.sleep(PASTE_SEC)
        # **Check once more before typing into the front window.** If we type
        # during the moment the front window changes, there is no telling
        # where the input goes.
        if not _focused(win_id):
            done["error"] = "打ち込む直前に、別の窓が前面に出た。中止した。"
            return done
        _run(["xdotool", "key", "--window", win_id, "ctrl+v"], timeout=8)
        time.sleep(PASTE_SEC)
        _run(["xdotool", "key", "--window", win_id, "Return"], timeout=8)
        time.sleep(SENT_SEC)
        done["text"] = True

        # **The QR code comes after the message text.** Sent first, the image
        # would stand alone above the URL.
        for path in files or []:
            if not Path(path).is_file():
                continue
            if attach(win_id, Path(path)):
                done["files"] += 1
            else:
                # **Do not lie and say it was sent.** The message text did
                # arrive, so the URL reached the readers.
                done["error"] = "QRを載せられなかった。文面は投げた。"
    finally:
        # **Give back the clipboard we took.** Only text can be restored.
        if saved is not None:
            clip_set_text(saved)
    return done
