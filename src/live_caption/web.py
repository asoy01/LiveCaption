"""ブラウザに字幕を出す。閲覧画面と操作画面、それぞれ別のポートで出す。

Zoom字幕APIはホスト権限（トークンのコピー）が要る。自分がホストでない会議では
使えない。そこで、ローカルのHTTPサーバでブラウザに字幕を出す道を用意してある。
見せ方は2つある。

1. **画面共有する。** 閲覧画面を全画面にして共有する。外には出ない
2. **閲覧URLを配る。** 参加者が自分の端末で開く。一時トンネルが要る（`tunnel.py`）

## 画面は2つ。ポートも分ける

    127.0.0.1:8081  操作画面  トークン・送信の開始停止・トンネル・記録・終了
    <bind>:8080     閲覧画面  字幕を見るだけ

**パスではなくポートで分けてある。** トンネルもリバースプロキシもオリジンごと
通すので、経路の書き間違い1つで、URLを知った人が字幕アプリを止められるようになる。
サーバを2つ立てておけばその事故は起きない。

**操作画面は常に `127.0.0.1` である。** `--web-bind` は閲覧画面にしか効かない。

閲覧画面には**推測できない経路**が付く（`/v/<ランダム>`）。閲覧ポートの `/` は
404を返すので、トンネルのURLだけでは何も見えない。

## 転送は長ポーリング。SSEは使えない

    GET /v/<secret>/lines?since=N  →  {"next": 42, "lines": [...]}

新しい行が出るまで最大25秒待ってから返す。**応答が毎回完結するので、途中で
溜め込む中継でも通る。**

**SSEは使えない。** 一時トンネル（TryCloudflare）は `text/event-stream` を端で
溜め込み、接続が閉じるまでブラウザに届かない。実測でも15秒間1バイトも来なかった。
長ポーリングは同じ経路で 0.4〜1.0秒だった。根拠は local/HANDOFF.md の
「実測結果: 一時トンネル」。

体感の遅延はSSEと変わらない。再接続はただのHTTP要求なので、むしろ単純である。

## 日本語の認識結果と文字の大きさ

どちらも**ブラウザ側で決める。** サーバは常に両方の行を送り、表示するかは
ブラウザが決める。参加者が自分の端末で読むので、文字の大きさも本人が変えられる。
選択は `localStorage` に残る。会議中に何度触ってもサーバとはやり取りしない。
"""

from __future__ import annotations

import io
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config, tunnel as tunnel_mod

# 画面に残す履歴の数。これを超えた分は古いほうから捨てる。
# 途中から開いた参加者に、直前の流れが見えるだけあればよい。
HISTORY = 200
# 受け取るリクエストの上限。トークンURLは長いが、数KBあれば足りる。
MAX_BODY = 64 * 1024


# --- 見た目（閲覧と操作で共通） ---------------------------------------------

STYLE = """
  :root {
    --bg: #0d1117; --fg: #f0f6fc; --ja: #7d8590; --line: #21262d;
    --accent: #2f81f7; --ok: #3fb950; --ng: #f85149;
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
    border: 1px solid var(--line); background: #161b22;
    font-size: 13px; white-space: nowrap;
  }
  .pill.on { border-color: var(--ok); color: var(--ok); }
  .pill.off { border-color: #30363d; color: var(--ja); }
  .pill.bad { border-color: var(--ng); color: var(--ng); }
  .pill.warn { border-color: #9e6a03; color: #d29922; }

  .toggle { display: flex; align-items: center; gap: 9px; cursor: pointer; user-select: none; }
  .toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
  .track {
    width: 42px; height: 24px; border-radius: 12px;
    background: #30363d; position: relative; transition: background .15s; flex: 0 0 auto;
  }
  .track::after {
    content: ""; position: absolute; top: 3px; left: 3px;
    width: 18px; height: 18px; border-radius: 50%;
    background: var(--fg); transition: transform .15s;
  }
  .toggle input:checked + .track { background: var(--accent); }
  .toggle input:checked + .track::after { transform: translateX(18px); }
  .toggle input:focus-visible + .track { outline: 2px solid var(--accent); outline-offset: 2px; }

  button {
    font: inherit; font-size: 14px; color: var(--fg);
    background: #21262d; border: 1px solid #30363d; border-radius: 6px;
    padding: 7px 14px; cursor: pointer;
  }
  button:hover { background: #30363d; }
  button:disabled { opacity: .45; cursor: default; }
  button.primary { background: var(--accent); border-color: var(--accent); }
  button.primary:hover { background: #4a91f9; }
  button.danger { border-color: #6e2b2b; color: #ff9c94; }
  .zoombtn { padding: 6px 11px; font-size: 15px; line-height: 1; }

  main {
    flex: 1 1 auto; overflow-y: auto;
    display: flex; flex-direction: column;
    padding: 18px 32px 28px; scrollbar-width: thin;
  }
  /* 字幕は下から積む。新しい行が下に出て、古い行が上へ押し上げられる。 */
  #lines {
    display: flex; flex-direction: column; justify-content: flex-end;
    margin-top: auto; min-height: calc(var(--lines) * var(--size) * 1.35);
  }
  .row { font-size: var(--size); line-height: 1.35; padding: 2px 0; }
  .row.en { color: var(--fg); }
  .row.ja { color: var(--ja); font-size: calc(var(--size) * .62); }
  body.hide-ja .row.ja { display: none; }
  .row.enter { animation: in .18s ease-out; }
  @keyframes in { from { opacity: 0; } to { opacity: 1; } }
  #empty { color: var(--ja); font-size: 18px; }
  /* 入力の音量。**音が来ているかを目で見るためのもの。** */
  .meter {
    width: 110px; height: 9px; border-radius: 5px; background: #21262d;
    border: 1px solid #30363d; overflow: hidden; flex: 0 0 auto;
  }
  .meter > i { display: block; height: 100%; width: 0; background: var(--ok); }
  .meter.hot > i { background: var(--ng); }
  select {
    font: inherit; font-size: 13px; color: var(--fg);
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 7px 8px; flex: 1 1 100%; min-width: 0; max-width: 100%;
  }
  .netstate { color: var(--ng); font-size: 13px; }
"""

