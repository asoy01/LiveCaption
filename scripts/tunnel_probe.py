#!/usr/bin/env python3
"""一時トンネル（TryCloudflare）越しに、どの転送方式が通るかを測る。

**本体に手を入れる前に、これで確かめること。** Cloudflare は「一時トンネルは
Server-Sent Events に対応しない」と明記している。事実なら `web.py` の転送を
変える必要があり、設計が変わる。

    pixi run python scripts/tunnel_probe.py            # サーバだけ立てる
    pixi run python scripts/tunnel_probe.py --measure <URL>   # 外から測る

使い方:

    1. この窓で   pixi run python scripts/tunnel_probe.py
    2. 別の窓で   local/bin/cloudflared.exe tunnel --url http://127.0.0.1:8099
    3. 3つ目の窓で pixi run python scripts/tunnel_probe.py --measure https://xxx.trycloudflare.com

1秒ごとに1行増えるだけのサーバである。測る側は、行が届くまでの時間を見る。

- **長ポーリング**が通れば、1秒前後で返る
- 溜め込まれていれば、待ち時間いっぱい（10秒）まで何も返らない
- SSEは、届くか届かないかを見る
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8099
# 測るときの待ち時間。本体（25秒）より短くして、判定を早くする。
WAIT = 10.0

_lines: list[str] = []
_cond = threading.Condition()


def _grow() -> None:
    """1秒ごとに1行増やす。"""
    while True:
        time.sleep(1.0)
        with _cond:
            _lines.append(f"line {len(_lines)} at {time.strftime('%H:%M:%S')}")
            _cond.notify_all()


def _poll(since: int, wait: float) -> tuple[int, list[str]]:
    deadline = time.monotonic() + wait
    with _cond:
        while True:
            if since < len(_lines):
                return len(_lines), _lines[since:]
            left = deadline - time.monotonic()
            if left <= 0:
                return len(_lines), []
            _cond.wait(left)


PAGE = """<!doctype html>
<meta charset="utf-8"><title>tunnel probe</title>
<body style="font:16px system-ui;padding:20px">
<h3>tunnel probe</h3>
<p>long-poll: <b id="lp">…</b></p>
<p>sse: <b id="sse">…</b></p>
<pre id="out"></pre>
<script>
let since = 0, n = 0;
const out = document.getElementById("out");
async function loop() {
  for (;;) {
    try {
      const t = Date.now();
      const r = await fetch("/lines?since=" + since);
      const d = await r.json();
      since = d.next;
      for (const l of d.lines) { out.textContent += "[lp " + (Date.now()-t) + "ms] " + l + "\\n"; }
      document.getElementById("lp").textContent = "届いている（" + (++n) + "回）";
    } catch (e) {
      document.getElementById("lp").textContent = "失敗: " + e;
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}
loop();
let m = 0;
const es = new EventSource("/sse");
es.onmessage = (e) => {
  document.getElementById("sse").textContent = "届いている（" + (++m) + "回）";
  out.textContent += "[sse] " + e.data + "\\n";
};
es.onerror = () => { if (!m) { document.getElementById("sse").textContent = "届かない"; } };
</script>
</body>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        pass

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/lines":
            q = parse_qs(u.query)
            since = int(q.get("since", ["0"])[0])
            wait = float(q.get("wait", [str(WAIT)])[0])
            nxt, lines = _poll(since, wait)
            body = json.dumps({"next": nxt, "lines": lines}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            since = len(_lines)
            try:
                while True:
                    since, lines = _poll(since, WAIT)
                    for line in lines or ["(keepalive)"]:
                        self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self.send_error(404)


def measure(base: str) -> int:
    """外から、長ポーリングとSSEの届き方を測る。"""
    base = base.rstrip("/")
    print(f"測る先: {base}")
    print()

    # --- 長ポーリング ---
    print("長ポーリング")
    ok = 0
    try:
        with urllib.request.urlopen(f"{base}/lines?since=0", timeout=WAIT + 15) as r:
            since = json.loads(r.read().decode())["next"]
        for i in range(3):
            t = time.monotonic()
            with urllib.request.urlopen(
                f"{base}/lines?since={since}", timeout=WAIT + 15
            ) as r:
                d = json.loads(r.read().decode())
            dt = time.monotonic() - t
            got = len(d["lines"])
            since = d["next"]
            verdict = "**溜め込まれている**" if got == 0 else "通る"
            print(f"  {i + 1}回目: {dt:5.2f}秒で {got} 行  → {verdict}")
            if got:
                ok += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  失敗: {exc}")
    print(f"  判定: {'通る' if ok >= 2 else '**使えない**'}")
    print()

    # --- SSE ---
    print("SSE")
    try:
        req = urllib.request.Request(f"{base}/sse", headers={"Accept": "text/event-stream"})
        t = time.monotonic()
        with urllib.request.urlopen(req, timeout=WAIT + 5) as r:
            line = r.readline()
            dt = time.monotonic() - t
        print(f"  最初の1行が {dt:5.2f}秒で届いた: {line[:60]!r}")
        print(f"  判定: {'通る' if dt < WAIT else '**溜め込まれている**'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  判定: **使えない**（{type(exc).__name__}: {exc}）")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--measure", metavar="URL", help="外から測る（トンネルのURL）")
    p.add_argument("--port", type=int, default=PORT)
    args = p.parse_args()

    if args.measure:
        return measure(args.measure)

    threading.Thread(target=_grow, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"http://127.0.0.1:{args.port}  （1秒ごとに1行増える）")
    print("次に、別の窓で:")
    print(f"  local/bin/cloudflared.exe tunnel --url http://127.0.0.1:{args.port}")
    print("Ctrl+C で終了する。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
