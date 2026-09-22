"""Show captions in a browser. The viewer page and the control page use
separate ports.

The Zoom caption API needs host rights (you copy a token). You cannot use it
in a meeting you do not host. So this module also serves the captions from a
local HTTP server. There are two ways to show them.

1. **Share your screen.** Put the viewer page in full screen and share it.
   Nothing leaves the machine
2. **Hand out the viewer URL.** Each participant opens it on their own device.
   This needs a temporary tunnel (`tunnel.py`)

## Two pages, two ports

    127.0.0.1:8081  control page  token, start/stop, tunnel, records, quit
    <bind>:8080     viewer page   captions only

**They are split by port, not by path.** Tunnels and reverse proxies pass a
whole origin, so one wrong path would let anyone who knows the URL stop the
caption app. Two servers make that accident impossible.

**The control page always listens on `127.0.0.1`.** `--web-bind` only affects
the viewer page.

The viewer page sits behind an **unguessable path** (`/v/<random>`). The `/`
path on the viewer port returns 404, so the tunnel URL alone shows nothing.

## Transport is long polling. SSE does not work

    GET /v/<secret>/lines?since=N  →  {"next": 42, "lines": [...]}

The server waits up to 25 seconds for a new line, then answers. **Each answer
is complete on its own, so it also passes through relays that buffer.**

**SSE does not work.** A temporary tunnel (TryCloudflare) buffers
`text/event-stream` at its edge, and nothing reaches the browser until the
connection closes. When measured, no byte arrived for 15 seconds. Long polling
over the same path took 0.4 to 1.0 seconds.

The delay feels the same as SSE. Reconnecting is just another HTTP request, so
it is in fact simpler.

## Source-language lines and text size

**The browser decides both.** The server always sends both kinds of line, and
the browser decides what to show. Each participant reads on their own device,
so each one can also change the text size. The choice is kept in
`localStorage`. Changing it during a meeting never talks to the server.
"""

from __future__ import annotations

import hmac
import io
import json
import os
import re
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from . import captions as captions_mod
from . import config, glossary as glossary_mod, i18n, meetings, tunnel as tunnel_mod
from . import meetings_page
from . import transcript as transcript_mod

# How many lines of history to keep on the page. Older lines are dropped.
# A participant who joins late only needs to see what came just before.
HISTORY = 200
# Maximum request size. A token URL is long, but a few KB is enough.
MAX_BODY = 64 * 1024


# --- Look and feel (shared by the viewer and the control page) --------------

STYLE = """
  /* --- Colors -------------------------------------------------------------
     **Dark text on a light background.** A dark color scheme is hard to read
     (reported 2026-09-20).

     **Colors are written only here.** They used to be written in many places,
     so changing the scheme meant chasing 50 spots. To go back to a dark
     scheme, replace only this block. */
  :root {
    /* **Without this line, only the select boxes, the date popup and the
       scrollbars stay dark.** On a machine whose OS uses a dark scheme, those
       parts alone remain black. */
    color-scheme: light;
    --bg:      #ffffff;   /* page background */
    --panel:   #f6f8fa;   /* background of the right-hand panel */
    --field:   #ffffff;   /* background of input fields and URL boxes */
    --btn:     #f6f8fa;   /* button background */
    --hover:   #eaeef2;   /* background on hover */
    --fg:      #1f2328;   /* body text */
    --ja:      #59636e;   /* source-language lines */
    --muted:   #59636e;   /* headings and notes */
    --dim:     #848d97;   /* even fainter marks */
    --line:    #d8dee4;   /* thin rules */
    --line2:   #c2cad2;   /* borders of controls */
    --accent:  #0969da; --accent-h: #0a58ca; --on-accent: #ffffff;
    --knob:    #ffffff;   /* the knob of a toggle */
    --ok:      #1a7f37; --ng: #cf222e;
    --warn:    #9a6700; --warn-line: #d4a72c;
    --ng-fg:   #a40e26; --ng-bg: #fff5f5; --ng-line: #f3c2c2;
    --lines: __LINES__;
    --size: calc(clamp(20px, 2.7vw, 44px) * var(--zoom, 1));
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%; background: var(--bg); color: var(--fg);
    font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif;
  }
  body { display: flex; flex-direction: column; }

  header {
    flex: 0 0 auto; display: flex; align-items: center; gap: 14px;
    padding: 10px 20px; border-bottom: 1px solid var(--line);
    font-size: 15px; color: var(--ja); flex-wrap: wrap;
  }
  .title { font-weight: 600; color: var(--fg); letter-spacing: .02em; }
  .spacer { flex: 1 1 auto; }

  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ng); flex: 0 0 auto; }
  .dot.on { background: var(--ok); }

  .pill {
    padding: 3px 11px; border-radius: 999px;
    border: 1px solid var(--line); background: var(--hover);
    font-size: 13px; white-space: nowrap;
  }
  .pill.on { border-color: var(--ok); color: var(--ok); }
  .pill.off { border-color: var(--line2); color: var(--ja); }
  .pill.bad { border-color: var(--ng); color: var(--ng); }
  .pill.warn { border-color: var(--warn-line); color: var(--warn); }

  .toggle { display: flex; align-items: center; gap: 9px; cursor: pointer; user-select: none; }
  .toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
  .track {
    width: 42px; height: 24px; border-radius: 12px;
    background: var(--line2); position: relative; transition: background .15s; flex: 0 0 auto;
  }
  .track::after {
    content: ""; position: absolute; top: 3px; left: 3px;
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--knob); transition: transform .15s;
  }
  .toggle input:checked + .track { background: var(--accent); }
  .toggle input:checked + .track::after { transform: translateX(18px); }
  .toggle input:focus-visible + .track { outline: 2px solid var(--accent); outline-offset: 2px; }

  button {
    font: inherit; font-size: 14px; color: var(--fg);
    background: var(--btn); border: 1px solid var(--line2); border-radius: 6px;
    padding: 7px 14px; cursor: pointer;
  }
  button:hover { background: var(--line2); }
  button:disabled { opacity: .45; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent);
                   color: var(--on-accent); }
  button.primary:hover { background: var(--accent-h); }
  button.danger { border-color: var(--ng-line); color: var(--ng-fg); }
  .zoombtn { padding: 6px 11px; font-size: 15px; line-height: 1; }

  main {
    flex: 1 1 auto; overflow-y: auto;
    display: flex; flex-direction: column;
    padding: 18px 32px 28px; scrollbar-width: thin;
  }
  /* Captions stack from the bottom. A new line appears at the bottom and
     pushes the older lines up. */
  #lines {
    display: flex; flex-direction: column; justify-content: flex-end;
    margin-top: auto; min-height: calc(var(--lines) * var(--size) * 1.35);
  }
  .row { font-size: var(--size); line-height: 1.35; padding: 2px 0; }
  .row.en { color: var(--fg); }
  .row.ja { color: var(--ja); font-size: calc(var(--size) * .62); }
  body.hide-ja .row.ja { display: none; }
  /* A transcript line that is still growing. **Do not fade it.** Transcript
     lines are already small and already dimmed. Fading them on top of that
     makes them unreadable. The trailing … shows that the line is still
     growing. It follows the transcript toggle. */
  .row.partial::after { content: "…"; margin-left: .15em; }
  .row.enter { animation: in .18s ease-out; }
  @keyframes in { from { opacity: 0; } to { opacity: 1; } }
  #empty { color: var(--ja); font-size: 18px; }
  /* Input level. **It lets you see that sound is arriving.** */
  .meter {
    width: 110px; height: 9px; border-radius: 5px; background: var(--hover);
    border: 1px solid var(--line2); overflow: hidden; flex: 0 0 auto;
  }
  .meter > i { display: block; height: 100%; width: 0; background: var(--ok); }
  .meter.hot > i { background: var(--ng); }
  select {
    font: inherit; font-size: 13px; color: var(--fg);
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 7px 8px; flex: 1 1 100%; min-width: 0; max-width: 100%;
  }
  .netstate { color: var(--ng); font-size: 13px; }
"""

# --- Receiving captions (shared by the viewer and the control page) ---------
#
# Long polling. Fetch, draw, fetch again at once. On failure, wait one second
# and retry.

FEED_JS = """
  const $ = (id) => document.getElementById(id);
  const lines = $("lines"), main = $("main"), dot = $("dot"), count = $("count");
  const ja = $("ja"), empty0 = $("empty"), netstate = $("netstate");
  const FEED = "__FEED__", MAX = __HISTORY__, SOURCE_DEFAULT = __SOURCE_DEFAULT__;
  let n = 0, removedEmpty = false, ended = false, since = 0, fails = 0, pv = -1;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function scrollDown() { main.scrollTop = main.scrollHeight; }

  // --- Source-language toggle. Kept in localStorage ----------------------
  // **The viewer page turns it on by default.** Someone who opens the URL you
  // handed out cannot check what the speaker actually said if the page shows
  // only the translation. The control page is for the operator, so it starts
  // with the toggle off.
  const showJaStored = localStorage.getItem("showJa");
  ja.checked = showJaStored === null ? SOURCE_DEFAULT : showJaStored === "1";
  applyJa();
  ja.addEventListener("change", () => {
    localStorage.setItem("showJa", ja.checked ? "1" : "0");
    applyJa(); scrollDown();
  });
  function applyJa() { document.body.classList.toggle("hide-ja", !ja.checked); }

  // --- Text size. The reader decides ------------------------------------
  let zoom = parseFloat(localStorage.getItem("zoom") || "1") || 1;
  applyZoom();
  $("bigger").addEventListener("click", () => { zoom = Math.min(zoom * 1.15, 3); applyZoom(); });
  $("smaller").addEventListener("click", () => { zoom = Math.max(zoom / 1.15, 0.5); applyZoom(); });
  function applyZoom() {
    document.documentElement.style.setProperty("--zoom", zoom.toFixed(3));
    localStorage.setItem("zoom", String(zoom));
    scrollDown();
  }

  function add(ev) {
    if (!removedEmpty && empty0) { empty0.remove(); removedEmpty = true; }
    const div = document.createElement("div");
    div.className = "row " + (ev.type === "asr" ? "ja" : "en") + " enter";
    div.textContent = ev.text;
    lines.insertBefore(div, partialRow);
    while (lines.children.length > MAX + 1) { lines.removeChild(lines.firstChild); }
    if (ev.type !== "asr") { n += 1; count.textContent = n + " lines"; }
    scrollDown();
  }

  // --- Transcript line that is still growing ----------------------------
  // **Nothing appears for a few seconds until a sentence is final.** The
  // speaker's words are only piling up, and to the reader the page looks
  // frozen. Show what has arrived so far, and replace it with a normal line
  // once the sentence is final.
  //
  // This row always stays last. Final lines are inserted before it (see
  // `add`).
  const partialRow = document.createElement("div");
  partialRow.className = "row ja partial";
  partialRow.hidden = true;
  lines.appendChild(partialRow);

  function showPartial(text) {
    if (partialRow.textContent === text) { return; }
    partialRow.textContent = text;
    // **Remove the whole row when it is empty.** If its height stays, the
    // captions look shifted up by one line.
    partialRow.hidden = !text;
    if (text && !removedEmpty && empty0) { empty0.remove(); removedEmpty = true; }
    scrollDown();
  }

  async function feed() {
    while (!ended) {
      try {
        const r = await fetch(FEED + "?since=" + since + "&pv=" + pv);
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        const d = await r.json();
        since = d.next;
        for (const ev of d.lines) { add(ev); }
        // **Insert the final lines first, then update the growing line.**
        // The other order shows a final sentence once more, for a moment, as
        // a growing line.
        if (typeof d.pv === "number") { pv = d.pv; showPartial(d.partial || ""); }
        dot.classList.add("on");
        fails = 0;
        netstate.textContent = "";
      } catch (e) {
        dot.classList.remove("on");
        // **Do not fail silently.** Participants use different devices and
        // different networks, so nobody can find the cause unless the page
        // says what is happening.
        fails += 1;
        netstate.textContent = "接続できません（" + e.message + "）。再試行 " + fails + "回目…";
        await sleep(1000);
      }
    }
  }
  feed();
"""


def check_zoom_token(url: str) -> None:
    """Check a token that arrives from outside, more strictly than
    `captions.parse_token`.

    **Do not make `parse_token` stricter.** That function also handles the
    local control page and `--token`, and those callers are already trusted.
    Only this entry point, which is reachable from outside, needs the strict
    check.

    `parse_token` checks only three things: the scheme is http or https, the
    path contains closedcaption, and there is an `id=`. **It does not check
    the destination.** Without the check below, **anyone who gets this entry
    point's URL could paste the URL of their own server and receive the whole
    translated meeting.** This is the most serious hole in the design.
    """
    text = str(url or "").strip()
    if not text:
        raise captions_mod.TokenError("トークンが空である。")
    if len(text) > 2048:
        raise captions_mod.TokenError("トークンが長すぎる。")
    if any(ord(c) < 32 for c in text):
        raise captions_mod.TokenError("トークンに使えない文字が入っている。")

    parsed = urlparse(text)
    if parsed.scheme != "https":
        # With http, the captions travel in the clear.
        raise captions_mod.TokenError("https のトークンだけを受け付ける。")
    host = (parsed.hostname or "").lower()
    # **Do not match on a substring.** `evil.com/zoom.us/closedcaption` would
    # pass.
    if host != "zoom.us" and not host.endswith(".zoom.us"):
        raise captions_mod.TokenError(
            f"Zoom のトークンではない（宛先が {host or '不明'}）。")
    query = parse_qs(parsed.query)
    meeting = (query.get("id") or [""])[0]
    if not re.fullmatch(r"\d{9,12}", meeting or ""):
        raise captions_mod.TokenError("会議IDの形がおかしい。")
    # Finally, run the same check the rest of the app uses.
    captions_mod.parse_token(text)


# Copying a URL. **The control page and the meeting management page use the
# same code.**
#
# They used to be written separately, and the management page was missing the
# `document.execCommand` step. **Over a tailnet, copying worked on the control
# page but not on the management page** (reported 2026-09-19). Keeping the code
# in one place means a fix reaches both.
#
# There are three steps. **Try them in order.**
#   1. navigator.clipboard   works only in a secure origin (https or localhost)
#   2. document.execCommand  the old way. **It also works over http on a
#                            tailnet**
#   3. select the text and ask for Ctrl+C   the last resort
COPY_JS = """  // --- Copying a URL ------------------------------------------------------
  // **Do not make the user select the URL by hand.** A tunnel URL is often
  // pasted into a chat. With `user-select: all` alone, nothing on the page
  // tells the user that a click selects the whole text.
  //
  // `navigator.clipboard` works only in a secure origin. The control page is
  // served over http, but localhost and 127.0.0.1 count as secure origins, so
  // it works there. The fallbacks cover the cases where it still fails (an old
  // browser, or the permission turned off).
  function selectAll(el) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  async function copyText(el, btn) {
    const text = el ? el.textContent.trim() : "";
    if (!text) { return; }
    let ok = true;
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // The old way. It also fails where writing is not allowed.
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
    }
    if (!ok) {
      // **If we cannot write, at least select the text.** Then Ctrl+C is
      // enough. Do not just tell the user to select it themselves. Do not add
      // work during a meeting.
      selectAll(el);
      say("クリップボードに書けない。選んであるので Ctrl+C を押すこと。", false);
      return;
    }
    // **Show that the button was pressed.** If nothing changes, the user
    // cannot tell whether the click worked.
    const before = btn.textContent;
    btn.textContent = "コピーした";
    setTimeout(() => { btn.textContent = before; }, 1400);
  }

  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".copybtn");
    if (!btn) { return; }
    copyText(document.getElementById(btn.dataset.copy), btn);
  });"""


def _qr_filename(name: str) -> str:
    """File name for a saved QR code. It contains the meeting name.

    **Each meeting must get a different file.** The usual way to work is to
    make QR codes for several future meetings and keep them, and that does not
    work if every file is called `livecaption-qr.png`.

    Characters that a file name cannot contain, and characters that would
    break the header (quotes, newlines, non-ASCII), are dropped. A name
    written only in non-ASCII characters disappears completely, and then the
    default name is used.
    """
    safe = "".join(c for c in name if c.isascii() and (c.isalnum() or c in "-_ ")).strip()
    safe = "-".join(safe.split())[:40]
    return f"livecaption-qr-{safe}.png" if safe else "livecaption-qr.png"


def _attachment(name: str) -> str:
    """The value of `Content-Disposition`. **It keeps non-ASCII file names.**

    A record file name contains the meeting name (`transcript.safe_filename`).
    A header can carry only ASCII, so a non-ASCII name in a plain `filename=`
    either turns into garbage or is dropped, depending on the browser. RFC 5987
    `filename*` carries UTF-8. **A plain `filename=` is also sent, for old
    browsers** (with the non-ASCII characters dropped). When both are present,
    current browsers use `filename*`.
    """
    plain = "".join(c for c in name if c.isascii() and c not in '"\\\r\n') or "record"
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"


def _head(title: str, lines_: int) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>"
        + STYLE.replace("__LINES__", str(lines_))
    )


# --- Viewer page ------------------------------------------------------------
#
# **No control markup and no control JS here.** This page goes outside.

