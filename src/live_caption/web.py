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

import hmac
import io
import json
import re
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import captions as captions_mod
from . import config, folder_pick, i18n, meetings, tunnel as tunnel_mod
from . import meetings_page

# 画面に残す履歴の数。これを超えた分は古いほうから捨てる。
# 途中から開いた参加者に、直前の流れが見えるだけあればよい。
HISTORY = 200
# 受け取るリクエストの上限。トークンURLは長いが、数KBあれば足りる。
MAX_BODY = 64 * 1024


# --- 見た目（閲覧と操作で共通） ---------------------------------------------

STYLE = """
  /* --- 配色 ---------------------------------------------------------------
     **明るい地に濃い字にする。** 暗い配色は見にくい（2026-09-20、麻生の指摘）。

     **色はここにしか書かない。** 以前は各所に直接書いていたので、配色を変えるには
     50か所を追いかける必要があった。暗い配色に戻すなら、この block だけを
     入れ替えればよい。 */
  :root {
    /* **これを書かないと、選択欄・日付の窓・スクロールバーだけが暗いままになる。**
       OSが暗い配色の機体で、そこだけ黒く残る。 */
    color-scheme: light;
    --bg:      #ffffff;   /* 地 */
    --panel:   #f6f8fa;   /* 右の欄の地 */
    --field:   #ffffff;   /* 入力欄・URL欄の地 */
    --btn:     #f6f8fa;   /* ボタンの地 */
    --hover:   #eaeef2;   /* 触れたときの地 */
    --fg:      #1f2328;   /* 本文 */
    --ja:      #59636e;   /* 元の言語の行 */
    --muted:   #59636e;   /* 見出しと注記 */
    --dim:     #848d97;   /* さらに薄い印 */
    --line:    #d8dee4;   /* 細い罫 */
    --line2:   #c2cad2;   /* 部品の枠 */
    --accent:  #0969da; --accent-h: #0a58ca; --on-accent: #ffffff;
    --knob:    #ffffff;   /* トグルのつまみ */
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
  /* 字幕は下から積む。新しい行が下に出て、古い行が上へ押し上げられる。 */
  #lines {
    display: flex; flex-direction: column; justify-content: flex-end;
    margin-top: auto; min-height: calc(var(--lines) * var(--size) * 1.35);
  }
  .row { font-size: var(--size); line-height: 1.35; padding: 2px 0; }
  .row.en { color: var(--fg); }
  .row.ja { color: var(--ja); font-size: calc(var(--size) * .62); }
  body.hide-ja .row.ja { display: none; }
  /* 書きかけの文字起こし。**薄くしない。** 文字起こしの行はもともと小さく、色も
     落としてある。そのうえ透かすと読めない。まだ伸びている途中であることは、
     末尾の … で分かる。文字起こしのトグルに従う。 */
  .row.partial::after { content: "…"; margin-left: .15em; }
  .row.enter { animation: in .18s ease-out; }
  @keyframes in { from { opacity: 0; } to { opacity: 1; } }
  #empty { color: var(--ja); font-size: 18px; }
  /* 入力の音量。**音が来ているかを目で見るためのもの。** */
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

# --- 字幕の受信（閲覧と操作で共通） -----------------------------------------
#
# 長ポーリング。取得 → 描画 → すぐ再取得。失敗したら1秒待って再試行する。

FEED_JS = """
  const $ = (id) => document.getElementById(id);
  const lines = $("lines"), main = $("main"), dot = $("dot"), count = $("count");
  const ja = $("ja"), empty0 = $("empty"), netstate = $("netstate");
  const FEED = "__FEED__", MAX = __HISTORY__, SOURCE_DEFAULT = __SOURCE_DEFAULT__;
  let n = 0, removedEmpty = false, ended = false, since = 0, fails = 0, pv = -1;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function scrollDown() { main.scrollTop = main.scrollHeight; }

  // --- 日本語トグル。localStorage に残す --------------------------------
  // **閲覧画面は既定でオンにする。** 配ったURLを開いた人は、字幕が翻訳だけだと
  // 話者が何と言ったのか確かめられない。操作画面は麻生が見るものなので既定で切る。
  const showJaStored = localStorage.getItem("showJa");
  ja.checked = showJaStored === null ? SOURCE_DEFAULT : showJaStored === "1";
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
    lines.insertBefore(div, partialRow);
    while (lines.children.length > MAX + 1) { lines.removeChild(lines.firstChild); }
    if (ev.type !== "asr") { n += 1; count.textContent = n + " lines"; }
    scrollDown();
  }

  // --- 書きかけの文字起こし ---------------------------------------------
  // **文が確定するまで数秒、画面には何も出ない。** 話している人の言葉は溜まって
  // いるだけで、読む側からは止まって見える。届いた分をそのまま薄く出しておき、
  // 確定したら普通の行に置き換わる。
  //
  // 行は常に最後尾に置く。確定した行はこの手前に挿す（`add` を見ること）。
  const partialRow = document.createElement("div");
  partialRow.className = "row ja partial";
  partialRow.hidden = true;
  lines.appendChild(partialRow);

  function showPartial(text) {
    if (partialRow.textContent === text) { return; }
    partialRow.textContent = text;
    // **空のときは行ごと消す。** 高さが残ると、字幕が1行ぶん上にずれて見える。
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
        // **確定した行を入れてから書きかけを更新する。** 逆にすると、確定した文が
        // 書きかけとして一瞬もう一度出る。
        if (typeof d.pv === "number") { pv = d.pv; showPartial(d.partial || ""); }
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


def check_zoom_token(url: str) -> None:
    """外から受け取るトークンを、`captions.parse_token` より厳しく見る。

    **`parse_token` を厳しくしてはいけない。** あちらは手元の操作画面と
    `--token` も通る道で、そこは既に信用してよい相手である。
    厳しくするのは、外から届くこの口だけにする。

    `parse_token` が見るのは「http か https」「経路に closedcaption がある」
    「`id=` がある」の3つだけである。**宛先を見ていない。**
    ここを塞がないと、**この口のURLを得た人が自分のサーバのURLを貼って、
    会議の翻訳文をまるごと受け取れる。** ここでいちばん重い穴である。
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
        # http だと字幕の中身が平文で流れる。
        raise captions_mod.TokenError("https のトークンだけを受け付ける。")
    host = (parsed.hostname or "").lower()
    # **部分一致で見ない。** `evil.com/zoom.us/closedcaption` が通ってしまう。
    if host != "zoom.us" and not host.endswith(".zoom.us"):
        raise captions_mod.TokenError(
            f"Zoom のトークンではない（宛先が {host or '不明'}）。")
    query = parse_qs(parsed.query)
    meeting = (query.get("id") or [""])[0]
    if not re.fullmatch(r"\d{9,12}", meeting or ""):
        raise captions_mod.TokenError("会議IDの形がおかしい。")
    # 最後に、本体と同じ検査も通す。
    captions_mod.parse_token(text)


# URLのコピー。**操作画面と会議の管理画面で、同じものを使う。**
#
# 別々に書いていたら、管理画面のほうに `document.execCommand` の段が抜けていて、
# **tailnet 越しだと操作画面ではコピーできるのに管理画面ではできない**という
# 食い違いが出た（2026-09-19、麻生の指摘）。直したら両方に効くよう、1か所に置く。
#
# 3段になっている。**上から順に試す。**
#   1. navigator.clipboard   安全なオリジン（https か localhost）でしか使えない
#   2. document.execCommand  古いやり方。**tailnet 越しの http でも通る**
#   3. 選んで Ctrl+C を促す   最後の手段
COPY_JS = """  // --- URLのコピー --------------------------------------------------------
  // **配るURLは、手で選ばせない。** トンネルのURLはチャットに貼ることがある。
  // `user-select: all` だけだと、クリックで全選択されることが画面から分からない。
  //
  // `navigator.clipboard` は安全なオリジンでしか使えない。操作画面は
  // http だが localhost / 127.0.0.1 は安全なオリジンとして扱われるので通る。
  // それでも使えない場合（古いブラウザ、権限を切っている）に備えて保険を置く。
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
      // 古いやり方。書き込みが許されていない環境ではこれも失敗する。
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
      // **書けないなら、せめて選んでおく。** Ctrl+C を押すだけで済む。
      // 「自分で選べ」と言って放り出さないこと。会議中に手間を増やさない。
      selectAll(el);
      say("クリップボードに書けない。選んであるので Ctrl+C を押すこと。", false);
      return;
    }
    // **押したことが分かるようにする。** 何も変わらないと、押せたのか分からない。
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
    """保存するQRのファイル名。会議の名前を入れる。

    **会議ごとに別のファイルになるようにする。** 先の会議ぶんを何枚か作って
    置いておく使い方なので、全部が `livecaption-qr.png` では区別が付かない。

    ファイル名に使えない文字と、ヘッダを壊す文字（引用符・改行・非ASCII）は
    落とす。日本語の名前は丸ごと消えるので、そのときは既定の名前に戻す。
    """
    safe = "".join(c for c in name if c.isascii() and (c.isalnum() or c in "-_ ")).strip()
    safe = "-".join(safe.split())[:40]
    return f"livecaption-qr-{safe}.png" if safe else "livecaption-qr.png"


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
<!-- 文字起こしは既定で見せる。切っている人の画面で一瞬出るのを避けるため、
     hide-ja は付けない（`applyJa()` が読み込み直後に付け直す）。 -->
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
    <!-- **「Japanese」とは書けない。** 向きが en2ja なら、ここに出るのは英語である。
         このトグルが出すのは「訳す前の言葉」であって、特定の言語ではない。 -->
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