# --- 字幕の受信（閲覧と操作で共通） -----------------------------------------
#
# 長ポーリング。取得 → 描画 → すぐ再取得。失敗したら1秒待って再試行する。

FEED_JS = """
  const $ = (id) => document.getElementById(id);
  const lines = $("lines"), main = $("main"), dot = $("dot"), count = $("count");
  const ja = $("ja"), empty0 = $("empty"), netstate = $("netstate");
  const FEED = "__FEED__", MAX = __HISTORY__;
  let n = 0, removedEmpty = false, ended = false, since = 0, fails = 0;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function scrollDown() { main.scrollTop = main.scrollHeight; }

  // --- 日本語トグル。localStorage に残す --------------------------------
  ja.checked = localStorage.getItem("showJa") === "1";
  applyJa();
  ja.addEventListener("change", () => {
    localStorage.setItem("showJa", ja.checked ? "1" : "0");
    applyJa(); scrollDown();
  });
  function applyJa() { document.body.classList.toggle("hide-ja", !ja.checked); }

  // --- 文字の大きさ。読む本人が決める -----------------------------------
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
    lines.appendChild(div);
    while (lines.children.length > MAX) { lines.removeChild(lines.firstChild); }
    if (ev.type !== "asr") { n += 1; count.textContent = n + " lines"; }
    scrollDown();
  }

  async function feed() {
    while (!ended) {
      try {
        const r = await fetch(FEED + "?since=" + since);
        if (!r.ok) { throw new Error("HTTP " + r.status); }
        const d = await r.json();
        since = d.next;
        for (const ev of d.lines) { add(ev); }
        dot.classList.add("on");
        fails = 0;
        netstate.textContent = "";
      } catch (e) {
        dot.classList.remove("on");
        // **黙って止まらない。** 参加者の端末も回線もばらばらなので、
        // 何が起きているか画面に出さないと、誰も原因を追えない。
        fails += 1;
        netstate.textContent = "接続できません（" + e.message + "）。再試行 " + fails + "回目…";
        await sleep(1000);
      }
    }
  }
  feed();
"""


def _head(title: str, lines_: int) -> str:
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>"
        + STYLE.replace("__LINES__", str(lines_))
    )


# --- 閲覧画面 ---------------------------------------------------------------
#
# **操作のマークアップもJSも入れない。** ここは外に出る。

