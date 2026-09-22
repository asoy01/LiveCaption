"""A window for a person to look at the screen inside. Starts TigerVNC and noVNC.

**Do not use this routinely.** There is no authentication, and anyone inside
the tailnet can reach it. Use it only to sign in to the meeting software, and
to look around when automatic joining gets stuck. The caption controls (start,
stop, delivery, token, record) are on the control page.

There are three ways to connect.

    https://<full-name>:6443/vnc.html   browser (`tailscale serve`)
    http://<host>:6080/vnc.html         browser (plain)
    <host>:5900                         VNC client

**If you want the clipboard to sync automatically, connect with a VNC client.**
Over `http` a browser is not a secure context, so `navigator.clipboard` does
not exist and the page cannot read the clipboard of the person watching. That
is why an `https` entrance is provided.

**You can start and stop this during a meeting.** `x0vncserver` only attaches
to an X display that is already up. Neither the meeting software nor the
caption generation stops. If this were a startup setting only, then **at the
moment you most want to look inside -- "something looks wrong during the
meeting" -- your only option would be to rebuild the container, which drops
you out of the meeting.**

**Hold on to the child processes and reap them.** When they are started from
the entry shell, the shell finally `exec`s into the main program, so the parent
of each child becomes the main program (Python). The main program never calls
`wait()`, so one zombie is left behind every time you stop. Since this is a
button that gets pressed to start and stop over and over, they pile up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config


class Vnc:
    """Start and stop TigerVNC (`x0vncserver`) plus noVNC.

    The caller is the HTTP server thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []
        self._error = ""

    # --- Can we use it? -----------------------------------------------------

    def _missing(self) -> str:
        """The reason it cannot be used. Empty when it can.

        **Do not require `autocutsel`.** Without it you only lose the clipboard
        connection; you can still see the screen.
        """
        if not os.environ.get("DISPLAY"):
            return "画面が無い（DISPLAY が空）。Windows では使わない。"
        for cmd in ("x0vncserver", "websockify"):
            if shutil.which(cmd) is None:
                return f"{cmd} が入っていない。"
        if not Path(config.NOVNC_ROOT).is_dir():
            return f"noVNC が {config.NOVNC_ROOT} に無い。"
        return ""

    @property
    def available(self) -> bool:
        return not self._missing()

    # --- State --------------------------------------------------------------

    def _reap(self) -> None:
        """Reap finished children and keep only the live ones.

        **The caller must hold the lock.**
        """
        alive = []
        for p in self._procs:
            if p.poll() is None:
                alive.append(p)
        self._procs = alive

    def status(self) -> dict:
        with self._lock:
            self._reap()
            why = self._missing()
            return {
                "on": bool(self._procs),
                "available": not why,
                "error": self._error or why,
                "web_port": config.VNC_WEB_PORT,
                "https_port": config.VNC_HTTPS_PORT,
                "rfb_port": config.VNC_RFB_PORT,
            }

    # --- Start and stop -----------------------------------------------------

    def start(self) -> dict:
        with self._lock:
            self._reap()
            if self._procs:
                return self._status_locked()
            why = self._missing()
            if why:
                self._error = why
                return self._status_locked()
            display = os.environ["DISPLAY"]
            try:
                # **Use TigerVNC.** The old cut-text of x11vnc carries Latin-1
                # only, so Japanese text in the clipboard turns into `???`.
                # `-AlwaysShared` = two or more people can watch at once.
                # `-SecurityTypes None` = there is no password.
                # **The only thing protecting this is that it stays inside the
                # tailnet.**
                #
                # **Always pass `-fg`.** On Debian, `x0vncserver` is a perl
                # wrapper that by default calls `setsid` and detaches itself.
                # The child we hold then exits at once, so **we wrongly decide
                # that it "did not come up", and the real process is left
                # running loose** (hit on 2026-09-21).
                #
                # **`-localhost no` is needed.** TigerVNC listens on localhost
                # only by default, and then a VNC client cannot connect. A
                # browser (noVNC) over `http` cannot read the Windows clipboard
                # (it is not a secure context, so `navigator.clipboard` is
                # missing). **If you want to avoid pasting by hand, connecting
                # directly with a VNC client is the only way.**
                #
                # **We pass `--I-KNOW-THIS-IS-INSECURE`.** TigerVNC refuses to
                # listen outside localhost without authentication. The warning
                # is fair, but **port 6080 (noVNC) on the same box is already
                # open to the whole tailnet without authentication**, so
                # opening 5900 does not change the kind of exposure. The only
                # thing protecting this is that it can be reached from inside
                # the tailnet only.
                # It does not run by default; open it from the control page
                # only when you need it.
                self._procs.append(subprocess.Popen(
                    ["x0vncserver", "-fg", "-display", display,
                     "-rfbport", str(config.VNC_RFB_PORT),
                     "-localhost", "no", "--I-KNOW-THIS-IS-INSECURE",
                     "-SecurityTypes", "None", "-AlwaysShared"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL))
                # **Wait a little until it listens.** If websockify is pointed
                # at it right away, it looks like it came up while nothing is
                # actually connected.
                time.sleep(1.0)
                self._procs.append(subprocess.Popen(
                    ["websockify", f"--web={config.NOVNC_ROOT}",
                     str(config.VNC_WEB_PORT),
                     f"localhost:{config.VNC_RFB_PORT}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL))
            except OSError as exc:
                self._error = f"起こせない: {exc}"
                self._stop_locked()
                return self._status_locked()

            time.sleep(0.5)
            self._reap()
            if len(self._procs) < 2:
                # Do not report "it is up" when only one of the two died.
                self._error = "上がらなかった。ポートが空いているか確かめること。"
                self._stop_locked()
                return self._status_locked()

            # **No clipboard bridge is needed.** TigerVNC watches the X
            # selection by itself (`autocutsel` was added for a while, but it
            # is not necessary).
            self._error = ""
            # **Publish over https as well.** Over plain `http` the browser
            # does not treat the page as a secure context, so
            # `navigator.clipboard` is missing. That is why noVNC cannot read
            # the clipboard of the person watching.
            # **Do not stop on failure.** Port 6080 over http still works.
            from . import tunnel as tunnel_mod
            tunnel_mod.serve_https(config.VNC_HTTPS_PORT, config.VNC_WEB_PORT)
            return self._status_locked()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            self._error = ""
            return self._status_locked()

    def _stop_locked(self) -> None:
        if self._procs:
            # **Close the https entrance too.** If the entrance stays open
            # while what is behind it is dead, the person who opens it only
            # learns that "it does not connect".
            from . import tunnel as tunnel_mod
            tunnel_mod.serve_off(config.VNC_HTTPS_PORT)
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 5.0
        for p in self._procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        self._procs = []

    def _status_locked(self) -> dict:
        why = self._missing()
        return {
            "on": bool(self._procs),
            "available": not why,
            "error": self._error or why,
            "web_port": config.VNC_WEB_PORT,
            "https_port": config.VNC_HTTPS_PORT,
            "rfb_port": config.VNC_RFB_PORT,
        }