# --- 操作画面 ---------------------------------------------------------------

CONTROL_BODY = """
  /* --- 設定の欄の文字の大きさ ---------------------------------------------
     **1か所で決める。** 以前は 11px〜13px を各所に直接書いていて、全体として
     小さすぎた。会議中に読むものなので、読めることを優先する。
     大きくするときは、この1行だけ変えればよい。 */
  :root { --ui: 15px; }

  /* --- 左右分割 -----------------------------------------------------------
     **字幕を主にする。** 設定を上に積むと、字幕が下へ押し込められて読めない。
     左に字幕、右に設定を置き、境目をドラッグで動かせるようにする。
     幅は localStorage に残す。 */
  #split {
    flex: 1 1 auto; min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) 7px var(--right, 440px);
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
    border-left: 1px solid var(--line); background: var(--panel);
    font-size: var(--ui); color: var(--ja);
  }
  /* 部品も欄と同じ大きさにする。**共通の指定（14px / 13px）のままだと、
     まわりの文字より小さくなって、押すものだけ読みにくくなる。**
     閲覧画面には `--ui` が無いので、#panel の中だけに効かせる。 */
  #panel button, #panel select { font-size: var(--ui); }
  /* パネルの上のタブ。**当日触るものと、据え付けたら触らないものを分ける。**
     選んだ側は localStorage に残す。次の起動も同じ側で開く。 */
  .tabs { display: flex; gap: 6px; margin: 0 0 14px; }
  .tabs > .tab {
    flex: 1 1 0; padding: 7px 10px; color: var(--muted); background: transparent;
  }
  .tabs > .tab:hover { background: var(--hover); }
  .tabs > .tab.on { color: var(--fg); background: var(--bg);
                    border-color: var(--accent); font-weight: 600; }
  /* **隠した側で起きた失敗を見落とさないようにする。** 設定を開いたまま
     会議が始まって、そちらで失敗していても気づけない。 */
  .tabs > .tab.alert::after { content: " ●"; color: var(--ng); }

  /* --- 会議の管理のタブ ----------------------------------------------------
     **ここだけ窓いっぱいに広げる。** 日時・Zoomのリンク・3つの数値・印を並べると、
     440px の欄には1行に1つしか入らない。それが辛いから別の窓に出したのであって
     （2026-09-19）、そのまま欄の中に戻すと元の木阿弥になる。
     タブにするなら、幅も一緒に連れてくること。 */
  #meetFrame { display: block; width: 100%; height: 100%; border: 0; }
  /* **消した2つは、格子の枠を数えるときに居なくなる。** 3列のまま
     `display: none` にすると、欄が1列目（幅0）へ繰り上がって消える。
     列も一緒に1つへ減らすこと。 */
  body.manage #split { grid-template-columns: minmax(0, 1fr); }
  body.manage #main, body.manage #sep { display: none; }
  body.manage #panel {
    display: flex; flex-direction: column; padding: 0; overflow: hidden;
    border-left: 0; max-height: none;
  }
  body.manage .tabs { flex: 0 0 auto; width: 100%; max-width: 980px;
                      margin: 12px auto 0; padding: 0 26px; }
  body.manage #paneMeet { flex: 1 1 auto; min-height: 0; }

  /* 置き場を選ぶための一覧。**窓を重ねない。** この画面に重なる窓は1つも
     無いので、ここだけ別の作りにしない。欄の中に開く。 */
  .browse { border: 1px solid var(--line2); border-radius: 6px;
            background: var(--field); padding: 8px 10px; margin-bottom: 9px; }
  .browse .list { max-height: min(32vh, 260px); overflow-y: auto; margin: 6px 0; }
  .browse .d {
    display: block; width: 100%; text-align: left; border: 0; background: none;
    color: var(--fg); font-size: var(--ui); padding: 5px 6px; border-radius: 4px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .browse .d:hover { background: var(--hover); }
  .browse .d.up { color: var(--muted); }
  .browse .none { color: var(--muted); font-size: calc(var(--ui) - 2px); padding: 6px; }

  /* 見せ方の3つ。畳んだ見出しを縦に積む。 */
  .grp > .fold { margin-bottom: 7px; }
  .grp > .fold:last-child { margin-bottom: 0; }

  /* まとまりごとに区切る。全部が地続きだと、どこが何の設定か分からない。 */
  .grp { padding: 12px 0; border-bottom: 1px solid var(--line); }
  .grp:first-child { padding-top: 0; }
  .grp:last-child { border-bottom: 0; }
  .grp > h2 {
    margin: 0 0 9px; font-size: calc(var(--ui) - 1px); font-weight: 600;
    letter-spacing: .06em; color: var(--muted);
  }
  .row2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 9px; }
  .row2:last-child { margin-bottom: 0; }
  /* 説明の行は地の文である。**flex にしない。** `<b>` が別の項目として
     切り出され、前後に隙間が空いて、1つの文に見えなくなる。 */
  .row2.hint { display: block; }
  /* 見出しは行を独り占めする。狭い欄で、ラベルと部品を横に並べると折り返しが汚い。 */
  .lbl { flex: 1 0 100%; color: var(--fg); font-size: var(--ui); }
  /* 用語集は畳んでおく。**表は増えていく。** 全部を並べると、右の欄が伸びて
     「アプリの終了」が画面の外へ出る。開いた時も高さを切って中で送らせる。 */
  /* **赤字は #msg にしか効いていなかった。** 「音が来ていない」も
     「上限で切り捨てられた」も class="ng" で書かれているのに、色が付いて
     いなかった。一番見てほしい警告なので、どこでも効くようにする。 */
  .ok { color: var(--ok); }
  .ng { color: var(--ng); }
  .fold { border: 1px solid var(--line2); border-radius: 6px; background: var(--field); }
  .fold > summary {
    cursor: pointer; padding: 8px 10px; font-size: var(--ui); color: var(--fg);
    list-style: none; display: flex; align-items: center; gap: 6px;
  }
  /* **既定の印は3通りの消し方が要る。** どれか1つでも残ると、こちらの三角と
     二重に出る。Chrome は ::marker、古い WebKit は ::-webkit-details-marker、
     Safari は list-style を見る。 */
  .fold > summary::marker { content: ""; }
  .fold > summary::-webkit-details-marker { display: none; }
  /* 開いているかどうかを三角で示す。畳んだままだと気づかれない。
     **文字ではなく罫線で描く。** 「▸」はフォントによって大きさも位置もばらつき、
     絵文字のフォントに落ちることもある。罫線なら、どの環境でも同じ形になる。 */
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
  /* 高さの上限。**画面の高さで決める。** 行数で決めると、低い画面で溢れる。 */
  .fold .list { max-height: min(38vh, 300px); overflow-y: auto; }
  /* 用語集のチェックは1行に1つ。名前と語数を並べると横に入りきらない。 */
  .gloss { display: flex; align-items: center; gap: 6px;
           cursor: pointer; font-size: var(--ui); padding: 4px 0; }
  .gloss input { cursor: pointer; flex: 0 0 auto; }
  /* 名前と語数は summary の中でも使う。.gloss ではなく .fold に付ける。 */
  .fold .n { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis;
             white-space: nowrap; }
  .fold .c { flex: 0 0 auto; color: var(--muted); font-size: calc(var(--ui) - 2px); }
  /* 選んでいる表を目立たせる。畳む前に、何にチェックが入っているかを見る。 */
  .gloss .n { color: var(--ja); }
  .gloss.on .n { color: var(--fg); font-weight: 600; }
  /* 遅延の調整。1行に「名前 / 入力欄」を置き、説明はその下に小さく敷く。
     **説明を横に置くと、名前が潰れて何の設定か分からなくなる。** */
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
  /* 既定と違う値は目立たせる。畳んだ後でも「触ってある」と分かるようにする。 */
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
  /* **URLはボタンでコピーできるようにする。** `user-select: all` はクリックで
     全選択されるが、画面にその手がかりが出ない。会議中に「コピーできない」と
     悩ませないこと。トンネルのURLは、チャットに貼って配ることがある。 */
  .copybtn, .savebtn { padding: 6px 12px; }
  /* 言語の選択。ヘッダに置くので、幅は内容ぶんだけにする。 */
  .langsel {
    font: inherit; font-size: 13px; color: var(--ja);
    background: transparent; border: 1px solid var(--line2); border-radius: 6px;
    padding: 4px 6px; flex: 0 0 auto; width: auto; min-width: 0;
  }
  /* 会議の一覧。1行に「選ぶ / 名前 / URL / ボタン」を積む。
     **URLは折り返して全部見せる。** 途中で切ると、目で確かめられない。

     **高さを切って、中で送らせる。** 会議は増えていく一方なので、そのまま並べると
     「Zoom字幕」から下が画面の外へ押し出される。用語集と同じ作りにしてある。 */
  #meetList {
    display: flex; flex-direction: column; gap: 8px; margin: 6px 0 2px;
    max-height: min(38vh, 300px); overflow-y: auto; scrollbar-width: thin;
    /* 中の行の `offsetTop` をこの枠からの距離にする。選んである行を枠の中へ
       送るのに使う。 */
    position: relative;
  }
  /* 送れる状態のときだけ、上下に切れ目を見せる。無いと、続きがあると気づけない。 */
  #meetList.more { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
                   padding: 6px 4px 6px 0; }
  /* 何件あるかを見出しの横に出す。畳まれていても数が分かる。 */
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
  /* 予定の要約。行の中で、名前の下に小さく敷く。 */
  .meet .when2 { flex: 1 1 100%; font-size: calc(var(--ui) - 3px); color: var(--muted); }
  .meet.armed .when2 { color: var(--ok); }
  /* 予定の入力欄。**行の中に開く。** この画面に重ねる窓は1つも無いので、
     ここだけ別の作りにしない。 */
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
  /* **自動で回す印は目立たせる。** これを押すと、無人で外に配信が始まる。 */
  .sched .auto { flex: 1 1 100%; color: var(--fg); font-size: var(--ui); }
  .sched .auto input { cursor: pointer; }
  /* ホスト用URLは畳んでおく。**参加者用と取り違えて配るのがいちばん怖い。** */
  .hostrow { flex: 1 1 100%; display: flex; flex-wrap: wrap; gap: 6px 8px;
             align-items: center; border-top: 1px solid var(--line);
             margin-top: 6px; padding-top: 8px; }
  .hostrow .warn2 { flex: 1 1 100%; font-size: calc(var(--ui) - 3px); color: var(--ng); }
  .hostrow .url { border-color: var(--ng-line); }
  /* 次の予定の一覧。 */
  #schedNext { display: flex; flex-direction: column; gap: 4px; margin: 4px 0 8px; }
  #schedNext .row { font-size: calc(var(--ui) - 2px); color: var(--ja);
                    display: flex; gap: 8px; }
  #schedNext .row .t { color: var(--fg); flex: 0 0 auto; }
  #schedNext .row .n { flex: 1 1 auto; overflow: hidden;
                       text-overflow: ellipsis; white-space: nowrap; }
  #schedFailRow { align-items: flex-start; }
  /* QRは白地でないと読めない端末がある。余白ごと白くする。 */
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
  <!-- 言語の名前は訳さない。**自分の言語は、自分の言語で書いてあるほうが探せる。** -->
  <select id="uiLang" class="langsel" title="Language">
    <option value="ja">日本語</option>
    <option value="en">English</option>
  </select>
  <!-- **「日本語」とは書けない。** 向きが en2ja なら、ここに出るのは英語である。
       このトグルが出すのは「訳す前の言葉」であって、特定の言語ではない。 -->
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

  <!-- **当日触るものと、据え付けたら触らないものを分ける。** 見出しを12個
       一列に並べていたら、どれが会議中に要るものか見分けが付かなかった
       （2026-09-20 の麻生の指摘）。 -->
  <div class="tabs">
    <button class="tab on" id="tabRun">会議</button>
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
    <!-- **音量メーターは、入力デバイスの欄ではなくここに置く。** 会議中に見る
         ものなので、設定の側に隠してはいけない。 -->
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
    <h2>この会議<span class="c" id="meetCount"></span></h2>
    <div class="row2">
      <label class="lbl" for="meetPick">配信する会議</label>
      <select id="meetPick"></select>
    </div>
    <div class="row2 hint" id="meetWhen"></div>
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

  <!-- **3つの出口を1つの枠に入れる。** 離して置いていたので、同時に使える
       ことが読み取れなかった。畳んだ見出しの右に、動いているかどうかを出す。 -->
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
        <div class="row2" id="tunnelErrBox" style="display:none">
          <pre class="err" id="tunnelErr"></pre>
        </div>
      </div>
    </details>

    <details class="fold" id="wayShare">
      <summary><span class="n">画面共有で見せる</span><span class="c"></span></summary>
      <div class="body">
        <div class="row2">
          <span class="url" id="viewerUrl"></span>
        </div>
        <div class="row2">
          <button id="openViewer">閲覧画面を開く</button>
          <button class="copybtn" data-copy="viewerUrl">URLをコピー</button>
        </div>
        <div class="row2 hint">
          <b>この画面は共有しないこと。</b>共有するのは閲覧画面のほう。
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
      <button id="openLog">途中まで読む</button>
      <span id="logState"></span>
    </div>
    <div class="row2" id="logPathRow" style="display:none">
      <span class="url" id="logPath"></span>
      <button class="copybtn" data-copy="logPath">パスをコピー</button>
    </div>
    <div class="row2">
      <label class="lbl" for="saveDir">置き場</label>
      <input type="text" id="saveDir" spellcheck="false" autocomplete="off">
    </div>
    <div class="row2">
      <button id="saveDirPick" class="primary">フォルダを選ぶ</button>
      <button id="saveDirSet">この場所にする</button>
      <span id="saveDirState"></span>
    </div>
    <!-- **画面の中でフォルダを辿る。** ネイティブの窓は字幕PCの画面にしか
         出ないので、tailnet 越しには使えない。 -->
    <div class="browse" id="browseBox" hidden>
      <div class="row2" id="browseHead">
        <span class="url" id="browseHere"></span>
      </div>
      <div class="list" id="browseList"></div>
      <div class="row2">
        <button id="browseTake" class="primary">ここにする</button>
        <button id="browseClose">閉じる</button>
      </div>
      <div class="row2 hint" id="browseOut" style="display:none">
        ホームフォルダの下だけを出している。
        <button id="browseNative">Windowsの窓を開く</button>
      </div>
    </div>
    <div class="row2 hint" id="saveDirHint">
      選び直すと `.env` に書く。<b>次の起動もこの置き場で始まる。</b>
      いま開いている記録は、その場で閉じて新しい置き場に開き直す。
    </div>
  </div>

</div>

<!-- **中身は `/meetings` をそのまま入れる。** 作りを2つに分けない。
     直に埋めると、`$` も `#msg` も `#list` も操作画面とぶつかる。 -->
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
    <div class="row2 hint">音量メーターは会議の側に出る。</div>
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
    <h2>アプリの終了</h2>
    <div class="row2">
      <button id="quit" class="danger">終了</button>
      <span class="hint">音声の取り込みも文字起こしも止まる</span>
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
  let glossLoaded = false;
  const dirSel = $("dirSel"), dirState = $("dirState");
  let dirLoaded = false;
  const tuneBox = $("tuneBox"), tuneState = $("tuneState"), tuneEnv = $("tuneEnv");
  const tuneFold = $("tuneFold"), tuneSummary = $("tuneSummary");
  const tuneSave = $("tuneSave"), tuneReset = $("tuneReset");
  let tuneLoaded = false;
  // 表がこれより多いときだけ絞り込みを出す。少ないうちは邪魔なだけである。
  const GLOSS_FILTER_FROM = 8;
  const gstart = $("gstart"), gstop = $("gstop");
  const gpill = $("genPill"), genState = $("genState");
  const devices = $("devices"), devReload = $("devReload");
  const meter = $("meter"), meterBar = $("meterBar"), audioState = $("audioState");
  const aerrBox = $("audioErrBox"), aerr = $("audioErr");
  let devLoaded = false;
  const quit = $("quit"), tstart = $("tstart"), tstop = $("tstop");
  const msg = $("msg"), pill = $("zoomPill"), zoomState = $("zoomState");
  const tpill = $("tunnelPill"), tstate = $("tunnelState");
  const wayNetState = $("wayNetState"), wayZoomState = $("wayZoomState");
  const saveDir = $("saveDir"), saveDirPick = $("saveDirPick");
  const saveDirSet = $("saveDirSet"), saveDirState = $("saveDirState");
  const qrbox = $("qrbox"), qr = $("qr"), publicUrl = $("publicUrl");
  const qrsave = $("qrsave");
  const tkind = $("tkind"), tkindHint = $("tkindHint");
  const meetPick = $("meetPick"), meetCount = $("meetCount");
  const meetWhen = $("meetWhen"), meetUrl = $("meetUrl");
  const meetUrlRow = $("meetUrlRow"), meetQr = $("meetQr");
  const schedState = $("schedState"), schedNext = $("schedNext");
  const schedFail = $("schedFail"), schedFailRow = $("schedFailRow");
  const schedAck = $("schedAck"), schedStop = $("schedStop"), schedSkip = $("schedSkip");
  // 一覧を組み直すと、打ちかけの名前や押した場所が飛ぶ。中身が変わったときだけ描く。
  let meetSeen = "";
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
  // 文字を大きくしたぶん、既定の幅も広げてある（CSS の --right と同じ値にすること）。
  const PANEL_MIN = 280, MAIN_MIN = 280, PANEL_DEFAULT = 440;
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

  // --- パネルのタブ -------------------------------------------------------
  // **隠すのは設定の側だけにする。** 会議中に見るもの（状態・音量・次の予定）は
  // どちらを開いていても見えていないと困るので、会議の側に集めてある。
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
    // 会議の管理だけは窓いっぱいに広げる。狭い欄では打てない。
    document.body.classList.toggle("manage", which === "meet");
    // **開かれるまで読み込まない。** 使わない人の画面で、3秒ごとの問い合わせを
    // もう1本増やさない。一度読んだら、そのままにしておく。
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
    // --- 字幕の生成。**これが親の関門である。** ---
    gpill.textContent = s.generating ? "生成: 中" : "生成: 停止中";
    gpill.className = "pill " + (s.generating ? "on" : "off");
    genState.textContent = s.generating
      ? "音を取り込み、文字起こしと翻訳をしている"
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
    if (!dirLoaded) { loadDirection(); }
    if (!glossLoaded) { loadGlossary(); }
    if (!tuneLoaded) { loadTuning(); }

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
    // 畳んだままでも、動いているかどうかが読めるようにする。
    wayZoomState.textContent = s.dry_run ? "--dry-run"
                             : s.active ? "送信中"
                             : s.has_token ? "登録済み" : "";

    const parts = [];
    parts.push(s.has_token ? ("登録済み（会議 " + s.meeting + "）") : "トークン未登録");
    if (s.has_token) { parts.push("seq " + s.seq); parts.push("送信 " + s.sent + " / 失敗 " + s.failed); }
    // 生成が止まっていれば、Zoomを送信中にしても字幕は1行も出ない。黙っていない。
    if (s.active && !s.generating) { parts.push("**生成が止まっているので何も流れない**"); }
    zoomState.textContent = parts.join("　");

    start.disabled = s.dry_run || !s.has_token || s.active;
    stop.disabled = !s.active;
    save.disabled = s.dry_run;

    // --- 配信 ---
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
    // 経路を触れるのは止まっている間だけ。張ったまま持ち替えると、消せない
    // トンネルが残る。
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

    // 会議の側で何か失敗していたら、タブに印を付ける。設定を開いたままでも
    // 気づけるようにするためである。
    const bad = !!(a.error || t.error || s.failed > 0 || (s.schedule || {}).failure);
    tabRun.classList.toggle("alert", bad);

    // --- 予定 ---
    drawSchedule(s.schedule || {});

    // --- 会議 ---
    drawMeetings(s.meetings || {}, t, s.schedule || {});

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

    // --- 記録の置き場 ---
    // **入力中は上書きしない。** 2秒ごとに状態を取りに行くので、書き換えると
    // 手で打っている途中のパスが消える。
    const rc = s.records || {};
    if (document.activeElement !== saveDir && !saveDirDirty) {
      saveDir.value = rc.dir || "";
    }
    saveDirPick.style.display = rc.can_pick ? "" : "none";
  }

  // --- 記録の置き場 -------------------------------------------------------
  // **窓は字幕PCの画面に出る。** 別の機体から開いた操作画面では使えないので、
  // そのときはサーバが断り、手入力に落ちる。
  let saveDirDirty = false;
  saveDir.addEventListener("input", () => { saveDirDirty = true; });

  async function setSaveDir(body, busy) {
    saveDirPick.disabled = true; saveDirSet.disabled = true;
    saveDirState.textContent = busy;
    try {
      const st = await post("/api/savedir", body);
      showStatus(st);
      saveDirDirty = false;
      saveDir.value = (st.records || {}).dir || saveDir.value;
      saveDirState.textContent = st.records_cancelled ? "" : "置き場を変えた";
    } catch (e) {
      saveDirState.innerHTML = '<span class="ng">' + e.message + "</span>";
    }
    saveDirPick.disabled = false; saveDirSet.disabled = false;
  }
  saveDirSet.addEventListener("click", () =>
    setSaveDir({ path: saveDir.value }, "確かめている…"));
  saveDir.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); saveDirSet.click(); }
  });

  // --- 画面の中でフォルダを辿る -------------------------------------------
  // **ネイティブの窓は、この機体の前でしか使えない。** 窓は字幕PCの画面に出る
  // ので、tailnet 越しに押した人には何も見えない。こちらは、どこから開いても
  // 同じように動く。
  //
  // **出すのはホームフォルダの下だけである。** その外を選ぶときは、パスを直接
  // 入れるか、機体の前でネイティブの窓を使う。
  const browseBox = $("browseBox"), browseList = $("browseList");
  const browseHere = $("browseHere"), browseTake = $("browseTake");
  const browseClose = $("browseClose"), browseOut = $("browseOut");
  const browseNative = $("browseNative");
  let browseAt = "";

  // ネイティブの窓を出せるのは、この機体の前で開いたときだけである。
  const atMachine = ["127.0.0.1", "localhost", "[::1]", "::1"]
    .indexOf(location.hostname) >= 0;

  async function browseTo(path) {
    browseList.textContent = "";
    try {
      const r = await fetch("/api/folders?p=" + encodeURIComponent(path || ""));
      const d = await r.json();
      browseAt = d.path || "";
      browseHere.textContent = browseAt;
      if (d.up) { browseList.appendChild(dirRow("↑ 上へ", d.up, true)); }
      (d.dirs || []).forEach((name) => {
        // **区切りは付けずに繋ぐ。** Windows も Linux も、末尾の重複を許す。
        browseList.appendChild(dirRow(name, browseAt + "/" + name, false));
      });
      if (d.error) {
        const e = document.createElement("div");
        e.className = "none ng"; e.textContent = d.error;
        browseList.appendChild(e);
      } else if (!(d.dirs || []).length) {
        const e = document.createElement("div");
        e.className = "none"; e.textContent = "中にフォルダが無い。";
        browseList.appendChild(e);
      }
    } catch (e) {
      browseList.textContent = "";
      const m = document.createElement("div");
      m.className = "none ng"; m.textContent = String(e.message);
      browseList.appendChild(m);
    }
  }

  function dirRow(label, path, up) {
    const b = document.createElement("button");
    b.className = "d" + (up ? " up" : "");
    b.textContent = label;
    b.addEventListener("click", () => browseTo(path));
    return b;
  }

  saveDirPick.addEventListener("click", () => {
    if (!browseBox.hidden) { browseBox.hidden = true; return; }
    browseBox.hidden = false;
    browseOut.style.display = atMachine ? "" : "none";
    browseTo(saveDir.value);
  });
  browseClose.addEventListener("click", () => { browseBox.hidden = true; });
  browseTake.addEventListener("click", async () => {
    await setSaveDir({ path: browseAt }, "確かめている…");
    browseBox.hidden = true;
  });
  browseNative.addEventListener("click", async () => {
    await setSaveDir({ pick: true }, "字幕PCの画面で選んでいる…");
    browseBox.hidden = true;
  });

  // --- 字幕の生成 ---------------------------------------------------------
  async function setGen(on) {
    const b = on ? gstart : gstop;
    b.disabled = true;
    try {
      const st = await post("/api/engine", { on: on });
      showStatus(st);
      // **何を止めたかを、そのつど書く。** 停止は生成だけでなく出口も閉じる。
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
        // 選んだ時点で切り替える。「適用」は押させない。
        cb.addEventListener("change", applyGlossary);
        const n = document.createElement("span");
        n.className = "n"; n.textContent = s.name;
        const c = document.createElement("span");
        c.className = "c"; c.textContent = s.terms + " 語";
        lab.append(cb, n, c);
        glossBox.appendChild(lab);
      }
    }
    // **絞り込みは、表が増えてから出す。** 数個のうちは邪魔なだけである。
    glossFilterRow.style.display = sets.length >= GLOSS_FILTER_FROM ? "" : "none";
    if (sets.length < GLOSS_FILTER_FROM) { glossFilter.value = ""; }
    applyGlossFilter();

    // **畳んでいる間も、何を使っているかは見えていないといけない。**
    // 開かないと分からない作りにすると、前回のままなことに気づけない。
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
    // **上限で切れた語は認識に届かない。** 黙って落とすと、表に足したのに
    // 効かない、という分かりにくい失敗になる。
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
      // **選んでいる表は、絞り込んでも隠さない。** 見えていない物のチェックを
      // 外せてしまうと、何を外したのか分からなくなる。
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
  // **どちらも、絞り込みで隠れている表にも効く。** 「全部」と書いてあるのに
  // 見えている物だけが変わると、何が選ばれているのか分からなくなる。
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
      await loadGlossary();   // 失敗したら実際の選択へ戻す
      return;
    }
    for (const c of glossBox.querySelectorAll("input")) { c.disabled = false; }
  }

  // --- 操作画面の言語 -----------------------------------------------------
  // **サーバ側で差し替える。** 選んだらサーバに覚えさせて、読み込み直す。
  // 画面の文字列はページを組み立てるときに置き換わるので、ここでは何も訳さない。
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

  // --- 字幕の向き ---------------------------------------------------------
  // **会議ごとに選ぶ。** 選んだ時点で切り替わる。適用ボタンは無い。
  // 認識は繋ぎ直さないので、生成を回したまま変えてよい。
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
      // **強制分割の長さが向きで変わる。** 「遅延の調整」の数字を取り直さないと、
      // 畳んだ中に古い値が残る。
      if (tuneLoaded) { await loadTuning(); }
    } catch (e) {
      say(String(e.message), false);
      await loadDirection();   // 失敗したら実際の向きへ戻す
    }
    dirSel.disabled = false;
  });

  // --- 遅延の調整 ---------------------------------------------------------
  // **よく変えるものではない。** 既定値は実測で決めてある。だから畳んである。
  // 触った値はすぐ効く。次の起動にも残したいときだけ「.env に保存」を押す。
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
      // 打っている途中で送らない。欄から離れたときと Enter で送る。
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
    // 組み合わせがおかしいときだけ出す（先回りが確定待ち以上、など）。
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
      await loadTuning();          // 失敗したら実際の値へ戻す
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

  // --- 配信 ---------------------------------------------------------------
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
  // **選ぶだけでは外に出ない。** 経路を持ち替えても、開始は別に押す。
  tkind.addEventListener("change", async () => {
    try {
      showStatus(await post("/api/tunnel", { kind: tkind.value }));
      say("経路を選んだ。「配信を開始」で始める。", true);
    } catch (e) { say(String(e.message), false); }
  });

  // --- 予定 ---------------------------------------------------------------
  // **無人で回すものは、次に何が起きるかが先に読めないと怖い。**
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

    // **失敗は押して消すまで残す。** 無人の機体では、流れた失敗は誰も見ない。
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

  // --- 会議 ---------------------------------------------------------------
  // **予定の入力はここに置かない。** 右の欄は狭すぎて、日時・Zoomのリンク・
  // 3つの数値・印を並べられない。別の画面（/meetings）に出す。
  // ここに残すのは、当日に使うものだけである。どれを配信するかと、そのURL。
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
          + (live.auto ? "　自動で回す" : "　自動は切り")
          + (live.zoom ? "　Zoomに入る" : "")
        : "予定なし";

    if (live && live.url) {
      meetUrl.textContent = live.url;
      meetUrlRow.style.display = "";
    } else {
      meetUrlRow.style.display = "none";
    }
  }

  meetPick.addEventListener("change", async () => {
    try {
      showStatus(await post("/api/meetings",
                            { action: "select", id: meetPick.value }));
      say("配信する会議: " + meetPick.options[meetPick.selectedIndex].text, true);
    } catch (e) { say(String(e.message), false); }
  });

  meetQr.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/qr?dl=1&id=" + encodeURIComponent(meetPick.value)
           + "&name=" + encodeURIComponent(
               meetPick.options[meetPick.selectedIndex].text);
    a.download = "livecaption-qr.png";
    document.body.appendChild(a); a.click(); a.remove();
  });

  // **別のタブで開く。** 操作画面は会議中に見ているので、置き換えない。

  openViewer.addEventListener("click", () => { window.open(viewerUrl.textContent, "_blank"); });

__COPY_JS__

  // --- QRコードの保存 -------------------------------------------------------
  // **画面のQRは貼り付けられない。** スライドや案内のメールに載せるには、
  // ファイルになったPNGが要る。サーバに大きく描き直させて、それを落とす。
  qrsave.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = "/api/qr?dl=1&t=" + encodeURIComponent(publicUrl.textContent);
    a.download = "livecaption-qr.png";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });

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
    wayNetState.textContent = ""; wayZoomState.textContent = "";
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
        # 操作画面を、127.0.0.1 に加えて待ち受けるアドレス（tailnet のIP）。
        # **必ず Tailscale の範囲だけにする**（run.py が確かめてから渡す）。
        self.control_extra: tuple[str, ...] = ()
        # tailnet のアドレスを、取れるまで背景で待つかどうか。
        self.control_retry = False
        self._stopping = False
        self.lines = lines
        self.bind = bind
        self.control = None
        # 字幕の生成（録音・認識・翻訳）の開始と停止（app.EngineControl）。
        self.engine = None
        # 入力デバイスの一覧と差し替え（app.AudioControl）。
        self.audio = None
        # 用語集の一覧と選び直し（app.GlossaryControl）。
        self.glossary = None
        # 遅延の調整つまみ（app.TuningControl）。
        self.tuning = None
        # 字幕の向き（app.DirectionControl）。
        self.direction = None
        # 予定された会議を回す見張り（schedule.Scheduler）。
        self.scheduler = None
        self.on_shutdown = None
        # 配信の経路（Cloudflare / Tailscale）。`tunnel.Delivery` が両方を持つ。
        self.tunnel: tunnel_mod.Delivery | None = None
        # 会議の記録（transcript.Transcript）。--no-save のときは None のまま。
        # **操作画面からしか見えない。** 閲覧側には出さない。
        self.transcript = None
        # 記録の置き場の選び直し（app.RecordControl）。
        self.records = None
        # 閲覧画面の経路。**会議ごとに変える**（meetings.py）。参加者が会議ごとに
        # 違うので、先週の会議のURLで今日の字幕が見えてはいけない。
        # 配信するのは選んである1つだけで、他の会議のURLは404になる。
        self.meetings = meetings.Store()
        self.meetings.on_change = self._meeting_changed
        # ホストの受け口。会議ごとに、失敗の数と「もう受け取った」印を持つ。
        self._host_attempts: dict[str, int] = {}
        self._host_done: set[str] = set()
        self._host_last_try = 0.0
        # 受け口に何があったか。操作画面に出す。**トークンそのものは入れない。**
        self.host_log: list[str] = []

        # 字幕の履歴と、長ポーリングの待ち合わせ。
        self._events: list[dict] = []
        self._first = 0  # _events[0] の通し番号
        self._cond = threading.Condition()
        self._waiting = 0
        # **書きかけの文字起こし。** 履歴には入れない。中身が置き換わるものなので、
        # 追記していく `_events` に混ぜると、同じ文が何行も残ってしまう。
        # 版番号を別に持ち、ブラウザは最後に見た版と違うときだけ描き直す。
        self._partial = ""
        self._partial_v = 0
        self._servers: list[ThreadingHTTPServer] = []

    # --- URL ---------------------------------------------------------------

    @property
    def viewer_path(self) -> str:
        """いま配信している会議の経路。閲覧サーバはここしか開けない。"""
        return self.meetings.viewer_path

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

    def _upcoming(self) -> list[dict]:
        """これから来る回。**予定の表示が見張りに依存しないようにする。**"""
        try:
            return self.meetings.upcoming(datetime.now(), limit=3)
        except Exception:  # noqa: BLE001
            return []

    def meetings_status(self) -> dict:
        """会議の一覧に、いまの経路で組み立てたURLを添える。"""
        st = self.meetings.status()
        for item in st["items"]:
            item["url"] = self.meeting_url(item["id"])
            # **ホスト用URLは、Tailscale のときだけ出る。** 出ないときは空。
            item["host_url"] = self.host_url(item["id"])
            item["host_taken"] = item["id"] in self._host_done
        return st

    def meeting_url(self, meeting_id: str) -> str:
        """その会議の閲覧URL。土台が分からなければ空。

        **Tailscale では、配信していなくても返る。** 会議の前日にURLを確定して
        案内に載せるための道である。Cloudflare はホスト名が毎回変わるので、
        張っている間しか返らない。
        """
        base = self.tunnel.base_url() if self.tunnel is not None else ""
        return f"{base}{self.meetings.path_of(meeting_id)}" if base else ""

    # --- 本体から呼ぶ -------------------------------------------------------

    def caption(self, text: str) -> None:
        """英語の字幕を1行流す。"""
        self._emit({"type": "caption", "text": text, "time": time.strftime("%H:%M:%S")})

    def asr(self, text: str) -> None:
        """確定した文字起こしを1行流す。表示するかはブラウザ側のトグルが決める。"""
        self._emit({"type": "asr", "text": text, "time": time.strftime("%H:%M:%S")})

    def partial(self, text: str) -> None:
        """**書きかけの文字起こし。** 確定を待たずに、声とほぼ同時に見せる。

        文が確定するまでの数秒、画面には何も出ない。話している人の言葉が
        溜まっているだけで、読む側からは止まって見える。ここを埋める。

        **翻訳とZoom字幕には流さない。** どちらも一度出した行を置き換えられないので、
        書きかけを送ると、訂正した完成版と二重に残る。置き換えられるのは、
        自分でDOMを持っているブラウザの画面だけである。
        """
        text = text.strip()
        with self._cond:
            if text == self._partial:
                return
            self._partial = text
            self._partial_v += 1
            self._cond.notify_all()

    def _meeting_changed(self) -> None:
        """配信する会議が変わったときに呼ばれる（`meetings.Store.on_change`）。

        **前の会議の字幕を捨てる。** 履歴は200行あり、新しく開いた画面には
        `since=0` で全部渡る。捨てないと、**次の会議の参加者に、前の会議の
        中身がそのまま見える。** 会議ごとにURLを分けている意味が無くなる。

        書きかけの文字起こしも同じ理由で捨てる。版番号を進めるので、待っている
        長ポーリングもここで起きる（古い経路はこの瞬間から404になるので、
        待たせたままにしても意味が無い）。

        ページそのものは要求のたびに組み立てるので（`_viewer_page`）、
        ここで作り直すものは無い。
        """
        with self._cond:
            # 通し番号は戻さない。戻すと、開いたままの画面が「新しい行が来た」と
            # 誤認して、消したはずの行を取りに来る。
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

    # --- 長ポーリング -------------------------------------------------------

    def poll(self, since: int, wait: float, pv: int = -1) -> tuple[int, list[dict], str, int]:
        """`since` 以降の行を返す。無ければ最大 `wait` 秒待つ。

        `since` が履歴から落ちるほど古ければ、残っている最古から返す。
        途中から開いた画面には `since=0` で全履歴が渡る。

        **書きかけの文字起こしが変わったときも返す。** `pv` はブラウザが最後に
        受け取った版番号で、`-1` なら版を問わず今の中身を返す。
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
            st["tunnel"] = {"state": "off", "url": "", "error": "", "base_url": "",
                            "kind": config.tunnel_kind_selection(),
                            "kinds": list(config.TUNNEL_KINDS), "preannounce": False,
                            "available": tunnel_mod.find_cloudflared() is not None}
        # **会議ごとの閲覧URLも一緒に返す。** 前もって配るURLは、配信していない
        # あいだも見えていないと意味がない。
        st["meetings"] = self.meetings_status()
        # 予定の見張りの状態。**2秒ごとの状態取得に相乗りさせる。**
        # 無人で回すものは、次に何が起きるかが先に読めないと怖い。
        #
        # **次の予定は、見張りが居なくても出す。** 起動直後、本体が組み上がるまでの
        # 短い間は `scheduler` がまだ入っていない。そこで予定が空に見えると、
        # 「予定が消えた」と誤解させる。予定は会議の一覧が持っているので、
        # そちらから直に読む。
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
        if self.transcript is not None:
            st["transcript"] = {
                "on": True,
                "path": str(self.transcript.path),
                "count": len(self.transcript.records),
                "error": self.transcript.error,
            }
        else:
            st["transcript"] = {"on": False, "path": "", "count": 0, "error": ""}
        # 記録の置き場。**--no-save でも返す。** どこに出ないのかが分かる。
        st["records"] = self.records.status() if self.records else {
            "dir": str(config.TRANSCRIPT_DIR), "saving": False, "can_pick": False}
        return st

    # --- 起動と停止 ---------------------------------------------------------

    def start(self) -> None:
        """閲覧と操作、2つのサーバを立てる。

        **操作画面は必ず 127.0.0.1 で待ち受ける。** `bind` は閲覧にしか効かない。

        `control_extra` があれば、**それに加えて** tailnet のアドレスでも待ち受ける。
        127.0.0.1 は必ず残す。**Tailscale が落ちていても、機体の前からは必ず
        操作できるようにするためである。**
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
                # **ここで起動を止めない。** 再起動の直後は tailscaled が
                # まだアドレスを配っていないことがある。操作画面が外に出ないだけで
                # 字幕アプリが立ち上がらないのでは、本末転倒である。
                print(f"  [操作] {addr} では待ち受けられない: {exc}")
        # **実際に待ち受けられたものだけを覚える。** 起動時の画面に出すURLと、
        # `Origin` の検査は、どちらもここから作る。開いていないURLを
        # 「開ける」と出すと、繋がらない理由を探すことになる。
        self.control_extra = tuple(bound)
        if self.control_retry:
            threading.Thread(target=self._retry_control_bind, daemon=True).start()

    def _retry_control_bind(self) -> None:
        """tailnet のアドレスが取れるまで、背景で待ち受けを足し続ける。

        **自動起動では、Tailscale がまだ上がっていないことがある。**
        起動のときに1回試して諦めると、再起動のたびに「その回はもう
        tailnet から操作画面が開けない」ことになる。字幕アプリの起動そのものは
        待たせず（127.0.0.1 は先に開いている）、ここで繰り返す。
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
                return

    # --- ホストがトークンを貼る受け口 ---------------------------------------
    # **トンネル越しに出る、唯一の書き込み口である。** 狭く作る。

    def host_path(self, meeting_id: str, host_id: str) -> str:
        return f"/h/{meeting_id}/{host_id}"

    def host_url(self, meeting_id: str) -> str:
        """ホストに渡すURL。開いていなければ空。

        **Cloudflare では作らない。** TLS が Cloudflare の入口で終わるので、
        Zoom の資格情報がそこを平文で通る。
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
        """いまトークンを受け付けてよい時間かどうか。

        **恒久的な口にしない。** 開けておくのは、その会議を回している間と、
        予定の前後 `HOST_TOKEN_WINDOW_MIN` 分だけにする。**これがこの設計で
        いちばん効く歯止めである。** 会議1本あたり1時間ほどに絞られる。
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
        """トークンを受け取る。`(HTTPの符号, 画面に出す文)` を返す。

        **断るときは、経路違いと同じ404にする。** 「鍵は合っているが時間外」と
        分かると、そこに口があることを教えることになる。
        """
        now = time.monotonic()
        if now - self._host_last_try < config.HOST_MIN_INTERVAL_SEC:
            return 429, "続けて送りすぎ。少し待つこと。"
        self._host_last_try = now

        active = self.meetings.active_id
        known = {m.id: m for m in self.meetings.items()}
        meeting = known.get(meeting_id)
        # **いま配信している会議だけ。** 先週の鍵は通らない。
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
            # **1回だけ。** 2度目は、間違いか、そうでなければ誰かである。
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
        """操作画面から、受け口をもう一度開く。"""
        self._host_done.discard(meeting_id)
        self._host_attempts.pop(meeting_id, None)

    def _host_note(self, meeting_id: str, peer: str, what: str) -> None:
        """**何があったかを必ず残す。** 無人の機体では、記録が唯一の痕跡である。

        **トークンそのものは絶対に書かない。** 先頭だけでも、会議IDと合わせれば
        ほとんど復元できる。
        """
        line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {what}"
                f"（会議 {meeting_id[:6]}…, 相手 {peer or '不明'}）")
        self.host_log.append(line)
        del self.host_log[:-20]
        print(f"[{time.strftime('%H:%M:%S')}] ホスト      {what}（相手 {peer or '不明'}）")

    def control_urls_extra(self) -> list[str]:
        """127.0.0.1 以外で操作画面が開けるURL。起動時の画面に出す。

        **名前を先に出す。** 人が覚えて栞に入れるのは名前のほうである。
        """
        names = [f"http://{n}:{self.control_port}" for n in self.control_names()]
        addrs = [f"http://{'[' + a + ']' if ':' in a else a}:{self.control_port}"
                 for a in self.control_extra]
        return names + addrs

    def allowed_origins(self) -> set[str]:
        """この操作画面自身のURL。`Origin` の検査に使う。

        **アドレスだけでは足りない。** tailnet には MagicDNS があるので、人は
        `http://ms-s1-max:8081` のように**名前で開く。** 名前を入れておかないと、
        画面は見えるのにボタンが全部断られる（2026-09-19 に踏んだ）。
        """
        out = {f"http://localhost:{self.control_port}",
               f"http://127.0.0.1:{self.control_port}"}
        for addr in self.control_extra:
            host = f"[{addr}]" if ":" in addr else addr
            out.add(f"http://{host}:{self.control_port}")
        for name in self.control_names():
            out.add(f"http://{name}:{self.control_port}")
        return out

    def control_names(self) -> list[str]:
        """tailnet 上でこの機体を指す名前。MagicDNS の長short両方。

        **tailnet に出していないときは空。** 名前で開けるのは、そこに
        待ち受けているときだけである。
        """
        if not self.control_extra:
            return []
        host, _ = tunnel_mod.tailscale_host()
        if not host:
            return []
        short = host.split(".")[0]
        return [host, short] if short and short != host else [host]

    def _serve(self, host: str, port: int, handler) -> ThreadingHTTPServer:  # noqa: ANN001
        # ThreadingHTTPServer にするのは、長ポーリングが1本ずつ居座るため。
        cls = ThreadingHTTPServer
        if ":" in host:
            # **IPv6 は族を変えないと開けない。** `ThreadingHTTPServer` の既定は
            # IPv4 で、tailnet の IPv6 アドレスを渡すと getaddrinfo で落ちる。
            class _V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            cls = _V6
        # **同じポートを2つのプロセスが掴めないようにする。** Windows では
        # `allow_reuse_address` が効いて、2つ目の LiveCaption も bind に成功する。
        # そうなると、どちらが応答するか分からない。**常時起動の機体では、
        # 二重起動に気づかないまま古いほうを操作することになる**（実際に踏んだ）。
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


