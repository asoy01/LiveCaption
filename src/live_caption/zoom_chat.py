"""Post the caption URL to the chat of the meeting.

**The API cannot do this.** The Zoom REST API has no endpoint that posts to the
chat of a meeting in progress (`/chat/messages` is Team Chat, outside the
meeting, which is a different thing). A Meeting SDK app or a Zoom App could do
it, but that needs app review and approval from the university. For the same
reason the caption token is handled by hand, this is not the road taken for now.

**So the Zoom client on the caption PC is driven through its windows.**

The windows, as measured (Zoom 7.0.6, Windows, 2026-09-20):

    meeting   ConfMultiTabContentWndClass
    chat      ZConfChatPopupContainerWndClass   a **separate** window that
                                                Alt+H opens and closes

**Whether the chat is open is decided by whether this window exists.** Alt+H
toggles, so it must not be pressed without checking the state first. Pressing it
while the chat is open closes the chat, and the paste and the Enter that follow
land in the meeting window. That does no damage, but nothing is posted while the
program records that it "sent" the message. **For unattended operation, that is
the worst outcome.**

## Be aware that this is a dangerous thing to do

**This tool types keys into another window.** If it types in the moment the
foreground window changes, there is no telling what goes where. Before every
step, check that the window in front is the intended one, and stop if it is not.
`_focused` is what does this.

## It takes the clipboard

The message is sent by pasting, so the clipboard is used. **The content is saved
and put back.** Only text can be put back, however. An image or a file that was
on the clipboard is lost. Typing the text instead (sending one key per
character) drops characters with URLs and Japanese, so it is not used.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from pathlib import Path

from . import config, zoom_join

# **This module can also be imported on Linux.** The functions that touch
# windows run only on Windows, but **building the message and the QR code
# (`compose` / `qr_file`) works on any machine.** The Linux version
# (`zoom_chat_linux.py`) borrows only those. **Do not copy them.** That was done
# once, and the two copies drifted apart. The behavior on Windows does not
# change.
if hasattr(ctypes, "windll"):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    user32 = kernel32 = None

# The class name of the chat window. **Measured** (see the notes above).
CHAT_WINDOW_CLASS = "ZConfChatPopupContainerWndClass"

# The message shown to the participants. **It is fixed to English.** The readers
# are the participants of the meeting, not the person who has the control page
# open. That is why it does not go through `i18n`.
DIRECTION_EN = {
    "ja2en": "Japanese to English",
    "en2ja": "English to Japanese",
}
MESSAGE = (
    "Live captions for this meeting ({way}):\n"
    "{url}\n"
    "Open the link in any browser. No app or sign-in needed."
)

# --- Waiting times -----------------------------------------------------------
# **Do not make these shorter.** Zoom takes a moment to build the pasted
# content. If they are cut down, Enter is pressed before the content is ready,
# and an empty message is sent.
FOCUS_SEC = 0.35        # After bringing a window to the front, until it settles
OPEN_CHAT_SEC = 4.0     # After Alt+H, the limit for the chat window to appear
PASTE_SEC = 0.9         # From pasting the text until Enter
SENT_SEC = 1.5          # After Enter, until the next step


# =========================================================================
# The Windows layer
# =========================================================================

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
SW_RESTORE = 9

VK = {"RETURN": 0x0D, "MENU": 0x12, "CONTROL": 0x11, "H": 0x48,
      "V": 0x56, "ESCAPE": 0x1B}

# **Always declare the argument and return types.** The default is a 32-bit int,
# so a 64-bit handle is truncated and raises `OverflowError` (hit on
# 2026-09-20). **Only on Windows.** On Linux both `user32` and `kernel32` are
# None.
if user32 is not None:
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key(vk: int, up: bool = False) -> _INPUT:
    item = _INPUT(type=INPUT_KEYBOARD)
    item.u.ki = _KEYBDINPUT(wVk=vk, wScan=0,
                            dwFlags=KEYEVENTF_KEYUP if up else 0,
                            time=0, dwExtraInfo=None)
    return item


def _send(*names: str, hold: tuple[str, ...] = ()) -> None:
    """Press keys. `hold` lists the modifier keys to keep held down."""
    seq: list[_INPUT] = [_key(VK[h]) for h in hold]
    for name in names:
        seq.append(_key(VK[name]))
        seq.append(_key(VK[name], up=True))
    seq.extend(_key(VK[h], up=True) for h in reversed(hold))
    arr = (_INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(_INPUT))


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _find_window(classes: frozenset[str] | set[str]) -> int:
    """Return one visible window of Zoom with that class name. 0 if none."""
    pids = zoom_join._zoom_pids()
    if not pids:
        return 0
    found: list[int] = []

    def visit(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (pid.value in pids and user32.IsWindowVisible(hwnd)
                and _class_of(hwnd) in classes):
            found.append(hwnd)
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proto(visit), 0)
    return found[0] if found else 0


def _raise(hwnd: int) -> bool:
    """Bring a window to the front. **Windows does not like the foreground being
    taken away.** Attach the input thread first, then ask."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    cur = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(user32.AttachThreadInput(cur, target, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, target, False)
    time.sleep(FOCUS_SEC)
    return _focused(hwnd)