VIEWER_BODY = """</style>
</head>
<!-- The transcript is shown by default. To avoid a flash on the screen of
     someone who turned it off, hide-ja is not set here (`applyJa()` sets it
     again right after load). -->
<body>
<header>
  <span class="dot" id="dot"></span>
  <span class="title">Live Captions</span>
  <span id="count"></span>
  <span id="netstate" class="netstate"></span>
  <span class="spacer"></span>
  <button id="smaller" class="zoombtn" title="文字を小さく">A&minus;</button>
  <button id="bigger" class="zoombtn" title="文字を大きく">A+</button>
  <label class="toggle">
    <input type="checkbox" id="ja">
    <span class="track"></span>
    <!-- **This cannot say "Japanese".** When the direction is en2ja, what
         appears here is English. This toggle shows the language before
         translation, not one fixed language. -->
    <span>Original language</span>
  </label>
</header>
<main id="main">
  <div id="lines"><div id="empty">Waiting for captions…</div></div>
</main>
<script>
__FEED_JS__
</script>
</body>
</html>
"""

# --- Control page -----------------------------------------------------------

# **This is a raw string.** The content is JavaScript, so a `\n` written here
# must reach the browser as a JS escape. In a normal string, Python would
# consume it first and turn it into a real newline, and then **a JS string
# would span two lines and the whole page would stop working.** That killed
# the entire control page once (2026-09-21).
CONTROL_BODY = r"""
  /* --- Text size in the settings panel ------------------------------------
     **Decided in one place.** Sizes of 11px to 13px used to be written all
     over, and the result was too small overall. People read this during a
     meeting, so readability comes first. To make it bigger, change this one
     line. */
  :root { --ui: 15px; }

  /* --- Left/right split ---------------------------------------------------
     **The captions come first.** Stacking the settings on top pushes the
     captions down until they cannot be read. Captions go on the left,
     settings on the right, and the divider can be dragged. The width is kept
     in localStorage. */
  #split {
    flex: 1 1 auto; min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 7px var(--right, 440px);
  }
  #sep {
    background: var(--line); cursor: col-resize; position: relative;
    touch-action: none;
  }
  /* The line itself should stay thin. Only the grab area is widened. */
  #sep::after { content: ""; position: absolute; top: 0; bottom: 0; left: -5px; right: -5px; }
  #sep:hover, #sep.drag { background: var(--accent); }

  /* The caption size follows **the width of the left column**, not the
     window. It changes as you drag the divider. A browser that does not
     understand cqw ignores this whole line and keeps the vw-based size from
     :root. */
  #main { container-type: inline-size; }
  #main { --size: calc(clamp(18px, 3.4cqw, 42px) * var(--zoom, 1)); }

  /* In a narrow window, stack the two parts. The divider cannot be dragged. */
  @media (max-width: 760px) {
    #split { display: flex; flex-direction: column; }
    #sep { display: none; }
    #panel { max-height: 45vh; border-left: 0; border-top: 1px solid var(--line); }
  }

  /* --- The settings panel ------------------------------------------------ */
  #panel {
    overflow-y: auto; scrollbar-width: thin;
    padding: 14px 16px 20px;
    border-left: 1px solid var(--line); background: var(--panel);
    font-size: var(--ui); color: var(--ja);
  }
  /* Controls use the same size as the panel. **With the shared sizes (14px /
     13px) they end up smaller than the text around them, so the things you
     click are the hardest to read.** The viewer page has no `--ui`, so this
     applies only inside #panel. */
  #panel button, #panel select { font-size: var(--ui); }
  /* The tabs at the top of the panel. **They separate what you touch on the
     day from what you set up once and leave alone.** The selected tab is kept
     in localStorage, so the next start opens the same one. */
  .tabs { display: flex; gap: 6px; margin: 0 0 14px; }
  .tabs > .tab {
    flex: 1 1 0; padding: 7px 10px; color: var(--muted); background: transparent;
  }
  .tabs > .tab:hover { background: var(--hover); }
  .tabs > .tab.on { color: var(--fg); background: var(--bg);
                    border-color: var(--accent); font-weight: 600; }
  /* **Do not let a failure on the hidden tab go unnoticed.** If a meeting
     starts while the settings tab is open, a failure on the other tab would
     never be seen. */
  .tabs > .tab.alert::after { content: " ●"; color: var(--ng); }

  /* --- The meeting management tab -----------------------------------------
     **This tab alone uses the full width.** With the date and time, the Zoom
     link, three numbers and the checkboxes side by side, a 440px column fits
     only one of them per row. That is why this page was moved to a separate
     window in the first place (2026-09-19), so putting it back into the
     column as it was would undo the fix. If it becomes a tab, it has to bring
     its width along. */
  #meetFrame { display: block; width: 100%; height: 100%; border: 0; }
  /* **Do not hide the captions, and do not change the width.** An earlier
     version collapsed the whole left column, and the next one remembered a
     width per tab. Both were rejected (2026-09-20). Losing the captions in
     the middle of a meeting is bad, and so is a column that grows and shrinks
     on every click. **The content stretches to the width the user chose**
     (`meetings_page.py`). */
  body.manage #panel {
    display: flex; flex-direction: column; padding: 0; overflow: hidden;
  }
  body.manage .tabs { flex: 0 0 auto; margin: 12px 16px 0; }
  body.manage #paneMeet { flex: 1 1 auto; min-height: 0; }

  /* The list of records. **Its height is fixed, and it scrolls inside.**
     Running unattended piles up several records a day, so an unbounded list
     would push the sections below it off the screen. */
  /* The buttons that download a record. **They are links, but they look like
     the other buttons.** The file goes to the device of whoever is looking,
     so only `<a download>` can deliver it. */
  a.dl {
    color: var(--fg); background: var(--btn); border: 1px solid var(--line2);
    border-radius: 6px; padding: 5px 10px; font-size: var(--ui);
    text-decoration: none; cursor: pointer;
  }
  a.dl:hover { background: var(--hover); }
  /* When there is nothing to download, show that clicking does nothing. */
  a.dl.off { color: var(--dim); background: var(--field); cursor: default; }
  a.dl.off:hover { background: var(--field); }

  /* The three ways to show captions. Collapsed headings, stacked. */
  .grp > .fold { margin-bottom: 7px; }
  .grp > .fold:last-child { margin-bottom: 0; }

  /* Separate the groups. Without breaks, it is impossible to tell which
     setting belongs to what. */
  .grp { padding: 12px 0; border-bottom: 1px solid var(--line); }
  .grp:first-child { padding-top: 0; }
  .grp:last-child { border-bottom: 0; }
  .grp > h2 {
    margin: 0 0 9px; font-size: calc(var(--ui) - 1px); font-weight: 600;
    letter-spacing: .06em; color: var(--muted);
  }
  .row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 9px; }
  .row2:last-child { margin-bottom: 0; }
  /* An explanation row is running text. **Do not make it flex.** A `<b>`
     would become a separate item with gaps around it, and the row would no
     longer read as one sentence. */
  .row2.hint { display: block; }
  /* A label takes a whole row. In a narrow column, putting the label and the
     control side by side wraps badly. */
  .lbl { flex: 1 0 100%; color: var(--fg); font-size: var(--ui); }
  /* The glossary list stays collapsed. **The number of tables keeps growing.**
     Listing them all makes the right column taller until "Quit the app" falls
     off the screen. Even when open, its height is capped and it scrolls
     inside. */
  /* **The red color used to apply only to #msg.** Both "no sound is arriving"
     and "dropped at the limit" are written with class="ng", but neither was
     colored. These are the warnings that matter most, so make the rule apply
     everywhere. */
  .ok { color: var(--ok); }
  .ng { color: var(--ng); }
  .fold { border: 1px solid var(--line2); border-radius: 6px; background: var(--field); }
  .fold > summary {
    cursor: pointer; padding: 8px 10px; font-size: var(--ui); color: var(--fg);
    list-style: none; display: flex; align-items: center; gap: 6px;
  }
  /* **The default marker needs three ways of hiding it.** If even one remains,
     it appears next to our own triangle. Chrome reads ::marker, old WebKit
     reads ::-webkit-details-marker, and Safari reads list-style. */
  .fold > summary::marker { content: ""; }
  .fold > summary::-webkit-details-marker { display: none; }
  /* A triangle shows whether the section is open. While collapsed, nobody
     notices it otherwise.
     **It is drawn with borders, not with a character.** The size and position
     of "▸" vary with the font, and it can fall back to an emoji font. Borders
     give the same shape everywhere. */
  .fold > summary::before {
    content: ""; flex: 0 0 auto; width: 0; height: 0; margin-right: 2px;
    border-left: 5px solid var(--dim);
    border-top: 4px solid transparent;
    border-bottom: 4px solid transparent;
    transition: transform .12s;
  }
  .fold[open] > summary::before { transform: rotate(90deg); }
  .fold > summary:hover { background: var(--hover); }
  .fold .body { padding: 0 10px 8px; }
  /* Maximum height. **It follows the screen height.** A limit in rows would
     overflow on a short screen. */
  .fold .list { max-height: min(38vh, 300px); overflow-y: auto; }
  /* One glossary checkbox per row. The name and the term count do not fit
     side by side. */
  .gloss { display: flex; align-items: center; gap: 6px;
           cursor: pointer; font-size: var(--ui); padding: 4px 0; }
  .gloss input { cursor: pointer; flex: 0 0 auto; }
  /* The name and the term count are also used inside the summary, so these
     rules hang off .fold rather than .gloss. */
  .fold .n { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis;
             white-space: nowrap; }
  .fold .c { flex: 0 0 auto; color: var(--muted); font-size: calc(var(--ui) - 2px); }
  /* Highlight the selected tables. Before collapsing the list, you check what
     is ticked. */
  .gloss .n { color: var(--ja); }
  .gloss.on .n { color: var(--fg); font-weight: 600; }
  /* Latency tuning. Each row holds the name and the input field, with the
     explanation in small text underneath.
     **Putting the explanation beside the name squeezes the name until you
     cannot tell which setting it is.** */
  .tune { padding: 6px 0; border-top: 1px solid var(--line); }
  .tune:first-child { border-top: 0; }
  .tune .top { display: flex; align-items: center; gap: 8px; }
  .tune .k { flex: 1 1 auto; font-size: calc(var(--ui) - 1px); color: var(--fg);
             overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tune input[type=number] {
    font: inherit; font-size: var(--ui); color: var(--fg); width: 92px; flex: 0 0 auto;
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 5px 8px; text-align: right;
  }
  /* Highlight a value that differs from the default, so that "this was
     changed" is visible even after the section is collapsed. */
  .tune.changed input[type=number] { border-color: var(--accent); }
  .tune .d { flex: 0 0 auto; font-size: calc(var(--ui) - 2px); color: var(--muted); width: 74px; }
  .tune .h { font-size: calc(var(--ui) - 2px); color: var(--ja); line-height: 1.5; margin: 4px 0 0; }
  .fold input[type=search] {
    font: inherit; font-size: calc(var(--ui) - 1px); color: var(--fg); width: 100%;
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 5px 8px; margin: 2px 0 6px;
  }
  input[type=password], input[type=text] {
    font: inherit; font-size: var(--ui); color: var(--fg);
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 7px 10px; flex: 1 1 100%; min-width: 0;
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: 0; border-color: var(--accent); }
  #msg { font-size: calc(var(--ui) - 1px); }
  #msg.ok { color: var(--ok); }
  #msg.ng { color: var(--ng); }
  .hint { font-size: calc(var(--ui) - 2px); color: var(--muted); line-height: 1.65; }
  .url {
    font-family: ui-monospace, Consolas, monospace; font-size: calc(var(--ui) - 2px); color: var(--fg);
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 6px 8px; word-break: break-all; user-select: all; flex: 1 1 100%;
  }
  /* **A URL must be copyable with a button.** `user-select: all` does select
     everything on a click, but nothing on the page hints at that. Do not leave
     someone puzzling over "I cannot copy this" during a meeting. A tunnel URL
     is often pasted into a chat. */
  .copybtn, .savebtn { padding: 6px 12px; }
  /* The language selector. It sits in the header, so it is only as wide as
     its content. */
  .langsel {
    font: inherit; font-size: 13px; color: var(--ja);
    background: transparent; border: 1px solid var(--line2); border-radius: 6px;
    padding: 4px 6px; flex: 0 0 auto; width: auto; min-width: 0;
  }
  /* The meeting list. Each row stacks the radio button, the name, the URL and
     the buttons.
     **The URL wraps and is shown in full.** Cutting it short makes it
     impossible to check by eye.

     **The height is capped and the list scrolls inside.** The number of
     meetings only grows, so an unbounded list would push everything from
     "Zoom captions" down off the screen. This works like the glossary list. */
  #meetList {
    display: flex; flex-direction: column; gap: 8px; margin: 6px 0 2px;
    max-height: min(38vh, 300px); overflow-y: auto; scrollbar-width: thin;
    /* This makes the `offsetTop` of the rows inside relative to this box,
       which is how the selected row is scrolled into view. */
    position: relative;
  }
  /* Show the top and bottom rules only when the list can scroll. Without
     them, nobody notices that there is more. */
  #meetList.more { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
                   padding: 6px 4px 6px 0; }
  /* Show the count next to the heading, so the number is visible even while
     the list is collapsed. */
  .grp > h2 .c { font-weight: 400; letter-spacing: 0; color: var(--dim); }
  .meet { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
          display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
  .meet.on { border-color: var(--accent); }
  .meet input[type=radio] { cursor: pointer; flex: 0 0 auto; }
  .meet .nm { flex: 1 1 auto; font-size: var(--ui); color: var(--ja);
              overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meet.on .nm { color: var(--fg); font-weight: 600; }
  .meet .when { flex: 0 0 auto; font-size: calc(var(--ui) - 3px); color: var(--muted); }
  .meet .url { flex: 1 1 100%; }
  .meet .none { flex: 1 1 100%; font-size: calc(var(--ui) - 2px); color: var(--muted); }
  /* A summary of the schedule, in small text under the name inside the row. */
  .meet .when2 { flex: 1 1 100%; font-size: calc(var(--ui) - 3px); color: var(--muted); }
  .meet.armed .when2 { color: var(--ok); }
  /* The schedule input fields. **They open inside the row.** This page has no
     overlay windows at all, so do not make this one place different. */
  .sched { flex: 1 1 100%; border-top: 1px solid var(--line); margin-top: 4px;
           padding-top: 8px; display: flex; flex-wrap: wrap; gap: 6px 8px; }
  .sched label { font-size: calc(var(--ui) - 2px); color: var(--ja);
                 display: flex; align-items: center; gap: 5px; }
  .sched input[type=datetime-local], .sched input[type=text] {
    font: inherit; font-size: calc(var(--ui) - 1px); color: var(--fg);
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 5px 8px; min-width: 0;
  }
  .sched input[type=number] {
    font: inherit; font-size: calc(var(--ui) - 1px); color: var(--fg); width: 68px;
    background: var(--field); border: 1px solid var(--line2); border-radius: 6px;
    padding: 5px 6px; text-align: right;
  }
  .sched .wide { flex: 1 1 100%; }
  /* **Make the auto-start checkbox stand out.** Ticking it starts an
     unattended delivery to the outside. */
  .sched .auto { flex: 1 1 100%; color: var(--fg); font-size: var(--ui); }
  .sched .auto input { cursor: pointer; }
  /* The host URL stays collapsed. **The worst mistake is handing it out by
     mistake instead of the participant URL.** */
  .hostrow { flex: 1 1 100%; display: flex; flex-wrap: wrap; gap: 6px 8px;
             align-items: center; border-top: 1px solid var(--line);
             margin-top: 6px; padding-top: 8px; }
  .hostrow .warn2 { flex: 1 1 100%; font-size: calc(var(--ui) - 3px); color: var(--ng); }
  .hostrow .url { border-color: var(--ng-line); }
  /* The list of upcoming meetings. */
  #schedNext { display: flex; flex-direction: column; gap: 4px; margin: 4px 0 8px; }
  #schedNext .row { font-size: calc(var(--ui) - 2px); color: var(--ja);
                    display: flex; gap: 8px; }
  #schedNext .row .t { color: var(--fg); flex: 0 0 auto; }
  #schedNext .row .n { flex: 1 1 auto; overflow: hidden;
                       text-overflow: ellipsis; white-space: nowrap; }
  #schedFailRow { align-items: flex-start; }
  /* Some devices cannot read a QR code unless the background is white. Make
     the padding white as well. */
  #qrbox { display: none; }
  #qrbox.on { display: flex; }
  #qr { background: #fff; padding: 8px; border-radius: 8px; width: 150px; height: 150px; }
  pre.err {
    white-space: pre-wrap; font-size: calc(var(--ui) - 2px); color: var(--ng-fg);
    background: var(--ng-bg); border: 1px solid var(--ng-line); border-radius: 6px;
    padding: 8px 10px; margin: 0; max-height: 9em; overflow: auto;
  }
</style>
</head>
<body class="hide-ja">
<header>
  <span class="dot" id="dot"></span>
  <span class="title">Live Captions ・ 操作</span>
  <span id="count"></span>
  <span id="netstate" class="netstate"></span>
  <span class="spacer"></span>
  <span class="pill off" id="genPill">生成: —</span>
  <span class="pill off" id="tunnelPill">配信: —</span>
  <span class="pill off" id="zoomPill">Zoom: —</span>
  <button id="smaller" class="zoombtn" title="文字を小さく">A&minus;</button>
  <button id="bigger" class="zoombtn" title="文字を大きく">A+</button>
  <!-- Language names are not translated. **People find their own language
       faster when it is written in that language.** -->
  <select id="uiLang" class="langsel" title="Language">
    <option value="ja">日本語</option>
    <option value="en">English</option>
  </select>
  <!-- **This cannot say "Japanese".** When the direction is en2ja, what
       appears here is English. This toggle shows the language before
       translation, not one fixed language. -->
  <label class="toggle" title="訳す前の言葉を、訳文の上に小さく出す">
    <input type="checkbox" id="ja">
    <span class="track"></span>
    <span>元の言語を表示</span>
  </label>
</header>

<div id="split">

<main id="main">
  <div id="lines"><div id="empty">字幕を待っています…</div></div>
</main>

<div id="sep" title="ドラッグで幅を変える"></div>

<aside id="panel">

  <!-- **Separate what you touch on the day from what you set up once and
       leave alone.** With twelve headings in one column, it was impossible to
       tell which ones are needed during a meeting (reported 2026-09-20). -->
  <div class="tabs">
    <button class="tab on" id="tabRun">この会議</button>
    <button class="tab" id="tabMeet">会議の管理</button>
    <button class="tab" id="tabSet">設定</button>
  </div>

<div id="paneRun">

  <div class="grp" id="schedGrp">
    <h2>いまの状態</h2>
    <div class="row2" id="schedFailRow" style="display:none">
      <pre class="err" id="schedFail"></pre>
      <button id="schedAck">了解</button>
    </div>
    <div class="row2">
      <button id="gstart" class="primary">開始</button>
      <button id="gstop" class="danger" title="配信とZoom字幕も一緒に止まる">停止</button>
      <span id="genState"></span>
    </div>
    <!-- **The level meter belongs here, not in the input device section.**
         People watch it during a meeting, so it must not be hidden away on
         the settings tab. -->
    <div class="row2">
      <span class="meter" id="meter"><i id="meterBar"></i></span>
      <span id="audioState"></span>
    </div>
    <div class="row2" id="audioErrBox" style="display:none">
      <pre class="err" id="audioErr"></pre>
    </div>
    <div class="row2 hint" id="genHint">
      開始するまで、<b>音は取り込まれず、認識も翻訳もしない。</b>
      会議に入る前に立ち上げておいてよい。
    </div>
    <div class="row2"><span id="schedState"></span></div>
    <div class="list" id="schedNext"></div>
    <div class="row2">
      <button id="schedStop" class="danger">いま止める</button>
      <button id="schedSkip">次の予定を飛ばす</button>
    </div>
  </div>

  <div class="grp">
    <h2>配信する会議<span class="c" id="meetCount"></span></h2>
    <div class="row2">
      <select id="meetPick"></select>
    </div>
    <div class="row2 hint" id="meetWhen"></div>
    <!-- **Run this meeting once, without waiting for its schedule.** The
         steps are the same as for a scheduled run (delivery, join Zoom,
         generation, chat). Do not fold this into "Start". That button only
         captures audio and recognizes it; it opens no output (2026-09-20). -->
    <div class="row2">
      <button id="meetStart" class="primary">この会議をいま始める</button>
      <span class="hint" id="meetStartWhat"></span>
    </div>
    <div class="row2" id="meetUrlRow" style="display:none">
      <span class="url" id="meetUrl"></span>
      <button class="copybtn" data-copy="meetUrl">URLをコピー</button>
      <button class="savebtn" id="meetQr">QRコードを保存</button>
    </div>
    <div class="row2">
      <label class="lbl" for="dirSel">字幕の向き</label>
      <select id="dirSel"><option>読み込み中…</option></select>
    </div>
    <div class="row2">
      <span id="dirState"></span>
    </div>
    <div class="row2 hint">
      予定の入力・追加・削除は、上の「会議の管理」で行う。
      <b>向きは選んだ時点で切り替わる。</b>
      逆の言語が混ざったときは、訳さずにそのまま出す。
    </div>
  </div>

  <!-- **Put the three outputs in one group.** They used to be far apart, and
       nothing showed that they can all run at once. The state of each one
       appears to the right of its collapsed heading. -->
  <div class="grp">
    <h2>見せ方</h2>
    <div class="row2 hint">3つとも同時に使える。</div>

    <details class="fold" id="wayNet" open>
      <summary><span class="n">ブラウザで見てもらう</span><span class="c" id="wayNetState"></span></summary>
      <div class="body">
        <div class="row2">
          <label class="lbl" for="tkind">経路</label>
          <select id="tkind">
            <option value="cloudflare">Cloudflare（その場で配る）</option>
            <option value="tailscale">Tailscale（前もって配る）</option>
          </select>
        </div>
        <div class="row2 hint" id="tkindHint"></div>
        <div class="row2">
          <button id="tstart" class="primary">配信を開始</button>
          <button id="tstop" class="danger">停止</button>
        </div>
        <div class="row2"><span id="tunnelState"></span></div>
        <div class="row2" id="qrbox">
          <img id="qr" alt="閲覧URLのQRコード">
        </div>
        <div class="row2" id="publicUrlRow" style="display:none">
          <span class="url" id="publicUrl"></span>
          <button class="copybtn" data-copy="publicUrl">URLをコピー</button>
          <button class="savebtn" id="qrsave">QRコードを保存</button>
        </div>
        <div class="row2 hint" id="tunnelHint" style="display:none">
          このURLをQRで配る。参加者はブラウザで開くだけでよい。
        </div>
        <div class="row2">
          <button id="chatPost">Zoomのチャットに投げる</button>
          <span id="chatState"></span>
        </div>
        <div class="row2 hint">
          いま入っている会議のチャットに、URLとQRを投げる。
          <b>参加者全員に見える。</b>予定の会議で毎回投げるなら、会議の管理で印を付ける。
        </div>
        <div class="row2" id="tunnelErrBox" style="display:none">
          <pre class="err" id="tunnelErr"></pre>
        </div>
      </div>
    </details>

    <details class="fold" id="wayShare">
      <summary><span class="n">閲覧画面を自分で開く</span><span class="c"></span></summary>
      <div class="body">
        <div class="row2">
          <span class="url" id="viewerUrl"></span>
        </div>
        <div class="row2">
          <button id="openViewer">閲覧画面を開く</button>
          <button class="copybtn" data-copy="viewerUrl">URLをコピー</button>
        </div>
        <div class="row2 hint">
          <b>外には出ない。</b>自分で見るか、この画面を全画面にして画面共有する。
          未公開の結果を扱う会議は、配信せずにこれで見せる。
        </div>
        <div class="row2 hint">
          <b>操作画面のほうは共有しないこと。</b>共有するのは閲覧画面である。
        </div>
      </div>
    </details>

    <details class="fold" id="wayZoom">
      <summary><span class="n">Zoomの字幕に流す</span><span class="c" id="wayZoomState"></span></summary>
      <div class="body">
        <div class="row2">
          <label class="lbl" for="token">APIトークン</label>
          <input type="password" id="token" placeholder="https://....zoom.us/closedcaption?id=..."
                 autocomplete="off" spellcheck="false">
          <button id="save" class="primary">登録</button>
        </div>
        <div class="row2">
          <button id="start" class="primary">開始</button>
          <button id="stop" class="danger">停止</button>
          <span id="zoomState"></span>
        </div>
        <div class="row2 hint">
          会議中にホストが取る。「字幕」→「∧」→「手動字幕の設定」で<b>手動字幕を有効にしてから、</b>
          「APIトークンをコピー」。<b>有効にしないとこの項目は出ない。</b><br>
          入力欄は伏せ字で、登録すると空になる。
        </div>
      </div>
    </details>
  </div>

  <div class="grp">
    <h2>会議の記録</h2>
    <div class="row2">
      <span id="logState"></span>
    </div>
    <!-- **Records pile up on the caption PC and are downloaded from here.**
         The caption PC runs all the time and is operated over a tailnet.
         Nobody should have to start RustDesk just to read a record.
         **Only the latest record can be downloaded.** An older record is
         almost never needed. -->
    <div class="row2">
      <a class="dl" id="recMd" download>読める形 (.md)</a>
      <a class="dl" id="recJsonl" download>原本 (.jsonl)</a>
    </div>
    <div class="row2 hint" id="recLatest"></div>
    <div class="row2 hint">
      記録は字幕PCの中に溜まる。<b>落とせるのは最新の1本である。</b>
      置き場を変えるなら .env の LIVECAPTION_SAVE_DIR。
    </div>
  </div>

</div>

<!-- **The content is `/meetings`, embedded as it is.** Do not build it twice.
     Inlining it would make `$`, `#msg` and `#list` collide with the control
     page. -->
<div id="paneMeet" hidden>
  <iframe id="meetFrame" title="会議の管理"></iframe>
</div>

<div id="paneSet" hidden>

  <div class="grp">
    <h2>音声の入力</h2>
    <div class="row2">
      <select id="devices"><option>読み込み中…</option></select>
    </div>
    <div class="row2">
      <button id="devReload">一覧を更新</button>
    </div>
    <div class="row2 hint">音量メーターは「この会議」の側に出る。</div>
  </div>

  <div class="grp" id="vncGrp" style="display:none">
    <h2>VNC</h2>
    <div class="row2">
      <button id="vncOn">起動</button>
      <button id="vncOff">停止</button>
      <span id="vncState"></span>
    </div>
    <div class="row2 hint" id="vncHint"></div>
  </div>

  <div class="grp">
    <h2>用語集</h2>
    <details class="fold" id="glossFold">
      <summary id="glossSummary">読み込み中…</summary>
      <div class="body">
        <div id="glossFilterRow" style="display:none">
          <input type="search" id="glossFilter" placeholder="名前で絞り込む">
        </div>
        <div class="list" id="glossBox"></div>
        <div class="row2" style="margin:8px 0 0">
          <button id="glossAll">全部選ぶ</button>
          <button id="glossNone">全部外す</button>
        </div>
        <div class="row2" style="margin:8px 0 0">
          <input type="file" id="glossFile" accept=".tsv,text/tab-separated-values,text/plain">
          <button id="glossUp">アップロード</button>
        </div>
        <div class="row2 hint">同じ名前があれば置き換える。ファイル名が表の名前になる。
          いま使っている表を置き換えると、その場で読み直す。</div>
      </div>
    </details>
    <div class="row2">
      <span id="glossState"></span>
    </div>
  </div>

  <div class="grp">
    <h2>遅延の調整</h2>
    <details class="fold" id="tuneFold">
      <summary id="tuneSummary">よく変えるものではない</summary>
      <div class="body">
        <div id="tuneBox"></div>
        <div class="row2" style="margin:8px 0 0">
          <button id="tuneSave" class="primary">.env に保存</button>
          <button id="tuneReset">既定に戻す</button>
        </div>
        <div class="hint" id="tuneEnv"></div>
      </div>
    </details>
    <div class="row2">
      <span id="tuneState"></span>
    </div>
  </div>

  <div class="grp">
    <h2 id="quitTitle">アプリの終了</h2>
    <div class="row2">
      <button id="quit" class="danger">終了</button>
      <span class="hint" id="quitHint">音声の取り込みも文字起こしも止まる</span>
    </div>
    <div class="row2"><span id="msg"></span></div>
  </div>

</div>

</aside>
</div>

<script>
__FEED_JS__

  const token = $("token"), save = $("save"), start = $("start"), stop = $("stop");
  const glossBox = $("glossBox"), glossState = $("glossState");
  const glossFold = $("glossFold"), glossSummary = $("glossSummary");
  const glossFilter = $("glossFilter"), glossFilterRow = $("glossFilterRow");
  const glossAll = $("glossAll"), glossNone = $("glossNone");
  const glossFile = $("glossFile"), glossUp = $("glossUp");
  const vncGrp = $("vncGrp"), vncOn = $("vncOn"), vncOff = $("vncOff");
  const vncState = $("vncState"), vncHint = $("vncHint");
  let glossLoaded = false;
  const dirSel = $("dirSel"), dirState = $("dirState");
  let dirLoaded = false;
  const tuneBox = $("tuneBox"), tuneState = $("tuneState"), tuneEnv = $("tuneEnv");
  const tuneFold = $("tuneFold"), tuneSummary = $("tuneSummary");
  const tuneSave = $("tuneSave"), tuneReset = $("tuneReset");
  let tuneLoaded = false;
  // Show the filter box only when there are more tables than this. With a few
  // tables it only gets in the way.
  const GLOSS_FILTER_FROM = 8;
  const gstart = $("gstart"), gstop = $("gstop");
  const gpill = $("genPill"), genState = $("genState");
  const devices = $("devices"), devReload = $("devReload");
  const meter = $("meter"), meterBar = $("meterBar"), audioState = $("audioState");
  const aerrBox = $("audioErrBox"), aerr = $("audioErr");
  let devLoaded = false;
  const quit = $("quit"), tstart = $("tstart"), tstop = $("tstop");
  const quitTitle = $("quitTitle"), quitHint = $("quitHint");
  let restartsShown = false;
  const msg = $("msg"), pill = $("zoomPill"), zoomState = $("zoomState");
  const tpill = $("tunnelPill"), tstate = $("tunnelState");
  const wayNetState = $("wayNetState"), wayZoomState = $("wayZoomState");
  const qrbox = $("qrbox"), qr = $("qr"), publicUrl = $("publicUrl");
  const qrsave = $("qrsave");
  const tkind = $("tkind"), tkindHint = $("tkindHint");
  const meetPick = $("meetPick"), meetCount = $("meetCount");
  const meetStart = $("meetStart"), meetStartWhat = $("meetStartWhat");
  const meetWhen = $("meetWhen"), meetUrl = $("meetUrl");
  const meetUrlRow = $("meetUrlRow"), meetQr = $("meetQr");
  const schedState = $("schedState"), schedNext = $("schedNext");
  const schedFail = $("schedFail"), schedFailRow = $("schedFailRow");
  const schedAck = $("schedAck"), schedStop = $("schedStop"), schedSkip = $("schedSkip");
  // Rebuilding the list loses a half-typed name and the click position. Draw
  // it only when the content changed.
  let meetSeen = "";
  const publicUrlRow = $("publicUrlRow"), tunnelHint = $("tunnelHint");
  const split = $("split"), sep = $("sep");
  const viewerUrl = $("viewerUrl"), openViewer = $("openViewer");
  const terrBox = $("tunnelErrBox"), terr = $("tunnelErr");
  const logState = $("logState");
  let lastQr = "";

  function say(text, ok) { msg.textContent = text; msg.className = ok ? "ok" : "ng"; }

  // --- The divider between left and right ---------------------------------
  // **The captions are the main thing; the settings come second.** The user
  // sets the width, and it is kept in localStorage. The default width grew
  // along with the text size (keep it equal to --right in the CSS).
  const PANEL_MIN = 280, MAIN_MIN = 280, PANEL_DEFAULT = 440;
  // **The width does not change when you switch tabs** (as requested
  // 2026-09-20). For a while it was remembered per tab, but a column that
  // grows and shrinks on every click is unsettling. When the meeting
  // management tab needs more room, drag the divider; its content stretches
  // to fit.
  // **Keep the width the user chose separate from the width we can show now.**
  // Overwriting the chosen width with a shrunken one, while the window is
  // temporarily narrow, would stop it from returning when the window grows
  // again.
  let wantW = parseInt(localStorage.getItem("panelW") || "", 10) || PANEL_DEFAULT;

  function fits(w) {
    const vw = window.innerWidth;
    // **Do not clamp while the window width is unknown.** Called before the
    // first paint, this can return 0, and clamping then leaves the panel
    // stuck at its minimum width. At 760px or less the layout stacks, so do
    // not clamp there either.
    if (!vw || vw <= 760) { return Math.max(w, PANEL_MIN); }
    const room = Math.max(PANEL_MIN, vw - MAIN_MIN);
    return Math.min(Math.max(w, PANEL_MIN), room);
  }
  function applySplit() {
    split.style.setProperty("--right", fits(wantW) + "px");
    scrollDown();
  }
  applySplit();
  window.addEventListener("resize", applySplit);
  // The width can become known only once the page is shown (a hidden tab, or
  // right after opening).
  if (window.ResizeObserver) { new ResizeObserver(applySplit).observe(document.body); }

  sep.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    sep.setPointerCapture(e.pointerId);
    sep.classList.add("drag");
    const move = (ev) => {
      // The panel width is the distance from the right edge of the divider to
      // the right edge of the window.
      wantW = fits(window.innerWidth - ev.clientX - 4);
      applySplit();
    };
    const up = () => {
      sep.classList.remove("drag");
      sep.removeEventListener("pointermove", move);
      sep.removeEventListener("pointerup", up);
      sep.removeEventListener("pointercancel", up);
      localStorage.setItem("panelW", String(wantW));
    };
    sep.addEventListener("pointermove", move);
    sep.addEventListener("pointerup", up);
    sep.addEventListener("pointercancel", up);
  });
  // A quick way back to the default.
  sep.addEventListener("dblclick", () => {
    wantW = PANEL_DEFAULT;
    applySplit();
    localStorage.setItem("panelW", String(wantW));
  });

  // --- Panel tabs ---------------------------------------------------------
  // **Only the settings side may be hidden.** What you watch during a meeting
  // (state, level, upcoming meetings) has to stay visible whichever tab is
  // open, so all of it lives on the meeting tab.
  const TABS = {
    run:  [$("tabRun"),  $("paneRun")],
    meet: [$("tabMeet"), $("paneMeet")],
    set:  [$("tabSet"),  $("paneSet")],
  };
  const meetFrame = $("meetFrame");
  function showTab(which) {
    if (!TABS[which]) { which = "run"; }
    Object.keys(TABS).forEach((k) => {
      const [btn, pane] = TABS[k];
      pane.hidden = (k !== which);
      btn.classList.toggle("on", k === which);
    });
    // The management content stretches to fill the column. **The width itself
    // is left alone.**
    document.body.classList.toggle("manage", which === "meet");
    // **Do not load it until it is opened.** Do not add another poll every
    // three seconds on the screen of someone who never uses this tab. Once
    // loaded, leave it loaded.
    if (which === "meet" && !meetFrame.src) { meetFrame.src = "/meetings"; }
    localStorage.setItem("panelTab", which);
  }
  Object.keys(TABS).forEach((k) => {
    TABS[k][0].addEventListener("click", () => showTab(k));
  });
  showTab(localStorage.getItem("panelTab") || "run");

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { throw new Error(data.error || ("HTTP " + res.status)); }
    return data;
  }

  function showStatus(s) {
    // --- VNC. It can be started and stopped during a meeting ---
    showVnc(s.vnc);

    // --- "Quit" means something different when the app comes back ---
    // Under Docker, compose starts it again, so it returns in a dozen seconds.
    // **If it comes back on its own while the button says "Quit", whoever
    // pressed it thinks something is broken.**
    if (s.restarts && !restartsShown) {
      restartsShown = true;
      quitTitle.textContent = "アプリの再起動";
      quit.textContent = "再起動";
      quitHint.textContent =
        "止まったあと、十数秒で戻ってくる。様子がおかしいときに使う";
    }

    // --- Caption generation. **This is the master switch.** ---
    gpill.textContent = s.generating ? "生成: 中" : "生成: 停止中";
    gpill.className = "pill " + (s.generating ? "on" : "off");
    genState.textContent = s.generating
      ? "音を取り込み、文字起こしと翻訳をしている"
      : "止まっている。音は取り込んでいない";
    gstart.disabled = !!s.generating;
    gstop.disabled = !s.generating;
    // Once it runs, hide the note about how to start. Keep the page clear.
    $("genHint").style.display = s.generating ? "none" : "";

    // --- Audio input ---
    const a = s.audio || {};
    // The meter moves only while generating. When stopped, the device is
    // closed.
    const lv = Math.min(Number(a.level || 0), 1);
    meterBar.style.width = (lv * 100).toFixed(0) + "%";
    meter.classList.toggle("hot", lv > 0.95);
    if (!a.selectable) {
      audioState.textContent = a.name || "—";
      devices.disabled = true; devReload.disabled = true;
    } else if (!s.generating) {
      audioState.textContent = "停止中（音量は生成中に出る）";
    } else if (lv < 0.005) {
      // The worst case is silence that nobody notices. Say it plainly.
      audioState.innerHTML = '<span class="ng">音が来ていない</span>';
    } else {
      audioState.textContent = "音が来ている（peak " + lv.toFixed(2) + "）"
        + (a.dropped ? "　取りこぼし " + a.dropped : "");
    }
    if (a.error) { aerr.textContent = a.error; aerrBox.style.display = ""; }
    else { aerrBox.style.display = "none"; }
    // Fetch each list only once. Do not replace the options while a select
    // box is open.
    // **Fetch it even when the device cannot be chosen.** Otherwise the
    // "loading" text stays on the screen.
    if (!devLoaded) { loadDevices(); }
    if (!dirLoaded) { loadDirection(); }
    if (!glossLoaded) { loadGlossary(); }
    if (!tuneLoaded) { loadTuning(); }

    // --- Zoom ---
    let label, cls;
    if (s.dry_run)        { label = "Zoom: --dry-run"; cls = "off"; }
    else if (s.active)    { label = "Zoom: 送信中";    cls = "on";  }
    else if (s.has_token) { label = "Zoom: 停止中";    cls = "off"; }
    else                  { label = "Zoom: 未登録";    cls = "off"; }
    // Do not hide failures. An expired token only gives a non-200 answer, so
    // unless it appears here, nobody notices that the captions are not
    // arriving.
    if (s.failed > 0) { label += "（失敗 " + s.failed + "）"; cls = "bad"; }
    pill.textContent = label; pill.className = "pill " + cls;
    // Make the running state readable even while the section is collapsed.
    wayZoomState.textContent = s.dry_run ? "--dry-run"
                             : s.active ? "送信中"
                             : s.has_token ? "登録済み" : "";

    const parts = [];
    parts.push(s.has_token ? ("登録済み（会議 " + s.meeting + "）") : "トークン未登録");
    if (s.has_token) { parts.push("seq " + s.seq); parts.push("送信 " + s.sent + " / 失敗 " + s.failed); }
    // If generation is stopped, turning Zoom sending on produces no caption
    // at all. Say so.
    if (s.active && !s.generating) { parts.push("**生成が止まっているので何も流れない**"); }
    zoomState.textContent = parts.join("　");

    start.disabled = s.dry_run || !s.has_token || s.active;
    stop.disabled = !s.active;
    save.disabled = s.dry_run;

    // --- Delivery ---
    const t = s.tunnel || {};
    const st = t.state || "off";
    const ts = (t.kind === "tailscale");
    const names = { off: "配信: 停止中", starting: "配信: 起動中…", on: "配信: 中", error: "配信: 失敗" };
    const cls2 = { off: "off", starting: "warn", on: "on", error: "bad" };
    tpill.textContent = names[st] || "配信: —";
    tpill.className = "pill " + (cls2[st] || "off");
    wayNetState.textContent = st === "on" ? "配信中"
                            : st === "starting" ? "起動中"
                            : st === "error" ? "配信の失敗" : "";
    // The route can be changed only while delivery is stopped. Switching it
    // while a tunnel is up leaves a tunnel that cannot be closed.
    if (document.activeElement !== tkind && t.kind) { tkind.value = t.kind; }
    tkind.disabled = (st === "on" || st === "starting");
    tkindHint.innerHTML = ts
      ? "ホスト名が変わらないので、<b>会議のURLを前もって配れる。</b>tailnet 側の設定が1回だけ要る。"
      : "準備は要らないが、<b>URLは起動のたびに変わる。</b>前もって配ることはできない。";
    tstate.textContent = st === "on" ? "参加者が閲覧URLを開ける"
                       : st === "starting" ? (ts ? "tailscale に設定させている" : "cloudflared を起こしている")
                       : t.available ? "" : (ts ? "tailscale が使えない" : "cloudflared が無い");
    tstart.disabled = (st === "on" || st === "starting");
    tstop.disabled = (st === "off" || st === "error");

    if (st === "on" && s.public_url) {
      publicUrl.textContent = s.public_url;
      qrbox.classList.add("on");
      publicUrlRow.style.display = ""; tunnelHint.style.display = "";
      if (s.public_url !== lastQr) { qr.src = "/api/qr?t=" + encodeURIComponent(s.public_url); lastQr = s.public_url; }
    } else {
      qrbox.classList.remove("on"); lastQr = "";
      publicUrlRow.style.display = "none"; tunnelHint.style.display = "none";
    }
    if (t.error) { terr.textContent = t.error; terrBox.style.display = ""; }
    else { terrBox.style.display = "none"; }

    // If anything failed on the meeting tab, mark the tab, so that it is
    // noticed even while the settings tab is open.
    const bad = !!(a.error || t.error || s.failed > 0 || (s.schedule || {}).failure);
    tabRun.classList.toggle("alert", bad);

    // --- Schedule ---
    drawSchedule(s.schedule || {});

    // --- Meetings ---
    drawMeetings(s.meetings || {}, t, s.schedule || {});

    viewerUrl.textContent = s.viewer_url || "";

    // --- Records ---
    // **The readable form (.md) is written when the app stops.** Sometimes
    // you want to read it while the meeting runs, and then you get it from
    // here. If writing failed, do not hide that.
    //
    // **Do not show the path inside the caption PC** (as requested
    // 2026-09-20). The record is downloaded from the links below, so where it
    // sits on disk does not concern the reader.
    const g = s.transcript || {};
    if (!g.on) {
      logState.textContent = "残さない（--no-save）";
    } else if (g.error) {
      logState.innerHTML = '<span class="ng">残せていない: ' + g.error + "</span>";
    } else if (g.count > 0) {
      logState.textContent = g.count + " 文を記録した（終了時に読める形も書く）";
    } else {
      // **Do not write "0 sentences recorded".** The latest record is shown
      // just below, so a 0 next to it would be mistaken for that record being
      // empty.
      logState.textContent = "";
    }
    // **Ask again when the first sentence arrives, and when the record file
    // changes.** Both are moments where "the latest one" becomes something
    // else.
    const key = (g.path || "") + (g.count > 0 ? ":1" : ":0");
    if (key !== recKey) { recKey = key; loadLatest(); }
  }
  // --- Post to the Zoom chat ---------------------------------------------
  // **This works by driving the Zoom window.** Zoom has no API for posting
  // into the chat of a running meeting. The result of the click is shown on
  // the spot. Whether it arrived cannot be seen from here.
  const chatPost = $("chatPost"), chatState = $("chatState");
  chatPost.addEventListener("click", async () => {
    chatPost.disabled = true;
    chatState.textContent = "投げている…";
    try {
      const st = await post("/api/chat", {});
      showStatus(st);
      const c = st.chat || {};
      chatState.textContent = !c.text ? ""
        : c.files ? "URLとQRを投げた" : "URLを投げた（QRは送れなかった）";
      if (!c.text) {
        chatState.innerHTML = '<span class="ng">' + (c.why || "投げられない") + "</span>";
      }
    } catch (e) {
      chatState.innerHTML = '<span class="ng">' + e.message + "</span>";
    }
    chatPost.disabled = false;
  });

  // --- Downloading the latest record --------------------------------------
  // **The records live on the caption PC.** It is operated remotely, so
  // without a download here, getting a file would mean starting RustDesk.
  //
  // **Only the latest record can be downloaded.** The server decides which
  // one, so the page never sends a string that names a file.
  //
  // This asks the server only on load and when the record file changes.
  // **It is not part of the two-second status.** There is no reason to read
  // the disk that often.
  const recMd = $("recMd"), recJsonl = $("recJsonl"), recLatest = $("recLatest");
  let recKey = null;

  function recReady(on) {
    [recMd, recJsonl].forEach((a) => {
      a.classList.toggle("off", !on);
      if (on) { a.removeAttribute("aria-disabled"); }
      else { a.setAttribute("aria-disabled", "true"); }
    });
  }

  async function loadLatest() {
    try {
      const r = await fetch("/api/records/latest");
      const d = await r.json();
      const it = d.item;
      if (!it) {
        recReady(false);
        recLatest.textContent = d.saving ? "まだ記録が無い。" : "残さない（--no-save）";
        return;
      }
      recReady(true);
      recLatest.textContent = "最新の記録: "
        + (it.label ? it.label + "　" : "") + it.when
        + "　確定した文: " + it.lines;
    } catch (e) {
      recReady(false);
      recLatest.innerHTML = '<span class="ng">記録が読めない: ' + e.message + "</span>";
    }
  }

  // Do not download while the link is disabled. **Never hand over an empty
  // file.**
  [recMd, recJsonl].forEach((a) => a.addEventListener("click", (e) => {
    if (a.classList.contains("off")) { e.preventDefault(); }
  }));
  recMd.href = "/api/records/file?fmt=md";
  recJsonl.href = "/api/records/file?fmt=jsonl";
  recReady(false);
  loadLatest();

  // --- Caption generation -------------------------------------------------
  async function setGen(on) {
    const b = on ? gstart : gstop;
    b.disabled = true;
    try {
      const st = await post("/api/engine", { on: on });
      showStatus(st);
      // **Say what was actually stopped, every time.** Stopping closes the
      // outputs as well as generation.
      const al = st.also || {};
      say(on ? "字幕の生成を開始した。"
             : (al.tunnel && al.zoom) ? "字幕の生成・配信・Zoom字幕を止めた。"
             : al.tunnel ? "字幕の生成と配信を止めた。閲覧URLは死んだ。"
             : al.zoom ? "字幕の生成とZoom字幕を止めた。"
             : "字幕の生成を停止した。", true);
    } catch (e) { say(String(e.message), false); b.disabled = false; }
  }
  gstart.addEventListener("click", () => setGen(true));
  gstop.addEventListener("click", () => setGen(false));

  // --- Audio input --------------------------------------------------------
  async function loadDevices() {
    devLoaded = true;
    try {
      const r = await fetch("/api/devices");
      const d = await r.json();
      if (!d.selectable) {
        devices.innerHTML = "";
        devices.appendChild(new Option(d.name || "選べない", ""));
        devices.disabled = true;
        return;
      }
      devices.innerHTML = "";
      // The same name appears under MME, DirectSound and WASAPI, so show the
      // host API as well.
      for (const dev of d.devices) {
        const label = dev.index + ": " + dev.name + "（" + dev.api + "、" + dev.channels + " ch）";
        devices.appendChild(new Option(label, String(dev.index)));
      }
      devices.value = d.index === null ? "" : String(d.index);
      devices.disabled = false;
    } catch (e) {
      devLoaded = false;
      say("入力の一覧を取れない: " + e.message, false);
    }
  }

  devReload.addEventListener("click", () => { loadDevices(); say("一覧を取り直した。", true); });

  // Switch as soon as the choice is made. There is no "Apply" button. If
  // someone forgot to press it during a meeting, they would not notice that
  // no sound is coming from the device they thought they had chosen.
  devices.addEventListener("change", async () => {
    devices.disabled = true;
    try {
      const s = await post("/api/device", { index: Number(devices.value) });
      showStatus(s);
      say("入力を " + (s.audio ? s.audio.name : "") + " にした。", true);
    } catch (e) {
      say(String(e.message), false);
      // If the switch failed, put the select box back to the device actually
      // in use. If only the select box changed, nobody can tell which device
      // is recording.
      await loadDevices();
      return;
    }
    devices.disabled = false;
  });

  // --- Glossary -----------------------------------------------------------
  // **Each meeting has its own vocabulary.** Stack only the tables you need.
  // One big table used for every meeting fills the recognizer's keyword
  // budget with unrelated terms.
  function showGlossary(g) {
    const sel = new Set(g.selected || []);
    const sets = g.sets || [];
    glossBox.innerHTML = "";
    if (!sets.length) {
      glossBox.textContent = "etc/glossary/ に .tsv が無い";
    } else {
      for (const s of sets) {
        const lab = document.createElement("label");
        lab.className = "gloss" + (sel.has(s.name) ? " on" : "");
        lab.dataset.name = s.name.toLowerCase();
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = s.name; cb.checked = sel.has(s.name);
        // Switch as soon as the choice is made. There is no "Apply" button.
        cb.addEventListener("change", applyGlossary);
        const n = document.createElement("span");
        n.className = "n"; n.textContent = s.name;
        const c = document.createElement("span");
        c.className = "c"; c.textContent = s.terms + " 語";
        // **The download link and the delete control sit in the same row.**
        // Never let the tables be editable only by logging into the caption
        // PC.
        const dl = document.createElement("a");
        dl.className = "c"; dl.textContent = "ダウンロード";
        dl.href = "/api/glossary/file?name=" + encodeURIComponent(s.name);
        dl.setAttribute("download", s.name + ".tsv");
        dl.addEventListener("click", e => e.stopPropagation());
        // **This cannot be a `<button>`.** The row is a `<label>`, so
        // pressing a button inside it would also toggle the checkbox. Give a
        // `<span>` the button role instead, and make it reachable from the
        // keyboard. **Delete must stay reachable.**
        const rm = document.createElement("span");
        rm.className = "c"; rm.textContent = "削除";
        rm.style.cursor = "pointer";
        rm.setAttribute("role", "button");
        rm.setAttribute("tabindex", "0");
        rm.addEventListener("keydown", e => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); rm.click(); }
        });
        rm.addEventListener("click", async e => {
          e.preventDefault(); e.stopPropagation();
          // **Deleting cannot be undone.** The table is gone unless it was
          // downloaded first, so ask for confirmation.
          // **Never write the Japanese quote marks 「」 inside a JS string.**
          // The translation table turns them into `"`, the literal ends
          // there, and **the whole script on the page dies.** Nothing happens
          // in Japanese, so only people who switch to English see it.
          if (!confirm("用語集 " + s.name + " を削除する。戻せない。\n"
                       + "取っておくなら、先にダウンロードすること。")) { return; }
          try {
            showGlossary(await post("/api/glossary/delete", { name: s.name }));
            say("用語集 " + s.name + " を削除した。", true);
          } catch (err) { say(String(err.message), false); }
        });
        lab.append(cb, n, c, dl, rm);
        glossBox.appendChild(lab);
      }
    }
    // **Show the filter only once there are many tables.** With a few, it
    // only gets in the way.
    glossFilterRow.style.display = sets.length >= GLOSS_FILTER_FROM ? "" : "none";
    if (sets.length < GLOSS_FILTER_FROM) { glossFilter.value = ""; }
    applyGlossFilter();

    // **What is in use must stay visible while the section is collapsed.**
    // If you had to open it to find out, you would not notice that the
    // selection is still the one from last time.
    const names = sets.filter(s => sel.has(s.name)).map(s => s.name);
    if (!names.length) {
      glossSummary.innerHTML = '<span class="n ng">選ばれていない</span>';
    } else {
      const head = names.slice(0, 2).join(", ")
        + (names.length > 2 ? "　ほか " + (names.length - 2) : "");
      glossSummary.innerHTML = '<span class="n"></span><span class="c"></span>';
      glossSummary.querySelector(".n").textContent = head;
      glossSummary.querySelector(".c").textContent = g.terms + " 語";
    }

    const parts = ["文字起こしに渡す語 " + g.keywords + " / " + g.limit];
    glossState.textContent = parts.join("　");
    // **Terms cut off at the limit never reach the recognizer.** Dropping
    // them silently produces a confusing failure: a term was added to the
    // table but has no effect.
    if (g.dropped > 0) {
      glossState.innerHTML = parts.join("　")
        + '<br><span class="ng">' + g.dropped
        + " 語が上限で切り捨てられた。選ぶ表を減らすこと</span>";
    }
  }

  function applyGlossFilter() {
    const q = glossFilter.value.trim().toLowerCase();
    let shown = 0;
    for (const lab of glossBox.querySelectorAll(".gloss")) {
      const hit = !q || lab.dataset.name.includes(q);
      // **A selected table is never hidden by the filter.** If you could
      // untick something you cannot see, you would not know what you
      // unticked.
      const on = lab.querySelector("input").checked;
      lab.style.display = (hit || on) ? "" : "none";
      if (hit || on) { shown++; }
    }
    let none = glossBox.querySelector(".nohit");
    if (!shown && !none) {
      none = document.createElement("div");
      none.className = "nohit hint"; none.textContent = "当てはまる表が無い";
      glossBox.appendChild(none);
    } else if (none) {
      none.style.display = shown ? "none" : "";
    }
  }

  glossFilter.addEventListener("input", applyGlossFilter);
  // **Both buttons also act on tables hidden by the filter.** If a button
  // says "all" but changes only what is visible, nobody can tell what is
  // selected.
  function setAllGloss(on) {
    let changed = false;
    for (const c of glossBox.querySelectorAll("input")) {
      if (c.checked !== on) { c.checked = on; changed = true; }
    }
    if (changed) { applyGlossary(); }
  }
  glossAll.addEventListener("click", () => setAllGloss(true));
  glossNone.addEventListener("click", () => setAllGloss(false));

  async function loadGlossary() {
    glossLoaded = true;
    try {
      const r = await fetch("/api/glossary");
      showGlossary(await r.json());
    } catch (e) {
      glossLoaded = false;
      say("用語集の一覧を取れない: " + e.message, false);
    }
  }

  async function applyGlossary() {
    const names = [...glossBox.querySelectorAll("input:checked")].map(c => c.value);
    for (const c of glossBox.querySelectorAll("input")) { c.disabled = true; }
    try {
      const g = await post("/api/glossary", { names });
      showGlossary(g);
      say("用語集を " + (names.join(", ") || "(なし)") + " にした。", true);
    } catch (e) {
      say(String(e.message), false);
      await loadGlossary();   // on failure, go back to the real selection
      return;
    }
    for (const c of glossBox.querySelectorAll("input")) { c.disabled = false; }
  }

  // **Tables are added by upload.** The caption PC runs inside a container,
  // and there is no other way to put a file there remotely. The tables live
  // on a volume, so they survive rebuilding the container.
  glossUp.addEventListener("click", async () => {
    const f = glossFile.files && glossFile.files[0];
    if (!f) { say("ファイルを選ぶこと。", false); return; }
    glossUp.disabled = true;
    try {
      const text = await f.text();
      // The file name without its extension becomes the table name. Do not
      // ask the user to type a name as well.
      const name = f.name.replace(/[.]tsv$/i, "");
      showGlossary(await post("/api/glossary/upload", { name, text }));
      glossFile.value = "";
      say("用語集 " + name + " を置いた。", true);
    } catch (e) {
      say(String(e.message), false);
    }
    glossUp.disabled = false;
  });

  // --- VNC ----------------------------------------------------------------
  // **It can be started and stopped during a meeting.** Use it to sign in to
  // the meeting software, and to look at the screen when auto-join gets
  // stuck. It has no authentication, so it does not run by default.
  function showVnc(v) {
    if (!v) { return; }
    // Where it cannot work (Windows has no X display here), hide the whole
    // section.
    vncGrp.style.display = (v.available || v.on) ? "" : "none";
    vncOn.disabled = v.on || !v.available;
    vncOff.disabled = !v.on;
    if (v.on) {
      // **Connect to the same host as the control page you have open.**
      // Building the name on the server would disagree with the page,
      // depending on whether it was opened by tailnet name, by IP or by
      // localhost.
      //
      // **Match the scheme too.** If the page is https, offer the https port.
      // Sending a user from an http page to an https port can fail, because
      // the certificate name does not match (a certificate is issued for the
      // full name).
      const https = location.protocol === "https:";
      const port = https ? v.https_port : v.web_port;
      const u = location.protocol + "//" + location.hostname + ":" + port
                + "/vnc.html";
      vncState.textContent = "動作中";
      vncHint.innerHTML = "";
      const a = document.createElement("a");
      a.href = u; a.target = "_blank"; a.rel = "noopener"; a.textContent = u;
      vncHint.append(a, document.createTextNode(
        "　VNCクライアントからは " + location.hostname + ":" + v.rfb_port
        + "。**認証は無い。** 用が済んだら停止すること。"));
    } else {
      vncState.textContent = v.error ? "" : "停止中";
      vncHint.textContent = v.error
        || "会議ソフトへのサインインと、自動参加が詰まったときに使う。"
           + "認証が無いので、常用しないこと。";
    }
  }

  async function setVnc(on) {
    vncOn.disabled = vncOff.disabled = true;
    try {
      showVnc(await post("/api/vnc", { on }));
      say(on ? "VNCを起動した。用が済んだら停止すること。" : "VNCを停止した。", true);
    } catch (e) {
      say(String(e.message), false);
    }
  }
  vncOn.addEventListener("click", () => setVnc(true));
  vncOff.addEventListener("click", () => setVnc(false));

  // --- Language of the control page ---------------------------------------
  // **The server does the substitution.** On a change, tell the server and
  // reload. The page text is replaced while the page is being built, so
  // nothing is translated here.
  const uiLang = $("uiLang");
  uiLang.value = "__UI_LANG__";
  uiLang.addEventListener("change", async () => {
    uiLang.disabled = true;
    try {
      await post("/api/lang", { lang: uiLang.value });
      location.reload();
    } catch (e) {
      say(String(e.message), false);
      uiLang.disabled = false;
    }
  });

  // --- Caption direction --------------------------------------------------
  // **Chosen per meeting.** It switches as soon as it is chosen. There is no
  // apply button. Speech recognition is not reconnected, so it can be changed
  // while generation runs.
  function showDirection(d) {
    const items = d.items || [];
    dirSel.innerHTML = "";
    for (const it of items) {
      const o = document.createElement("option");
      o.value = it.name;
      o.textContent = it.label;
      if (it.name === d.current) { o.selected = true; }
      dirSel.appendChild(o);
    }
    const cur = items.find((x) => x.name === d.current);
    dirState.textContent = cur ? ("Zoomへは " + cur.lang + " として送る") : "";
  }

  async function loadDirection() {
    dirLoaded = true;
    try {
      const r = await fetch("/api/direction");
      showDirection(await r.json());
    } catch (e) {
      dirLoaded = false;
      say("字幕の向きを取れない: " + e.message, false);
    }
  }

  dirSel.addEventListener("change", async () => {
    dirSel.disabled = true;
    try {
      const d = await post("/api/direction", { name: dirSel.value });
      showDirection(d);
      say("字幕の向きを変えた。", true);
      // **The forced split length depends on the direction.** Unless the
      // numbers under "Latency tuning" are fetched again, old values stay
      // inside the collapsed section.
      if (tuneLoaded) { await loadTuning(); }
    } catch (e) {
      say(String(e.message), false);
      await loadDirection();   // on failure, go back to the real direction
    }
    dirSel.disabled = false;
  });

  // --- Latency tuning -----------------------------------------------------
  // **These are not changed often.** The defaults were measured on real
  // meeting audio, which is why this section is collapsed. A changed value
  // takes effect at once. Press "Save to .env" only to keep it for the next
  // start.
  function showTuning(t) {
    const items = t.items || [];
    tuneBox.innerHTML = "";
    let changed = 0;
    for (const it of items) {
      const off = Number(it.value) !== Number(it.default);
      if (off) { changed += 1; }
      const row = document.createElement("div");
      row.className = "tune" + (off ? " changed" : "");
      const top = document.createElement("div");
      top.className = "top";
      const k = document.createElement("span");
      k.className = "k"; k.textContent = it.name;
      const inp = document.createElement("input");
      inp.type = "number"; inp.value = it.value;
      inp.min = it.min; inp.step = it.step;
      inp.dataset.name = it.name;
      // Do not send while the user is typing. Send on blur and on Enter.
      inp.addEventListener("change", applyTuning);
      const d = document.createElement("span");
      d.className = "d"; d.textContent = "既定 " + it.default;
      top.append(k, inp, d);
      const h = document.createElement("p");
      h.className = "h"; h.textContent = it.help || "";
      row.append(top, h);
      tuneBox.appendChild(row);
    }
    tuneSummary.textContent = changed
      ? changed + " 個を既定から変えている"
      : "よく変えるものではない";
    tuneEnv.textContent = t.env_path ? "保存先: " + t.env_path : "";
    // Shown only when the combination makes no sense (for example, the
    // look-ahead is larger than the wait for a final result).
    if (t.warning) {
      tuneState.innerHTML = '<span class="ng"></span>';
      tuneState.querySelector(".ng").textContent = t.warning;
    } else {
      tuneState.textContent = "";
    }
  }

  async function loadTuning() {
    tuneLoaded = true;
    try {
      const r = await fetch("/api/tuning");
      showTuning(await r.json());
    } catch (e) {
      tuneLoaded = false;
      say("設定を取れない: " + e.message, false);
    }
  }

  async function applyTuning(ev) {
    const inp = ev.target;
    const values = {}; values[inp.dataset.name] = inp.value;
    inp.disabled = true;
    try {
      showTuning(await post("/api/tuning", { values }));
      say(inp.dataset.name + " を " + inp.value + " にした。", true);
    } catch (e) {
      say(String(e.message), false);
      await loadTuning();          // on failure, go back to the real values
      return;
    }
    inp.disabled = false;
  }

  tuneReset.addEventListener("click", async () => {
    const values = {};
    for (const inp of tuneBox.querySelectorAll("input[type=number]")) {
      const row = inp.closest(".tune");
      values[inp.dataset.name] = row.querySelector(".d").textContent.replace("既定 ", "");
    }
    try {
      showTuning(await post("/api/tuning", { values }));
      say("既定値に戻した。", true);
    } catch (e) { say(String(e.message), false); }
  });

  tuneSave.addEventListener("click", async () => {
    tuneSave.disabled = true;
    try {
      const t = await post("/api/tuning/save", {});
      showTuning(t);
      say(".env に保存した: " + (t.saved || ""), true);
    } catch (e) { say(String(e.message), false); }
    tuneSave.disabled = false;
  });

  // --- Zoom ---------------------------------------------------------------
  save.addEventListener("click", async () => {
    const value = token.value.trim();
    if (!value) { say("トークンを貼ること。", false); return; }
    save.disabled = true;
    try {
      const s = await post("/api/token", { token: value });
      token.value = "";                 // do not leave it on the screen
      say("登録した。会議 " + s.meeting + "。「開始」で送信を始める。", true);
      showStatus(s);
    } catch (e) { say(String(e.message), false); save.disabled = false; }
  });
  token.addEventListener("keydown", (e) => { if (e.key === "Enter") { save.click(); } });

  start.addEventListener("click", async () => {
    try { showStatus(await post("/api/zoom", { on: true })); say("送信を開始した。", true); }
    catch (e) { say(String(e.message), false); }
  });
  stop.addEventListener("click", async () => {
    try { showStatus(await post("/api/zoom", { on: false })); say("送信を停止した。", true); }
    catch (e) { say(String(e.message), false); }
  });

  // --- Delivery -----------------------------------------------------------
  tstart.addEventListener("click", async () => {
    tstart.disabled = true;
    try {
      showStatus(await post("/api/tunnel", { on: true }));
      say("配信を始めている。URLが出るまで数秒かかる。", true);
    } catch (e) { say(String(e.message), false); }
  });
  tstop.addEventListener("click", async () => {
    try { showStatus(await post("/api/tunnel", { on: false })); say("配信を止めた。閲覧URLは死んだ。", true); }
    catch (e) { say(String(e.message), false); }
  });
  // **Choosing alone does not put anything outside.** After switching the
  // route, you still press Start separately.
  tkind.addEventListener("change", async () => {
    try {
      showStatus(await post("/api/tunnel", { kind: tkind.value }));
      say("経路を選んだ。「配信を開始」で始める。", true);
    } catch (e) { say(String(e.message), false); }
  });

  // --- Schedule -----------------------------------------------------------
  // **Something that runs unattended is frightening unless you can see what
  // it will do next.**
  function mmss(sec) {
    if (sec < 0) { return ""; }
    const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return m + ":" + String(s).padStart(2, "0");
  }
  function inWords(sec) {
    if (sec < 0) { return "過ぎている"; }
    if (sec < 90) { return "まもなく"; }
    const m = Math.round(sec / 60);
    if (m < 60) { return "あと " + m + " 分"; }
    return "あと " + Math.floor(m / 60) + " 時間 " + (m % 60) + " 分";
  }

  function drawSchedule(sc) {
    const st = sc.state || "idle";
    const parts = [];
    if (st === "running") {
      parts.push("動作中: " + (sc.meeting || ""));
      if (sc.silence_left_sec >= 0) { parts.push("無音まで " + mmss(sc.silence_left_sec)); }
      if (sc.max_left_sec >= 0) { parts.push("上限まで " + mmss(sc.max_left_sec)); }
    } else if (st === "joining" || st === "arming") {
      parts.push("始めているところ: " + (sc.meeting || ""));
      if (sc.note) { parts.push(sc.note); }
    } else if (st === "stopping") {
      parts.push("片付けているところ");
    } else {
      const up = sc.upcoming || [];
      parts.push(up.length ? "待機中" : "待機中（予定は入っていない）");
      if (sc.note) { parts.push(sc.note); }
    }
    schedState.textContent = parts.join("　");

    // **A failure stays until it is acknowledged.** On an unattended machine,
    // a failure that scrolls away is seen by nobody.
    if (sc.failure) {
      schedFail.textContent = (sc.failure_at || "") + "　" + sc.failure;
      schedFailRow.style.display = "";
    } else {
      schedFailRow.style.display = "none";
    }

    const up = sc.upcoming || [];
    const key = JSON.stringify(up);
    if (key !== schedNext.dataset.seen) {
      schedNext.dataset.seen = key;
      schedNext.textContent = "";
      for (const it of up) {
        const row = document.createElement("div");
        row.className = "row";
        const t = document.createElement("span");
        t.className = "t"; t.textContent = it.at;
        const n = document.createElement("span");
        n.className = "n"; n.textContent = it.name + (it.zoom ? "" : "（Zoomには入らない）");
        const w = document.createElement("span");
        w.textContent = inWords(it.in_sec);
        row.appendChild(t); row.appendChild(n); row.appendChild(w);
        schedNext.appendChild(row);
      }
    }
    schedStop.disabled = (st === "idle");
    schedSkip.disabled = !up.length;
  }

  schedAck.addEventListener("click", async () => {
    try { showStatus(await post("/api/schedule", { action: "ack" })); }
    catch (e) { say(String(e.message), false); }
  });
  schedStop.addEventListener("click", async () => {
    try {
      showStatus(await post("/api/schedule", { action: "stop" }));
      say("いま回している会議を止めた。", true);
    } catch (e) { say(String(e.message), false); }
  });
  schedSkip.addEventListener("click", async () => {
    try {
      showStatus(await post("/api/schedule", { action: "skip" }));
      say("次の予定を飛ばした。", true);
    } catch (e) { say(String(e.message), false); }
  });

  // --- Meetings -----------------------------------------------------------
  // **Schedule editing does not belong here.** The right column is too narrow
  // for the date and time, the Zoom link, three numbers and the checkboxes.
  // That lives on a separate page (/meetings).
  // What stays here is only what you use on the day: which meeting to deliver,
  // and its URL.
  function drawMeetings(m, t, sc) {
    const items = m.items || [];
    const key = JSON.stringify([m.active, items]);
    if (key !== meetSeen) {
      meetSeen = key;
      meetPick.textContent = "";
      for (const it of items) {
        const opt = document.createElement("option");
        opt.value = it.id;
        opt.textContent = it.name;
        meetPick.appendChild(opt);
      }
      meetCount.textContent = items.length > 1 ? "（" + items.length + "）" : "";
    }
    if (document.activeElement !== meetPick) { meetPick.value = m.active; }

    const live = items.find((x) => x.id === m.active);
    meetWhen.textContent = !live ? ""
      : live.start
        ? (live.repeat === "weekly" ? "毎週 " : "") + live.start
          + (live.auto ? "　自動で開始" : "　自動は切り")
          + (live.zoom ? "　Zoomに入る" : "")
        : "予定なし";

    if (live && live.url) {
      meetUrl.textContent = live.url;
      meetUrlRow.style.display = "";
    } else {
      meetUrlRow.style.display = "none";
    }

    // **Say what the button will do, every time.** It differs per meeting
    // (whether it joins Zoom, whether it posts to the chat), so the button
    // label alone does not tell you.
    const what = ["配信"];
    if (live && live.zoom) { what.push("Zoomに入る"); }
    what.push("字幕の生成");
    if (live && live.chat) { what.push("チャットに投げる"); }
    meetStartWhat.textContent = live ? what.join(" → ") : "";
    // Disabled while a meeting runs. Stopping is done with "Stop now".
    meetStart.disabled = !live || ((sc || {}).state || "idle") !== "idle";
  }

  meetPick.addEventListener("change", async () => {
    try {
      showStatus(await post("/api/meetings",
                            { action: "select", id: meetPick.value }));
      say("配信する会議: " + meetPick.options[meetPick.selectedIndex].text, true);
    } catch (e) { say(String(e.message), false); }
  });

  meetStart.addEventListener("click", async () => {
    meetStart.disabled = true;
    try {
      showStatus(await post("/api/schedule",
                            { action: "start", id: meetPick.value }));
      // **It takes tens of seconds to start.** That includes launching Zoom
      // and bringing the delivery up. The progress appears under "Current
      // state".
      say("この会議を始める。進み具合は上に出る。", true);
    } catch (e) { say(String(e.message), false); meetStart.disabled = false; }
  });

  meetQr.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/qr?dl=1&id=" + encodeURIComponent(meetPick.value)
           + "&name=" + encodeURIComponent(
               meetPick.options[meetPick.selectedIndex].text);
    a.download = "livecaption-qr.png";
    document.body.appendChild(a); a.click(); a.remove();
  });

  // **Open it in another tab.** The control page is watched during the
  // meeting, so do not replace it.

  openViewer.addEventListener("click", () => { window.open(viewerUrl.textContent, "_blank"); });

__COPY_JS__

  // --- Saving the QR code ---------------------------------------------------
  // **The QR code on the page cannot be pasted anywhere.** Putting it on a
  // slide or into an announcement email needs a PNG file. Ask the server to
  // draw a larger one and download that.
  qrsave.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/qr?dl=1&t=" + encodeURIComponent(publicUrl.textContent);
    a.download = "livecaption-qr.png";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });

  // --- Records ------------------------------------------------------------
  // Shown in another tab. **This page is never shared, so the records are not
  // shown from here either.**

  // --- Quitting -----------------------------------------------------------
  // When the app stops, the server goes with it. **No answer can still mean
  // success.** Do not report a network failure as a failure here.
  quit.addEventListener("click", async () => {
    // **If the app comes back, say so.** Someone who presses the button,
    // thinks it is over and leaves would leave it running with nobody
    // watching.
    const back = restartsShown;
    if (!confirm(back ? "字幕アプリを再起動する。十数秒で戻ってくる。"
                      : "字幕アプリを終了する。よろしいですか。")) { return; }
    quit.disabled = true;
    try { await post("/api/shutdown", {}); } catch (e) { /* see above */ }
    ended = true;
    clearInterval(timer);
    dot.classList.remove("on");
    pill.textContent = "終了した"; pill.className = "pill off";
    tpill.textContent = "配信: 停止"; tpill.className = "pill off";
    zoomState.textContent = ""; qrbox.classList.remove("on");
    wayNetState.textContent = ""; wayZoomState.textContent = "";
    gpill.textContent = "生成: 停止"; gpill.className = "pill off";
    genState.textContent = "";
    for (const b of [save, start, stop, quit, tstart, tstop, gstart, gstop,
                     devReload]) { b.disabled = true; }
    devices.disabled = true; meterBar.style.width = "0";
    say(back ? "再起動している。十数秒したら、この画面を開き直すこと。"
             : "終了した。この画面を閉じる。", true);

    // Close this tab. **Sometimes it cannot be closed.**
    // A browser only lets a script close a window that a script opened. This
    // page was opened by the app with webbrowser.open(), so the close request
    // may be ignored silently. For that case, show a page that makes the
    // failure to close obvious.
    // **If the app comes back, do not close the tab.** This page is where you
    // would reopen it.
    if (!back) {
      window.close();
      setTimeout(() => {
        if (ended) { showClosed(); }
      }, 400);
    } else {
      showClosed();
    }
  });

  function showClosed() {
    // We get here when the browser refused to close the tab.
    // Leaving the controls in place would make the page look usable. Remove
    // them all.
    document.getElementById("panel").remove();
    sep.remove();
    split.style.setProperty("--right", "0px");
    main.innerHTML =
      '<div style="margin:auto;text-align:center;color:var(--ja);font-size:20px;line-height:1.9">'
      + "字幕アプリを終了した。<br>このタブは閉じてよい。"
      + '<div style="font-size:15px;margin-top:14px">'
      + "（ブラウザがタブを自動で閉じない設定になっている）</div></div>";
  }

  // --- Refreshing the status ----------------------------------------------
  // The number sent, the number failed and the tunnel state change without
  // anyone touching this page. The server is our own machine, so polling it
  // regularly is fine.
  function refresh() {
    if (ended) { return; }
    fetch("/api/status").then((r) => r.json()).then(showStatus).catch(() => {});
  }
  refresh();
  const timer = setInterval(refresh, 2000);
</script>
</body>
</html>
"""


