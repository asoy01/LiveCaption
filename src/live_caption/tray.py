"""The task tray icon, and writing the log to a file.

**On a machine that runs all the time, you do not want to keep a terminal
open.** But once the window is gone, you cannot tell whether the program is
alive or dead. So we put an icon in the tray.

    ● green   captions are running for a meeting
    ● blue    idle
    ● red     a failure is pending (it stays until you press the
              acknowledge button on the control page)

Right-click gives you the control page, the log, and quit.

**Decide where the log goes before you hide the window.** For now, the only
record this application keeps is the lines it prints to the terminal (about 40
lines for one meeting). If you hide the window without keeping them somewhere,
you will have nothing to look at when something fails.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

# Icon colors. Each state is drawn differently.
COLORS = {
    "idle": (88, 166, 255),      # blue. idle
    "running": (63, 185, 80),    # green. captions are running
    "failed": (248, 81, 73),     # red. a failure is pending
}


# =========================================================================
# Log
# =========================================================================


class _Tee(io.TextIOBase):
    """Write to both the terminal and a file.

    **When there is a terminal, write there as well.** This keeps the view
    unchanged when you run `pixi run caption` by hand. When the program is
    started with the window hidden, only the file is left.
    """

    def __init__(self, stream, handle) -> None:  # noqa: ANN001
        self._stream = stream
        self._handle = handle
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.write(text)
                    self._stream.flush()
                except (OSError, ValueError):
                    self._stream = None
            try:
                self._handle.write(text)
                self._handle.flush()
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return bool(self._stream is not None and self._stream.isatty())


def start_logging() -> Path | None:
    """Start writing the log under `local/log/`. Return the path written.

    **Do not crash on failure.** Losing captions is worse than losing the log.
    Old files are dropped, keeping only the newest `LOG_KEEP` of them.
    """
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LOG_DIR / time.strftime("livecaption_%Y-%m-%d_%H%M%S.log")
        handle = path.open("a", encoding="utf-8", errors="replace")
    except OSError:
        return None

    sys.stdout = _Tee(sys.__stdout__, handle)
    sys.stderr = _Tee(sys.__stderr__, handle)

    try:
        old = sorted(config.LOG_DIR.glob("livecaption_*.log"))
        for stale in old[:-config.LOG_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
    return path


def open_in_editor(path: Path | None) -> None:
    """Open a file with the default application."""
    if path is None or not Path(path).exists():
        return
    try:
        os.startfile(str(path))  # noqa: S606
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)], shell=False)


# =========================================================================
# Tray
# =========================================================================


def _icon_image(color) -> object:  # noqa: ANN001
    """Overlay a small state badge on the application icon.

    **Do not draw only a circle.** Other applications sit in the tray as well,
    so you cannot tell which application it is (reported by a user on
    2026-09-19). Use `etc/LiveCaption.ico` as the base and put a colored badge
    at the bottom right.

    When the icon cannot be read, fall back to drawing only the badge, large.
    **A different shape is better than no tray icon at all.**
    """
    from PIL import Image, ImageDraw

    size = 64
    base = None
    try:
        icon = Image.open(config.APP_ICON)
        # An .ico holds several sizes. Pick the closest one, then resize.
        icon.size = min(icon.info.get("sizes", [(size, size)]),
                        key=lambda wh: abs(wh[0] - size))
        icon.load()
        base = icon.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        base = None

    if base is None:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((6, 6, size - 6, size - 6),
                                      fill=(*color, 255))
        return image

    # Put the badge at the bottom right. **Cut a dark rim around it.** The
    # badge stays readable even where it overlaps the icon artwork.
    draw = ImageDraw.Draw(base)
    r = 22
    box = (size - r - 2, size - r - 2, size - 2, size - 2)
    draw.ellipse(box, fill=(13, 17, 23, 255))
    draw.ellipse((box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3),
                 fill=(*color, 255))
    return base


class Tray:
    """One tray icon. **It runs on a separate thread.**

    It is not tied to the main event loop. It picks up the state on its own by
    reading `web.status()`. **Menu actions also go through the HTTP
    interface** (they never touch the main program directly).
    """

    def __init__(self, web, log_path: Path | None = None) -> None:  # noqa: ANN001
        self.web = web
        self.log_path = log_path
        self._icon = None
        self._state = ""
        self._stop = threading.Event()

    def start(self) -> bool:
        """Show the tray icon. False if it cannot be shown (the main program
        keeps going).
        """
        try:
            import pystray
        except ImportError:
            print("  [tray] pystray is not installed, so no icon is shown.")
            return False

        menu = pystray.Menu(
            pystray.MenuItem("Open the control page", self._open_control, default=True),
            pystray.MenuItem("Open the log", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._quit),
        )
        self._icon = pystray.Icon(
            "LiveCaption", _icon_image(COLORS["idle"]), "LiveCaption", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass

    # --- Appearance ---------------------------------------------------------

    def _watch(self) -> None:
        """Watch the state and rewrite the color and the tooltip.

        **It must never crash.**
        """
        while not self._stop.wait(2.0):
            try:
                self._refresh()
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        sched = self.web.scheduler
        sc = sched.status() if sched is not None else {}
        if sc.get("failure"):
            state = "failed"
        elif sc.get("state") in ("joining", "arming", "running"):
            state = "running"
        else:
            state = "idle"

        parts = ["LiveCaption"]
        if state == "running":
            parts.append(f"Captions: {sc.get('meeting') or ''}")
        elif sc.get("upcoming"):
            nxt = sc["upcoming"][0]
            parts.append(f"Next: {nxt['name']} {nxt['at'][-5:]}")
        else:
            parts.append("Waiting")
        if sc.get("failure"):
            parts.append("A failure is pending")
        # **The tooltip shows 128 characters at most** (a Windows limit).
        tip = "\n".join(parts)[:127]

        if self._icon is None:
            return
        if state != self._state:
            self._state = state
            self._icon.icon = _icon_image(COLORS[state])
        self._icon.title = tip

    # --- Menu ---------------------------------------------------------------

    def _open_control(self) -> None:
        import webbrowser

        webbrowser.open(self.web.control_url())

    def _open_log(self) -> None:
        open_in_editor(self.log_path)

    def _quit(self) -> None:
        """**Do not touch the main program directly.** Use the same interface
        as the control page.

        With two ways to shut down, one of them ends up missing some cleanup.
        """
        import urllib.request

        try:
            req = urllib.request.Request(
                self.web.control_url() + "/api/shutdown", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001
            pass
        self.stop()