def _focused(hwnd: int) -> bool:
    """Is this window the one in front right now? **Always check before
    typing.**"""
    return user32.GetForegroundWindow() == hwnd


# --- Clipboard ---------------------------------------------------------------


_owner_hwnd = 0


def _owner() -> int:
    """Create one invisible window to own the clipboard.

    **Do not open it with `OpenClipboard(NULL)`.** When the owner is NULL,
    `SetClipboardData` returns success but nothing is stored. Reading it back
    gives an empty clipboard (hit on 2026-09-20; you can tell because
    `EnumClipboardFormats` returns nothing).
    """
    global _owner_hwnd
    if _owner_hwnd:
        return _owner_hwnd
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    hwnd_message = wintypes.HWND(-3)   # HWND_MESSAGE. It never appears on
                                       # screen.
    _owner_hwnd = user32.CreateWindowExW(
        0, "STATIC", "LiveCaptionClipboard", 0, 0, 0, 0, 0,
        hwnd_message, None, None, None) or 0
    return _owner_hwnd


def _clip_open(tries: int = 10) -> bool:
    owner = _owner()
    for _ in range(tries):
        if user32.OpenClipboard(owner if owner else None):
            return True
        time.sleep(0.05)
    return False


def _clip_put(fmt: int, blob: bytes) -> bool:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(blob))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, blob, len(blob))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def clip_get_text() -> str | None:
    """The text on the clipboard now. **This is all that can be put back**
    (images and files cannot)."""
    if not _clip_open():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
    except OSError:
        return None
    finally:
        user32.CloseClipboard()


def clip_set_text(text: str) -> bool:
    if not _clip_open():
        return False
    try:
        user32.EmptyClipboard()
        return _clip_put(CF_UNICODETEXT, (text + "\x00").encode("utf-16-le"))
    finally:
        user32.CloseClipboard()


# =========================================================================
# The main part
# =========================================================================


# --- Attaching a file --------------------------------------------------------
#
# **Do not hand the file over through the clipboard.** Putting a file list
# (CF_HDROP) on the clipboard breaks the clipboard sync of RustDesk (narrowed
# down on 2026-09-20; text alone does not break it). As requested, the file is
# now picked up through the attach button of Zoom.
#
# **That way is also more reliable.** Pressing the attach button opens the
# standard Windows file dialog (`#32770`), so the controls inside can be found
# by ID and the path can be typed into them. Coordinates are needed only for
# that first press.

