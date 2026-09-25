"""The exit that gives participants a viewer URL. There are two routes, and
the control page chooses between them.

**Cloudflare temporary tunnel** (`Tunnel`). Start `cloudflared` as a child
process and pick up the `https://<random words>.trycloudflare.com` that
appears in its output.

    cloudflared tunnel --url http://127.0.0.1:8080

**Tailscale Funnel** (`Funnel`). Let `tailscale` set up the delivery.

    tailscale funnel --bg 8080

`Delivery` holds which one is used. The table below is the basis for the
choice.

| | Cloudflare | Tailscale |
|---|---|---|
| URL | **changes on every start** | **does not change** |
| Preparation | none | one tailnet setup |
| Can the URL be given out in advance? | no | **yes** |

**Choose Tailscale when the meeting URL has to go into the announcement in
advance.** The host name does not change, so combined with the path that
`meetings.py` builds, the whole URL is fixed the day before.

**The connection is made outward from the caption PC.** No incoming
connection is needed. No port forwarding, no router setup, and no request to
an administrator. It works on the campus LAN, on meeting room WiFi, and on
tethering.

**No account and no domain are needed.** In exchange there are the following
limits (Cloudflare states them).

- The URL changes on every start. The previous URL dies
- There is no SLA. It is described as being for testing and development
- Up to 200 concurrent requests. Long polling takes one per person, so this
  is the limit on the number of viewers
- **Server-Sent Events do not work.** That is why `web.py` uses long polling

**No tunnel is set up by default.** Meeting captions pass through Cloudflare,
and TLS terminates there. For meetings that handle unpublished observation
results, keep to screen sharing, which does not leave the room. Start the
tunnel from the control page only when it is needed.

The app does not stop when `cloudflared` is missing, because screen sharing
and the Zoom caption API still work.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config

# The temporary tunnel URL that cloudflared prints.
URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
# How many output lines to show when it fails.
TAIL_LINES = 40

if os.name == "nt":
    INSTALL_HINT = (
        "cloudflared was not found. Install it in one of these two ways.\n"
        "  1. Drop in one executable (no administrator rights needed):\n"
        "     https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-windows-amd64.exe\n"
        f"     Put it at {config.TUNNEL_LOCAL}\n"
        "  2. winget install --id Cloudflare.cloudflared"
    )
else:
    # **The Docker image carries cloudflared.** Seeing this there means the
    # container was built from an older tree; rebuilding fixes it.
    INSTALL_HINT = (
        "cloudflared was not found.\n"
        "  In Docker: rebuild the image (docker compose up -d --build).\n"
        "  Otherwise: put the cloudflared-linux-amd64 binary on PATH as "
        "cloudflared:\n"
        "     https://github.com/cloudflare/cloudflared/releases/latest"
    )


def find_cloudflared(explicit: str | None = None) -> str | None:
    """Return where `cloudflared` is. None if it is not found."""
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which(config.TUNNEL_CMD)
    if found:
        return found
    return str(config.TUNNEL_LOCAL) if config.TUNNEL_LOCAL.exists() else None


class Tunnel:
    """One `cloudflared` child process.

    There are four states: `off` -> `starting` -> `on`, or `error`.
    **It is started and stopped from the control page (a thread of the HTTP
    server).** `_lock` protects it.
    """

    def __init__(self, port: int, command: str | None = None) -> None:
        self.port = port
        self.command = command
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._state = "off"
        self._url = ""
        self._error = ""
        self._tail: list[str] = []
        # Called when the URL appears. Used to keep the screens in step.
        self.on_change = None

    # --- State --------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "url": self._url,
                "error": self._error,
                "available": find_cloudflared(self.command) is not None,
            }

    @property
    def url(self) -> str:
        with self._lock:
            return self._url

    # --- Start and stop -----------------------------------------------------

    def start(self) -> dict:
        """Set up the tunnel. **Returns at once.** The URL appears later."""
        with self._lock:
            if self._state in ("starting", "on"):
                return self._status_locked()
            exe = find_cloudflared(self.command)
            if exe is None:
                self._state, self._error, self._url = "error", INSTALL_HINT, ""
                return self._status_locked()

            try:
                proc = subprocess.Popen(
                    [exe, "tunnel", "--url", f"http://127.0.0.1:{self.port}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    # Do not pass the terminal's Ctrl+C to the child. We stop
                    # it ourselves.
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as exc:
                self._state, self._error, self._url = "error", str(exc), ""
                return self._status_locked()

            self._proc = proc
            self._state, self._url, self._error, self._tail = "starting", "", "", []
            started = time.monotonic()

        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        threading.Thread(target=self._watch, args=(proc, started), daemon=True).start()
        return self.status()

    def stop(self) -> dict:
        """Take the tunnel down. The URL dies on the spot."""
        with self._lock:
            was = self._state
            proc, self._proc = self._proc, None
            self._state, self._url, self._error = "off", "", ""
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # If no tunnel was up, return quietly. We do not want to print
        # "stopped" on every exit.
        if was != "off":
            self._changed()
        return self.status()

    # --- Watching the child process -----------------------------------------

    def _read(self, proc: subprocess.Popen) -> None:
        """Pick the URL out of the output. Also keep the last lines, in case
        it fails."""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            with self._lock:
                if self._proc is not proc:
                    return  # stop() already ran. This process no longer matters
                self._tail.append(line)
                if len(self._tail) > TAIL_LINES:
                    del self._tail[: len(self._tail) - TAIL_LINES]
                m = URL_RE.search(line)
                found = bool(m) and self._state != "on"
                if m:
                    self._url, self._state, self._error = m.group(0), "on", ""
            if found:
                self._changed()

        # The output ended, which means the process ended.
        with self._lock:
            if self._proc is not proc:
                return
            self._proc = None
            if self._state != "off":
                self._state = "error"
                self._url = ""
                self._error = "cloudflared exited.\n" + "\n".join(self._tail[-8:])
        self._changed()

    def _watch(self, proc: subprocess.Popen, started: float) -> None:
        """If the time runs out with no URL, show it as a failure."""
        time.sleep(config.TUNNEL_TIMEOUT_SEC)
        with self._lock:
            if self._proc is not proc or self._state != "starting":
                return
            elapsed = time.monotonic() - started
            self._state = "error"
            self._error = (
                f"No URL after {elapsed:.0f} s.\n" + "\n".join(self._tail[-8:])
            )
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _status_locked(self) -> dict:
        return {
            "state": self._state,
            "url": self._url,
            "error": self._error,
            "available": True,
        }


# =========================================================================
# Tailscale Funnel
# =========================================================================

FUNNEL_HINT = (
    "tailscale was not found. Install Tailscale on the caption PC and sign in.\n"
    "  https://tailscale.com/download/windows"
)
# The first time Funnel is used, two things must be enabled on the tailnet
# side. For both, `tailscale funnel` opens a consent page and guides the user.
FUNNEL_SETUP_HINT = (
    "Tailscale Funnel is not enabled. Run this once on the caption PC, and\n"
    "accept the consent page that opens in the browser (it needs tailnet\n"
    "administrator rights).\n"
    "  tailscale funnel 8080\n"
    "It enables two things: the HTTPS certificate, and the funnel attribute "
    "in the policy."
)


def find_tailscale(explicit: str | None = None) -> str | None:
    """Return where `tailscale` is. None if it is not found."""
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which(config.TAILSCALE_CMD)
    if found:
        return found
    return str(config.TAILSCALE_LOCAL) if config.TAILSCALE_LOCAL.exists() else None


# --- Serve over https inside the tailnet only (`tailscale serve`) ------------
#
# **This is not the same thing as delivery (Funnel).** Funnel can be seen by
# anyone. `serve` can only be reached from inside the tailnet. It is used to
# put the control page and noVNC on https.
#
# **Tailscale obtains the certificate and takes care of renewing it.** There
# is no need to run `tailscale cert` yourself. The name is the full name
# (`<machine>.<tailnet>.ts.net`), so opening a short name or an IP address
# does not match the certificate.
#
# **Do not use `reset`.** `serve` and `funnel` share the same settings, so
# clearing one clears both. Specify the port when stopping. Stopping delivery
# uses `funnel --https=443 off`, which takes down only 443, so 8443 / 6443
# here are not caught up in it (measured on 2026-09-21).


def serve_https(public_port: int, local_port: int) -> str:
    """Connect `https://<full name>:<public_port>` to
    `127.0.0.1:<local_port>`.

    Return a reason only on failure. Empty on success. **The caller does not
    stop on failure.** https is convenient, but without it http still works.
    """
    exe = find_tailscale()
    if exe is None:
        return "tailscale was not found."
    try:
        out = subprocess.run(
            [exe, "serve", "--bg", f"--https={public_port}", str(local_port)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=config.TUNNEL_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if out.returncode != 0:
        return ((out.stderr or "") + (out.stdout or "")).strip()[:300]
    return ""


def serve_off(public_port: int) -> None:
    """Stop just this one `serve`. **Do not use `reset`.**"""
    exe = find_tailscale()
    if exe is None:
        return
    try:
        subprocess.run(
            [exe, "serve", f"--https={public_port}", "off"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


# Remember the host name that was looked up last.
# `(time of the lookup, host name, reason for failure)`.
_HOST_CACHE: tuple[float, str, str] = (0.0, "", "")
_HOST_CACHE_LOCK = threading.Lock()


def tailscale_host(command: str | None = None, max_age: float | None = None
                   ) -> tuple[str, str]:
    """Return this PC's host name on the tailnet. `(host name, reason for
    failure)`.

    **It is known even when nothing is being delivered.** This is the reason
    to choose Tailscale. The URL can be fixed the day before the meeting and
    put into the announcement.

        tailscale status --json  ->  Self.DNSName  ->  livecaption.tail1234.ts.net

    **Remember the result for a short time.** This is called from
    `Funnel.status()`, and `status()` is called from `/api/status`, which the
    control page hits every 2 seconds. Without the cache, **one process would
    be started every 2 seconds while the page is open.** On a machine that
    runs all the time, that is tens of thousands of times a day. The host
    name changes only when the machine is renamed, so an old value causes no
    trouble. `max_age=0` always looks it up again.
    """
    age = config.TAILSCALE_HOST_CACHE_SEC if max_age is None else max_age
    now = time.monotonic()
    with _HOST_CACHE_LOCK:
        at, name, why = _HOST_CACHE
        if at and now - at < age:
            return name, why

    name, why = _tailscale_host_now(command)
    with _HOST_CACHE_LOCK:
        globals()["_HOST_CACHE"] = (time.monotonic(), name, why)
    return name, why


def is_tailscale_addr(addr: str) -> bool:
    """Whether the address is in the range Tailscale hands out.

    **This one place decides the range the control page may be exposed on.**
    It must not become a general bind option. Writing `0.0.0.0` would make
    the control page, which has no authentication, visible to everyone on the
    campus LAN. Allow only the Tailscale range.

        IPv4  100.64.0.0/10   (the CGNAT range Tailscale uses)
        IPv6  fd7a:115c:a1e0::/48
    """
    try:
        ip = ipaddress.ip_address(str(addr).strip())
    except ValueError:
        return False
    return any(ip in net for net in config.TAILSCALE_NETS)


def tailscale_addrs(command: str | None = None) -> list[str]:
    """This PC's IP addresses on the tailnet. Empty when it is not connected.

    **Do not make the user type them.** The IP differs from machine to
    machine, and after a typo you cannot tell whether the control page is not
    up at all or is up at a different address.
    """
    exe = find_tailscale(command)
    if exe is None:
        return []
    try:
        out = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode != 0:
            return []
        state = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if state.get("BackendState") != "Running":
        return []
    return [a for a in state.get("TailscaleIPs", []) if is_tailscale_addr(a)]


def _tailscale_host_now(command: str | None = None) -> tuple[str, str]:
    """Actually run `tailscale status --json` and look it up."""
    exe = find_tailscale(command)
    if exe is None:
        return "", FUNNEL_HINT
    try:
        out = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"tailscale status does not run: {exc}"
    if out.returncode != 0:
        return "", f"tailscale status failed: {(out.stderr or out.stdout).strip()}"
    try:
        state = json.loads(out.stdout)
    except ValueError as exc:
        return "", f"The output of tailscale status cannot be read: {exc}"
    if state.get("BackendState") != "Running":
        return "", ("Tailscale is not connected "
                    f"({state.get('BackendState', 'state unknown')}). Sign in.")
    # Drop the trailing dot. Do not put the formal DNS spelling into a URL.
    name = str((state.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not name:
        return "", "tailscale status has no host name. Turn MagicDNS on."
    return name, ""


class Funnel:
    """The Tailscale Funnel side. It is used the same way as `Tunnel`.

    **No child process is left behind.** `tailscale funnel --bg` writes the
    settings and exits. The delivery itself is carried on by tailscaled. So
    no watcher like the one in `Tunnel` is needed.

    **The URL is known before delivery starts.** `tailscale_host()` returns
    the host name.
    """

    def __init__(self, port: int, command: str | None = None) -> None:
        self.port = port
        self.command = command
        self._lock = threading.Lock()
        self._state = "off"
        self._url = ""
        self._error = ""
        self.on_change = None

    # --- State --------------------------------------------------------------

    def base_url(self) -> str:
        """The base URL, known even when nothing is being delivered. Empty
        when it is not known."""
        host, _ = tailscale_host(self.command)
        return f"https://{host}" if host else ""

    def status(self) -> dict:
        with self._lock:
            state, url, error = self._state, self._url, self._error
        host, why = tailscale_host(self.command)
        if not host and state == "off":
            error = error or why
        return {
            "state": state,
            "url": url,
            "error": error,
            "available": bool(host),
            "base_url": f"https://{host}" if host else "",
        }

    @property
    def url(self) -> str:
        with self._lock:
            return self._url

    # --- Start and stop -----------------------------------------------------

    def start(self) -> dict:
        """Start delivery. **Unlike `Tunnel`, the connection is already up
        when this returns.**"""
        exe = find_tailscale(self.command)
        if exe is None:
            with self._lock:
                self._state, self._error, self._url = "error", FUNNEL_HINT, ""
            return self.status()
        host, why = tailscale_host(self.command)
        if not host:
            with self._lock:
                self._state, self._error, self._url = "error", why, ""
            return self.status()

        with self._lock:
            self._state, self._error = "starting", ""
        try:
            out = subprocess.run(
                # **Do not add `--yes`.** With it, the tailnet settings (the
                # issuing of the HTTPS certificate and the adding of the
                # funnel attribute to the policy) **would be changed without
                # a person's consent.** The tailnet settings are not about
                # this one machine. Enabling is something a person does once,
                # and here we only report that it is not enabled yet.
                # Close standard input so the command cannot sit and wait.
                [exe, "funnel", "--bg",
                 f"--https={config.FUNNEL_PUBLIC_PORT}", str(self.port)],
                stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=config.TUNNEL_TIMEOUT_SEC,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            with self._lock:
                self._state, self._error, self._url = "error", str(exc), ""
            self._changed()
            return self.status()

        if out.returncode != 0:
            # **The most common failure is that it is not enabled yet.**
            # In that case, print the steps.
            text = ((out.stderr or "") + (out.stdout or "")).strip()
            low = text.lower()
            hint = FUNNEL_SETUP_HINT if ("funnel" in low and
                                         ("enable" in low or "not allowed" in low or
                                          "attribute" in low or "https" in low)) else ""
            with self._lock:
                self._state = "error"
                self._url = ""
                self._error = f"{hint}\n\n{text}".strip() if hint else text
            self._changed()
            return self.status()

        with self._lock:
            self._state, self._url, self._error = "on", f"https://{host}", ""
        self._changed()
        return self.status()

    def stop(self) -> dict:
        """Take delivery down. **The settings are removed with it.** The URL
        dies on the spot."""
        exe = find_tailscale(self.command)
        with self._lock:
            was = self._state
            self._state, self._url, self._error = "off", "", ""
        if exe is not None and was != "off":
            try:
                subprocess.run(
                    [exe, "funnel", f"--https={config.FUNNEL_PUBLIC_PORT}", "off"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                with self._lock:
                    # **Do not suggest `reset`.** It would clear the `serve`
                    # settings too. If the control page is exposed on the
                    # tailnet, the path to it would be taken down as well.
                    self._error = (
                        "It could not be stopped. Run this by hand: "
                        f"tailscale funnel --https={config.FUNNEL_PUBLIC_PORT} off"
                        f" ({exc})")
        if was != "off":
            self._changed()
        return self.status()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


# =========================================================================
# Switching the route
# =========================================================================


class Delivery:
    """Bring the delivery routes together into one. **This is all the
    control page sees.**

    Hold both routes, and send to the one that is selected. When switching,
    **always stop the previous route.** Switching without stopping leaves
    behind a tunnel that cannot be taken down.
    """

    def __init__(self, port: int, kind: str | None = None,
                 command: str | None = None) -> None:
        self.port = port
        self.cloudflare = Tunnel(port, command)
        self.tailscale = Funnel(port)
        self._kind = kind if kind in config.TUNNEL_KINDS else config.tunnel_kind_selection()
        self._on_change = None

    # --- Route --------------------------------------------------------------

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def active(self):  # noqa: ANN201
        return self.tailscale if self._kind == "tailscale" else self.cloudflare

    @property
    def on_change(self):  # noqa: ANN201
        return self._on_change

    @on_change.setter
    def on_change(self, fn) -> None:  # noqa: ANN001
        self._on_change = fn
        self.cloudflare.on_change = fn
        self.tailscale.on_change = fn

    def select(self, kind: str) -> dict:
        """Select the route again. **Stop what is already up first.**"""
        kind = str(kind)
        if kind not in config.TUNNEL_KINDS:
            raise ValueError(
                f"Unknown route: 「{kind}」。{' / '.join(config.TUNNEL_KINDS)} のどちらか。")
        if kind != self._kind:
            self.active.stop()
            self._kind = kind
            config.remember_tunnel_kind(kind)
            if self._on_change is not None:
                self._on_change()
        return self.status()

    # --- Delegation ---------------------------------------------------------

    def base_url(self) -> str:
        """The base for building the meeting URL. **With Cloudflare it is
        empty until the tunnel is up.**"""
        if self._kind == "tailscale":
            return self.tailscale.base_url()
        return self.cloudflare.url

    @property
    def url(self) -> str:
        return self.active.url

    def start(self) -> dict:
        self.active.start()
        return self.status()

    def stop(self) -> dict:
        self.active.stop()
        return self.status()

    def stop_all(self) -> None:
        """Called on exit. **Stops the route that is not selected too.**"""
        self.cloudflare.stop()
        self.tailscale.stop()

    def status(self) -> dict:
        st = dict(self.active.status())
        st["kind"] = self._kind
        st["kinds"] = list(config.TUNNEL_KINDS)
        st.setdefault("base_url", st.get("url", ""))
        # **Only Tailscale can give out the URL in advance.** Show that on
        # the screen.
        st["preannounce"] = self._kind == "tailscale"
        return st