# --- HTTPの受け口 -----------------------------------------------------------


class _Base(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args) -> None:  # noqa: ANN001
        # 本体のログに混ざると読めなくなるので、アクセスログは出さない。
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
        """JSONを返す。**既定で操作画面の言語に合わせる。**

        状態や説明はここを通るので、1か所で訳せる。**字幕そのものには使わない。**
        会議の中身を訳してはいけないので、`_send_lines` は `translate=False` で呼ぶ。
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
        # **字幕は訳さない。** ここを通るのは会議の中身そのものである。
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
    """ホストがトークンを貼るページ。

    **操作画面とは別物にする。** 見るのは会議のホストで、麻生ではない。
    操作画面の言語設定とも関係が無いので、日本語と英語を並べて出す。
    **ここから他のことは何もできない。** 貼る欄と送るボタンだけである。
    """
    name = (meeting_name or "").replace("&", "&amp;").replace("<", "&lt;")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zoom caption token</title>
<style>
  /* **このページは単独で立っている。** 操作画面の配色（`STYLE`）は読み込まれない
     ので、色はここに直接書く。操作画面と同じ明るい配色に合わせてある。 */
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
        t.value = "";                      // 画面に残さない
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
    """閲覧画面を組み立てる。

    **要求のたびに組み立てる。** 経路（`/v/<会議のID>`）はページの中のJSにも
    焼き込まれる。作り置きにすると、**会議を切り替えたときに、新しい経路で
    ページは出るのに、中のJSが古い経路を叩いて404になる。**
    組み立ては文字列の置換だけで、費用は無視できる（`_control_page` と同じ）。
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
    """閲覧画面。**ここが外に出る。**

    出す経路は次の4つだけである。**増やさないこと。**

        GET  /v/<会議>          字幕のページ
        GET  /v/<会議>/lines    字幕の中身
        GET  /h/<会議>/<鍵>     ホストがトークンを貼るページ
        POST /h/<会議>/<鍵>     トークンを受け取る

    最後の1つが、**外から状態を変えられる唯一の口**である。ここに何かを足すと、
    操作画面を 127.0.0.1 に縛ってある意味が薄れる。足す前に `web.py` の
    冒頭の説明を読むこと。
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
                    # **経路違いと同じ404にする。** 「鍵は合っているが時間外」と
                    # 分かると、そこに口があることを教えることになる。
                    self.send_error(404)
                    return
                self._send_bytes(200, "text/html; charset=utf-8",
                                 _host_page(meeting.name))
                return
            # 経路を知らなければ何も見えない。`/` も404にする。
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            found = self._host_parts(u.path)
            if found is None:
                self.send_error(404)
                return
            meeting_id, host_id = found
            try:
                # **上限を小さくする。** 来るのはURL1本だけである。
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
            # **訳さない。** この画面を見るのはホストで、操作画面の言語とは関係ない。
            self._send_json(code, {"error" if code >= 400 else "ok": message},
                            translate=False)

    return Handler


def _meetings_page(web: WebCaptions, lang: str) -> bytes:
    """会議の管理画面。**要求のたびに組み立てる**（言語の切り替えのため）。"""
    page = (_head("Live Captions ・ 会議の管理", web.lines)
            + meetings_page.BODY.replace("__COPY_JS__", COPY_JS))
    return i18n.apply(page, lang).encode("utf-8")


def _control_page(web: WebCaptions, lang: str) -> bytes:
    """操作画面を組み立てる。

    **要求のたびに組み立てる。** 言語を切り替えたら読み込み直すので、作り置きだと
    古い言語のままになる。組み立ては文字列の置換だけで、費用は無視できる。
    """
    page = _head("Live Captions ・ 操作", web.lines) + CONTROL_BODY.replace(
        "__FEED_JS__",
        FEED_JS.replace("__FEED__", "/api/lines")
        .replace("__HISTORY__", str(HISTORY))
        .replace("__SOURCE_DEFAULT__", "false"),
    ).replace("__COPY_JS__", COPY_JS)
    # **言語の差し替えを先に済ませる。** `__UI_LANG__` は言語の名前そのものなので、
    # 訳表に通してはいけない。
    return i18n.apply(page, lang).replace("__UI_LANG__", lang).encode("utf-8")


def _control_handler(web: WebCaptions):
    """操作画面。**既定では 127.0.0.1 からしか届かない。**

    `--control-bind` を付けると、tailnet のアドレスでも待ち受ける。そのときは
    「この機体の前に座っている人だけ」という前提が崩れるので、下の `_same_origin`
    で、**別のページから押されるのを断る。**
    """

    class Handler(_Base):
        def _same_origin(self) -> bool:
            """この画面から来た要求かどうかを見る。違えば断って False を返す。

            **ブラウザで開いたページは、裏で他のアドレスへ要求を送れる。**
            答えは読めない（ブラウザが止める）が、要求は届く。つまり、
            無関係なページが「終了」や「配信を開始」を押せてしまう。

            要求には `Origin` という札が付く。**それを見ていなかった。**
            自分の画面以外から来たものは断る。

            `Origin` が無いものは通す。`curl` や手元の道具は札を付けないし、
            **ブラウザは POST に必ず付ける。** 札が無い＝ブラウザ以外である。
            """
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            if origin in web.allowed_origins():
                return True
            # **理由を残す。** 無人で回す機体では、記録が唯一の痕跡になる。
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
                # 会議の管理。**操作ポートにしか無い。** 会議の名前もホスト用URLも
                # 外に出してよいものではない。
                self._send_bytes(200, "text/html; charset=utf-8",
                                 _meetings_page(web, config.UI_LANG))
            elif u.path == "/api/lines":
                self._send_lines(web, parse_qs(u.query))
            elif u.path == "/api/status":
                self._send_json(200, web.status())
            elif u.path == "/api/qr":
                # **どの会議のQRでも出せる。** 来週の会議のQRを今日のうちに
                # 保存して、案内に載せるための道である。`id` が無ければ
                # いま配信している会議のもの。
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
            elif u.path == "/api/folders":
                # **遠くからでも置き場を選べるようにする。** ネイティブの窓は
                # 字幕PCの画面にしか出ないので、tailnet 越しには使えない
                # （2026-09-20 の麻生の指示）。画面の中で辿れるようにする。
                #
                # **返すのはホームフォルダの下だけである。** 操作画面には認証が
                # 無いので、ディスク全体を見せる口をここに足さない。
                want = parse_qs(u.query).get("p", [""])[0]
                self._send_json(200, config.browse(want))
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

        def _send_qr(self, url: str, query: dict | None = None) -> None:
            """閲覧URLのQRコードを返す。

            画面に出すぶんは SVG で足りる。**`dl=1` を付けるとPNGを添付として
            返す。** スライドやチャットに貼るには、画面の中のQRではなく、
            ファイルになったQRが要る。
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
                # **保存するぶんは大きく作る。** 縮小は誰でもできるが、
                # 粗いPNGを引き伸ばすと読めなくなる。
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
            # 白地・余白つき。画面共有の圧縮でも読めるように、粗い方が良い。
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
                            "/api/tuning", "/api/tuning/save", "/api/direction",
                            "/api/lang", "/api/meetings", "/api/schedule",
                            "/api/savedir"):
                self.send_error(404)
                return
            try:
                body = self._read_json()
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

            if path == "/api/savedir":
                self._savedir(body)
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

            if path == "/api/lang":
                try:
                    lang = config.apply_ui_lang(str(body.get("lang", "")))
                except ValueError as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                config.remember_ui_lang(lang)
                print(f"[{time.strftime('%H:%M:%S')}] 言語        操作画面を "
                      f"{'日本語' if lang == 'ja' else 'English'} にした")
                # **訳さずに返す。** 言語の名前そのものである。
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
                    # .env が書けない（読み取り専用、同期中など）。会議は止めない。
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
                # トークンの書式違いなど、操作した人に伝えるべき失敗。
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _savedir(self, body: dict) -> None:
            """会議の記録の置き場を選び直す。

            `pick` が来たらフォルダ選択の窓を出す。`path` が来たらそれを使う。

            **窓は字幕PCの画面に出る。** 遠くの機体から開いた操作画面では、
            押した人には何も見えないまま、字幕PCの画面に窓が残る。無人で回す
            機体でそれをやると、予定の会議がその窓の後ろで始まる。
            **127.0.0.1 から来た要求でなければ、窓は開かない。**
            """
            if web.records is None:
                self._send_json(503, {"error": "記録の受け口が用意できていない。"})
                return
            if body.get("pick"):
                peer = self.client_address[0] if self.client_address else ""
                if peer not in ("127.0.0.1", "::1"):
                    self._send_json(400, {"error":
                        "フォルダを選ぶ窓は字幕PCの画面に出る。"
                        "別の機体からは使えない。パスを直接入れること。"})
                    return
                chosen = folder_pick.pick(
                    web.records.status().get("dir", ""), "LiveCaption")
                if not chosen:
                    # 取り消し。**失敗ではない。** 画面に赤い字を出さない。
                    self._send_json(200, dict(web.status(), records_cancelled=True))
                    return
                body = {"path": chosen}
            try:
                web.records.set_dir(str(body.get("path", "")))
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, web.status())

        def _stop_outputs(self) -> dict:
            """字幕の生成を止めるとき、出口も一緒に閉じる。

            **止めたつもりで流れ続けるのが一番困る**（2026-09-20 の麻生の指摘）。
            生成だけ止めても、トンネルは張られたままで、参加者のURLは開ける。
            Zoom側も「送信中」のまま残る。次の会議の字幕が前のトークンへ流れる
            入口にもなる。

            **どちらかが失敗しても、もう片方は必ず試す。** 生成はすでに
            止まっているので、ここで例外を投げても得るものが無い。
            何を実際に止めたかを返す。画面に出す一言をそれで決める。
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
            """配信の開始・停止と、経路の選び直し。

            **経路を選ぶのは開始とは別の操作にする。** 選んだだけで外に出ては
            いけない。`kind` だけが来たら、持ち替えて止まったままにする。
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
            """予定の見張りへの指示。**止める・飛ばす・失敗を消す、の3つだけ。**"""
            if web.scheduler is None:
                self._send_json(503, {"error": "予定の受け口が用意できていない。"})
                return
            action = str(body.get("action", ""))
            if action == "stop":
                web.scheduler.stop_now()
            elif action == "skip":
                web.scheduler.skip_next()
            elif action == "ack":
                web.scheduler.ack()
            else:
                self._send_json(400, {"error": f"知らない操作: 「{action}」。"})
                return
            self._send_json(200, web.status())

        def _meetings(self, body: dict) -> None:
            """会議を作る・選ぶ・消す。

            **作るのと選ぶのは別の操作である。** 先の会議のURLを作っている最中に、
            今日の配信が切り替わってはいけない。
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