# The position of the attach button. **The distance from the bottom left corner
# of the chat window** (at 96 dpi). Measured (Zoom 7.0.6, Windows, 2026-09-20.
# At 168 dpi it was 167 from the left and 48 from the bottom).
ATTACH_FROM_LEFT = 95
ATTACH_FROM_BOTTOM = 27
FILE_DIALOG_CLASS = "#32770"
# The controls of the file dialog. These numbers are fixed by the Windows common
# dialog.
DLG_FILENAME = 1148
DLG_OPEN = 1
DLG_CANCEL = 2
DIALOG_SEC = 6.0        # Limit for the dialog to appear after pressing attach
DIALOG_GONE_SEC = 8.0   # Limit for the dialog to close after pressing Open

WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
DPI_PER_MONITOR_V2 = -4


def _dpi_aware():
    """Make this thread alone work in real screen coordinates.

    **The rest of the program runs without knowing the real screen size.** Left
    as it is, `GetWindowRect` returns stretched coordinates, and the press lands
    in the wrong place. Changing the setting for the whole process would affect
    screen capture and other windows, so only this thread is changed.
    """
    try:
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        return user32.SetThreadDpiAwarenessContext(
            ctypes.c_void_p(DPI_PER_MONITOR_V2))
    except (AttributeError, OSError):
        return None


def _dpi_restore(token) -> None:  # noqa: ANN001
    if token:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(token))
        except (AttributeError, OSError):
            pass


def _file_dialog() -> int:
    """The file dialog that Zoom has opened. 0 if there is none."""
    return _find_window({FILE_DIALOG_CLASS})


def _click(x: int, y: int) -> None:
    """Click once at that position. **Always move the pointer back to where it
    was.**"""
    where = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(where))
    try:
        user32.SetCursorPos(x, y)
        time.sleep(0.2)
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    finally:
        time.sleep(0.2)
        user32.SetCursorPos(where.x, where.y)


def attach(chat: int, path: Path) -> bool:
    """Attach a file to the chat and send it. **The clipboard is not used.**

    Only the position of the attach button depends on coordinates. **If no
    dialog appears after the press, always press Escape to get back.** The
    button next to it captures the screen, so a missed press that is left alone
    would keep that open for the rest of the meeting.
    """
    if not _raise(chat):
        return False
    token = _dpi_aware()
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(chat, ctypes.byref(rect))
        try:
            scale = (user32.GetDpiForWindow(chat) or 96) / 96.0
        except (AttributeError, OSError):
            scale = 1.0
        _click(rect.left + int(ATTACH_FROM_LEFT * scale),
               rect.bottom - int(ATTACH_FROM_BOTTOM * scale))
    finally:
        _dpi_restore(token)

    deadline = time.monotonic() + DIALOG_SEC
    dialog = 0
    while time.monotonic() < deadline:
        time.sleep(0.25)
        dialog = _file_dialog()
        if dialog:
            break
    if not dialog:
        # Something else may have opened. **Do not leave it open.**
        _send("ESCAPE")
        print("  [chat] the file dialog did not appear. "
              "The QR code is not sent.")
        return False

    edit = user32.GetDlgItem(dialog, DLG_FILENAME)
    if edit:
        # The real control is the Edit inside the ComboBoxEx32.
        inner = user32.FindWindowExW(edit, None, "ComboBox", None)
        if inner:
            edit = user32.FindWindowExW(inner, None, "Edit", None) or edit
    if not edit:
        user32.SendMessageW(user32.GetDlgItem(dialog, DLG_CANCEL), BM_CLICK, 0, 0)
        return False
    user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.c_wchar_p(str(path)))
    time.sleep(0.3)
    user32.SendMessageW(user32.GetDlgItem(dialog, DLG_OPEN), BM_CLICK, 0, 0)

    gone = time.monotonic() + DIALOG_GONE_SEC
    while time.monotonic() < gone:
        time.sleep(0.25)
        if not user32.IsWindow(dialog) or not user32.IsWindowVisible(dialog):
            break
    else:
        # It could not open the file (a wrong path, for example). **Close the
        # dialog before returning.**
        user32.SendMessageW(user32.GetDlgItem(dialog, DLG_CANCEL), BM_CLICK, 0, 0)
        print("  [chat] the file dialog does not close. "
              "The QR code is not sent.")
        return False

    if not _raise(chat):
        return False
    _send("RETURN")
    time.sleep(SENT_SEC)
    return True