VIEWER_BODY = """</style>
</head>
<body class="hide-ja">
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
    <span>Japanese</span>
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

# --- 操作画面 ---------------------------------------------------------------

CONTROL_BODY = """
  /* --- 左右分割 -----------------------------------------------------------
     **字幕を主にする。** 設定を上に積むと、字幕が下へ押し込められて読めない。
     左に字幕、右に設定を置き、境目をドラッグで動かせるようにする。
     幅は localStorage に残す。 */
  #split {
    flex: 1 1 auto; min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 7px var(--right, 400px);
  }
  #sep {
    background: var(--line); cursor: col-resize; position: relative;
    touch-action: none;
  }
  /* 線そのものは細くしたい。掴む範囲だけ左右に広げる。 */
  #sep::after { content: ""; position: absolute; top: 0; bottom: 0; left: -5px; right: -5px; }
  #sep:hover, #sep.drag { background: var(--accent); }

  /* 字幕の大きさは、窓ではなく**左の欄の幅**で決める。境目を動かすと追従する。
     cqw を解さないブラウザではこの1行ごと無視され、:root の vw 基準が残る。 */
  #main { container-type: inline-size; }
  #main { --size: calc(clamp(18px, 3.4cqw, 42px) * var(--zoom, 1)); }

  /* 窓が狭いときは上下に積む。境目は動かせない。 */
  @media (max-width: 760px) {
    #split { display: flex; flex-direction: column; }
    #sep { display: none; }
    #panel { max-height: 45vh; border-left: 0; border-top: 1px solid var(--line); }
  }

  /* --- 設定の欄 ----------------------------------------------------------- */
  #panel {
    overflow-y: auto; scrollbar-width: thin;
    padding: 14px 16px 20px;
    border-left: 1px solid var(--line); background: #11161d;
    font-size: 13px; color: var(--ja);
  }
  /* まとまりごとに区切る。全部が地続きだと、どこが何の設定か分からない。 */
  .grp { padding: 12px 0; border-bottom: 1px solid var(--line); }
  .grp:first-child { padding-top: 0; }
  .grp:last-child { border-bottom: 0; }
  .grp > h2 {
    margin: 0 0 9px; font-size: 12px; font-weight: 600; letter-spacing: .08em;
    color: #6e7681; text-transform: uppercase;
  }
  .row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 9px; }
  .row2:last-child { margin-bottom: 0; }
  /* 見出しは行を独り占めする。狭い欄で、ラベルと部品を横に並べると折り返しが汚い。 */
  .lbl { flex: 1 0 100%; color: var(--fg); font-size: 13px; }
  /* 用語集のチェックは1行に1つ。名前と語数を並べると横に入りきらない。 */
  .gloss { flex: 1 0 100%; display: flex; align-items: center; gap: 6px;
           cursor: pointer; font-size: 13px; }
  .gloss input { cursor: pointer; }
  input[type=password], input[type=text] {
    font: inherit; font-size: 13px; color: var(--fg);
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 7px 10px; flex: 1 1 100%; min-width: 0;
  }
  input:focus { outline: 2px solid var(--accent); outline-offset: 0; border-color: var(--accent); }
  #msg { font-size: 12px; }
  #msg.ok { color: var(--ok); }
  #msg.ng { color: var(--ng); }
  .hint { font-size: 11.5px; color: #6e7681; line-height: 1.6; }
  .url {
    font-family: ui-monospace, Consolas, monospace; font-size: 12px; color: var(--fg);
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 6px 8px; word-break: break-all; user-select: all; flex: 1 1 100%;
  }
  /* QRは白地でないと読めない端末がある。余白ごと白くする。 */
  #qrbox { display: none; }
  #qrbox.on { display: flex; }
  #qr { background: #fff; padding: 8px; border-radius: 8px; width: 150px; height: 150px; }
  pre.err {
    white-space: pre-wrap; font-size: 12px; color: #ff9c94;
    background: #1b1113; border: 1px solid #6e2b2b; border-radius: 6px;
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
  <label class="toggle">
    <input type="checkbox" id="ja">
    <span class="track"></span>
    <span>日本語</span>
  </label>
</header>

<div id="split">

<main id="main">
  <div id="lines"><div id="empty">字幕を待っています…</div></div>
</main>

<div id="sep" title="ドラッグで幅を変える"></div>

<aside id="panel">

  <div class="grp">
    <h2>字幕の生成</h2>
    <div class="row2">
      <button id="gstart" class="primary">開始</button>
      <button id="gstop" class="danger">停止</button>
      <span id="genState"></span>
    </div>
    <div class="row2 hint" id="genHint">
      開始するまで、<b>音は取り込まれず、認識も翻訳もしない。</b>
      会議に入る前に立ち上げておいてよい。
    </div>
  </div>

  <div class="grp">
    <h2>参加者への配信</h2>
    <div class="row2">
      <button id="tstart" class="primary">トンネルを開始</button>
      <button id="tstop" class="danger">停止</button>
    </div>
    <div class="row2"><span id="tunnelState"></span></div>
    <div class="row2" id="qrbox">
      <img id="qr" alt="閲覧URLのQRコード">
    </div>
    <div class="row2" id="publicUrlRow" style="display:none">
      <span class="url" id="publicUrl"></span>
    </div>
    <div class="row2 hint" id="tunnelHint" style="display:none">
      このURLをQRで配る。参加者はブラウザで開くだけでよい。
      <b>URLは起動のたびに変わる。</b>停止すると、その場で見られなくなる。
    </div>
    <div class="row2" id="tunnelErrBox" style="display:none">
      <pre class="err" id="tunnelErr"></pre>
    </div>
  </div>

  <div class="grp">
    <h2>Zoom字幕</h2>
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
      会議中にホストが取る。「字幕」→「∧」→「手動字幕の設定」→「APIトークンをコピー」。<br>
      入力欄は伏せ字で、登録すると空になる。
    </div>
  </div>

  <div class="grp">
    <h2>画面共有で見せる</h2>
    <div class="row2">
      <span class="url" id="viewerUrl"></span>
    </div>
    <div class="row2">
      <button id="openViewer">閲覧画面を開く</button>
    </div>
    <div class="row2 hint">
      <b>この画面は共有しないこと。</b>共有するのは閲覧画面のほう。
    </div>
  </div>

  <div class="grp">
    <h2>会議の記録</h2>
    <div class="row2">
      <button id="openLog">途中まで読む</button>
      <span id="logState"></span>
    </div>
    <div class="row2" id="logPathRow" style="display:none">
      <span class="url" id="logPath"></span>
    </div>
  </div>

  <div class="grp">
    <h2>音声の入力</h2>
    <div class="row2">
      <select id="devices"><option>読み込み中…</option></select>
    </div>
    <div class="row2">
      <button id="devReload">一覧を更新</button>
    </div>
    <div class="row2">
      <span class="meter" id="meter"><i id="meterBar"></i></span>
      <span id="audioState"></span>
    </div>
    <div class="row2" id="audioErrBox" style="display:none">
      <pre class="err" id="audioErr"></pre>
    </div>
  </div>

  <div class="grp">
    <h2>用語集</h2>
    <div class="row2" id="glossBox">
      <span id="glossLoading">読み込み中…</span>
    </div>
    <div class="row2">
      <span id="glossState"></span>
    </div>
  </div>

  <div class="grp">
    <h2>アプリの終了</h2>
    <div class="row2">
      <button id="quit" class="danger">終了</button>
      <span class="hint">音声の取り込みも認識も止まる</span>
    </div>
    <div class="row2"><span id="msg"></span></div>
  </div>

</aside>
</div>

<script>
__FEED_JS__

  const token = $("token"), save = $("save"), start = $("start"), stop = $("stop");
  const glossBox = $("glossBox"), glossState = $("glossState");
  let glossLoaded = false;
  const gstart = $("gstart"), gstop = $("gstop");
  const gpill = $("genPill"), genState = $("genState");
  const devices = $("devices"), devReload = $("devReload");
  const meter = $("meter"), meterBar = $("meterBar"), audioState = $("audioState");
  const aerrBox = $("audioErrBox"), aerr = $("audioErr");
  let devLoaded = false;
  const quit = $("quit"), tstart = $("tstart"), tstop = $("tstop");
  const msg = $("msg"), pill = $("zoomPill"), zoomState = $("zoomState");
  const tpill = $("tunnelPill"), tstate = $("tunnelState");
  const qrbox = $("qrbox"), qr = $("qr"), publicUrl = $("publicUrl");
  const publicUrlRow = $("publicUrlRow"), tunnelHint = $("tunnelHint");
  const split = $("split"), sep = $("sep");
  const viewerUrl = $("viewerUrl"), openViewer = $("openViewer");
  const terrBox = $("tunnelErrBox"), terr = $("tunnelErr");
  const openLog = $("openLog"), logState = $("logState");
  const logPath = $("logPath"), logPathRow = $("logPathRow");
  let lastQr = "";

  function say(text, ok) { msg.textContent = text; msg.className = ok ? "ok" : "ng"; }

  // --- 左右の境目 ---------------------------------------------------------
  // **字幕が主で、設定は従である。** 幅は本人が決める。localStorage に残す。
  const PANEL_MIN = 260, MAIN_MIN = 280, PANEL_DEFAULT = 400;
  // **本人が決めた幅と、いま出せる幅を分けて持つ。** 窓が一時的に狭くなったときに
  // 縮めた値で上書きすると、窓を広げても元の幅に戻らなくなる。
  let wantW = parseInt(localStorage.getItem("panelW") || "", 10) || PANEL_DEFAULT;

  function fits(w) {
    const vw = window.innerWidth;
    // **窓の幅が分からないうちは切り詰めない。** 描画前に呼ばれると 0 が返ることが
    // あり、そこで詰めると最小幅に張り付いたまま戻らなくなる。
    // 760px 以下は上下に積む見た目になるので、そこでも切り詰めない。
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
  // 表示されたときに幅が決まることがある（隠れたタブ、開いた直後）。
  if (window.ResizeObserver) { new ResizeObserver(applySplit).observe(document.body); }

  sep.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    sep.setPointerCapture(e.pointerId);
    sep.classList.add("drag");
    const move = (ev) => {
      // 境目の右端から窓の右端までが設定欄の幅になる。
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
  // 素早く元に戻したいとき。
  sep.addEventListener("dblclick", () => {
    wantW = PANEL_DEFAULT;
    applySplit();
    localStorage.setItem("panelW", String(wantW));
  });

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
    // --- 字幕の生成。**これが親の関門である。** ---
    gpill.textContent = s.generating ? "生成: 中" : "生成: 停止中";
    gpill.className = "pill " + (s.generating ? "on" : "off");
    genState.textContent = s.generating
      ? "音を取り込み、認識と翻訳をしている"
      : "止まっている。音は取り込んでいない";
    gstart.disabled = !!s.generating;
    gstop.disabled = !s.generating;
    // 動き出したら、始め方の説明は畳む。画面を混ませない。
    $("genHint").style.display = s.generating ? "none" : "";

    // --- 音声の入力 ---
    const a = s.audio || {};
    // メーターが振れるのは生成中だけである。止まっていればデバイスは閉じている。
    const lv = Math.min(Number(a.level || 0), 1);
    meterBar.style.width = (lv * 100).toFixed(0) + "%";
    meter.classList.toggle("hot", lv > 0.95);
    if (!a.selectable) {
      audioState.textContent = a.name || "—";
      devices.disabled = true; devReload.disabled = true;
    } else if (!s.generating) {
      audioState.textContent = "停止中（音量は生成中に出る）";
    } else if (lv < 0.005) {
      // 無音のまま気づかないのが一番困る。はっきり出す。
      audioState.innerHTML = '<span class="ng">音が来ていない</span>';
    } else {
      audioState.textContent = "音が来ている（peak " + lv.toFixed(2) + "）"
        + (a.dropped ? "　取りこぼし " + a.dropped : "");
    }
    if (a.error) { aerr.textContent = a.error; aerrBox.style.display = ""; }
    else { aerrBox.style.display = "none"; }
    // 一覧は最初の1回だけ取る。開いている選択肢を勝手に差し替えない。
    // **選べないときも取りに行く。** 取らないと「読み込み中…」が残ってしまう。
    if (!devLoaded) { loadDevices(); }
    if (!glossLoaded) { loadGlossary(); }

    // --- Zoom ---
    let label, cls;
    if (s.dry_run)        { label = "Zoom: --dry-run"; cls = "off"; }
    else if (s.active)    { label = "Zoom: 送信中";    cls = "on";  }
    else if (s.has_token) { label = "Zoom: 停止中";    cls = "off"; }
    else                  { label = "Zoom: 未登録";    cls = "off"; }
    // 失敗は隠さない。トークンが切れていても200以外で返るだけなので、
    // ここに出ていないと、字幕が届いていないことに気づけない。
    if (s.failed > 0) { label += "（失敗 " + s.failed + "）"; cls = "bad"; }
    pill.textContent = label; pill.className = "pill " + cls;

    const parts = [];
    parts.push(s.has_token ? ("登録済み（会議 " + s.meeting + "）") : "トークン未登録");
    if (s.has_token) { parts.push("seq " + s.seq); parts.push("送信 " + s.sent + " / 失敗 " + s.failed); }
    // 生成が止まっていれば、Zoomを送信中にしても字幕は1行も出ない。黙っていない。
    if (s.active && !s.generating) { parts.push("**生成が止まっているので何も流れない**"); }
    zoomState.textContent = parts.join("　");

    start.disabled = s.dry_run || !s.has_token || s.active;
    stop.disabled = !s.active;
    save.disabled = s.dry_run;

    // --- トンネル ---
    const t = s.tunnel || {};
    const st = t.state || "off";
    const names = { off: "配信: 停止中", starting: "配信: 起動中…", on: "配信: 中", error: "配信: 失敗" };
    const cls2 = { off: "off", starting: "warn", on: "on", error: "bad" };
    tpill.textContent = names[st] || "配信: —";
    tpill.className = "pill " + (cls2[st] || "off");
    tstate.textContent = st === "on" ? "参加者が閲覧URLを開ける"
                       : st === "starting" ? "cloudflared を起こしている"
                       : t.available ? "" : "cloudflared が無い";
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

    viewerUrl.textContent = s.viewer_url || "";

    // --- 記録 ---
    // **読める形（.md）が書かれるのは終了時である。** 途中で見たいことがあるので、
    // そのときはここから読む。書き込みが失敗していたら、それを隠さない。
    const g = s.transcript || {};
    if (!g.on) {
      logState.textContent = "残さない（--no-save）";
      logPath.textContent = "";
      openLog.disabled = true;
    } else if (g.error) {
      logState.innerHTML = '<span class="ng">残せていない: ' + g.error + "</span>";
      logPath.textContent = g.path || "";
      openLog.disabled = true;
    } else {
      logState.textContent = g.count + " 文を記録した（終了時に読める形も書く）";
      logPath.textContent = g.path || "";
      openLog.disabled = (g.count === 0);
    }
    // 空のパス欄は、中身の無い箱として見えてしまう。出さない。
    logPathRow.style.display = logPath.textContent ? "" : "none";
  }

  // --- 字幕の生成 ---------------------------------------------------------
  async function setGen(on) {
    const b = on ? gstart : gstop;
    b.disabled = true;
    try {
      showStatus(await post("/api/engine", { on: on }));
      say(on ? "字幕の生成を開始した。" : "字幕の生成を停止した。", true);
    } catch (e) { say(String(e.message), false); b.disabled = false; }
  }
  gstart.addEventListener("click", () => setGen(true));
  gstop.addEventListener("click", () => setGen(false));

  // --- 音声の入力 ---------------------------------------------------------
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
      // 同じ名前が MME・DirectSound・WASAPI に出るので、ホストAPIまで見せる。
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

  // 選んだ時点で切り替える。「適用」は押させない。会議中に押し忘れると、
  // 選んだつもりのデバイスから音が来ていないことに気づけない。
  devices.addEventListener("change", async () => {
    devices.disabled = true;
    try {
      const s = await post("/api/device", { index: Number(devices.value) });
      showStatus(s);
      say("入力を " + (s.audio ? s.audio.name : "") + " にした。", true);
    } catch (e) {
      say(String(e.message), false);
      // 切り替えに失敗したら、選択欄を実際に使っているデバイスへ戻す。
      // 選択欄だけが変わったままだと、どれで録っているのか分からなくなる。
      await loadDevices();
      return;
    }
    devices.disabled = false;
  });

  // --- 用語集 -------------------------------------------------------------
  // **会議によって語彙が違う。** 必要な表だけを重ねる。1つの大きな表を
  // 全部の会議で使うと、関係の無い語が認識の keywords を食う。
  function showGlossary(g) {
    const sel = new Set(g.selected || []);
    glossBox.innerHTML = "";
    if (!g.sets || !g.sets.length) {
      glossBox.textContent = "docs/glossary/ に .tsv が無い";
    } else {
      for (const s of g.sets) {
        const lab = document.createElement("label");
        lab.className = "gloss";
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = s.name; cb.checked = sel.has(s.name);
        // 選んだ時点で切り替える。「適用」は押させない。
        cb.addEventListener("change", applyGlossary);
        lab.appendChild(cb);
        lab.appendChild(document.createTextNode(" " + s.name + "（" + s.terms + " 語）"));
        glossBox.appendChild(lab);
      }
    }
    const parts = [];
    parts.push("合計 " + g.terms + " 語");
    parts.push("認識に渡す語 " + g.keywords + " / " + g.limit);
    glossState.textContent = parts.join("　");
    // **上限で切れた語は認識に届かない。** 黙って落とすと、表に足したのに
    // 効かない、という分かりにくい失敗になる。
    if (g.dropped > 0) {
      glossState.innerHTML = parts.join("　")
        + '<br><span class="ng">' + g.dropped
        + " 語が上限で切り捨てられた。選ぶ表を減らすこと</span>";
    }
  }

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
      await loadGlossary();   // 失敗したら実際の選択へ戻す
      return;
    }
    for (const c of glossBox.querySelectorAll("input")) { c.disabled = false; }
  }

  // --- Zoom ---------------------------------------------------------------
  save.addEventListener("click", async () => {
    const value = token.value.trim();
    if (!value) { say("トークンを貼ること。", false); return; }
    save.disabled = true;
    try {
      const s = await post("/api/token", { token: value });
      token.value = "";                 // 画面に残さない
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

  // --- トンネル -----------------------------------------------------------
  tstart.addEventListener("click", async () => {
    tstart.disabled = true;
    try {
      showStatus(await post("/api/tunnel", { on: true }));
      say("トンネルを起こしている。URLが出るまで数秒かかる。", true);
    } catch (e) { say(String(e.message), false); }
  });
  tstop.addEventListener("click", async () => {
    try { showStatus(await post("/api/tunnel", { on: false })); say("トンネルを止めた。閲覧URLは死んだ。", true); }
    catch (e) { say(String(e.message), false); }
  });

  openViewer.addEventListener("click", () => { window.open(viewerUrl.textContent, "_blank"); });

  // --- 記録 ---------------------------------------------------------------
  // 別のタブに出す。**この画面は共有しないので、記録もここから出さない。**
  openLog.addEventListener("click", () => { window.open("/api/transcript", "_blank"); });

  // --- 終了 ---------------------------------------------------------------
  // 本体が終わればサーバも消える。**返事が来なくても成功でありうる。**
  // 通信の失敗を失敗として出さないこと。
  quit.addEventListener("click", async () => {
    if (!confirm("字幕アプリを終了する。よろしいですか。")) { return; }
    quit.disabled = true;
    try { await post("/api/shutdown", {}); } catch (e) { /* 上の通り */ }
    ended = true;
    clearInterval(timer);
    dot.classList.remove("on");
    pill.textContent = "終了した"; pill.className = "pill off";
    tpill.textContent = "配信: 停止"; tpill.className = "pill off";
    zoomState.textContent = ""; qrbox.classList.remove("on");
    gpill.textContent = "生成: 停止"; gpill.className = "pill off";
    genState.textContent = "";
    for (const b of [save, start, stop, quit, tstart, tstop, gstart, gstop,
                     devReload]) { b.disabled = true; }
    devices.disabled = true; meterBar.style.width = "0";
    say("終了した。この画面を閉じる。", true);

    // このタブを閉じる。**閉じられないことがある。**
    // ブラウザは「スクリプトが開いた窓」しか閉じさせない。この画面はアプリが
    // webbrowser.open() で開いたものなので、閉じる要求が黙って無視される場合がある。
    // そのときのために、閉じられなかったと分かる画面を出す。
    window.close();
    setTimeout(() => {
      if (ended) { showClosed(); }
    }, 400);
  });

  function showClosed() {
    // ここに来たのは、ブラウザがタブを閉じさせなかったときである。
    // 操作の並びをそのまま残すと、まだ使えるように見える。全部消す。
    document.getElementById("panel").remove();
    sep.remove();
    split.style.setProperty("--right", "0px");
    main.innerHTML =
      '<div style="margin:auto;text-align:center;color:var(--ja);font-size:20px;line-height:1.9">'
      + "字幕アプリを終了した。<br>このタブは閉じてよい。"
      + '<div style="font-size:15px;margin-top:14px">'
      + "（ブラウザがタブを自動で閉じない設定になっている）</div></div>";
  }

  // --- 状態の取り直し ------------------------------------------------------
  // 送信数・失敗数・トンネルの起動は、こちらが操作しなくても変わる。
  // 相手は自分の機体なので、定期的に取り直してよい。
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
    """字幕の履歴を持ち、閲覧と操作の2つのHTTPサーバを動かす。

    `caption()` と `asr()` は本体のイベントループから呼ばれる。どちらも待たない
    （履歴に足して起こすだけ）ので、字幕の送信を遅らせない。

    `control` には `app.ZoomControl`、`on_shutdown` には `app.App.request_stop`、
    `tunnel` には `tunnel.Tunnel` が入る。**操作はHTTPサーバのスレッドから来る。**
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
        self.lines = lines
        self.bind = bind
        self.control = None
        # 字幕の生成（録音・認識・翻訳）の開始と停止（app.EngineControl）。
        self.engine = None
        # 入力デバイスの一覧と差し替え（app.AudioControl）。
        self.audio = None
        # 用語集の一覧と選び直し（app.GlossaryControl）。
        self.glossary = None
        self.on_shutdown = None
        self.tunnel: tunnel_mod.Tunnel | None = None
        # 会議の記録（transcript.Transcript）。--no-save のときは None のまま。
        # **操作画面からしか見えない。** 閲覧側には出さない。
        self.transcript = None
        # 閲覧画面の経路。トンネルのホスト名もランダムだが、経路にも入れておく。
        self.secret = secrets.token_urlsafe(config.VIEWER_SECRET_BYTES)
        self.viewer_path = f"/v/{self.secret}"

        # 字幕の履歴と、長ポーリングの待ち合わせ。
        self._events: list[dict] = []
        self._first = 0  # _events[0] の通し番号
        self._cond = threading.Condition()
        self._waiting = 0
        self._servers: list[ThreadingHTTPServer] = []

    # --- URL ---------------------------------------------------------------

    def viewer_url(self) -> str:
        """自分の機体から開く閲覧URL。画面共有で見せるときはこれ。"""
        host = "localhost" if self.bind in ("127.0.0.1", "0.0.0.0", "") else self.bind
        return f"http://{host}:{self.port}{self.viewer_path}"

    def control_url(self) -> str:
        return f"http://localhost:{self.control_port}"

    def public_url(self) -> str:
        """トンネル越しの閲覧URL。張っていなければ空。"""
        url = self.tunnel.url if self.tunnel is not None else ""
        return f"{url}{self.viewer_path}" if url else ""

    # --- 本体から呼ぶ -------------------------------------------------------

    def caption(self, text: str) -> None:
        """英語の字幕を1行流す。"""
        self._emit({"type": "caption", "text": text, "time": time.strftime("%H:%M:%S")})

    def asr(self, text: str) -> None:
        """日本語の認識結果を流す。表示するかはブラウザ側のトグルが決める。"""
        self._emit({"type": "asr", "text": text, "time": time.strftime("%H:%M:%S")})

    def _emit(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            if len(self._events) > HISTORY:
                drop = len(self._events) - HISTORY
                del self._events[:drop]
                self._first += drop
            self._cond.notify_all()

    # --- 長ポーリング -------------------------------------------------------

    def poll(self, since: int, wait: float) -> tuple[int, list[dict]]:
        """`since` 以降の行を返す。無ければ最大 `wait` 秒待つ。

        `since` が履歴から落ちるほど古ければ、残っている最古から返す。
        途中から開いた画面には `since=0` で全履歴が渡る。
        """
        deadline = time.monotonic() + wait
        with self._cond:
            self._waiting += 1
            try:
                while True:
                    end = self._first + len(self._events)
                    if since < self._first:
                        since = self._first
                    if since < end:
                        return end, self._events[since - self._first :]
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return end, []
                    self._cond.wait(left)
            finally:
                self._waiting -= 1

    @property
    def viewers(self) -> int:
        """いま字幕を待っている画面の数。1画面が1本の要求を占める。"""
        with self._cond:
            return self._waiting

    # --- 状態 ---------------------------------------------------------------

    def status(self) -> dict:
        """操作画面に返す状態。Zoomとトンネルの両方を1つにまとめる。"""
        if self.control is not None:
            st = dict(self.control.status())
        else:
            st = {"enabled": False, "has_token": False, "active": False,
                  "dry_run": False, "meeting": "", "seq": 0, "sent": 0, "failed": 0}
        if self.tunnel is not None:
            st["tunnel"] = self.tunnel.status()
        else:
            st["tunnel"] = {"state": "off", "url": "", "error": "",
                            "available": tunnel_mod.find_cloudflared() is not None}
        st["generating"] = bool(self.engine.status()["generating"]) if self.engine else True
        st["audio"] = self.audio.status() if self.audio else {
            "selectable": False, "name": "—", "index": None,
            "level": 0.0, "dropped": 0, "error": ""}
        st["viewer_url"] = self.viewer_url()
        st["public_url"] = self.public_url()
        if self.transcript is not None:
            st["transcript"] = {
                "on": True,
                "path": str(self.transcript.path),
                "count": len(self.transcript.records),
                "error": self.transcript.error,
            }
        else:
            st["transcript"] = {"on": False, "path": "", "count": 0, "error": ""}
        return st

    # --- 起動と停止 ---------------------------------------------------------

    def start(self) -> None:
        """閲覧と操作、2つのサーバを立てる。

        **操作画面は必ず 127.0.0.1 に縛る。** `bind` は閲覧にしか効かない。
        """
        viewer = self._serve(self.bind, self.port, _viewer_handler(self))
        try:
            control = self._serve("127.0.0.1", self.control_port, _control_handler(self))
        except OSError:
            viewer.shutdown()
            viewer.server_close()
            raise
        self._servers = [viewer, control]

    def _serve(self, host: str, port: int, handler) -> ThreadingHTTPServer:  # noqa: ANN001
        # ThreadingHTTPServer にするのは、長ポーリングが1本ずつ居座るため。
        server = ThreadingHTTPServer((host, port), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers = []


# --- HTTPの受け口 -----------------------------------------------------------


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        # 本体のログに混ざると読めなくなるので、アクセスログは出さない。
        pass

    def _send_bytes(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, "application/json; charset=utf-8", body)

    def _send_lines(self, web: WebCaptions, query: dict) -> None:
        try:
            since = int(query.get("since", ["0"])[0])
        except ValueError:
            since = 0
        nxt, lines = web.poll(max(since, 0), config.LONGPOLL_WAIT_SEC)
        self._send_json(200, {"next": nxt, "lines": lines})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("要求が大きすぎる。")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSONとして読めない。") from exc
        if not isinstance(data, dict):
            raise ValueError("JSONのオブジェクトではない。")
        return data


def _viewer_handler(web: WebCaptions):
    """閲覧画面。**ここが外に出る。字幕を返すことしかしない。**"""
    page = (
        _head("Live Captions", web.lines)
        + VIEWER_BODY.replace(
            "__FEED_JS__",
            FEED_JS.replace("__FEED__", web.viewer_path + "/lines").replace(
                "__HISTORY__", str(HISTORY)
            ),
        )
    ).encode("utf-8")

    class Handler(_Base):
        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == web.viewer_path:
                self._send_bytes(200, "text/html; charset=utf-8", page)
            elif u.path == web.viewer_path + "/lines":
                self._send_lines(web, parse_qs(u.query))
            else:
                # 経路を知らなければ何も見えない。`/` も404にする。
                self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            self.send_error(404)

    return Handler


def _control_handler(web: WebCaptions):
    """操作画面。**127.0.0.1 からしか届かない。**"""
    page = (
        _head("Live Captions ・ 操作", web.lines)
        + CONTROL_BODY.replace(
            "__FEED_JS__",
            FEED_JS.replace("__FEED__", "/api/lines").replace(
                "__HISTORY__", str(HISTORY)
            ),
        )
    ).encode("utf-8")

    class Handler(_Base):
        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                self._send_bytes(200, "text/html; charset=utf-8", page)
            elif u.path == "/api/lines":
                self._send_lines(web, parse_qs(u.query))
            elif u.path == "/api/status":
                self._send_json(200, web.status())
            elif u.path == "/api/qr":
                self._send_qr(web.public_url())
            elif u.path == "/api/glossary":
                if web.glossary is None:
                    self._send_json(503, {"error": "用語集の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.glossary.status())
            elif u.path == "/api/devices":
                if web.audio is None:
                    self._send_json(503, {"error": "音声の受け口が用意できていない。"})
                else:
                    self._send_json(200, web.audio.devices())
            elif u.path == "/api/transcript":
                self._send_transcript()
            else:
                self.send_error(404)

        def _send_transcript(self) -> None:
            """ここまでの記録を読める形で返す。

            **操作ポートにしか無い。** 会議の中身そのものなので、閲覧側や
            トンネルの向こうからは触れない。ブラウザで読めるように
            `text/plain` で返す（保存させるのが目的ではない）。
            """
            if web.transcript is None:
                self._send_bytes(404, "text/plain; charset=utf-8",
                                 "記録を残さない設定で起動している（--no-save）。".encode())
                return
            self._send_bytes(200, "text/plain; charset=utf-8",
                             web.transcript.markdown().encode("utf-8"))

        def _send_qr(self, url: str) -> None:
            if not url:
                self.send_error(404)
                return
            import segno

            buf = io.BytesIO()
            # 白地・余白つき。画面共有の圧縮でも読めるように、粗い方が良い。
            segno.make(url, error="m").save(
                buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff"
            )
            self._send_bytes(200, "image/svg+xml", buf.getvalue())

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/shutdown":
                self._shutdown()
                return
            if path not in ("/api/token", "/api/zoom", "/api/tunnel",
                            "/api/engine", "/api/device", "/api/glossary"):
                self.send_error(404)
                return
            try:
                body = self._read_json()
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            if path == "/api/tunnel":
                self._tunnel(bool(body.get("on")))
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

            if path == "/api/engine":
                if web.engine is None:
                    self._send_json(503, {"error": "生成の受け口が用意できていない。"})
                    return
                web.engine.set_running(bool(body.get("on")))
                self._send_json(200, web.status())
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
                # トークンの書式違いなど、操作した人に伝えるべき失敗。
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _tunnel(self, on: bool) -> None:
            if web.tunnel is None:
                self._send_json(503, {"error": "トンネルの受け口が用意できていない。"})
                return
            if on:
                st = web.tunnel.start()
                if st["state"] == "error":
                    self._send_json(400, {"error": st["error"]})
                    return
            else:
                web.tunnel.stop()
            self._send_json(200, web.status())

        def _shutdown(self) -> None:
            """字幕アプリそのものを終わらせる。

            **先に返事を書いてから頼む。** 本体が終わればこのサーバも閉じるので、
            順序を逆にするとブラウザが応答を受け取れないことがある。
            """
            if web.on_shutdown is None:
                self._send_json(503, {"error": "終了の受け口がまだ用意できていない。"})
                return
            self._send_json(200, {"ok": True})
            web.on_shutdown("ブラウザの操作画面")

    return Handler
