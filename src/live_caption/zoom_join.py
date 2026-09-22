"""Join and leave a Zoom meeting. Windows only.

Zoom registers a `zoommtg:` scheme with the OS. When a URL is sent there, the
Zoom client starts and joins the meeting.

    zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=<hash>

**We cannot tell whether the join succeeded.** Sending the URL is one-way, and
nothing comes back whether it ends in the waiting room, a wrong passcode, or an
update dialog. **The only confirmation of a join comes from the audio**
(`schedule.py` checks whether a transcript appeared).

**The only way to leave is to quit Zoom.** Zoom has no interface to ask it to
leave a meeting from outside. This is acceptable on a dedicated caption PC, but
**never kill a Zoom other than the one we started.** A meeting that someone
left open must not be taken down with it.

Settings on the Zoom side (once; not needed every time):

- send the audio output to `CABLE Input`, and mute the microphone
- set the display name to something like `Live Captions`
- turn on "mute my microphone when joining" and "turn off my video when joining"
- **turn off the "Join with Computer Audio" dialog.**
  If it keeps appearing, an unattended join gets no audio, and the failure has
  no visible cause
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

# A meeting number has 9 to 11 digits. When a person pastes one, spaces and
# hyphens get mixed in.
_DIGITS = re.compile(r"[0-9]")
# The shape of an invitation URL. Picks up `/j/<number>`. The host is `zoom.us`
# or something under it.
_JOIN_PATH = re.compile(r"/j/(\d{9,12})")
# A personal link. **It carries no number, so we cannot turn it into a meeting
# number.**
_PERSONAL = re.compile(r"/my/([A-Za-z0-9._-]+)")
# Class names of the meeting window. **Confirmed by measurement** (Zoom as of
# 2026-09, Windows). Do not look at the title. It changes with the display
# language (in Japanese it reads "Zoom ミーティング").
MEETING_WINDOW_CLASSES = frozenset({
    "ConfMultiTabContentWndClass",   # the meeting window of current Zoom
    "ZPContentViewWndClass",         # older versions
})
# The default location on Windows. Used when the registry cannot be read.
_FALLBACK_EXE = os.path.expandvars(r"%APPDATA%\Zoom\bin\Zoom.exe")


class JoinError(ValueError):
    """A string that cannot be accepted as a meeting specification."""


def parse_meeting(text: str) -> tuple[str, str]:
    """Take `(meeting number, passcode)` out of a meeting specification.

    Accepted forms:

        https://zoom.us/j/1234567890?pwd=abc
        https://example.zoom.us/j/1234567890?pwd=abc
        zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=abc
        1234567890
        123 4567 890        (this is what a person pastes)

    **`pwd` is the hash carried in the URL, used as is.** It is not the
    passcode a person reads (six digits, for example). Cut it out of the
    invitation URL.
    """
    raw = str(text or "").strip()
    if not raw:
        raise JoinError("The meeting is not given.")
    if len(raw) > 500:
        raise JoinError("The meeting text is too long. "
                        "Paste the invitation URL as it is.")

    # If it holds only digits and separators, treat it as the meeting number.
    if re.fullmatch(r"[0-9 \-]+", raw):
        digits = "".join(_DIGITS.findall(raw))
        if not (9 <= len(digits) <= 12):
            raise JoinError(f"Wrong number of digits in the meeting number: "
                            f"{raw}. It has 9 to 11 digits.")
        return digits, ""

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https", "zoommtg"):
        raise JoinError(f"Unknown form: {raw[:80]}. "
                        "Give an invitation URL or a meeting number.")

    # **Check that the address is a Zoom one.** Not because it is dangerous
    # (we only take the number), but so that a paste mistake is not swallowed
    # silently. Picking digits out of an unrelated URL and joining a different
    # meeting is the hardest failure to understand.
    host = (parsed.hostname or "").lower()
    if host and host != "zoom.us" and not host.endswith(".zoom.us"):
        raise JoinError(
            f"This is not a Zoom invitation URL: {raw[:80]} "
            f"(the host is {host}). Give a zoom.us URL or a meeting number."
        )

    query = urllib.parse.parse_qs(parsed.query)
    pwd = (query.get("pwd") or [""])[0]

    # A zoommtg: URL carries confno directly.
    confno = (query.get("confno") or [""])[0]
    if confno:
        digits = "".join(_DIGITS.findall(confno))
        if not (9 <= len(digits) <= 12):
            raise JoinError(f"Wrong number of digits in the meeting number: "
                            f"{confno}.")
        return digits, pwd

    if _PERSONAL.search(parsed.path or ""):
        # **A personal link cannot be turned into a meeting number.** It can
        # open a different meeting each time, and the URL holds no number. Do
        # not fail silently; say what to do instead.
        raise JoinError(
            "A personal link (/my/...) is not supported. "
            "Paste the invitation URL with the meeting number (/j/...) that "
            "appears when the meeting starts."
        )

    found = _JOIN_PATH.search(parsed.path or "")
    if not found:
        raise JoinError(f"No meeting number found: {raw[:80]}")
    return found.group(1), pwd


def join_url(confno: str, pwd: str = "", name: str = "") -> str:
    """Build the `zoommtg:` URL. **Always encode the values.**

    They come from a string a person pasted, so they must not be concatenated
    as is.
    """
    parts = ["action=join", "confno=" + urllib.parse.quote(str(confno))]
    if pwd:
        parts.append("pwd=" + urllib.parse.quote(str(pwd)))
    if name:
        parts.append("uname=" + urllib.parse.quote(str(name)))
    return "zoommtg://zoom.us/join?" + "&".join(parts)


def zoom_exe() -> str | None:
    """The location of `Zoom.exe`. None if it is not found.

    **Do not hard-code it.** Zoom moves itself on every update. Look first at
    the `zoommtg:` open command registered with the OS, and fall back to the
    default location.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"zoommtg\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, "")
        # It is stored in the form `"C:\...\Zoom.exe" "--url=%1"`.
        found = re.match(r'\s*"([^"]+)"', str(command))
        path = found.group(1) if found else str(command).split()[0]
        if os.path.exists(path):
            return path
    except (ImportError, OSError, IndexError, AttributeError):
        pass
    return _FALLBACK_EXE if os.path.exists(_FALLBACK_EXE) else None