def compose(url: str, direction: str = "") -> str:
    """Build the message shown to the participants. **Always in English.**"""
    way = DIRECTION_EN.get(direction or config.DIRECTION, "")
    if not way:
        return f"Live captions for this meeting:\n{url}\n" \
               "Open the link in any browser. No app or sign-in needed."
    return MESSAGE.format(way=way, url=url)


def qr_file(url: str, name: str = "") -> Path | None:
    """Write the QR code that goes with the chat message. None if it cannot be
    written.

    **Give it a name the receiver can understand.** The file is sent through the
    clipboard, so the receiver sees the file name as it is. With `{GUID}.png`
    nobody can tell what the file is.

    It is written in `local/`, away from the meeting records. **It is
    overwritten the next time a message is posted.**
    """
    try:
        import segno

        safe = "".join(c for c in name
                       if c.isascii() and (c.isalnum() or c in "-_ ")).strip()
        safe = "-".join(safe.split())[:40]
        out = config.LOG_DIR.parent / (
            f"live-captions-qr-{safe}.png" if safe else "live-captions-qr.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        segno.make(url, error="m").save(
            str(out), scale=8, border=3, dark="#000000", light="#ffffff")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"  [chat] cannot write the QR code: {exc}")
        return None


def meeting_window() -> int:
    return _find_window(zoom_join.MEETING_WINDOW_CLASSES)


def chat_window() -> int:
    return _find_window({CHAT_WINDOW_CLASS})


def open_chat() -> int:
    """Open the chat window and return it. 0 if it cannot be opened.

    **If it is already open, Alt+H is not pressed.** Pressing it would close the
    chat.
    """
    found = chat_window()
    if found:
        return found
    meeting = meeting_window()
    if not meeting or not _raise(meeting):
        return 0
    _send("H", hold=("MENU",))
    deadline = time.monotonic() + OPEN_CHAT_SEC
    while time.monotonic() < deadline:
        time.sleep(0.25)
        found = chat_window()
        if found:
            return found
    return 0


def post(text: str, files: list[Path] | None = None) -> dict:
    """Post to the chat. Return what could be posted. **It never raises.**

    **Send the text first.** Files can be blocked by the setting of the host,
    and from this side there is no way to tell that they were blocked. If the
    text goes first, the URL still arrives even when the file is blocked.

    It returns `{"text": bool, "files": int, "why": str}`.
    """
    done = {"text": False, "files": 0, "why": ""}
    files = list(files or [])

    chat = open_chat()
    if not chat:
        done["why"] = ("チャットの窓を出せない。会議に入っているか確かめること。"
                       if meeting_window() else "Zoomの会議の窓が無い。")
        return done

    saved = clip_get_text()
    try:
        if text:
            done["text"] = _paste_and_send(chat, lambda: clip_set_text(text),
                                           PASTE_SEC)
            if not done["text"]:
                done["why"] = "貼り付けの途中で、前面の窓が入れ替わった。"
                return done
        for path in files:
            if not Path(path).exists():
                continue
            if not attach(chat, Path(path)):
                # **Do not stop here.** The text has already arrived.
                done["why"] = "ファイルを添付できなかった。URLだけは届いている。"
                break
            done["files"] += 1
    finally:
        # **Always put it back.** Do not quietly take away what a person had
        # placed on the clipboard.
        if saved is not None:
            clip_set_text(saved)
    return done


def _paste_and_send(chat: int, load, wait: float) -> bool:  # noqa: ANN001
    """Put the text on the clipboard, paste it, and send it. Stop if the window
    is no longer in front."""
    if not _raise(chat):
        return False
    if not load():
        return False
    time.sleep(0.25)
    if not _focused(chat):
        return False
    _send("V", hold=("CONTROL",))
    time.sleep(wait)
    if not _focused(chat):
        return False
    _send("RETURN")
    time.sleep(SENT_SEC)
    return True
