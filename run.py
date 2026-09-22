#!/usr/bin/env python3
"""LiveCaption — live English captions for a meeting held in Japanese.

Use it in a meeting:

    pixi run python run.py --device "CABLE Output" --token "<Zoom API token URL>"

How to get the token (the host does this during the meeting):

    1. Click the "^" next to "Captions" on the toolbar
    2. Choose "Set up manual captioner", then "Copy the API token"
    3. Send it to the caption PC over chat

Use it in a meeting you do not host (show the captions in a browser and
share that window):

    pixi run python run.py --web

**The easiest way to start is to double click "StartLiveCaption.bat".**
It starts with `--web` and opens the control page in a browser. The Zoom
token, the URL for the participants, and quitting are all handled there.

Hand a viewer URL to the participants (no host rights, no screen sharing):

    Press "Start delivering" on the control page. A QR code and a viewer URL
    appear

There are two routes. **Cloudflare** needs no preparation, but the URL
changes every time. **Tailscale** keeps the same host name, so you can put
the meeting URL in the invitation in advance. Each meeting has its own URL,
and only the one chosen on the control page is delivered.

**Delivery is off by default.** The captions pass through Cloudflare or
Tailscale, so for a meeting about unpublished results, use screen sharing
only. To deliver from startup, use --tunnel.

**With --web, caption generation begins in the stopped state.** Until you
press Start on the control page, no audio is read and nothing is transcribed
or translated. You can launch it before you join the meeting. A start
without --web (only --token, or only --dry-run) has no button to press, so
it begins at once.

**The meeting record is kept by default.** The Japanese transcript and the
English caption are stored as a pair in **`local/transcripts/`**, as
`live-caption_<date-time>.jsonl` and `.md`.
**You can download it from "Meeting record" on the control page.** Use
--no-save when you do not want it, and LIVECAPTION_SAVE_DIR in .env or
--save-dir to change where it goes.

Try the whole thing without a meeting (plays a recording in real time):

    pixi run python run.py --from-file local/SampleRecordings/_wav24k/mix.wav --dry-run

List the input devices:

    pixi run python run.py --list-devices

**Each meeting uses a different set of glossaries.** You stack as many of the
.tsv files in `etc/glossary/` as you need. Choose them under "Glossary" on
the control page. The choice is remembered, so the next start uses the same
set. To choose it at startup:

    pixi run python run.py --glossary Optics Control
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from live_caption import app as app_mod  # noqa: E402
from live_caption import audio as audio_mod  # noqa: E402
from live_caption import config  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live English captions for a Zoom meeting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--token", help="Zoom API token URL")
    p.add_argument("--device", help="part of the input device name "
                                    "(default: CABLE Output)",
                   default="CABLE Output")
    p.add_argument("--from-file", help="play a WAV in real time instead of a "
                                       "device (24 kHz mono)")
    p.add_argument("--loop", action="store_true", help="repeat --from-file")
    p.add_argument("--dry-run", action="store_true",
                   help="print to the screen only; send nothing to Zoom")
    p.add_argument("--delay", default=config.ASR_DELAY,
                   choices=["minimal", "low", "medium", "high", "xhigh"],
                   help="trade transcription delay against accuracy "
                        "(default: low)")
    p.add_argument("--model", default=config.TRANSLATE_MODEL,
                   help="the translation model")
    p.add_argument("--direction", choices=list(config.DIRECTIONS), default=None,
                   help="caption direction. ja2en puts English captions on a "
                        "Japanese meeting, en2ja puts Japanese captions on an "
                        "English meeting. Without this, the last choice is "
                        "used (you can change it any time on the control page)")
    p.add_argument("--glossary", nargs="*", metavar="NAME", default=None,
                   help="the glossaries to use (names of the .tsv files in "
                        "etc/glossary/). You can stack several. "
                        "Example: --glossary Optics Control. "
                        "Without this, the last choice is used (you can change "
                        "it any time on the control page)")
    p.add_argument("--web", nargs="?", type=int, const=config.WEB_PORT, default=None,
                   metavar="PORT",
                   help=f"show the captions in a browser (the viewer page uses "
                        f"port {config.WEB_PORT} by default). Can be used "
                        "together with --token")
    p.add_argument("--control-port", type=int, default=config.CONTROL_PORT,
                   metavar="PORT",
                   help=f"port of the control page (default: {config.CONTROL_PORT})")
    p.add_argument("--control-bind", nargs="?", const="auto", default=None,
                   metavar="ADDRESS",
                   help="**also open the control page to the tailnet.** "
                        "Without a value, this PC looks up its own Tailscale "
                        "address. 127.0.0.1 always stays. "
                        "**Nothing outside the Tailscale range is accepted** "
                        "(the control page has no authentication)")
    p.add_argument("--web-bind", default=config.WEB_BIND, metavar="ADDRESS",
                   help=f"address the **viewer page** listens on "
                        f"(default: {config.WEB_BIND}). Use 0.0.0.0 to show it "
                        "to devices on the same LAN. The control page is not "
                        "affected by this")
    p.add_argument("--tunnel", action="store_true",
                   help="open a temporary tunnel at startup and make a URL for "
                        "the participants. Off by default (you can start it any "
                        "time on the control page)")
    p.add_argument("--cloudflared", metavar="PATH",
                   help="where cloudflared is. Not needed when it is on PATH "
                        "or in local/bin")
    p.add_argument("--no-browser", action="store_true",
                   help="do not open the browser by itself with --web")
    p.add_argument("--tray", action="store_true",
                   help="**stay resident in the task tray.** The icon colour "
                        "shows the state, and right click gives you the control "
                        "page, the log and quit. The log is also kept in "
                        "local/log/ (needed when you start with no window)")
    p.add_argument("--no-save", action="store_true",
                   help="do not keep the meeting record (it is kept by default)")
    # **Keep the default at None.** `.env` is read after parse_args, so a
    # default filled in here would overwrite the directory chosen on the
    # control page.
    p.add_argument("--save-dir", metavar="FOLDER", default=None,
                   help=f"where the record goes (default: {config.TRANSCRIPT_DIR}, "
                        f"or {config.SAVE_DIR_ENV} in .env)")
    p.add_argument("--list-devices", action="store_true",
                   help="list the input devices")
    p.add_argument("--check-audio", nargs="?", type=float, const=20.0, default=None,
                   metavar="SECONDS",
                   help="show the audio level only (no API calls). Start here "
                        "when testing the hardware")
    return p.parse_args()


def _control_extra(value: str) -> tuple[str, ...] | None:
    """Check the value of `--control-bind`. If it is wrong, print the reason
    and return None.

    **The control page has no authentication.** The only thing that protects
    it is where the request comes from. So this must not become a general
    bind option. Writing `0.0.0.0` would make **the control page, which has
    no authentication, visible to everyone on the campus LAN.** Allow only
    the Tailscale range.

    If the value is omitted (`auto`), this PC looks up its own Tailscale
    address. If the user types it by hand, a typo makes it impossible to tell
    whether the page is not up at all or is up at a different address.
    """
    from live_caption import tunnel as tunnel_mod

    if value == "auto":
        addrs = tunnel_mod.tailscale_addrs()
        if not addrs:
            # **Do not stop the startup.** On an automatic startup, Tailscale
            # may not be up yet. It is a problem if the caption app fails to
            # start just because it is a few tens of seconds late right after
            # a reboot. Listen on 127.0.0.1 and retry in the background.
            print("Control page: the Tailscale address is not known yet. "
                  "It will be added once it is (retried in the background)")
            return ()
        return tuple(addrs)

    if not tunnel_mod.is_tailscale_addr(value):
        print(f"--control-bind: address not accepted: \"{value}\"")
        print("  The control page has no authentication, so only the Tailscale")
        print("  range is allowed. Give an address in 100.64.0.0/10 or")
        print("  fd7a:115c:a1e0::/48, or leave the value out and let it look")
        print("  the address up by itself (--control-bind).")
        return None
    return (value,)


def _hostpart(addr: str) -> str:
    """The form that goes into a URL. IPv6 is wrapped in square brackets."""
    return f"[{addr}]" if ":" in addr else addr


def main() -> int:
    # **Device names contain characters the console encoding cannot write.**
    # Example: the (R) in "マイク配列 (デジタルマイク向けインテル(R) スマート・サウンド".
    # With the default settings, printing that name raises UnicodeEncodeError
    # and the app dies. It is a problem if the app stops just because the
    # input device was changed, so replace the characters it cannot write.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = parse_args()
    # **Open the log first.** When the app starts without a window, the print
    # calls from here on are the only clue left.
    log_path = None
    if args.tray:
        from live_caption import tray as tray_mod

        log_path = tray_mod.start_logging()
    config.load_env()

    if args.list_devices:
        for i, name, ch, api in audio_mod.list_devices():
            print(f"  {i:3d}  {name}  ({ch} ch, {api})")
        return 0

    if args.check_audio is not None:
        capture = audio_mod.Capture(device=audio_mod.find_device(args.device))
        ok = asyncio.run(audio_mod.check_level(capture, args.check_audio))
        return 0 if ok else 1

    if not args.dry_run and not args.token and args.web is None:
        print("There is no way out. Give --token (the Zoom caption API) or "
              "--web (captions in a browser).")
        print("To try it without a meeting, add --dry-run.")
        return 1

    # Where the meeting record goes. The order is --save-dir, then .env, then
    # the default (local/transcripts/).
    # **An unusable directory does not stop the startup.** It is a problem if
    # no captions appear on the day of the meeting just because the folder is
    # gone. Say so, and fall back to the default directory.
    chosen = (args.save_dir or os.environ.get(config.SAVE_DIR_ENV, "").strip()
              or str(config.TRANSCRIPT_DIR))
    save_dir = Path(chosen)
    if not args.no_save:
        try:
            save_dir = config.check_save_dir(save_dir)
        except ValueError as exc:
            print(f"The record folder cannot be used ({exc}), "
                  f"so {config.TRANSCRIPT_DIR} is used instead.")
            save_dir = config.TRANSCRIPT_DIR

    # The token cannot be obtained until the meeting starts. Starting without
    # one is fine. With --web, it can be entered later from the control page
    # in the browser.
    settings = config.Settings(
        caption_url=args.token or "",
        device=args.device,
        delay=args.delay,
        translate_model=args.model,
        dry_run=args.dry_run,
        save=not args.no_save,
        transcript_dir=save_dir,
        glossary_names=None if args.glossary is None else tuple(args.glossary),
        direction=args.direction,
    )

    if args.from_file:
        capture = audio_mod.FileCapture(args.from_file, loop_forever=args.loop)
    else:
        capture = audio_mod.Capture(device=audio_mod.find_device(args.device))

    web = None
    tray = None
    if args.web is not None:
        from live_caption import tunnel as tunnel_mod
        from live_caption import web as web_mod

        web = web_mod.WebCaptions(
            port=args.web, control_port=args.control_port, bind=args.web_bind
        )
        # **Whether to expose the control page on the tailnet.** Off by
        # default.
        if args.control_bind is not None:
            extra = _control_extra(args.control_bind)
            if extra is None:
                return 1
            web.control_extra = extra
            # Start even when the address is not available yet. Retry in the
            # background until it is.
            web.control_retry = True
        # Prepare only the delivery endpoint. Whether to expose it is a
        # separate matter (off by default). The route (Cloudflare /
        # Tailscale) carries over the previous selection.
        web.tunnel = tunnel_mod.Delivery(args.web, command=args.cloudflared)

        def announce() -> None:
            """Print changes in the delivery state to the terminal too.

            **The URL appears after the startup messages.** cloudflared takes
            a few seconds to finish setting up the tunnel. Without printing
            it here, there would be no way to learn the delivery URL when
            --no-browser is used.
            """
            st = web.tunnel.status()
            stamp = time.strftime("%H:%M:%S")
            if st["state"] == "on":
                print(f"[{stamp}] delivery    URL for the participants: "
                      f"{web.public_url()}")
                print(f"[{stamp}] delivery    the QR code is on the control "
                      f"page: {web.control_url()}")
            elif st["state"] == "error":
                print(f"[{stamp}] delivery    could not start delivering:")
                print(st["error"])
            elif st["state"] == "off":
                print(f"[{stamp}] delivery    stopped. The viewer URL is dead.")

        web.tunnel.on_change = announce
        try:
            web.start()
        except OSError as exc:
            print(f"Cannot start the browser captions "
                  f"(viewer {args.web} / control {args.control_port}): {exc}")
            print("Give ports that are not in use. "
                  "Example: --web 8090 --control-port 8091")
            return 1
        if args.tunnel:
            st = web.tunnel.start()
            if st["state"] == "error":
                # The app keeps running even when delivery fails, because
                # screen sharing and the Zoom caption API still work.
                print("Cannot start delivering:")
                print(st["error"])
            else:
                print("Starting the delivery. The URL takes a few seconds.")
        # When the app is started by a double click, do not make the user
        # open the browser. **What opens is the control page.** The viewer
        # page can be opened from there.
        if not args.no_browser:
            webbrowser.open(web.control_url())

        # **The tray icon takes the place of the window.** When the window is
        # hidden and the app stays resident, there is no way to tell whether
        # it is alive or dead. Show the state as a color, and allow operation
        # by right click. The app keeps running even if the icon fails.
        if args.tray:
            tray = tray_mod.Tray(web, log_path)
            if tray.start():
                print(f"Tray:         resident. Log: {log_path}")

    application = app_mod.App(settings, web=web)
    # **With a control page, start in the stopped state.** This lets the app
    # be launched before joining the meeting. Small talk during setup is not
    # sent to speech recognition. Without a control page there is no way to
    # start it, so start right away.
    start_now = web is None
    try:
        asyncio.run(application.run(capture, start_now=start_now))
    except KeyboardInterrupt:
        print()
        print("Stopping.")
    finally:
        if tray is not None:
            tray.stop()
        capture.stop()
        if web is not None:
            if web.tunnel is not None:
                # **Stop the route that is not selected too.** If the app
                # exits after the route was switched, the other one stays up.
                web.tunnel.stop_all()
            web.stop()
        application.report(capture)
    return 0


if __name__ == "__main__":
    sys.exit(main())
