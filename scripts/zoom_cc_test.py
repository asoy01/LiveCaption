#!/usr/bin/env python3
"""A tool to check that the Zoom caption API (third-party closed caption)
works.

Measure everything at once:
    python scripts/zoom_cc_test.py --auto "<API token URL>"

Watch how the captions behave on screen:
    python scripts/zoom_cc_test.py --display "<API token URL>"

Start from a seq you name (the default continues local/seq_state.json):
    python scripts/zoom_cc_test.py --display --seq 5000 "<API token URL>"

Important: seq has to keep increasing over the whole meeting session. Roll it
back and Zoom drops the caption silently, without an error. If the caption
program dies during a real meeting and you restart it with seq back at 1, the
captions stop without a word.

Interactive:
    python scripts/zoom_cc_test.py "<API token URL>"

How to get the token:
    1. Start a meeting as the host
    2. Click the "^" next to "Captions" in the toolbar
    3. "Set up manual captioner" -> "Copy the API token"
    The token lasts for that session only. Take a new one if you reopen the
    meeting.

Before you start:
    Join the same meeting from a second device (a phone is fine) and turn
    "Show captions" on. That is how you see what the captions look like while
    you measure.

Interactive commands:
    <any text>          send that text as a caption
    /rate N [interval]  send N short captions, interval seconds apart
    /long N             send one caption of N characters
    /lang xx-XX         change the language code (default en-US)
    /seq                show the current seq
    /quit               exit
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 10.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# seq has to keep increasing over the whole meeting session. It is saved per
# meeting, so that a rerun of the script continues where it left off.
STATE_PATH = PROJECT_ROOT / "local" / "seq_state.json"


class CaptionSender:
    def __init__(self, base_url: str, lang: str = "en-US", seq: int | None = None) -> None:
        self.base_url = base_url.rstrip("&")
        self.lang = lang
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.base_url).query)
        self.meeting_key = query.get("id", ["unknown"])[0]
        self.seq = seq if seq is not None else self._load_seq()

    def _state(self) -> dict:
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _load_seq(self) -> int:
        """Continue from what was sent before in the same meeting. Roll seq
        back and Zoom drops the caption silently."""
        return self._state().get(self.meeting_key, 0) + 1

    def _save_seq(self) -> None:
        state = self._state()
        state[self.meeting_key] = self.seq - 1
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def post(self, text: str):
        """Send one caption. Returns (status, body, seconds taken). status is
        None when the connection fails."""
        url = f"{self.base_url}&seq={self.seq}&lang={self.lang}"
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                status = res.status
                body = res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            return None, str(e.reason), time.perf_counter() - t0
        dt = time.perf_counter() - t0
        if status == 200:
            # seq moves on only after a success. A retry does not increase it.
            self.seq += 1
            self._save_seq()
        return status, body, dt


def show(status, body, dt) -> None:
    label = "OK " if status == 200 else "NG "
    code = status if status is not None else "----"
    line = f"  {label} {code}  {dt * 1000:6.0f} ms"
    if body.strip():
        line += f"  {body.strip()[:120]}"
    print(line)


def burst(sender: CaptionSender, n: int, interval: float, prefix: str):
    """Send n in a row and return (successes, failures, list of delays,
    seconds measured)."""
    ok, ng, lat = 0, 0, []
    t0 = time.perf_counter()
    for i in range(n):
        status, body, dt = sender.post(f"{prefix} {i + 1}/{n}")
        lat.append(dt)
        if status == 200:
            ok += 1
        else:
            ng += 1
            if ng <= 2:
                show(status, body, dt)
        if interval:
            time.sleep(interval)
    return ok, ng, lat, time.perf_counter() - t0


def cmd_rate(sender: CaptionSender, args: list[str]) -> None:
    n = int(args[0]) if args else 20
    interval = float(args[1]) if len(args) > 1 else 0.0
    print(f"  Sending {n} times (interval {interval} s)")
    ok, ng, lat, total = burst(sender, n, interval, "rate test")
    print(f"  ok {ok} / failed {ng} / total {total:.2f} s ({n / total:.1f}/s)")
    print(f"  per call  min {min(lat)*1000:.0f} ms"
          f"  mean {sum(lat)/len(lat)*1000:.0f} ms  max {max(lat)*1000:.0f} ms")


def make_text(n: int) -> str:
    """Build a string of n characters, with the position written as a number
    every 10 characters.

    The last number you can see on screen is how many characters were shown.
    The final five characters are [END], so if it was shown to the end, you
    see that.
    """
    if n < 10:
        return "x" * n
    parts = []
    pos = 0
    while pos < n:
        pos += 10
        parts.append(f"{pos:04d}......")
    text = "".join(parts)[:n]
    return text[:-5] + "[END]" if n >= 15 else text


def cmd_long(sender: CaptionSender, args: list[str]) -> None:
    n = int(args[0]) if args else 1000
    print(f"  Sending {n} characters")
    show(*sender.post(make_text(n)))


def auto(sender: CaptionSender) -> int:
    print("=" * 62)
    print("Zoom caption API: all measurements")
    print("=" * 62)
    print("Turn on \"Show Captions\" on a second device, and watch that screen.")
    print()

    # 1. Does it reach Zoom
    print("1. Does it reach Zoom")
    status, body, dt = sender.post("Hello. This is a caption API test.")
    show(status, body, dt)
    if status != 200:
        print("\n   It does not reach Zoom. The rest is not measured.")
        print("   In the web portal, check \"Manual captions\" and \"Allow use of caption API token\".")
        return 1
    print("   => Look at the Zoom screen and check that the English line appears.")
    print()

    # 2. How long a caption stays on screen
    print("2. How long a caption stays on screen")
    print("   One marker is sent. Count the seconds until it leaves the Zoom screen.")
    sender.post("=== WATCH THIS LINE. Count seconds until it disappears. ===")
    for i in range(10, 0, -1):
        print(f"\r   waiting {i} s...", end="", flush=True)
        time.sleep(1)
    print("\r   (done waiting)              ")
    print()

    # 3. Rate
    print("3. The upper limit of the sending rate")
    print("   It sends repeatedly at several intervals. The measured rate is lower")
    print("   than the interval because of the round trip time.")
    print(f"   {'interval':>10}  {'ok':>4} {'fail':>4}  {'rate':>9}  {'avg delay':>9}")
    for label, n, interval in [
        ("1.0 s", 10, 1.0),
        ("0.5 s", 10, 0.5),
        ("0.2 s", 15, 0.2),
        ("0.1 s", 20, 0.1),
        ("none", 20, 0.0),
    ]:
        ok, ng, lat, total = burst(sender, n, interval, f"rate {label}")
        print(f"   {label:>10}  {ok:>4} {ng:>4}  {n/total:>7.1f}/s  {sum(lat)/len(lat)*1000:>6.0f} ms")
        time.sleep(1.0)
    print("   => The rows above the first failure are the usable limit.")
    print()

    # 4. Number of characters
    print("4. How many characters one call can send")
    for n in (100, 500, 1000, 2000, 4000, 8000):
        status, body, dt = sender.post(make_text(n))
        mark = "OK" if status == 200 else f"NG ({status})"
        note = f"  {body.strip()[:80]}" if status != 200 and body.strip() else ""
        print(f"   {n:>5} chars  {mark}{note}")
        time.sleep(0.5)
    print("   => On the Zoom screen, also check that the text is shown to the end.")
    print("      The POST can succeed while the display is cut off partway.")
    print()

    print("=" * 62)
    print("Measurement done. Record the results together with what you saw on screen.")
    return 0


def interactive(sender: CaptionSender) -> int:
    print(f"Sending to: {sender.base_url[:60]}...")
    print(f"Language:   {sender.lang}")
    print("Type a line and it is sent as a caption. /quit to stop.")
    print()
    while True:
        try:
            line = input("caption> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.startswith("/"):
            parts = line.split()
            cmd, args = parts[0], parts[1:]
            if cmd == "/quit":
                return 0
            elif cmd == "/seq":
                print(f"  seq = {sender.seq}")
            elif cmd == "/lang":
                if args:
                    sender.lang = args[0]
                print(f"  lang = {sender.lang}")
            elif cmd == "/rate":
                cmd_rate(sender, args)
            elif cmd == "/long":
                cmd_long(sender, args)
            else:
                print(f"  Unknown command: {cmd}")
            continue
        show(*sender.post(line))


def display_test(sender: CaptionSender) -> int:
    """A mode for watching how the captions behave. It sends slowly, taking
    its time."""
    print("=" * 62)
    print("Zoom captions: how the display behaves")
    print("=" * 62)
    print("Watch only the screen of the second device. You need not look at this one.")
    print("It takes about 2 minutes.")
    print()

    input("Press Enter when you are ready > ")
    print()

    print("--- Test 0: does a line arrive when seq jumps ---")
    print("  Two lines are sent. The second one jumps seq by 500.")
    sender.post("TEST 0a ... normal seq. You should see this line.")
    time.sleep(4)
    sender.seq += 500
    sender.post("TEST 0b ... seq jumped by 500. Do you see this line too?")
    time.sleep(4)
    print("  => Did you see 0b? If you did, seq may jump (coming back after a")
    print("     restart is then easier). If not, seq must go up by 1 each time.")
    print()

    print("--- Test A: how long until a caption disappears ---")
    print("  One line is sent. Count the seconds until it leaves the screen.")
    sender.post("TEST A ... count the seconds until this line disappears")
    for i in range(1, 31):
        print(f"  {i} s", end="  ", flush=True)
        if i % 10 == 0:
            print()
        time.sleep(1)
    print()
    print("  => Record the number of seconds. If it is still there after 30 s,")
    print("     record that instead.")
    print()

    print("--- Test B: what a new caption does to the previous one ---")
    print("  Three lines are sent, 3 seconds apart. See whether the previous line")
    print("  scrolls up, or disappears and is replaced.")
    for i, line in enumerate(
        ["TEST B ... line ONE of three",
         "TEST B ... line TWO of three",
         "TEST B ... line THREE of three"], 1):
        sender.post(line)
        print(f"  sent line {i}")
        time.sleep(3)
    time.sleep(2)
    print("  => How many lines are on the screen? Is ONE still there?")
    print()

    print("--- Test C: how much of a long line is shown ---")
    print("  The position is written as a number every 10 characters.")
    print("  Record how far you can see.")
    for n in (200, 500, 1000, 2000):
        sender.post(make_text(n))
        print(f"  sent {n} characters")
        time.sleep(8)
    print("  => For each one, record the last number you could see on the screen.")
    print()

    print("=" * 62)
    print("What to record:")
    print("  A. The seconds until a caption disappears")
    print("  B. Does the previous line scroll or get replaced? How many lines show at once")
    print("  C. How many characters were shown")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    auto_mode = "--auto" in args
    disp_mode = "--display" in args
    for flag in ("--auto", "--display"):
        if flag in args:
            args.remove(flag)
    seq = None
    if "--seq" in args:
        i = args.index("--seq")
        seq = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 1
    sender = CaptionSender(args[0], seq=seq)
    print(f"seq starts at {sender.seq} (continuing from {STATE_PATH.name})")
    if auto_mode:
        return auto(sender)
    if disp_mode:
        return display_test(sender)
    return interactive(sender)


if __name__ == "__main__":
    sys.exit(main())
