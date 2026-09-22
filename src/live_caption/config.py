"""Settings. Values that came from measurements are built in as defaults.

The comment above each value says why it is what it is. Read that comment
before you change the value.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# --- Audio -------------------------------------------------------------------
# gpt-live-transcribe needs 24 kHz, 16 bit, mono PCM.
ASR_RATE = 24_000
# How much audio we send at a time. 100 ms is the recommended value. A shorter
# chunk means more network round trips; a longer one means more delay.
CHUNK_MS = 100
# Capture at the device's own rate and downsample to 24 kHz here.
# VB-CABLE is set to 48 kHz in the Windows sound settings (so we can drop
# every other sample, 2:1).
CAPTURE_RATE = 48_000
# The amplitude we treat as "sound is arriving". The silence watchdog and
# `--check-audio` use the same value.
# **This comes from measurement.** Silence stayed between 0.003 and 0.008,
# and speech reached 0.698.
VOICE_PEAK = 0.01

# --- Speech recognition ------------------------------------------------------
ASR_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
ASR_MODEL = "gpt-live-transcribe"
# Both minimal and high missed glossary terms. low was the best on real
# meeting audio.
ASR_DELAY = "low"
# The language can change in the middle of a meeting. Do not fix it.
ASR_LANGUAGES = ("ja", "en")

# The field the meeting is about. **This goes into both the transcription
# prompt and the translation prompt.**
#
# The default text names no field. **Writing the field of your own meeting
# makes technical terms more accurate.** Put one line in `.env`:
#
#   LIVECAPTION_MEETING_CONTEXT=Weekly meeting on optics and control systems.
#
# This has a different job from the glossary. The glossary gives the
# translation of each single term. This tells the model which field to read
# the words as.
MEETING_CONTEXT_ENV = "LIVECAPTION_MEETING_CONTEXT"
MEETING_CONTEXT_DEFAULT = "技術的な内容の定例会議。"


def meeting_context() -> str:
    """The field the meeting is about. `.env` can replace it.

    **Read it on every start.** As a constant it would be evaluated before
    `load_env()`, and the value in `.env` would have no effect.
    """
    return os.environ.get(MEETING_CONTEXT_ENV, "").strip() or MEETING_CONTEXT_DEFAULT


def asr_prompt() -> str:
    """The prompt we pass to transcription."""
    return f"{meeting_context()}日本語と英語が混ざる。"


# The largest number of words we can pass in keywords.
#
# **Keep this large enough to hold every term in the glossary.** `keywords()`
# passes both the Japanese and the English side, so the number of candidates
# is about twice the number of glossary entries. When this was 100, the last
# 27 terms in the glossary never reached speech recognition (2026-09-07).
# The translation stage still gets every term, so it can recover them, but
# the recognition stage cannot.
#
# OpenAI does not document a limit for keywords. We have not tested what
# happens when the number of words goes up.
# When words are cut, the startup screen prints a warning (app.py).
ASR_KEYWORD_LIMIT = 200

# --- Translation -------------------------------------------------------------
# nano was no faster and only lowered the quality. Reasoning models were far
# too slow to use. Both were measured on real meeting audio.
TRANSLATE_MODEL = "gpt-4.1-mini"
TRANSLATE_URL = "https://api.openai.com/v1/chat/completions"
# Translating a single sentence on its own breaks words like "it" and "that".
# We pass the sentences just before it as context.
CONTEXT_SENTENCES = 3

# --- Caption segmentation and sending ----------------------------------------
# The Zoom caption overlay fits about 80 characters per line in its default
# font.
# **This is the value for putting out English.** Japanese characters are
# twice as wide, so direction `en2ja` replaces this with 40 (see "Caption
# direction" below).
MAX_CAPTION_CHARS = 80
# The length at which we cut by force, when the text keeps growing and no
# sentence-ending mark arrives.
#
# **Keep this short.** Speakers talk for a long time, joining clauses with
# commas. At 120 characters, one sentence became 4 or 5 caption lines, and
# the first line was pushed out of the window the moment we sent it.
# At 70 characters it fits in about 2 lines.
#
# **This is the value for listening to Japanese.** English needs about twice
# as many characters to say the same thing, so direction `en2ja` replaces
# this with 140. A value set explicitly in `.env` or on the control page is
# kept (see "Caption direction" below and `apply_direction()`).
FORCE_CUT_CHARS = 70
# How many seconds after the speaker stops we finalize the sentence, even
# with no sentence-ending mark.
IDLE_FLUSH_SEC = 2.5
# How often we check whether the timer above has expired.
#
# **Keep this fine-grained.** At 0.5 seconds, we only noticed the timer in
# 0.5 second steps, and that added 0.25 seconds on average and 0.5 seconds
# at worst to the caption delay. A coroutine that wakes up every 0.1 seconds
# costs nothing worth counting.
IDLE_POLL_SEC = 0.1
# How many seconds of silence before we send the translation ahead of time,
# without waiting for the sentence to be final. 0 turns this off.
#
# **This is the only way left to make the silence wait faster.**
# `IDLE_FLUSH_SEC` cannot go lower: the p99 of the gap between deltas was
# 2.57 seconds, above the 2.5 second threshold. So we translate during the
# 2.5 seconds we are waiting, and put the caption out the moment the
# sentence becomes final.
# **This does not add any risk of cutting a sentence. The safety margin
# stays the same.**
#
# Set this to the latest moment at which the translation still finishes
# before the sentence is final. Sending earlier only throws away more work
# when the speaker starts again, and gains nothing.
#   IDLE_FLUSH_SEC 2.5 - median translation 0.9 - margin 0.2 = 1.4
SPECULATE_AFTER_SEC = 1.4
# When one translation produces several lines, how long we wait between them.
# Sending them all at once is too fast to read. The window holds only 4 lines
# at its smallest.
LINE_INTERVAL_SEC = 0.6
# Throwaway captions we send when the meeting starts. The first few never
# reach the receiving side; this was measured on real meetings.
# **With direction `en2ja`, these are replaced by three Japanese lines.**
WARMUP_CAPTIONS = (
    "Live captions are starting.",
    "Please widen the caption area to see more lines.",
    "---",
)

# --- Zoom caption API --------------------------------------------------------
# **This is the value for putting out English.** With direction `en2ja` it is
# replaced by `ja-JP`.
CAPTION_LANG = "en-US"
CAPTION_TIMEOUT = 10.0
# seq has to grow monotonically across the whole meeting session.
# If it goes backwards, Zoom returns no error and silently drops the caption.
# We save it per meeting ID.
SEQ_STATE_PATH = PROJECT_ROOT / "local" / "seq_state.json"
# A fallback for when we lose the saved state. Skipping numbers is allowed;
# we measured this. The measured send rate is 3.5 per second, so the factor
# is set above that.
SEQ_TIME_SCALE = 10
SEQ_EPOCH = 1_767_225_600  # 2026-01-01 UTC. A base that keeps seq in 32 bits.

# --- Caption direction -------------------------------------------------------
# **Pick one for each meeting.** English captions for a Japanese meeting,
# Japanese captions for an English meeting. When the other language appears,
# we put it out as it is, without translating. The prompt handles both.
#
# The direction decides the translation prompt and the four values below,
# and nothing else.
# **It does not touch speech recognition.** `keywords` always passes both the
# Japanese and the English side of the glossary (`glossary.keywords()`), so
# changing the direction sends the same words in the same order.
# That is why switching does not need a new WebSocket. This is where it
# differs from choosing a different glossary.


@dataclass(frozen=True)
class Direction:
    name: str
    label: str                # the name shown on the control page
    caption_lang: str         # the lang of the Zoom caption API
    max_caption_chars: int    # characters per line
    force_cut_chars: int      # length at which we cut when no ending mark comes
    warmup: tuple[str, ...]   # throwaway captions sent at the start


DIRECTIONS: dict[str, Direction] = {
    "ja2en": Direction(
        name="ja2en",
        label="日本語 → 英語",
        caption_lang=CAPTION_LANG,
        max_caption_chars=MAX_CAPTION_CHARS,
        force_cut_chars=FORCE_CUT_CHARS,
        warmup=WARMUP_CAPTIONS,
    ),
    "en2ja": Direction(
        name="en2ja",
        label="英語 → 日本語",
        caption_lang="ja-JP",
        # Japanese characters are twice as wide, so half as many fit in the
        # same window.
        max_caption_chars=40,
        # English needs about twice as many characters to say the same thing.
        # At 70 the sentences would be chopped into fragments.
        force_cut_chars=140,
        warmup=(
            "字幕を開始します。",
            "字幕の表示領域を広げてください。",
            "---",
        ),
    ),
}
# --- Control page language ---------------------------------------------------
# **The viewer page is not covered here.** That page is in English from the
# start, and participants are the ones who read it.
# The translation tables are in `i18n.py`. We remember the choice, so the
# next start uses the same language.
UI_LANG = "ja"
UI_LANG_STATE_PATH = PROJECT_ROOT / "local" / "ui_lang_state.json"

DIRECTION_DEFAULT = "ja2en"
# The direction chosen right now. `apply_direction()` rewrites it.
DIRECTION = DIRECTION_DEFAULT
# The last choice. We write it every time the control page changes it.
# The next start begins with this value.
DIRECTION_STATE_PATH = PROJECT_ROOT / "local" / "direction_state.json"

# --- Browser captions --------------------------------------------------------
# The Zoom caption API needs host rights (to copy the token). It cannot be
# used in a meeting you do not host, so there is also a way to put captions
# in a browser (web.py). There are two ways to show them: share your screen,
# or hand the viewer URL to the participants.
#
# **There are two separate pages, on two separate ports.**
#   viewer (WEB_PORT)    only shows captions. This is the only one that
#                        leaves the machine
#   control (CONTROL_PORT) token, start and stop of sending, shutdown,
#                        tunnel, records. 127.0.0.1 only
#
# We do not separate them by path, because both tunnels and reverse proxies
# forward a whole origin. One typo in a path, and anyone who learns the URL
# can stop the caption app.
WEB_PORT = 8080
CONTROL_PORT = 8081
# **The https exit for the control page** (`tailscale serve`, inside the
# tailnet only).
# This does not add secrecy; the tailnet is already encrypted by WireGuard.
# What it does is make the browser treat the page as a secure context.
# `navigator.clipboard` then works, and the button that copies the URL does
# not have to fall back to the old method.
#
# **Add this exit to `allowed_origins()`.** Without it, the page opens but
# the `Origin` does not match, and **every button returns 403** (we hit this
# exact problem on 2026-09-19).
CONTROL_HTTPS_PORT = 8443
# The address the viewer listens on. By default, this machine only.
# When you use a tunnel, cloudflared connects to 127.0.0.1, so the default
# is fine. Set it to 0.0.0.0 (--web-bind) only when you want to show the
# page directly to another machine on the same LAN.
# **This setting does not affect the control page. That is always 127.0.0.1.**
WEB_BIND = "127.0.0.1"
# How often we hand the unfinished transcript to the viewer page.
#
# **For the few seconds until a sentence is final, the page shows nothing.**
# To fill that gap, we send the partial text without waiting for the
# sentence to be final. Deltas arrive with a median gap of 0.01 seconds, so
# passing every one of them would keep the long poll running without a
# break. We thin them out here.
#
# **We do not send partial text to the Zoom captions or to translation.**
# Neither one can replace a line it has already put out.
WEB_PARTIAL = True
PARTIAL_INTERVAL_SEC = 0.2
# How many lines the page shows. The Zoom window holds only 4 at its
# smallest, but here we decide for ourselves.
# In a shared screen, compression destroys small letters, so we do not ask
# for many lines and we show them large.
WEB_LINES = 8
# The length, in bytes, of the unguessable path in the viewer URL.
# The host name of a temporary tunnel is random too, but we put a secret in
# the path as well.
VIEWER_SECRET_BYTES = 8
# In the long poll, how many seconds we wait before returning an empty
# answer when no new line has arrived.
#
# **SSE does not work.** A temporary tunnel (TryCloudflare) buffers
# text/event-stream at its edge, and nothing reaches the browser until the
# connection closes. In our measurement, not one byte arrived for 15
# seconds. A long poll works, because each response completes on its own
# (measured at 0.4 to 1.0 seconds).
LONGPOLL_WAIT_SEC = 25.0

# --- Tunnel (handing the viewer URL to participants) -------------------------
# cloudflared opens the temporary tunnel outward from the caption PC. No
# incoming connection is needed, so it works on a campus LAN, on meeting
# room WiFi, and on a phone hotspot.
#
# **We do not open it by default.** This keeps unpublished results from
# going outside by accident.
# To use it, start it from "Tunnel" on the control page, or start the app
# with --tunnel.
TUNNEL_CMD = "cloudflared"
# Where we look for it. If it is not in PATH, we look here (we keep it in
# local/bin).
TUNNEL_LOCAL = PROJECT_ROOT / "local" / "bin" / "cloudflared.exe"
# How many seconds we wait for the URL to appear. After that we treat it as
# a failure.
TUNNEL_TIMEOUT_SEC = 30.0

# There are two delivery routes. You pick one on the control page, and we
# remember the last choice.
#
#   cloudflare  A temporary tunnel. **The URL changes on every start.**
#               Nothing to prepare. Good for a meeting decided on the spot.
#   tailscale   Tailscale Funnel. **The host name does not change.** You can
#               build the meeting URL in advance and put it in the
#               invitation. It needs one setup step on the tailnet side.
TUNNEL_KINDS = ("cloudflare", "tailscale")
TUNNEL_KIND = "cloudflare"
TUNNEL_KIND_STATE_PATH = PROJECT_ROOT / "local" / "tunnel_kind.json"
TAILSCALE_CMD = "tailscale"
# The default location on Windows. If it is not in PATH, we look here.
TAILSCALE_LOCAL = Path(r"C:\Program Files\Tailscale\tailscale.exe")
# The port Funnel listens on for the public side. **Only 443, 8443 and 10000
# can be chosen** (a Tailscale restriction).
FUNNEL_PUBLIC_PORT = 443
# How many seconds we remember the host name on the tailnet. **At 0 we start
# a process every time.** The control page asks for the status every 2
# seconds, so without remembering it, that would be tens of thousands of
# times a day.
TAILSCALE_HOST_CACHE_SEC = 30.0
# The address ranges Tailscale hands out. **These are the only addresses the
# control page may be served on.**
# Do not replace this with a general bind setting. Writing 0.0.0.0 would
# show the control page, which has no authentication, to everyone on the
# campus LAN.
# How often, in seconds, we retry in the background until we get a tailnet
# address.
# When the app starts automatically, Tailscale may not be up yet.
CONTROL_BIND_RETRY_SEC = 15.0

# --- Background running (tray icon and logs) ---------------------------------
# **If you remove the window, decide where the log goes first.** Right now,
# the only record this app keeps is the lines it prints to the terminal.
# Once the window is gone, a failure cannot be traced unless the lines are
# kept somewhere.
# The application icon. The Start menu shortcut and the tray icon use it.
APP_ICON = PROJECT_ROOT / "etc" / "LiveCaption.ico"
LOG_DIR = PROJECT_ROOT / "local" / "log"
LOG_KEEP = 20
TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

# --- A viewer URL for each meeting -------------------------------------------
# **Each meeting uses its own URL.** The participants differ from meeting to
# meeting, and today's captions must not be visible through an older
# meeting's URL. We build the `/v/<path>` part of the URL per meeting.
#
# **You can build it in advance.** With Tailscale the host name is known, so
# the URL can be fixed the day before the meeting. This is what lets you put
# it in the invitation next to the Zoom link.
#
# **Only the one meeting you selected is delivered.** The URL of any other
# meeting returns 404 that day.
MEETINGS_STATE_PATH = PROJECT_ROOT / "local" / "meetings.json"
# The length, in bytes, of the path in the URL where the host pastes the
# token.
# **Make it longer than the viewer one (VIEWER_SECRET_BYTES).** If the viewer
# path is guessed, only the captions leak, and it is handed to dozens of
# people anyway. This path decides what we send to Zoom.
HOST_SECRET_BYTES = 32

# --- Joining scheduled meetings automatically --------------------------------
# **By default this does nothing.** Only meetings marked `auto` are handled.
# Delivery is off by default, following the rule "do not use it for meetings
# with unpublished content". Starting delivery with nobody watching turns
# that default around, so a person selects each meeting one by one.
SCHEDULE_TICK_SEC = 5.0
# We no longer pick up a meeting this many minutes after its start time.
# This keeps us from starting, all at once, every meeting that passed while
# the app was stopped.
SCHEDULE_GRACE_MIN = 10
# How long we give it before we check that generation really started.
# **Do not look at `generating` alone.** It also goes back to False when the
# audio input could not be opened.
SCHEDULE_ARM_SEC = 90.0
# How many seconds after we tell Zoom to join, with no sound ever arriving,
# before we decide that we are not in the meeting.
# **Do not rush this.** Meetings start late. A waiting room, a wrong
# passcode and an update dialog can all only be observed as "no sound
# arrives".
SCHEDULE_JOIN_AUDIO_SEC = 300.0
# **How long we give the first sentence to appear.** At the start of a
# meeting, several minutes of silence while people arrive is normal
# (reported 2026-09-21). Running the silence timer during that would
# **close a meeting that has not begun yet.** If the meeting's own
# `silence_min` is longer, we use that instead.
#
# **We do not leave it sitting there forever.** Past this point we close it.
# The recognition connection is billed even while nobody speaks, so we do
# not let it sit in an empty meeting for hours.
SCHEDULE_OPENING_SEC = 900.0

# When we post to the Zoom chat. **We post after the scheduled start time.**
#
# **The Zoom chat does not show messages sent before you joined.** Posting
# early leaves nothing for people who join later (reported 2026-09-20). The
# caption app starts `lead_min` minutes before the start, so posting at that
# moment means everyone misses it.
#
# **We post twice: at the start time, and 3 minutes later** (as requested,
# 2026-09-20). The text is the same both times. The first reaches people who
# joined on time, the second reaches people who were a little late.
SCHEDULE_CHAT_AT_MIN = (0.0, 3.0)
# The smallest gap we keep between the two posts. If joining Zoom is delayed
# and the first post is pushed back, the time for the second one may already
# have passed. **The same sentence appearing twice, seconds apart, looks
# broken.**
SCHEDULE_CHAT_GAP_SEC = 60.0
# From there, how long we keep trying before giving up, and how often we
# check.
# **Zoom takes tens of seconds to several minutes to show the meeting window
# after we hand it a `zoommtg:` link.** In our measurement, it was still not
# there 5 seconds after launch (a real meeting, 2026-09-20).
SCHEDULE_CHAT_WAIT_SEC = 600.0
SCHEDULE_CHAT_RETRY_SEC = 10.0
# The display name in Zoom. **Use a name that shows this participant will
# not speak.**
ZOOM_DISPLAY_NAME = "Live Captions"
# The command that starts Zoom on Linux. Not used on Windows, where we look
# it up in the registry.
ZOOM_CMD = "zoom"

# --- Where the host pastes the token -----------------------------------------
# **This is the only place that accepts a write through the tunnel.** The
# viewer server has no other way to change any state. Keep it narrow, and
# keep it open for a short time.
#
# We accept the token for this many minutes around the scheduled start.
# **Do not make it a permanent endpoint.** This narrows it down to about an
# hour per meeting.
HOST_TOKEN_WINDOW_MIN = 30
# After this many failures, we close the endpoint for that meeting.
# Nobody is going to brute-force a 32 byte path, but this stops a scanner or
# a bug from hammering it.
HOST_MAX_ATTEMPTS = 5
# The shortest interval, in seconds, between two requests to the endpoint.
# The same machine runs the long poll and the real-time transcription, so we
# make sure it cannot be flooded.
HOST_MIN_INTERVAL_SEC = 1.0
# The largest body we accept. Only one token URL ever arrives, so it can be
# small.
HOST_MAX_BODY = 4096
# **We do not open this on Cloudflare.** TLS ends at the Cloudflare edge, so
# the Zoom credential would pass through it in the clear. With Tailscale,
# TLS ends on this machine.
HOST_TOKEN_KINDS = ("tailscale",)


# --- Meeting record ----------------------------------------------------------
# For each final sentence, we keep the recognized text and the caption as a
# pair (transcript.py).
# **We keep it by default.** A misrecognition only shows on the recognized
# side, and we need that to grow the glossary.
# The content of the meeting stays on the caption PC's disk, so --no-save
# turns it off when you do not want that.
#
# **They go in `local/transcripts/`** (as requested, 2026-09-20). **They
# collect on the caption PC, and you download them from the control page.**
# The caption PC runs all the time, and we operate it over the tailnet.
# Writing them to the download folder would mean starting a remote desktop
# session to fetch them, which is too much work just to read a record.
#
# (From 2026-09-08 to 2026-09-20 they went to the download folder. The
# reason then was "open it right after the meeting", which assumed you were
# sitting at the caption PC.)
#
# To change the location, use `LIVECAPTION_SAVE_DIR` in `.env`, or
# --save-dir.
#
# We prefix the name with `live-caption_`. `scripts/transcript_to_md.py`
# looks for that prefix too (searching for `*.jsonl` would pick up unrelated
# files).
TRANSCRIPT_DIR = PROJECT_ROOT / "local" / "transcripts"
TRANSCRIPT_PREFIX = "live-caption_"

# The environment variable that changes the location. Put it in `.env` and
# it takes effect on the next start.
SAVE_DIR_ENV = "LIVECAPTION_SAVE_DIR"

# How many seconds after "stop" is pressed before we close the record.
#
# **Stop means "this meeting is over"** (it closes delivery and the Zoom
# captions too). So we close the record there and write the `.md`. Without
# closing it, a downloaded `.md` would still say the meeting is going on
# (reported 2026-09-20).
#
# **We do not close it right away.** At the moment you stop, the last
# sentence may still be in translation (median translation 0.9 seconds, line
# interval 0.6 seconds). Closing immediately would drop that one sentence
# into the next record. Waiting 3 seconds lets it finish first.
# **If you start again within that time, we do not close it.** If you only
# stopped for a break, the record stays as one file.
STOP_ROLL_WAIT_SEC = 3.0


def check_save_dir(path: str | Path) -> Path:
    """Check that this can hold the records. Return a usable absolute path.

    **Create it if it does not exist.** Creating it is better than stopping
    before a meeting because the folder is missing. Places we cannot create
    (permissions, a drive that does not exist) are rejected here.

    **We test whether we can write by actually writing.** On Windows there
    are folders you can read but not write (the root of a drive, a folder
    OneDrive is syncing). Looking at the attributes alone would pass, and
    then the record alone would quietly fail in the middle of a meeting.
    """
    text = str(path).strip().strip('"')
    if not text:
        raise ValueError("フォルダを指定すること。")
    target = Path(os.path.expandvars(text)).expanduser()
    if not target.is_absolute():
        raise ValueError("絶対パスで指定すること。")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"そのフォルダは作れない: {exc}") from exc
    if not target.is_dir():
        raise ValueError("フォルダではない。")
    probe = target / ".livecaption_write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"そのフォルダには書けない: {exc}") from exc
    return target

# --- Other -------------------------------------------------------------------
# A glossary is a .tsv file in etc/glossary/. **You change which ones you
# combine for each meeting.**
# They are not in docs/. **They are not something to read; they are data the
# app reads.**
# Different subsystems use different words, so using one big table for every
# meeting means unrelated words eat the recognition keywords, and the words
# you actually need fall off the end at the limit.
GLOSSARY_DIR = PROJECT_ROOT / "etc" / "glossary"
# **The location can be changed from outside.** Under Docker, the glossaries
# are user data that people add and remove from the control page, so they go
# in a volume and not in the repository. Otherwise an uploaded glossary
# would disappear when the container is rebuilt.
GLOSSARY_DIR_ENV = "LIVECAPTION_GLOSSARY_DIR"
# What we load when nothing is selected (without the file extension).
# **The repository ships no glossary, so the default is empty.** Once you
# have one, select it from the control page. You can also write its name
# here.
GLOSSARY_DEFAULT: tuple[str, ...] = ()


def glossary_dir() -> Path:
    """Where the glossaries live.

    **Read the environment variable on every call.** `load_env()` runs after
    the import, so making this a constant would make the setting in `.env`
    have no effect.
    """
    raw = os.environ.get(GLOSSARY_DIR_ENV, "").strip()
    return Path(raw).expanduser() if raw else GLOSSARY_DIR
# The last choice. We write it every time the control page changes it.
# The next start begins with this value.
GLOSSARY_STATE_PATH = PROJECT_ROOT / "local" / "glossary_state.json"
ENV_PATH = PROJECT_ROOT / ".env"

# --- VNC (only when running in a container) ----------------------------------
# **Do not use this routinely.** It has no authentication, and anyone inside
# the tailnet can reach it.
# We use it to sign in to the meeting software, and to look at what is
# happening when automatic joining gets stuck.
VNC_RFB_PORT = 5900          # what a VNC client connects to
VNC_WEB_PORT = 6080          # what a browser connects to (noVNC, http)
# **The https exit** (`tailscale serve`, inside the tailnet only).
# A browser does not treat `http` as a secure context, so
# `navigator.clipboard` does not exist there. **That is why noVNC cannot
# read the clipboard of the machine you are watching from.**
VNC_HTTPS_PORT = 6443
NOVNC_ROOT = "/usr/share/novnc"
# 1 starts it together with the app. By default we do not start it; you open
# it from the control page.
VNC_ENV = "LIVECAPTION_VNC"

# --- Does it come back after you end it --------------------------------------
# **Under Docker it comes back** (`restart: unless-stopped` in compose).
# There, "shut down" means "restart". **A button that says "shut down" while
# the app comes back on its own makes the person who pressed it think
# something is broken.** We switch the wording on the page here.
#
# **We read the same variable compose passes to `restart:`, with the same
# value.**
# It used to be a separate variable, `LIVECAPTION_RESTARTS` (0/1). With two
# variables, someone fixes one of them and the wording on the page ends up
# disagreeing with what actually happens.
# A native start on Windows has no such variable, so this is False.
RESTART_ENV = "LIVECAPTION_RESTART"

# The Docker restart policies that **bring the container back even after a
# clean exit**.
# `on-failure` does not bring it back on exit code 0, so it is not listed
# here. "Shut down" on the control page exits with 0.
_RESTART_ALWAYS = ("always", "unless-stopped")


def restarts() -> bool:
    """Will something outside start it again after we end it?"""
    return os.environ.get(RESTART_ENV, "").strip().lower() in _RESTART_ALWAYS


@dataclass
class Settings:
    caption_url: str
    device: str | None = None
    # The names of the glossaries to use. None means the last choice, or
    # GLOSSARY_DEFAULT if there is none.
    glossary_names: tuple[str, ...] | None = None
    # The caption direction. None means the last choice, or
    # DIRECTION_DEFAULT if there is none.
    direction: str | None = None
    delay: str = ASR_DELAY
    translate_model: str = TRANSLATE_MODEL
    dry_run: bool = False
    languages: tuple[str, ...] = field(default_factory=lambda: ASR_LANGUAGES)
    save: bool = True
    transcript_dir: Path = TRANSCRIPT_DIR


# --- Tuning knobs you can override from .env ---------------------------------
#
# **The defaults come from measurement.** The comments above say why.
# Write them in `.env` only when you want to change them on site, to match
# the room or the way people speak.
#
# (environment variable, constant name, type, smallest accepted value)
_TUNABLE = (
    ("LIVECAPTION_IDLE_FLUSH_SEC", "IDLE_FLUSH_SEC", float, 0.3),
    ("LIVECAPTION_SPECULATE_AFTER_SEC", "SPECULATE_AFTER_SEC", float, 0.0),
    ("LIVECAPTION_FORCE_CUT_CHARS", "FORCE_CUT_CHARS", int, 20),
    ("LIVECAPTION_LINE_INTERVAL_SEC", "LINE_INTERVAL_SEC", float, 0.0),
)

# The seconds we leave between sending the speculative translation and the
# sentence becoming final. Median translation 0.9 plus a margin of 0.2.
# When only `IDLE_FLUSH_SEC` is overridden, we derive `SPECULATE_AFTER_SEC`
# from it with this.
SPECULATE_MARGIN_SEC = 1.1

# **Keep the values from before any override.** "Reset to default" on the
# control page uses them.
# `apply_env_overrides()` rewrites globals(), so we take them before that.
_DEFAULTS = {attr: globals()[attr] for _e, attr, _c, _f in _TUNABLE}

# **Knobs a person changed explicitly.** Switching the direction also
# changes the default of `FORCE_CUT_CHARS` (70 for Japanese, 140 for
# English), but **it must not undo a value someone set explicitly.**
# Anything listed here does not follow the direction. Setting it back to the
# default value removes it from this set.
_EXPLICIT: set[str] = set()
# The explanation shown on the page. **Give the unit, and say what changes.**
_TUNING_HELP = {
    "IDLE_FLUSH_SEC": "秒。発話が途切れてから、文末記号が無くても確定させるまで。"
                      "実測の delta 間隔の p99 が 2.57 秒なので、下げると話の途中で切る",
    "SPECULATE_AFTER_SEC": "秒。無音がこれだけ続いたら、確定を待たずに翻訳を投げる。"
                           "当たれば約0.9秒早く出る。0 で止める",
    "FORCE_CUT_CHARS": "文字。日本語がこれより長くなったら強制的に切る。"
                       "字幕が速すぎて読めないときは下げる",
    "LINE_INTERVAL_SEC": "秒。Zoomへ1行ずつ送る間隔。字幕の窓は最小4行しかない。"
                         "閲覧画面には効かない",
}


def coerce_tuning(attr: str, raw) -> float | int:
    """Check a tuning value. Raise `ValueError` when it is not usable.

    **The same rule rejects values from `.env` and from the control page.**
    Written in two places, the two would drift apart.
    """
    for _env_name, name, cast, floor in _TUNABLE:
        if name != attr:
            continue
        try:
            value = cast(str(raw).strip())
        except (ValueError, TypeError):
            raise ValueError(f"{attr}: 「{raw}」は数字として読めない。") from None
        if value < floor:
            raise ValueError(f"{attr}: {value} は小さすぎる（{floor} 以上にすること）。")
        return value
    raise ValueError(f"{attr} は変えられる設定ではない。")


def default_of(attr: str) -> float | int:
    """The default of that knob. **For ones that follow the direction, take
    it from the direction chosen right now.**

    `FORCE_CUT_CHARS` depends on the language we listen to (70 for Japanese,
    140 for English). We branch here so that "reset to default" on the
    control page gives the value that matches the direction.
    """
    if attr == "FORCE_CUT_CHARS":
        return direction().force_cut_chars
    return _DEFAULTS[attr]


def _remember_explicit(attr: str, value) -> None:
    """Remember whether a person set this value explicitly.

    **A value equal to the default goes back to "not explicit".** "Save to
    .env" on the control page writes out every item, including the ones left
    at their default, so a value being present in `.env` is not by itself
    proof that someone meant it. Only values that differ from the default
    are protected from a direction switch.
    """
    if value == default_of(attr):
        _EXPLICIT.discard(attr)
    else:
        _EXPLICIT.add(attr)


def set_tuning(attr: str, raw) -> float | int:
    """Check a tuning value and set it. Called from the control page."""
    value = coerce_tuning(attr, raw)
    globals()[attr] = value
    _remember_explicit(attr, value)
    return value


def tuning() -> list[dict]:
    """The current and default value of each knob. Returned to the control
    page."""
    return [
        {
            "name": attr,
            "env": env_name,
            "value": globals()[attr],
            "default": default_of(attr),
            "min": floor,
            "step": 1 if cast is int else 0.1,
            "help": _TUNING_HELP.get(attr, ""),
        }
        for env_name, attr, cast, floor in _TUNABLE
    ]


def direction() -> Direction:
    """The direction chosen right now."""
    return DIRECTIONS[DIRECTION]


def apply_direction(name: str) -> Direction:
    """Switch the caption direction and replace the output constants.
    Return the direction chosen.

    **We do not rebuild the translation prompt here.** The caller
    (`app.apply_direction`) holds the glossary, so it does that. All this
    function replaces is constants.
    """
    if name not in DIRECTIONS:
        known = " / ".join(DIRECTIONS)
        raise ValueError(f"字幕の向きが違う: 「{name}」。{known} のどちらか。")
    d = DIRECTIONS[name]
    globals()["DIRECTION"] = name
    globals()["CAPTION_LANG"] = d.caption_lang
    globals()["MAX_CAPTION_CHARS"] = d.max_caption_chars
    globals()["WARMUP_CAPTIONS"] = d.warmup
    # Keep values set explicitly. Only untouched ones follow the default of
    # the new direction.
    if "FORCE_CUT_CHARS" not in _EXPLICIT:
        globals()["FORCE_CUT_CHARS"] = d.force_cut_chars
    return d


def direction_selection() -> str:
    """The remembered direction, or the default if there is none.

    **We remember it so that you do not have to pick it again for every
    meeting.** This follows the same idea as the glossary. It appears on the
    startup screen and on the control page, so you can see when it is still
    set to the last choice.
    """
    try:
        saved = json.loads(DIRECTION_STATE_PATH.read_text(encoding="utf-8"))
        name = str(saved.get("name", ""))
    except (OSError, ValueError, AttributeError):
        name = ""
    return name if name in DIRECTIONS else DIRECTION_DEFAULT


def tunnel_kind_selection() -> str:
    """The remembered delivery route, or Cloudflare if there is none."""
    try:
        saved = json.loads(TUNNEL_KIND_STATE_PATH.read_text(encoding="utf-8"))
        kind = str(saved.get("kind", ""))
    except (OSError, ValueError, AttributeError):
        kind = ""
    return kind if kind in TUNNEL_KINDS else TUNNEL_KIND


def remember_tunnel_kind(kind: str) -> None:
    """Remember it for the next start. Do not crash if we cannot write."""
    try:
        TUNNEL_KIND_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TUNNEL_KIND_STATE_PATH.write_text(
            json.dumps({"kind": kind}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [配信] 経路を覚えられない: {exc}")


def ui_lang_selection() -> str:
    """The remembered control page language, or Japanese if there is none."""
    from . import i18n

    try:
        saved = json.loads(UI_LANG_STATE_PATH.read_text(encoding="utf-8"))
        name = str(saved.get("lang", ""))
    except (OSError, ValueError, AttributeError):
        name = ""
    return name if name in i18n.LANGS else i18n.DEFAULT


def apply_ui_lang(name: str) -> str:
    """Switch the control page language. Return the language chosen."""
    from . import i18n

    if name not in i18n.LANGS:
        raise ValueError(f"言語が違う: 「{name}」。{' / '.join(i18n.LANGS)} のどちらか。")
    globals()["UI_LANG"] = name
    return name


def remember_ui_lang(name: str) -> None:
    """Remember it for the next start. Do not crash if we cannot write."""
    try:
        UI_LANG_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UI_LANG_STATE_PATH.write_text(
            json.dumps({"lang": name}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [言語] 選択を覚えられない: {exc}")


def remember_direction(name: str) -> None:
    """Remember the choice for the next start. Do not crash if we cannot
    write."""
    try:
        DIRECTION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIRECTION_STATE_PATH.write_text(
            json.dumps({"name": name}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [向き] 選択を覚えられない: {exc}")


def tuning_warning() -> str:
    """One line about a bad combination of settings. Empty when there is
    none."""
    if SPECULATE_AFTER_SEC and SPECULATE_AFTER_SEC >= IDLE_FLUSH_SEC:
        return (f"先回り（{SPECULATE_AFTER_SEC}秒）が確定待ち（{IDLE_FLUSH_SEC}秒）"
                f"以上なので、先回りは一度も走らない。")
    return ""


def save_env(values: dict[str, str], path: Path | None = None) -> Path:
    """Rewrite the matching lines of `.env`. Return the path we wrote.

    **Do not delete any other line.** `.env` holds `OPENAI_API_KEY`.
    A line that already exists is replaced in place; a missing one is added
    at the end.
    A line commented out as `#LIVECAPTION_...=` has the comment mark removed
    and is then used (a `.env` copied straight from `.env.example` looks
    like this).

    **We write through a temporary file.** If we crashed halfway and broke
    `.env`, the API key would be lost with it.
    """
    # **Do not capture ENV_PATH in a default argument.** It is evaluated once
    # at import time, and then it could not be replaced later (we fell into
    # the same trap in Segmenter).
    path = ENV_PATH if path is None else path
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)

    for i, line in enumerate(lines):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        key = bare.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def apply_env_overrides() -> None:
    """Override the tuning knobs from environment variables (including
    `.env`).

    **Do not read `os.environ` on the line that defines a constant.**
    `config` is imported before `load_env()` runs, so at that moment `.env`
    has not been read yet. We apply the overrides here, after reading
    `.env`.

    **The app must not fail to start on the day of a meeting because of a
    typo.** A value that cannot be read as a number, or that is too small,
    produces a warning and leaves the default in place. **We never ignore it
    silently.**
    """
    changed: list[str] = []
    for env_name, attr, _cast, _floor in _TUNABLE:
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        before = globals()[attr]
        try:
            value = coerce_tuning(attr, raw)
        except ValueError as exc:
            print(f"  [設定の警告] {env_name}: {exc} 既定の {before} を使う。")
            continue
        globals()[attr] = value
        _remember_explicit(attr, value)
        # The value written is often the same as the default, because "save
        # to .env" writes every item. Reporting an unchanged value as
        # "overridden" would confuse the reader.
        if value != before:
            changed.append(f"{attr} {before} → {value}")

    # When only `IDLE_FLUSH_SEC` was changed, move the speculation time with
    # it.
    # **Without that, speculation fires too early and only throws away more
    # work.**
    if (not os.environ.get("LIVECAPTION_SPECULATE_AFTER_SEC", "").strip()
            and os.environ.get("LIVECAPTION_IDLE_FLUSH_SEC", "").strip()
            and SPECULATE_AFTER_SEC):
        derived = round(max(0.3, IDLE_FLUSH_SEC - SPECULATE_MARGIN_SEC), 2)
        if derived != SPECULATE_AFTER_SEC:
            changed.append(f"SPECULATE_AFTER_SEC {SPECULATE_AFTER_SEC} → {derived}（自動）")
            globals()["SPECULATE_AFTER_SEC"] = derived

    if changed:
        print("  [設定] .env で差し替えた: " + "、".join(changed))

    # Speculation is pointless unless it fires before the sentence is final.
    warning = tuning_warning()
    if warning:
        print(f"  [設定の警告] {warning}")


def load_env(path: Path = ENV_PATH) -> None:
    """Read .env and apply the tuning knobs.

    **The values in .env win, and overwrite the environment variables.**
    """
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            # The value in .env wins. It overwrites an environment variable
            # of the same name.
            if value:
                os.environ[key] = value
    # Even without a .env, the environment variables alone can override.
    apply_env_overrides()


def openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が無い。.env に書くか、環境変数に設定すること。")
    return key
