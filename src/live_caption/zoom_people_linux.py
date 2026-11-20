"""How many people are in the Zoom meeting: read the number on the Participants button.

    count()          # 2, or None when it cannot be read

Zoom for Linux writes no roster to its logs and has no API for a guest. The only place
the number shows is the toolbar of the meeting window: a small "2" to the upper right of
the people icon, above the label "Participants". This module reads it from the screen:

1. take the bottom strip of the meeting window (`import -window root`, then crop);
2. find the word "Participants" with tesseract, and so the button;
3. crop the icon row above the word, and read only digits there.

The end-of-meeting check (schedule.py, end condition 2) calls count() every few
seconds. The count includes LiveCaption itself: 1 means LiveCaption is alone.
When the toolbar is hidden or the button is folded into "More", count() returns
None, and the check does nothing.

**Linux (the container) only.** It needs xdotool, ImageMagick and tesseract-ocr
(docker/Dockerfile). The Windows native version is frozen and has no such check.

Checked in real Zoom meetings (2026-11-20): windows 1188 px and 1629 px wide,
1 to 27 people.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import zoom_chat_linux as zoom_chat

BAR_H = 80   # the toolbar is at the bottom of the meeting window, about 70 px high
SCALE = 3    # tesseract reads the small digits better when they are larger
LABEL = "participants"


def _run(args: list[str], timeout: float = 20.0) -> str:
    out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return out.stdout


def _geometry(win: int) -> tuple[int, int, int, int] | None:
    """(x, y, width, height) of a window on the screen."""
    vals = {}
    for line in _run(["xdotool", "getwindowgeometry", "--shell", str(win)]).splitlines():
        k, _, v = line.partition("=")
        if v.strip().lstrip("-").isdigit():
            vals[k] = int(v)
    if not {"X", "Y", "WIDTH", "HEIGHT"} <= vals.keys():
        return None
    return vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"]


def _words(png: Path) -> list[tuple[str, int, int, int, int]]:
    """(text, left, top, width, height) of each word tesseract finds."""
    out = []
    for line in _run(["tesseract", str(png), "-", "--psm", "11", "tsv"]).splitlines()[1:]:
        f = line.split("\t")
        if len(f) == 12 and f[11].strip() and float(f[10]) > 0:
            out.append((f[11].strip(), int(f[6]), int(f[7]), int(f[8]), int(f[9])))
    return out


def count_in(shot: Path, geometry: tuple[int, int, int, int]) -> int | None:
    """Read the number from a screenshot of the whole screen."""
    x, y, w, h = geometry
    with tempfile.TemporaryDirectory() as tmp:
        bar = Path(tmp) / "bar.png"
        top = y + h - BAR_H
        _run(["convert", str(shot), "-crop", f"{w}x{BAR_H}+{x}+{top}", "+repage", "-alpha", "off",
              "-resize", f"{SCALE * 100}%", "-negate", str(bar)])
        label = [wd for wd in _words(bar) if wd[0].lower().startswith(LABEL)]
        if not label:
            return None
        _, lx, ly, lw, lh = label[0]
        # The number is above the label, between the people icon and the menu arrow "^".
        # On the 1188 px window of the first test, the icon ended at 0.59 of the label's
        # width, the "2" took 0.72-0.80, and the arrow began at 1.08.
        icon = Path(tmp) / "icon.png"
        cx = (lx + int(lw * 0.63)) // SCALE
        cw = int(lw * 0.40) // SCALE
        cy = max(0, ly - 3 * lh) // SCALE
        ch = max(1, 3 * lh // SCALE)
        _run(["convert", str(shot), "-crop", f"{cw}x{ch}+{x + cx}+{top + cy}", "+repage", "-alpha", "off",
              "-resize", "600%", "-negate", str(icon)])
        text = _run(["tesseract", str(icon), "-", "--psm", "7",
                     "-c", "tessedit_char_whitelist=0123456789"]).strip()
    return int(text) if text.isdigit() else None


def count() -> int | None:
    """The number on the Participants button of the meeting window, or None."""
    win = zoom_chat.meeting_window()
    if not win:
        return None
    geometry = _geometry(win)
    if geometry is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        shot = Path(tmp) / "screen.png"
        _run(["import", "-window", "root", str(shot)])
        if not shot.exists():
            return None
        return count_in(shot, geometry)


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 6:  # test on a saved screenshot: shot.png x y w h
        print(count_in(Path(sys.argv[1]), tuple(int(v) for v in sys.argv[2:])))
    else:
        print(count())