class WebCaptions:
    """Holds the caption history and runs two HTTP servers: viewer and
    control.

    `caption()` and `asr()` are called from the main event loop. Neither
    waits (they only append to the history and wake the waiters), so they do
    not delay caption delivery.

    `control` holds an `app.ZoomControl`, `on_shutdown` holds
    `app.App.request_stop`, and `tunnel` holds a `tunnel.Tunnel`.
    **Operations arrive on the HTTP server threads.**
    """

    def __init__(
        self,
        port: int = config.WEB_PORT,
        control_port: int = config.CONTROL_PORT,
        lines: int = config.WEB_LINES,
        bind: str = config.WEB_BIND,
    ) -> None:
        self.port = port
        self.control_port = control_port
        # Extra addresses (tailnet IPs) where the control page listens, in
        # addition to 127.0.0.1.
        # **These must stay inside the Tailscale range** (run.py checks them
        # before passing them in).
        self.control_extra: tuple[str, ...] = ()
        # Whether to keep waiting in the background for a tailnet address.
        self.control_retry = False
        self._stopping = False
        self.lines = lines
        self.bind = bind
        self.control = None
        # Start and stop of caption generation: recording, recognition,
        # translation (app.EngineControl).
        self.engine = None
        # Listing and switching the input device (app.AudioControl).
        self.audio = None
        # Listing and selecting glossaries (app.GlossaryControl).
        self.glossary = None
        # The latency tuning knobs (app.TuningControl).
        self.tuning = None
        # The caption direction (app.DirectionControl).
        self.direction = None
        # The scheduler that runs scheduled meetings (schedule.Scheduler).
        self.scheduler = None
        # VNC (vnc.Vnc). **It can be started and stopped during a meeting.**
        self.vnc = None
        self.on_shutdown = None
        # The delivery route (Cloudflare / Tailscale). `tunnel.Delivery` holds
        # both.
        self.tunnel: tunnel_mod.Delivery | None = None
        # The meeting record (transcript.Transcript). It stays None with
        # --no-save.
        # **Only the control page can see it.** It is not exposed on the
        # viewer side.
        self.transcript = None
        # Choosing where records are stored (app.RecordControl).
        self.records = None
        # The path of the viewer page. **It changes per meeting**
        # (meetings.py). Participants differ per meeting, so last week's URL
        # must not show today's captions.
        # Only the selected meeting is delivered; the URLs of the others
        # return 404.
        self.meetings = meetings.Store()
        self.meetings.on_change = self._meeting_changed
        # The host token entry point. Per meeting, it keeps a failure count
        # and an "already received" flag.
        self._host_attempts: dict[str, int] = {}
        self._host_done: set[str] = set()
        self._host_last_try = 0.0
        # What happened at that entry point, shown on the control page.
        # **The token itself is never put in here.**
        self.host_log: list[str] = []

        # The caption history, and the wait used by long polling.
        self._events: list[dict] = []
        self._first = 0  # sequence number of _events[0]
        self._cond = threading.Condition()
        self._waiting = 0
        # **The transcript line that is still growing.** It is not part of the
        # history. Its content is replaced, so mixing it into `_events`, which
        # only grows, would leave many copies of the same sentence.
        # It carries its own version number, and the browser redraws only when
        # that differs from the last version it saw.
        self._partial = ""
        self._partial_v = 0
        self._servers: list[ThreadingHTTPServer] = []

    # --- URL ---------------------------------------------------------------

    @property
    def viewer_path(self) -> str:
        """The path of the meeting being delivered now. The viewer server
        serves nothing else."""
        return self.meetings.viewer_path

    def viewer_url(self) -> str:
        """**The viewer URL that stays inside.** A way to watch the captions
        without opening a delivery (Funnel).

        **Return an address that actually works.** While listening on
        `0.0.0.0`, this used to return `localhost`. **Inside a container, that
        points at the container itself.** The page presented a URL nobody
        could open as the way to watch (reported 2026-09-21).

        With `0.0.0.0`, prefer the tailnet name, because the server really
        does listen there. It falls back to `localhost` only when no name is
        available (when working at the machine itself).
        """
        host = self.bind
        if host in ("127.0.0.1", ""):
            host = "localhost"
        elif host == "0.0.0.0":  # noqa: S104
            names = self.control_names()
            host = names[-1] if names else "localhost"
        return f"http://{host}:{self.port}{self.viewer_path}"

    def control_url(self) -> str:
        return f"http://localhost:{self.control_port}"

    def public_url(self) -> str:
        """The viewer URL through the tunnel. Empty when no tunnel is up."""
        url = self.tunnel.url if self.tunnel is not None else ""
        return f"{url}{self.viewer_path}" if url else ""

    def _upcoming(self) -> list[dict]:
        """The upcoming occurrences. **This keeps the schedule display
        independent of the scheduler.**"""
        try:
            return self.meetings.upcoming(datetime.now(), limit=3)
        except Exception:  # noqa: BLE001
            return []

    def meetings_status(self) -> dict:
        """The meeting list, with URLs built from the current route."""
        st = self.meetings.status()
        for item in st["items"]:
            item["url"] = self.meeting_url(item["id"])
            # **The host URL exists only with Tailscale.** Otherwise it is
            # empty.
            item["host_url"] = self.host_url(item["id"])
            item["host_taken"] = item["id"] in self._host_done
        return st

    def meeting_url(self, meeting_id: str) -> str:
        """The viewer URL of that meeting. Empty when the base URL is unknown.

        **With Tailscale it works even when nothing is being delivered.** That
        is how you fix the URL the day before a meeting and put it in the
        announcement. With Cloudflare the host name changes every time, so the
        URL exists only while the tunnel is up.
        """
        base = self.tunnel.base_url() if self.tunnel is not None else ""
        return f"{base}{self.meetings.path_of(meeting_id)}" if base else ""

    # --- Called from the main app -------------------------------------------

    def caption(self, text: str) -> None:
        """Send one translated caption line."""
        self._emit({"type": "caption", "text": text, "time": time.strftime("%H:%M:%S")})

    def asr(self, text: str) -> None:
        """Send one final transcript line. The toggle in the browser decides
        whether it is shown."""
        self._emit({"type": "asr", "text": text, "time": time.strftime("%H:%M:%S")})

    def partial(self, text: str) -> None:
        """**A transcript line that is still growing.** It is shown almost
        with the voice, without waiting for the sentence to be final.

        For the few seconds until a sentence is final, nothing appears on the
        page. The speaker's words are only piling up, and to the reader the
        page looks frozen. This fills that gap.

        **It is not sent to translation or to the Zoom captions.** Neither can
        replace a line it already showed, so a growing line would stay next to
        the corrected final one. Only the browser page, which owns its own
        DOM, can replace a line.
        """
        text = text.strip()
        with self._cond:
            if text == self._partial:
                return
            self._partial = text
            self._partial_v += 1
            self._cond.notify_all()

    def _meeting_changed(self) -> None:
        """Called when the delivered meeting changes
        (`meetings.Store.on_change`).

        **Throw away the captions of the previous meeting.** The history holds
        200 lines, and a newly opened page receives all of them with
        `since=0`. Without this, **the participants of the next meeting would
        see the content of the previous one.** That would defeat the point of
        a separate URL per meeting.

        The growing transcript line is dropped for the same reason. The
        version number advances, so any waiting long poll wakes up here (the
        old path returns 404 from this moment on, so there is no point in
        keeping it waiting).

        The page itself is built on every request (`_viewer_page`), so there
        is nothing to rebuild here.
        """
        with self._cond:
            # Do not rewind the sequence number. Rewinding would make an open
            # page think new lines arrived and fetch the lines we just
            # deleted.
            self._first += len(self._events)
            self._events.clear()
            self._partial = ""
            self._partial_v += 1
            self._cond.notify_all()

    def _emit(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            if len(self._events) > HISTORY:
                drop = len(self._events) - HISTORY
                del self._events[:drop]
                self._first += drop
            self._cond.notify_all()

    # --- Long polling -------------------------------------------------------

    def poll(self, since: int, wait: float, pv: int = -1) -> tuple[int, list[dict], str, int]:
        """Return the lines after `since`. If there are none, wait up to
        `wait` seconds.

        If `since` is so old that those lines already fell out of the history,
        return from the oldest line still kept. A page opened in the middle
        sends `since=0` and receives the whole history.

        **This also returns when the growing transcript line changed.** `pv`
        is the version number the browser last received; with `-1` it returns
        the current content whatever the version.
        """
        deadline = time.monotonic() + wait
        with self._cond:
            self._waiting += 1
            try:
                while True:
                    end = self._first + len(self._events)
                    if since < self._first:
                        since = self._first
                    if since < end or self._partial_v != pv:
                        return (end, self._events[since - self._first:],
                                self._partial, self._partial_v)
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return end, [], self._partial, self._partial_v
                    self._cond.wait(left)
            finally:
                self._waiting -= 1

    @property
    def viewers(self) -> int:
        """How many pages are waiting for captions now. Each page holds one
        request."""
        with self._cond:
            return self._waiting

    # --- Status -------------------------------------------------------------

    def status(self) -> dict:
        """The status returned to the control page. It merges the Zoom state
        and the tunnel state into one object."""
        if self.control is not None:
            st = dict(self.control.status())
        else:
            st = {"enabled": False, "has_token": False, "active": False,
                  "dry_run": False, "meeting": "", "seq": 0, "sent": 0, "failed": 0}
        if self.tunnel is not None:
            st["tunnel"] = self.tunnel.status()
        else:
            st["tunnel"] = {"state": "off", "url": "", "error": "", "base_url": "",
                            "kind": config.tunnel_kind_selection(),
                            "kinds": list(config.TUNNEL_KINDS), "preannounce": False,
                            "available": tunnel_mod.find_cloudflared() is not None}
        # **Return the per-meeting viewer URLs as well.** A URL handed out in
        # advance is useless unless it is visible while nothing is being
        # delivered.
        st["meetings"] = self.meetings_status()
        # The scheduler state. **It rides along with the two-second status
        # poll.** Something that runs unattended is frightening unless you can
        # see what it will do next.
        #
        # **The upcoming meetings are shown even without a scheduler.** Right
        # after start, for the short time until the app is assembled,
        # `scheduler` is not set yet. An empty list there would suggest that
        # the schedule was lost. The schedule belongs to the meeting list, so
        # read it directly from there.
        st["schedule"] = self.scheduler.status() if self.scheduler else {
            "state": "idle", "meeting": "", "occurrence": "",
            "note": "本体を組み立てているところ", "failure": "", "failure_at": "",
            "silence_left_sec": -1.0, "max_left_sec": -1.0,
            "upcoming": self._upcoming()}
        st["generating"] = bool(self.engine.status()["generating"]) if self.engine else True
        st["audio"] = self.audio.status() if self.audio else {
            "selectable": False, "name": "—", "index": None,
            "level": 0.0, "dropped": 0, "error": ""}
        st["viewer_url"] = self.viewer_url()
        st["public_url"] = self.public_url()
        # Whether the app comes back after quitting. **It changes the wording
        # of the Quit button.**
        st["restarts"] = config.restarts()
        # VNC. **On Windows this reports "not available"** (there is no
        # DISPLAY).
        st["vnc"] = self.vnc.status() if self.vnc else {
            "on": False, "available": False, "error": "",
            "web_port": config.VNC_WEB_PORT,
            "https_port": config.VNC_HTTPS_PORT,
            "rfb_port": config.VNC_RFB_PORT}
        if self.transcript is not None:
            st["transcript"] = {
                "on": True,
                "path": str(self.transcript.path),
                "count": len(self.transcript.records),
                "error": self.transcript.error,
            }
        else:
            st["transcript"] = {"on": False, "path": "", "count": 0, "error": ""}
        # Where records are stored. **This is returned even with --no-save**,
        # so it is clear where nothing is being written.
        st["records"] = self.records.status() if self.records else {
            "dir": str(config.TRANSCRIPT_DIR), "saving": False, "can_pick": False}
        return st

    # --- Start and stop -----------------------------------------------------

    def start(self) -> None:
        """Bring up the two servers: viewer and control.

        **The control page always listens on 127.0.0.1.** `bind` affects only
        the viewer.

        If `control_extra` is set, the control page also listens on those
        tailnet addresses, **in addition** to 127.0.0.1. 127.0.0.1 always
        stays. **That way the machine can always be operated from in front of
        it, even when Tailscale is down.**
        """
        viewer = self._serve(self.bind, self.port, _viewer_handler(self))
        try:
            control = self._serve("127.0.0.1", self.control_port, _control_handler(self))
        except OSError:
            viewer.shutdown()
            viewer.server_close()
            raise
        self._servers = [viewer, control]

        bound = []
        for addr in self.control_extra:
            try:
                self._servers.append(
                    self._serve(addr, self.control_port, _control_handler(self)))
                bound.append(addr)
            except OSError as exc:
                # **Do not abort the start here.** Right after a reboot,
                # tailscaled may not have handed out an address yet. Failing
                # to start the caption app just because the control page is
                # not reachable from outside would defeat the purpose.
                print(f"  [操作] {addr} では待ち受けられない: {exc}")
        # **Remember only the addresses we really bound.** Both the URLs
        # printed at start and the `Origin` check are built from this. Showing
        # a URL that is not open as if it were would send someone hunting for
        # why it does not connect.
        self.control_extra = tuple(bound)
        self.serve_control_https()
        if self.control_retry:
            threading.Thread(target=self._retry_control_bind, daemon=True).start()

    def serve_control_https(self) -> None:
        """Also serve the control page over https, inside the tailnet only
        (`tailscale serve`).

        **This does nothing when the page is not on a tailnet.** If it is used
        only over `localhost`, the browser already treats it as a secure
        context.

        On failure it continues silently. **https is convenient, but the page
        works over http without it.** Losing the control page on the day of a
        meeting over a certificate problem would defeat the purpose.
        """
        if not self.control_extra:
            return
        why = tunnel_mod.serve_https(config.CONTROL_HTTPS_PORT, self.control_port)
        if why:
            print(f"[{time.strftime('%H:%M:%S')}] 操作        "
                  f"https では出せなかった（{why[:120]}）。http は使える。")
            return
        for name in self.control_names():
            print(f"[{time.strftime('%H:%M:%S')}] 操作        "
                  f"https でも開ける: https://{name}:{config.CONTROL_HTTPS_PORT}")
            break

    def _retry_control_bind(self) -> None:
        """Keep adding listeners in the background until a tailnet address
        appears.

        **When the app starts automatically, Tailscale may not be up yet.**
        Trying once at start and giving up would mean that after every reboot,
        the control page cannot be opened from the tailnet for that whole run.
        The caption app itself is not made to wait (127.0.0.1 is already
        open); the retry happens here.
        """
        while not self._stopping:
            time.sleep(config.CONTROL_BIND_RETRY_SEC)
            if self._stopping or self.control_extra:
                return
            addrs = tunnel_mod.tailscale_addrs()
            if not addrs:
                continue
            bound = []
            for addr in addrs:
                try:
                    self._servers.append(
                        self._serve(addr, self.control_port, _control_handler(self)))
                    bound.append(addr)
                except OSError:
                    pass
            if bound:
                self.control_extra = tuple(bound)
                for url in self.control_urls_extra():
                    print(f"[{time.strftime('%H:%M:%S')}] 操作        "
                          f"tailnet からも開けるようになった: {url}")
                # **Set it up here as well.** At start, Tailscale may not be
                # up yet, and neither the name nor the address is available.
                self.serve_control_https()
                return

    # --- The entry point where a host pastes a token -------------------------
    # **This is the only write endpoint exposed through the tunnel.** Keep it
    # as narrow as possible.

    def host_path(self, meeting_id: str, host_id: str) -> str:
        return f"/h/{meeting_id}/{host_id}"

    def host_url(self, meeting_id: str) -> str:
        """The URL handed to the host. Empty when the entry point is not open.

        **It is not created with Cloudflare.** TLS ends at the Cloudflare
        edge, so the Zoom credential would pass through there in the clear.
        """
        if self.tunnel is None or self.tunnel.kind not in config.HOST_TOKEN_KINDS:
            return ""
        base = self.tunnel.base_url()
        if not base:
            return ""
        try:
            host_id = self.meetings.ensure_host_id(meeting_id)
        except ValueError:
            return ""
        return f"{base}{self.host_path(meeting_id, host_id)}"

    def host_window_open(self, meeting_id: str) -> bool:
        """Whether a token may be accepted right now.

        **This must not be a permanent endpoint.** It is open only while that
        meeting is running, and for `HOST_TOKEN_WINDOW_MIN` minutes around its
        scheduled time. **This is the strongest limit in the design.** It
        narrows the endpoint to about one hour per meeting.
        """
        sched = self.scheduler
        if sched is not None and sched.meeting_id == meeting_id and sched.state != "idle":
            return True
        for m in self.meetings.items():
            if m.id != meeting_id or not m.start:
                continue
            when = self.meetings.next_occurrence(m, datetime.now())
            if when is None:
                return False
            gap = abs((datetime.now() - when).total_seconds())
            return gap <= config.HOST_TOKEN_WINDOW_MIN * 60
        return False

    def host_take_token(self, meeting_id: str, host_id: str, token: str,
                        peer: str = "") -> tuple[int, str]:
        """Receive a token. Returns `(HTTP status, message for the page)`.

        **A refusal returns the same 404 as a wrong path.** Revealing that
        "the key is right but it is outside the window" would tell the caller
        that an endpoint exists there.
        """
        now = time.monotonic()
        if now - self._host_last_try < config.HOST_MIN_INTERVAL_SEC:
            return 429, "続けて送りすぎ。少し待つこと。"
        self._host_last_try = now

        active = self.meetings.active_id
        known = {m.id: m for m in self.meetings.items()}
        meeting = known.get(meeting_id)
        # **Only the meeting being delivered now.** Last week's key does not
        # work.
        if meeting is None or meeting_id != active or not meeting.host_id:
            return 404, ""
        if not hmac.compare_digest(str(host_id), str(meeting.host_id)):
            self._host_note(meeting_id, peer, "経路が違う")
            return 404, ""
        if not self.host_window_open(meeting_id):
            self._host_note(meeting_id, peer, "受付の時間外")
            return 404, ""
        if self._host_attempts.get(meeting_id, 0) >= config.HOST_MAX_ATTEMPTS:
            self._host_note(meeting_id, peer, "失敗が続いたので閉じた")
            return 404, ""
        if meeting_id in self._host_done:
            # **Once only.** A second attempt is either a mistake or somebody
            # else.
            return 409, "もう受け取っている。入れ直すなら操作画面から。"

        if self.control is None:
            return 503, "Zoom字幕の受け口が用意できていない。"
        try:
            check_zoom_token(token)
            self.control.set_token(token)
            self.control.set_enabled(True)
        except captions_mod.TokenError as exc:
            self._host_attempts[meeting_id] = self._host_attempts.get(meeting_id, 0) + 1
            self._host_note(meeting_id, peer, f"断った: {exc}")
            return 400, str(exc)
        self._host_done.add(meeting_id)
        self._host_note(meeting_id, peer, "受け取った")
        return 200, "受け取った。字幕をZoomに送り始める。"

    def host_rearm(self, meeting_id: str) -> None:
        """Open the entry point again, from the control page."""
        self._host_done.discard(meeting_id)
        self._host_attempts.pop(meeting_id, None)

    def _host_note(self, meeting_id: str, peer: str, what: str) -> None:
        """**Always record what happened.** On an unattended machine, the log
        is the only trace.

        **Never write the token itself.** Even its first characters, combined
        with the meeting ID, would be almost enough to reconstruct it.
        """
        line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {what}"
                f"（会議 {meeting_id[:6]}…, 相手 {peer or '不明'}）")
        self.host_log.append(line)
        del self.host_log[:-20]
        print(f"[{time.strftime('%H:%M:%S')}] ホスト      {what}（相手 {peer or '不明'}）")

    def control_urls_extra(self) -> list[str]:
        """URLs, other than 127.0.0.1, where the control page can be opened.
        They are printed at start.

        **Names come first.** A name is what people remember and bookmark.
        """
        names = [f"http://{n}:{self.control_port}" for n in self.control_names()]
        addrs = [f"http://{'[' + a + ']' if ':' in a else a}:{self.control_port}"
                 for a in self.control_extra]
        return names + addrs

    def allowed_origins(self) -> set[str]:
        """The URLs of this control page itself. Used for the `Origin` check.

        **Addresses alone are not enough.** A tailnet has MagicDNS, so people
        **open the page by name**, for example `http://livecaption:8081`.
        Without the names here, the page loads but every button is refused
        (this happened on 2026-09-19).
        """
        out = {f"http://localhost:{self.control_port}",
               f"http://127.0.0.1:{self.control_port}"}
        for addr in self.control_extra:
            host = f"[{addr}]" if ":" in addr else addr
            out.add(f"http://{host}:{self.control_port}")
        for name in self.control_names():
            out.add(f"http://{name}:{self.control_port}")
            # **Include the https endpoint too** (`tailscale serve`, inside
            # the tailnet only). Without it, the page opens but the `Origin`
            # does not match and every button returns 403. **Do not switch to
            # "allow everything".** That would remove the point of checking
            # `Origin` at all. What is added here is only the name of this
            # machine combined with the port we serve.
            out.add(f"https://{name}:{config.CONTROL_HTTPS_PORT}")
        return out

    def control_names(self) -> list[str]:
        """The names that point at this machine on the tailnet: both the long
        and the short MagicDNS form.

        **Empty when the page is not on a tailnet.** A name works only while
        the server listens there.
        """
        if not self.control_extra:
            return []
        host, _ = tunnel_mod.tailscale_host()
        if not host:
            return []
        short = host.split(".")[0]
        return [host, short] if short and short != host else [host]

    def _serve(self, host: str, port: int, handler) -> ThreadingHTTPServer:  # noqa: ANN001
        # ThreadingHTTPServer is used because each long poll occupies one
        # thread.
        cls = ThreadingHTTPServer
        if ":" in host:
            # **IPv6 needs a different address family.** The default of
            # `ThreadingHTTPServer` is IPv4, and passing it a tailnet IPv6
            # address fails in getaddrinfo.
            class _V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            cls = _V6
        # **Do not let two processes hold the same port.** On Windows,
        # `allow_reuse_address` lets a second LiveCaption bind successfully,
        # and then it is unclear which one answers. **On a machine that runs
        # all the time, you end up operating the older instance without
        # noticing the second one** (this really happened).
        cls.allow_reuse_address = False
        server = cls((host, port), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        self._stopping = True
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers = []


# --- HTTP request handlers --------------------------------------------------


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        # Access logs are not printed. Mixed into the app log, they would make
        # it unreadable.
        pass

    def _send_bytes(self, code: int, ctype: str, body: bytes,
                    headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict, translate: bool = True) -> None:
        """Return JSON. **By default it follows the language of the control
        page.**

        State and explanations all pass through here, so they can be
        translated in one place. **This is never used for the captions
        themselves.** The content of a meeting must not be translated here, so
        `_send_lines` calls this with `translate=False`.
        """
        if translate:
            payload = i18n.apply_json(payload, config.UI_LANG)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, "application/json; charset=utf-8", body)

    def _send_lines(self, web: WebCaptions, query: dict) -> None:
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            since = 0
        try:
            pv = int(query.get("pv", ["-1"])[0])
        except ValueError:
            pv = -1
        nxt, lines, partial, partial_v = web.poll(
            max(since, 0), config.LONGPOLL_WAIT_SEC, pv)
        # **Captions are not translated here.** What passes through is the
        # content of the meeting itself.
        self._send_json(
            200, {"next": nxt, "lines": lines, "partial": partial, "pv": partial_v},
            translate=False)

    def _read_json(self, limit: int | None = None) -> dict:
        cap = MAX_BODY if limit is None else limit
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > cap:
            raise ValueError("要求が大きすぎる。")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSONとして読めない。") from exc
        if not isinstance(data, dict):
            raise ValueError("JSONのオブジェクトではない。")
        return data