def running() -> bool:
    """Whether `Zoom.exe` is running.

    **Do not use this to decide whether we are in a meeting.** Zoom keeps a
    resident window after leaving a meeting, so on a machine that runs all the
    time this is **almost always True**. Use `in_meeting()` to check whether we
    are in a meeting.
    """
    return bool(_zoom_pids())


def _zoom_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Zoom.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids = set()
    for line in (out.stdout or "").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].strip().isdigit():
            pids.add(int(parts[1].strip()))
    return pids


def in_meeting() -> bool:
    """Whether we are in a meeting right now.

    **The presence of the process does not tell you.** Zoom keeps a resident
    window after leaving a meeting. On a caption PC that runs all the time,
    `Zoom.exe` is almost always running, and reading that as "in a meeting"
    means **we never leave the meeting we joined.**

    Look at the class name of the meeting window. These names were confirmed by
    measurement (2026-09-19). **Do not look at the title.** It changes with the
    display language (in Japanese it reads "Zoom ミーティング").
    """
    pids = _zoom_pids()
    if not pids:
        return False
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        user32 = ctypes.windll.user32
        found = []

        def visit(hwnd, _lparam):  # noqa: ANN001
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                name = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, name, 256)
                if name.value in MEETING_WINDOW_CLASSES:
                    found.append(hwnd)
            return True

        proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(proto(visit), 0)
        return bool(found)
    except (OSError, AttributeError, ValueError):
        # When we cannot tell, answer "in a meeting". **The safe side is to
        # leave things alone.** A meeting a person had open must not be cut off
        # because the check failed.
        return True


def join(text: str, name: str = "") -> str:
    """Start Zoom and make it join the meeting. Return the URL that was sent.

    **We cannot tell whether the join succeeded.** A `zoommtg:` URL is only
    handed to the handler, and nothing comes back whether it ends in the
    waiting room, a wrong passcode, or an update dialog. Confirm with the
    audio.
    """
    confno, pwd = parse_meeting(text)
    url = join_url(confno, pwd, name)
    exe = zoom_exe()
    try:
        if exe:
            # **Do not go through a shell.** `pwd` comes from a string a
            # person pasted.
            subprocess.Popen(
                [exe, f"--url={url}"], shell=False, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            # When the executable is not found, leave it to the OS file
            # association.
            os.startfile(url)  # noqa: S606
    except OSError as exc:
        raise JoinError(
            f"Cannot start Zoom: {exc}\n"
            f"  Looked in: {exe or _FALLBACK_EXE}\n"
            "  Check that Zoom is installed."
        ) from exc
    return url


def leave() -> bool:
    """Quit Zoom. True if it was quit.

    **There is no interface to leave a meeting.** Quitting is the only way.
    This kills every `Zoom.exe`, so **call it only when we are the one who
    joined the meeting** (`schedule.py` remembers that).
    """
    if not in_meeting():
        return False
    try:
        subprocess.run(
            ["taskkill", "/IM", "Zoom.exe", "/T", "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
