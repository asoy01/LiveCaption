"""会議の管理画面。**幅いっぱいを使う。**

操作画面の右の欄は狭い。日時・Zoomのリンク・3つの数値・印を並べると、1行に
1つしか入らず、打つのも読むのも辛い。**幅を使える場所に移した。**

**いまは操作画面のタブの中に、この頁をそのまま入れて出している**（2026-09-20）。
タブを選ぶと窓いっぱいに広がるので、幅は別の窓で開いたときと変わらない。
`/meetings` を直に開く道も残してある。**作りは1つで、置き場所が2つある。**

**操作ポートにしか無い。** 閲覧側には出さない。会議の名前も、ホスト用URLも、
外に出してよいものではない。

この画面が使う口は `/api/status` と `/api/meetings` だけである。どちらも
操作ポートにしかない。
"""

from __future__ import annotations

BODY = """</style>
<style>
  /* **`display: block` に戻すこと。** 共通の `STYLE` は body を縦並びの flex に
     している（操作画面と閲覧画面のため）。そのままだと `.wrap` が flex の品目に
     なって中身の幅まで縮み、**左右に 130px 以上の余白ができる**
     （2026-09-20 の麻生の指摘）。 */
  body { display: block; margin: 0; background: var(--bg); color: var(--fg);
         font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 22px 26px 40px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { font-size: 13px; color: var(--ja); margin: 0 0 18px; line-height: 1.7; }
  .add { display: flex; gap: 8px; margin: 0 0 18px; }
  .add input { flex: 1 1 auto; }
  /* 1件ぶん。幅があるので、欄を横に並べられる。 */
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px;
          margin-bottom: 14px; }
  .card.on { border-color: var(--accent); }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin-bottom: 12px; }
  .head .nm { font-size: 17px; font-weight: 600; color: var(--fg); }
  .head .when { font-size: 13px; color: var(--ja); }
  .head .made { font-size: 12px; color: var(--muted); margin-left: auto; }
  /* 開く・閉じるの札。**見出しの右端に置く。** */
  .head .editbtn { font-size: 13px; padding: 4px 12px; }
  .badge { font-size: 12px; padding: 2px 9px; border-radius: 999px;
           border: 1px solid var(--accent); color: var(--accent); }
  .grid { display: grid; grid-template-columns: auto 1fr; gap: 9px 12px;
          align-items: center; margin-bottom: 12px; }
  /* 見出しは右に寄せて、入力欄の左端を揃える。
     **`.opt` は除く。** 値の側に置く `<label>`（チェックの説明）まで右へ
     飛んでしまい、入力欄の列から浮いて見える。 */
  .grid > label:not(.opt) { font-size: 13px; color: var(--ja);
                            justify-self: end; white-space: nowrap; }
  .grid > label.opt { display: flex; align-items: center; gap: 7px;
                      font-size: 13px; color: var(--ja); cursor: pointer; }
  .grid input[type=text], .grid input[type=datetime-local] {
    width: 100%; box-sizing: border-box; font: inherit; font-size: 14px;
    color: var(--fg); background: var(--field); border: 1px solid var(--line2);
    border-radius: 6px; padding: 7px 9px;
  }
  .nums { display: flex; gap: 16px; flex-wrap: wrap; align-items: center;
          margin-bottom: 4px; }
  .nums label { font-size: 13px; color: var(--ja); display: flex;
                align-items: center; gap: 6px; }
  .nums input { width: 74px; font: inherit; font-size: 14px; color: var(--fg);
                background: var(--field); border: 1px solid var(--line2);
                border-radius: 6px; padding: 5px 7px; text-align: right; }
  .autorow { display: flex; align-items: center; gap: 8px; margin: 14px 0 4px;
             font-size: 15px; color: var(--fg); cursor: pointer; }
  .autorow input { cursor: pointer; }
  .warn { font-size: 12px; color: var(--ng); margin: 0 0 12px 25px;
          line-height: 1.6; }
  .urls { border-top: 1px solid var(--line); padding-top: 12px; margin-top: 6px; }
  .urlline { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
             margin-bottom: 8px; }
  .urlline .tag { font-size: 12px; color: var(--ja); width: 76px; flex: 0 0 auto; }
  .urlline .url { flex: 1 1 320px; }
  .hostwarn { font-size: 12px; color: var(--ng); margin: 0 0 8px 84px;
              line-height: 1.6; }
  .acts { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
  #msg { font-size: 14px; min-height: 1.6em; margin-bottom: 12px; }
  #msg.ok { color: var(--ok); }
  #msg.ng { color: var(--ng); }
  .back { font-size: 13px; margin-top: 22px; }
  .back a { color: var(--accent); }
</style>
</head>
<body>
<div class="wrap">
  <h1>会議の管理</h1>
  <p class="sub">会議ごとに別の閲覧URLを使う。<b>配信するのは選んである1つだけで、
     他の会議のURLは開けない。</b><br>
     予定を入れておけば、時刻が来たときに自動で開始する。</p>
  <div id="msg"></div>

  <div class="add">
    <input type="text" id="newName" placeholder="会議の名前（例: KAGRA朝礼 9/25）">
    <button id="add" class="primary">追加</button>
  </div>

  <div id="list"></div>
  <p class="back"><a href="/">操作画面に戻る</a></p>
</div>

<script>
  const $ = (id) => document.getElementById(id);
  const msg = $("msg"), list = $("list"), newName = $("newName");

  // 操作画面のタブの中に入れて開くことがある。そのときは、見出しと戻る道を
  // 出さない。**タブの札が見出しになっているし、戻るのはタブを押せばよい。**
  // 自分の窓で開いたときは、どちらも要る。
  if (window.self !== window.top) {
    document.querySelectorAll("h1, .back").forEach((e) => { e.hidden = true; });
    // **タブの中では幅いっぱいに使う。** 欄の幅は本人が境目で決めているので、
    // そこから更に 980px で頭を打つと、決めた幅が使われない。余白も詰める。
    const wrap = document.querySelector(".wrap");
    wrap.style.maxWidth = "none";
    wrap.style.padding = "6px 16px 28px";
  }
  // **触っている欄があるうちは描き直さない。** 打ちかけの値が手の下で消える。
  let seen = "";
  const touched = new Set();
  // 入力欄を開いてある会議。**描き直しをまたいで覚える。**
  // 覚えないと、30秒ごとの取り直しで手の下の欄が畳まれる。
  const opened = new Set();

  function say(text, ok) { msg.textContent = text; msg.className = ok ? "ok" : "ng"; }

  // datetime-local が受ける形。**UTCにしない。** toISOString は時差のぶんずれる。
  function nowLocal() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
         + "T" + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  async function post(body) {
    const r = await fetch("/api/meetings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { throw new Error(d.error || ("失敗しました（" + r.status + "）")); }
    return d;
  }

  function draw(state) {
    const m = state.meetings || {}, t = state.tunnel || {};
    const items = m.items || [];
    const key = JSON.stringify([m.active, items, t.kind]);
    if (key === seen || touched.size) { return; }
    seen = key;
    list.textContent = "";

    for (const it of items) {
      list.appendChild(card(it, items, m, t));
    }
  }

  function card(it, items, m, t) {
    const box = document.createElement("div");
    box.className = "card" + (it.id === m.active ? " on" : "");
    const mark = () => touched.add(it.id);

    // --- 見出し ---
    const head = document.createElement("div");
    head.className = "head";
    const pick = document.createElement("input");
    pick.type = "radio"; pick.name = "live"; pick.checked = (it.id === m.active);
    pick.title = "この会議を配信する";
    pick.addEventListener("change", async () => {
      try {
        const st = await post({ action: "select", id: it.id });
        seen = ""; draw(st);
        say("配信する会議: " + it.name, true);
      } catch (e) { say(String(e.message), false); }
    });
    head.appendChild(pick);
    const nm = document.createElement("span");
    nm.className = "nm"; nm.textContent = it.name;
    head.appendChild(nm);
    if (it.id === m.active) {
      const badge = document.createElement("span");
      badge.className = "badge"; badge.textContent = "配信中";
      head.appendChild(badge);
    }
    // **畳んでいる間も、いつの会議かは見えていないといけない。**
    // 名前だけの一覧にすると、どれを開けばよいか分からなくなる。
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = it.start
      ? (it.repeat === "weekly" ? "毎週 " : "") + it.start
        + (it.auto ? "　自動で開始" : "")
      : "予定なし";
    head.appendChild(when);

    const made = document.createElement("span");
    made.className = "made"; made.textContent = "作成 " + it.created;
    head.appendChild(made);

    // **入力欄は畳んでおく。** 予定が増えると、全部を開いたままでは下の会議が
    // 画面の外へ押し出される（麻生の指摘、2026-09-21）。編集するときだけ開く。
    const edit = document.createElement("button");
    edit.className = "editbtn";
    head.appendChild(edit);
    box.appendChild(head);

    const body = document.createElement("div");
    body.className = "cardbody";
    function setOpen(on) {
      if (on) { opened.add(it.id); } else { opened.delete(it.id); }
      body.style.display = on ? "" : "none";
      // 畳んであるときは、見出しの下の余白も要らない。
      head.style.marginBottom = on ? "" : "0";
      edit.textContent = on ? "閉じる" : "編集";
    }
    edit.addEventListener("click", () => setOpen(body.style.display === "none"));
    // **触ってある会議は開けておく。** 打ちかけの値が畳まれて見えなくなると、
    // 保存し忘れる。
    setOpen(opened.has(it.id) || touched.has(it.id));

    // --- 予定 ---
    const grid = document.createElement("div");
    grid.className = "grid";
    function row(labelText, node) {
      const l = document.createElement("label");
      l.textContent = labelText;
      grid.appendChild(l);
      grid.appendChild(node);
      return node;
    }
    const start = document.createElement("input");
    start.type = "datetime-local";
    // **予定が無い会議には、いまの時刻を入れておく。** 空欄から打ち始めるより、
    // 近い値を直すほうが速い。会議はたいてい「これから」のものである。
    start.value = it.start ? it.start.replace(" ", "T") : nowLocal();
    start.addEventListener("input", mark);
    // **欄のどこを押しても暦と時計が開くようにする。** 既定では右端の小さな
    // アイコンを狙わないと開かない。`showPicker` はブラウザによっては無い。
    start.addEventListener("click", () => {
      try { start.showPicker(); } catch (e) { /* 手入力に任せる */ }
    });
    row("開始", start);

    const repWrap = document.createElement("label");
    repWrap.className = "opt";
    const rep = document.createElement("input");
    rep.type = "checkbox"; rep.checked = (it.repeat === "weekly");
    rep.addEventListener("change", mark);
    repWrap.appendChild(rep);
    repWrap.appendChild(document.createTextNode("同じ曜日・同じ時刻で繰り返す"));
    row("毎週", repWrap);

    const zoom = document.createElement("input");
    zoom.type = "text";
    zoom.placeholder = "Zoomの招待URLか会議番号。空なら自分では入らない";
    zoom.value = it.zoom || "";
    zoom.addEventListener("input", mark);
    row("Zoomのリンク", zoom);
    body.appendChild(grid);

    const nums = document.createElement("div");
    nums.className = "nums";
    function num(labelText, value, min, max, step) {
      const l = document.createElement("label");
      l.appendChild(document.createTextNode(labelText));
      const i = document.createElement("input");
      i.type = "number"; i.value = value; i.min = min; i.max = max;
      if (step) { i.step = step; }
      i.addEventListener("input", mark);
      l.appendChild(i);
      nums.appendChild(l);
      return i;
    }
    const lead = num("何分前から", it.lead_min, 0, 60);
    const silence = num("無音で終了（分）", it.silence_min, 0.5, 240, "0.5");
    const cap = num("安全上限（分）", it.max_min, 5, 1440);
    body.appendChild(nums);

    const autorow = document.createElement("label");
    autorow.className = "autorow";
    const auto = document.createElement("input");
    // **保存されている値をそのまま出す。** 印の既定は作るときに決まっていて、
    // 自分で足した会議には最初から入っている（`meetings.py` の `create`）。
    // ここで付け直すと、外したものが勝手に戻る。
    auto.type = "checkbox"; auto.checked = !!it.auto;
    auto.addEventListener("change", mark);
    autorow.appendChild(auto);
    autorow.appendChild(document.createTextNode(
      "この会議を自動で開始する（時刻が来たら配信を始める）"));
    body.appendChild(autorow);

    const warn = document.createElement("div");
    warn.className = "warn";
    warn.textContent = "自動で開始すると、人が見ていなくても字幕が外に出る。"
      + "外に出せない内容の会議では印を付けないこと。";
    body.appendChild(warn);

    // **Zoomのチャットに投げる印。** 押すと、会議の参加者全員に字幕のURLが
    // 見える。既定は `auto` と同じ扱いである。
    const chatrow = document.createElement("label");
    chatrow.className = "autorow";
    const chat = document.createElement("input");
    chat.type = "checkbox"; chat.checked = !!it.chat;
    chat.addEventListener("change", mark);
    chatrow.appendChild(chat);
    chatrow.appendChild(document.createTextNode(
      "字幕が出たら、ZoomのチャットにURLとQRを投げる"));
    body.appendChild(chatrow);

    const chatWarn = document.createElement("div");
    chatWarn.className = "warn";
    chatWarn.textContent = "参加者全員にURLが見える。"
      + "ホストがファイル送信を切っている会議では、URLだけが届く。";
    body.appendChild(chatWarn);

    // --- URL ---
    const urls = document.createElement("div");
    urls.className = "urls";

    const line = document.createElement("div");
    line.className = "urlline";
    const tag = document.createElement("span");
    tag.className = "tag"; tag.textContent = "参加者用";
    line.appendChild(tag);
    const u = document.createElement("span");
    u.className = "url"; u.id = "u-" + it.id;
    u.textContent = it.url || (t.preannounce
      ? "URLがまだ決まらない。Tailscale に繋がっているか確かめること。"
      : "Cloudflare ではURLが毎回変わる。配信を始めると出る。");
    line.appendChild(u);
    if (it.url) {
      const cp = document.createElement("button");
      cp.className = "copybtn"; cp.dataset.copy = u.id;
      cp.textContent = "URLをコピー";
      line.appendChild(cp);
      const qr = document.createElement("button");
      qr.textContent = "QRコードを保存";
      qr.addEventListener("click", () => {
        const a = document.createElement("a");
        a.href = "/api/qr?dl=1&id=" + encodeURIComponent(it.id)
               + "&name=" + encodeURIComponent(it.name);
        a.download = "livecaption-qr.png";
        document.body.appendChild(a); a.click(); a.remove();
      });
      line.appendChild(qr);
    }
    urls.appendChild(line);

    const hostLine = document.createElement("div");
    hostLine.className = "urlline";
    const htag = document.createElement("span");
    htag.className = "tag"; htag.textContent = "ホスト用";
    hostLine.appendChild(htag);
    const show = document.createElement("button");
    show.textContent = "出す";
    show.addEventListener("click", () => {
      show.remove();
      const hu = document.createElement("span");
      hu.className = "url"; hu.id = "h-" + it.id;
      hu.textContent = it.host_url
        || "経路を Tailscale にすると出る（Cloudflare では出さない）。";
      hostLine.appendChild(hu);
      if (it.host_url) {
        const cp = document.createElement("button");
        cp.className = "copybtn"; cp.dataset.copy = hu.id;
        cp.textContent = "URLをコピー";
        hostLine.appendChild(cp);
      }
      const w = document.createElement("div");
      w.className = "hostwarn";
      w.textContent = "ホストにだけ送ること。参加者用のURLと取り違えないこと。"
        + "このURLを持つ人は、この会議の字幕をZoomに流し込める。";
      urls.appendChild(w);
    });
    hostLine.appendChild(show);
    urls.appendChild(hostLine);
    body.appendChild(urls);

    // --- ボタン ---
    const acts = document.createElement("div");
    acts.className = "acts";
    const save = document.createElement("button");
    save.className = "primary"; save.textContent = "予定を保存";
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        const st = await post({ action: "schedule", id: it.id, fields: {
          start: start.value,
          repeat: rep.checked ? "weekly" : "",
          zoom: zoom.value,
          lead_min: Number(lead.value),
          silence_min: Number(silence.value),
          max_min: Number(cap.value),
          auto: auto.checked,
          chat: chat.checked,
        }});
        // **通ってから、触った印を消す。** 先に消すと、断られたときに描き直されて
        // 「保存したのに戻った」ように見える。
        touched.delete(it.id);
        seen = "";
        draw(st);
        say("予定を保存した: " + it.name, true);
      } catch (e) { say(String(e.message), false); }
      save.disabled = false;
    });
    acts.appendChild(save);

    const del = document.createElement("button");
    del.className = "danger"; del.textContent = "削除";
    const last = (items.length === 1);
    del.addEventListener("click", async () => {
      if (!confirm("この会議を消す: " + it.name
                   + "。このURLは開けなくなる。よろしいですか。")) { return; }
      try {
        const st = await post({ action: "delete", id: it.id });
        touched.delete(it.id);
        seen = "";
        draw(st);
        say(last ? "会議を消した。閲覧URLが要るので、新しい会議を1つ作った。"
                 : "会議を消した: " + it.name, true);
      } catch (e) { say(String(e.message), false); }
    });
    acts.appendChild(del);
    body.appendChild(acts);

    box.appendChild(body);
    return box;
  }

  async function addMeeting() {
    const name = newName.value.trim();
    if (!name) { say("会議の名前を入れること。", false); return; }
    try {
      const st = await post({ action: "create", name });
      newName.value = "";
      seen = "";
      draw(st);
      say("会議を作った: " + name + "。配信する会議は変えていない。", true);
    } catch (e) { say(String(e.message), false); }
  }
  $("add").addEventListener("click", addMeeting);
  newName.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { addMeeting(); }
  });

__COPY_JS__

  async function refresh() {
    try {
      draw(await (await fetch("/api/status")).json());
    } catch (e) { /* 本体が終わっただけ。次の周期で直る */ }
  }
  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""