def _host_page(meeting_name: str) -> bytes:
    """The page where a host pastes the token.

    **It is a separate page from the control page.** The reader is the meeting
    host, not the operator. It has nothing to do with the language setting of
    the control page either, so it shows Japanese and English side by side.
    **Nothing else can be done from here.** There is one input field and one
    send button.
    """
    name = (meeting_name or "").replace("&", "&amp;").replace("<", "&lt;")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zoom caption token</title>
<style>
  /* **This page stands on its own.** The color scheme of the control page
     (`STYLE`) is not loaded here, so the colors are written directly. They
     match the light scheme of the control page. */
  :root {{ color-scheme: light; }}
  body {{ margin: 0; padding: 24px 18px; background: #ffffff; color: #1f2328;
         font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif;
         line-height: 1.7; }}
  main {{ max-width: 620px; margin: 0 auto; }}
  h1 {{ font-size: 19px; margin: 0 0 4px; }}
  .meet {{ color: #0969da; font-weight: 600; }}
  p {{ font-size: 15px; color: #424a53; margin: 10px 0; }}
  .en {{ color: #59636e; font-size: 14px; }}
  ol {{ font-size: 15px; color: #424a53; padding-left: 22px; }}
  input {{ width: 100%; box-sizing: border-box; font: inherit; font-size: 15px;
           color: #1f2328; background: #ffffff; border: 1px solid #c2cad2;
           border-radius: 8px; padding: 11px 12px; margin: 10px 0; }}
  button {{ font: inherit; font-size: 16px; color: #ffffff; background: #0969da;
            border: 0; border-radius: 8px; padding: 11px 26px; cursor: pointer; }}
  button:disabled {{ opacity: .5; cursor: default; }}
  #msg {{ font-size: 15px; margin-top: 14px; min-height: 1.6em; }}
  .ok {{ color: #1a7f37; }}
  .ng {{ color: #cf222e; }}
</style>
</head>
<body>
<main>
  <h1>Zoom字幕トークン <span class="meet">{name}</span></h1>
  <p class="en">Zoom caption token</p>
  <ol>
    <li>Zoomの<b>「字幕」→「∧」→「手動字幕の設定」</b>で、手動字幕を有効にする<br>
      <span class="en">Turn manual captions on: Captions → ∧ → Manual captions setup</span></li>
    <li><b>「APIトークンをコピー」</b>を選ぶ<br>
      <span class="en">Choose “Copy the API token”</span></li>
    <li>下に貼って送る<br>
      <span class="en">Paste it below and send</span></li>
  </ol>
  <input id="t" type="text" placeholder="https://....zoom.us/closedcaption?id=..."
         autocomplete="off" spellcheck="false">
  <button id="go">送信 / Send</button>
  <div id="msg"></div>
</main>
<script>
  const t = document.getElementById("t");
  const go = document.getElementById("go");
  const msg = document.getElementById("msg");
  async function send() {{
    const value = t.value.trim();
    if (!value) {{ msg.textContent = "トークンを貼ること。/ Paste the token.";
                   msg.className = "ng"; return; }}
    go.disabled = true;
    try {{
      const r = await fetch(location.pathname, {{
        method: "POST", headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ token: value }}),
      }});
      const d = await r.json().catch(() => ({{}}));
      if (r.ok) {{
        msg.textContent = d.ok || "受け取った。/ Received.";
        msg.className = "ok";
        t.value = "";                      // do not leave it on the screen
      }} else {{
        msg.textContent = d.error || ("送れない（" + r.status + "）");
        msg.className = "ng";
        go.disabled = false;
      }}
    }} catch (e) {{
      msg.textContent = "送れない。/ Could not send.";
      msg.className = "ng";
      go.disabled = false;
    }}
  }}
  go.addEventListener("click", send);
  t.addEventListener("keydown", (e) => {{ if (e.key === "Enter") {{ send(); }} }});
</script>
</body>
</html>
""".encode("utf-8")


def _viewer_page(web: WebCaptions) -> bytes:
    """Build the viewer page.

    **It is built on every request.** The path (`/v/<meeting id>`) is also
    baked into the JS inside the page. If the page were built once and kept,
    then **after switching meetings the page would be served at the new path
    while the JS inside it still called the old one and got a 404.**
    Building is only string replacement, so the cost is negligible (the same
    as `_control_page`).
    """
    return (
        _head("Live Captions", web.lines)
        + VIEWER_BODY.replace(
            "__FEED_JS__",
            FEED_JS.replace("__FEED__", web.viewer_path + "/lines")
            .replace("__HISTORY__", str(HISTORY))
            .replace("__SOURCE_DEFAULT__", "true"),
        )
    ).encode("utf-8")


def _viewer_handler(web: WebCaptions):
    """The viewer server. **This is what goes outside.**

    It serves exactly these four paths. **Do not add more.**

        GET  /v/<meeting>        the caption page
        GET  /v/<meeting>/lines  the captions themselves
        GET  /h/<meeting>/<key>  the page where a host pastes the token
        POST /h/<meeting>/<key>  receive the token

    The last one is **the only endpoint that can change state from outside.**
    Adding anything here weakens the reason for keeping the control page on
    127.0.0.1. Read the explanation at the top of `web.py` before adding
    anything.
    """

    class Handler(_Base):
        def _host_parts(self, path: str) -> tuple[str, str] | None:
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "h":
                return parts[1], parts[2]
            return None

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == web.viewer_path:
                self._send_bytes(200, "text/html; charset=utf-8", _viewer_page(web))
                return
            if u.path == web.viewer_path + "/lines":
                self._send_lines(web, parse_qs(u.query))
                return
            found = self._host_parts(u.path)
            if found is not None:
                meeting_id, host_id = found
                items = {m.id: m for m in web.meetings.items()}
                meeting = items.get(meeting_id)
                ok = (meeting is not None
                      and meeting_id == web.meetings.active_id
                      and meeting.host_id
                      and hmac.compare_digest(host_id, meeting.host_id)
                      and web.host_window_open(meeting_id))
                if not ok:
                    # **Return the same 404 as a wrong path.** Revealing that
                    # "the key is right but it is outside the window" would
                    # tell the caller that an endpoint exists there.
                    self.send_error(404)
                    return
                self._send_bytes(200, "text/html; charset=utf-8",
                                 _host_page(meeting.name))
                return
            # Without the path, nothing is visible. `/` returns 404 as well.
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            found = self._host_parts(u.path)
            if found is None:
                self.send_error(404)
                return
            meeting_id, host_id = found
            try:
                # **Use a small limit.** What arrives is a single URL.
                body = self._read_json(limit=config.HOST_MAX_BODY)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)}, translate=False)
                return
            peer = self.client_address[0] if self.client_address else ""
            code, message = web.host_take_token(
                meeting_id, host_id, str(body.get("token", "")), peer)
            if code == 404:
                self.send_error(404)
                return
            # **Do not translate.** This page is read by the host, and it has
            # nothing to do with the language of the control page.
            self._send_json(code, {"error" if code >= 400 else "ok": message},
                            translate=False)

    return Handler


def _meetings_page(web: WebCaptions, lang: str) -> bytes:
    """The meeting management page. **Built on every request**, so that the
    language can be switched."""
    page = (_head("Live Captions ・ 会議の管理", web.lines)
            + meetings_page.BODY.replace("__COPY_JS__", COPY_JS))
    return i18n.apply(page, lang).encode("utf-8")


def _control_page(web: WebCaptions, lang: str) -> bytes:
    """Build the control page.

    **It is built on every request.** Switching the language reloads the page,
    so a page built once and kept would stay in the old language. Building is
    only string replacement, so the cost is negligible.
    """
    page = _head("Live Captions ・ 操作", web.lines) + CONTROL_BODY.replace(
        "__FEED_JS__",
        FEED_JS.replace("__FEED__", "/api/lines")
        .replace("__HISTORY__", str(HISTORY))
        .replace("__SOURCE_DEFAULT__", "false"),
    ).replace("__COPY_JS__", COPY_JS)
    # **Do the language substitution first.** `__UI_LANG__` is the name of a
    # language itself, so it must not pass through the translation table.
    return i18n.apply(page, lang).replace("__UI_LANG__", lang).encode("utf-8")


def _control_handler(web: WebCaptions):
    """The control server. **By default it is reachable only from
    127.0.0.1.**

    With `--control-bind`, it also listens on tailnet addresses. That breaks
    the assumption that only someone sitting at this machine can reach it, so
    `_same_origin` below **refuses requests made from another page.**
    """

    class Handler(_Base):
        def _same_origin(self) -> bool:
            """Check whether the request came from this page. If not, refuse
            it and return False.

            **A page open in a browser can send requests to other addresses in
            the background.** It cannot read the answers (the browser blocks
            that), but the requests arrive. In other words, an unrelated page
            could press "Quit" or "Start delivery".

            A request carries an `Origin` header. **We were not looking at
            it.** Anything that did not come from our own page is refused.

            A request with no `Origin` is allowed. `curl` and local tools do
            not send one, and **a browser always sends it on a POST.** No
            header means the caller is not a browser.
            """
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            if origin in web.allowed_origins():
                return True
            # **Record the reason.** On a machine that runs unattended, the
            # log is the only trace.
            print(f"[{time.strftime('%H:%M:%S')}] 操作        別のページからの操作を断った"
                  f"（Origin: {origin[:100]}）")
            self._send_json(403, {"error": "この画面以外からは操作できない。"})
            return False
        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send_bytes(200, "text/html; charset=utf-8",
                                 _control_page(web, config.UI_LANG))
            elif u.path == "/meetings":
                # Meeting management. **It exists only on the control port.**
                # Neither the meeting names nor the host URLs may go outside.
                self._send_bytes(200, "text/html; charset=utf-8",
                                 _meetings_page(web, config.UI_LANG))
            elif u.path == "/api/lines":
                self._send_lines(web, parse_qs(u.query))
            elif u.path == "/api/status":
                self._send_json(200, web.status())
            elif u.path == "/api/qr":
                # **A QR code can be produced for any meeting.** That is how
                # you save the QR code of next week's meeting today and put it
                # in the announcement. Without `id`, it is the meeting being
                # delivered now.
                q = parse_qs(u.query)
                mid = q.get("id", [""])[0]
                target = web.meeting_url(mid) if mid else web.public_url()
                self._send_qr(target, q)
            elif u.path == "/api/glossary":
                if web.glossary is None:
                    self._send_json(503, {"error": "用語集の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.glossary.status())
            elif u.path == "/api/tuning":
                if web.tuning is None:
                    self._send_json(503, {"error": "設定の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.tuning.status())
            elif u.path == "/api/direction":
                if web.direction is None:
                    self._send_json(503, {"error": "字幕の向きの受け口が用意できていない。"})
                else:
                    self._send_json(200, web.direction.status())
            elif u.path == "/api/records/latest":
                # Which record can be downloaded. **Control port only.**
                if web.records is None:
                    self._send_json(503, {"error": "記録の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.records.latest())
            elif u.path == "/api/records/file":
                self._send_record(parse_qs(u.query).get("fmt", ["md"])[0])
            elif u.path == "/api/glossary/file":
                # Download a glossary. **Never let a table exist only inside
                # the caption PC.**
                if web.glossary is None:
                    self._send_json(503, {"error": "用語集の受け口が用意できていない。"})
                    return
                try:
                    name, text = web.glossary.read(
                        parse_qs(u.query).get("name", [""])[0])
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_bytes(
                    200, "text/tab-separated-values; charset=utf-8",
                    text.encode("utf-8"),
                    {"Content-Disposition": _attachment(f"{name}.tsv")})
            elif u.path == "/api/devices":
                if web.audio is None:
                    self._send_json(503, {"error": "音声の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.audio.devices())
            else:
                self.send_error(404)

        def _send_record(self, fmt: str) -> None:
            """Return **the single latest record** as a download.

            **Control port only.** It is the content of the meeting itself, so
            it is not reachable from the viewer side or through the tunnel.

            **The server decides which record to return.** It never accepts a
            string from the page that names a file. Accepting one would mean
            writing code to validate it.

            **The `.md` form is built on every request.** A `.md` file is
            written only when the app stops, so it does not exist during a
            meeting, nor after a power loss. Building it means both cases
            download the same way.

            **A record that is still being written is marked as unfinished**
            (`final`). Giving it the same heading as a finished record would
            let someone mistake a partial record for a complete one.
            """
            if web.records is None:
                self._send_json(503, {"error": "記録の受け口が用意できていない。"})
                return
            path = web.records.latest_path()
            if path is None:
                self._send_json(404, {"error": "まだ記録が無い。"})
                return
            stem = path.stem
            live = web.transcript is not None and web.transcript.path.stem == stem
            try:
                if fmt == "jsonl":
                    body = path.read_bytes()
                    ctype = "application/x-ndjson"
                    name = f"{stem}.jsonl"
                else:
                    body = transcript_mod.markdown_of(
                        path, final=not live).encode("utf-8")
                    ctype = "text/markdown; charset=utf-8"
                    name = f"{stem}.md"
            except OSError as exc:
                self._send_json(500, {"error": f"記録が読めない: {exc}"})
                return
            self._send_bytes(200, ctype, body,
                             {"Content-Disposition": _attachment(name)})

        def _send_qr(self, url: str, query: dict | None = None) -> None:
            """Return a QR code for the viewer URL.

            SVG is enough for showing it on the page. **With `dl=1` it returns
            a PNG as an attachment.** Pasting it into a slide or a chat needs
            a QR code as a file, not one inside the page.
            """
            if not url:
                self.send_error(404)
                return
            import segno

            q = query or {}
            download = q.get("dl", ["0"])[0] == "1"
            name = q.get("name", [""])[0].strip()
            buf = io.BytesIO()
            if download:
                # **Make the saved one large.** Anyone can shrink an image,
                # but stretching a coarse PNG makes it unreadable.
                segno.make(url, error="m").save(
                    buf, kind="png", scale=16, border=4,
                    dark="#000000", light="#ffffff",
                )
                self._send_bytes(
                    200, "image/png", buf.getvalue(),
                    headers={"Content-Disposition":
                             f'attachment; filename="{_qr_filename(name)}"'},
                )
                return
            # White background, with a quiet zone. A coarser code survives the
            # compression of screen sharing better.
            segno.make(url, error="m").save(
                buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff"
            )
            self._send_bytes(200, "image/svg+xml", buf.getvalue())

        def do_POST(self) -> None:  # noqa: N802
            if not self._same_origin():
                return
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                self._shutdown()
                return
            if path not in ("/api/token", "/api/zoom", "/api/tunnel",
                            "/api/engine", "/api/device", "/api/glossary",
                            "/api/glossary/upload", "/api/glossary/delete",
                            "/api/vnc",
                            "/api/tuning", "/api/tuning/save", "/api/direction",
                            "/api/lang", "/api/meetings", "/api/schedule",
                            "/api/chat"):
                self.send_error(404)
                return
            try:
                # **Only the upload endpoint is large.** The others need a few
                # KB, so do not widen them. The limit is the glossary limit
                # plus room for the JSON around it.
                body = self._read_json(
                    glossary_mod.MAX_UPLOAD_BYTES + MAX_BODY
                    if path == "/api/glossary/upload" else None)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            if path == "/api/tunnel":
                self._tunnel(body)
                return

            if path == "/api/meetings":
                self._meetings(body)
                return

            if path == "/api/schedule":
                self._schedule(body)
                return

            if path == "/api/chat":
                self._chat()
                return

            if path == "/api/device":
                if web.audio is None:
                    self._send_json(503, {"error": "音声の受け口が用意できていない。"})
                    return
                raw = body.get("index")
                try:
                    web.audio.set_device(None if raw is None else int(raw))
                except (ValueError, TypeError) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, web.status())
                return

            if path == "/api/glossary":
                if web.glossary is None:
                    self._send_json(503, {"error": "用語集の受け口が用意できていない。"})
                    return
                raw = body.get("names")
                if not isinstance(raw, list):
                    self._send_json(400, {"error": "names は配列で渡すこと。"})
                    return
                try:
                    st = web.glossary.select([str(n) for n in raw])
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, st)
                return

            if path == "/api/vnc":
                # **The point is that it can be opened and closed during a
                # meeting.** x11vnc only attaches to an X display that is
                # already running, so neither the meeting nor the captions
                # stop. If this were a start-up option only, then the moment
                # you most want to look inside, "something looks wrong during
                # a meeting", the only way in would be to rebuild.
                if web.vnc is None:
                    self._send_json(503, {"error": "VNCの受け口が用意できていない。"})
                    return
                st = (web.vnc.start() if bool(body.get("on"))
                      else web.vnc.stop())
                self._send_json(200, st)
                return

            if path in ("/api/glossary/upload", "/api/glossary/delete"):
                # **A glossary grows with every meeting.** Never let it be
                # editable only by logging into the caption PC. The tables
                # live on a volume, so they survive rebuilding the container
                # (`LIVECAPTION_GLOSSARY_DIR`).
                if web.glossary is None:
                    self._send_json(503, {"error": "用語集の受け口が用意できていない。"})
                    return
                name = str(body.get("name", ""))
                try:
                    if path.endswith("/upload"):
                        text = body.get("text")
                        if not isinstance(text, str):
                            self._send_json(400, {"error": "text は文字列で渡すこと。"})
                            return
                        st = web.glossary.upload(name, text)
                    else:
                        st = web.glossary.remove(name)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                except OSError as exc:
                    self._send_json(500, {"error": f"用語集を書けない: {exc}"})
                    return
                self._send_json(200, st)
                return

            if path == "/api/lang":
                try:
                    lang = config.apply_ui_lang(str(body.get("lang", "")))
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                config.remember_ui_lang(lang)
                print(f"[{time.strftime('%H:%M:%S')}] 言語        操作画面を "
                      f"{'日本語' if lang == 'ja' else 'English'} にした")
                # **Return it untranslated.** It is the name of a language.
                self._send_json(200, {"lang": lang}, translate=False)
                return

            if path == "/api/direction":
                if web.direction is None:
                    self._send_json(503, {"error": "字幕の向きの受け口が用意できていない。"})
                    return
                try:
                    st = web.direction.select(body.get("name"))
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, st)
                return

            if path in ("/api/tuning", "/api/tuning/save"):
                if web.tuning is None:
                    self._send_json(503, {"error": "設定の受け口が用意できていない。"})
                    return
                try:
                    if path == "/api/tuning/save":
                        st = web.tuning.save()
                    else:
                        raw = body.get("values")
                        if not isinstance(raw, dict):
                            self._send_json(400, {"error": "values はオブジェクトで渡すこと。"})
                            return
                        st = web.tuning.set(raw)
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                except OSError as exc:
                    # .env cannot be written (read-only, being synced, and so
                    # on). Do not stop the meeting over it.
                    self._send_json(500, {"error": f".env に書けなかった: {exc}"})
                    return
                self._send_json(200, st)
                return

            if path == "/api/engine":
                if web.engine is None:
                    self._send_json(503, {"error": "生成の受け口が用意できていない。"})
                    return
                on = bool(body.get("on"))
                web.engine.set_running(on)
                st = web.status()
                if not on:
                    st["also"] = self._stop_outputs()
                self._send_json(200, st)
                return

            if web.control is None:
                self._send_json(503, {"error": "操作の受け口がまだ用意できていない。"})
                return
            try:
                if path == "/api/token":
                    web.control.set_token(str(body.get("token", "")))
                else:
                    web.control.set_enabled(bool(body.get("on")))
            except Exception as exc:  # noqa: BLE001
                # A malformed token and similar failures: these must be told
                # to the person who pressed the button.
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _chat(self) -> None:
            """Post the caption URL and its QR code into the chat of the
            meeting we are currently in.

            **This endpoint assumes that the person who pressed the button is
            watching.** Posting automatically for a scheduled meeting is done
            in `schedule.py`, and needs the per-meeting checkbox.
            """
            url = web.public_url() or web.viewer_url()
            if not url:
                self._send_json(400, {"error": "投げる先のURLがまだ無い。"})
                return
            try:
                # **The implementation differs per platform.** Windows uses
                # window classes and the clipboard API; Linux (in the
                # container) uses a window on Xvfb and `xdotool`. The
                # interface is the same (`meeting_window` / `compose` /
                # `qr_file` / `post`).
                if os.name == "nt":
                    from . import zoom_chat
                else:
                    from . import zoom_chat_linux as zoom_chat

                live = web.meetings.active_id
                name = next((m.name for m in web.meetings.items()
                             if m.id == live), "")
                shot = zoom_chat.qr_file(url, name)
                done = zoom_chat.post(zoom_chat.compose(url),
                                      [shot] if shot else [])
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self._send_json(200, dict(web.status(), chat=done))

        def _stop_outputs(self) -> dict:
            """When caption generation stops, close the outputs as well.

            **The worst case is that captions keep flowing after you believe
            you stopped them** (reported 2026-09-20). Stopping generation
            alone leaves the tunnel up and the participant URL open, and the
            Zoom side still says "sending". It also becomes a way for the
            captions of the next meeting to reach the previous token.

            **If one of the two fails, still try the other.** Generation has
            already stopped, so raising an exception here would gain nothing.
            This returns what was actually stopped, and the page uses that to
            choose its message.
            """
            done = {"tunnel": False, "zoom": False}
            if web.tunnel is not None:
                try:
                    if web.tunnel.status().get("state") in ("on", "starting"):
                        web.tunnel.stop()
                        done["tunnel"] = True
                except Exception as exc:  # noqa: BLE001
                    print(f"[停止] 配信を止められない: {exc}")
            if web.control is not None:
                try:
                    if web.status().get("active"):
                        web.control.set_enabled(False)
                        done["zoom"] = True
                except Exception as exc:  # noqa: BLE001
                    print(f"[停止] Zoom字幕を止められない: {exc}")
            return done

        def _tunnel(self, body: dict) -> None:
            """Start and stop delivery, and switch the route.

            **Choosing a route is a separate action from starting.** Choosing
            alone must not put anything outside. When only `kind` arrives,
            switch the route and stay stopped.
            """
            if web.tunnel is None:
                self._send_json(503, {"error": "配信の受け口が用意できていない。"})
                return
            if "kind" in body:
                try:
                    web.tunnel.select(str(body["kind"]))
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                if "on" not in body:
                    self._send_json(200, web.status())
                    return
            if body.get("on"):
                st = web.tunnel.start()
                if st["state"] == "error":
                    self._send_json(400, {"error": st["error"]})
                    return
            else:
                web.tunnel.stop()
            self._send_json(200, web.status())

        def _schedule(self, body: dict) -> None:
            """Commands for the scheduler: start now, stop, skip, and clear a
            failure."""
            if web.scheduler is None:
                self._send_json(503, {"error": "予定の受け口が用意できていない。"})
                return
            action = str(body.get("action", ""))
            try:
                if action == "start":
                    # **Run the selected meeting once, without waiting for its
                    # schedule.** The steps are the same as for a scheduled
                    # run (delivery, Zoom, generation, chat).
                    web.scheduler.start_now(str(body.get("id", "")))
                elif action == "stop":
                    web.scheduler.stop_now()
                elif action == "skip":
                    web.scheduler.skip_next()
                elif action == "ack":
                    web.scheduler.ack()
                else:
                    self._send_json(400, {"error": f"知らない操作: 「{action}」。"})
                    return
            except ValueError as exc:
                # No meeting selected, one already running, and so on. Report
                # it to the person who pressed the button.
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _meetings(self, body: dict) -> None:
            """Create, select and delete meetings.

            **Creating and selecting are separate actions.** While you prepare
            the URL of a future meeting, today's delivery must not switch to
            it.
            """
            action = str(body.get("action", ""))
            try:
                if action == "create":
                    web.meetings.create(str(body.get("name", "")))
                elif action == "select":
                    web.meetings.select(str(body.get("id", "")))
                elif action == "delete":
                    web.meetings.delete(str(body.get("id", "")))
                elif action == "schedule":
                    fields = body.get("fields")
                    if not isinstance(fields, dict):
                        raise ValueError("fields はオブジェクトで渡すこと。")
                    web.meetings.set_schedule(str(body.get("id", "")), **fields)
                elif action == "host_key":
                    web.meetings.ensure_host_id(str(body.get("id", "")), renew=True)
                elif action == "host_rearm":
                    web.host_rearm(str(body.get("id", "")))
                else:
                    raise ValueError(f"知らない操作: 「{action}」。")
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _shutdown(self) -> None:
            """Stop the caption app itself.

            **Write the answer first, then ask it to stop.** This server
            closes together with the app, so in the other order the browser
            may never receive the answer.
            """
            if web.on_shutdown is None:
                self._send_json(503, {"error": "終了の受け口がまだ用意できていない。"})
                return
            self._send_json(200, {"ok": True})
            web.on_shutdown("ブラウザの操作画面")

    return Handler
