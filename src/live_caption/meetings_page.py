"""The page for managing meetings. **It uses the full width.**

The right column of the control page is narrow. Putting the date and time, the
Zoom link, three numbers and the flags side by side leaves room for only one of
them per line, which is hard both to type into and to read. **So it was moved to
a place where the width can be used.**

**Right now this page is embedded as it is inside a tab of the control page**
(2026-09-20). Selecting the tab expands it to the whole window, so the width is
the same as when it is opened in a separate window. The way to open `/meetings`
directly is still there. **There is one implementation, in two places.**

**It exists only on the control port.** It is not shown on the viewer side.
Neither the names of the meetings nor the URL for the host may go outside.

The only endpoints this page uses are `/api/status` and `/api/meetings`. Both
exist only on the control port.
"""

from __future__ import annotations

BODY = """</style>
<style>
  /* **Set this back to `display: block`.** The shared `STYLE` makes body a
     vertical flex container (for the control page and the viewer page). Left as
     it is, `.wrap` becomes a flex item and shrinks to the width of its content,
     **leaving more than 130px of empty space on both sides** (reported by a
     user, 2026-09-20). */
  body { display: block; margin: 0; background: var(--bg); color: var(--fg);
         font-family: "Segoe UI", "Yu Gothic UI", system-ui, sans-serif; }
  .wrap { max-width: 980px; margin: 0 auto; padding: 22px 26px 40px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { font-size: 13px; color: var(--ja); margin: 0 0 18px; line-height: 1.7; }
  .add { display: flex; gap: 8px; margin: 0 0 18px; }
  .add input { flex: 1 1 auto; }
  /* One meeting. There is width, so the fields can be placed side by side. */
  .card { border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px;
          margin-bottom: 14px; }
  .card.on { border-color: var(--accent); }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin-bottom: 12px; }
  .head .nm { font-size: 17px; font-weight: 600; color: var(--fg); }
  .head .when { font-size: 13px; color: var(--ja); }
  .head .made { font-size: 12px; color: var(--muted); margin-left: auto; }
  /* The open / close button. **It sits at the right end of the heading.** */
  .head .editbtn { font-size: 13px; padding: 4px 12px; }
  .badge { font-size: 12px; padding: 2px 9px; border-radius: 999px;
           border: 1px solid var(--accent); color: var(--accent); }
  .grid { display: grid; grid-template-columns: auto 1fr; gap: 9px 12px;
          align-items: center; margin-bottom: 12px; }
  /* The labels are pushed to the right so that the left edges of the input
     fields line up. **`.opt` is excluded.** Otherwise the `<label>` on the value
     side (the text of a checkbox) also flies to the right and looks detached
     from the column of input fields. */
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
    <input type="text" id="newName" placeholder="会議の名前（例: 定例会議 9/25）">
    <button id="add" class="primary">追加</button>
  </div>

  <div id="list"></div>
  <p class="back"><a href="/">操作画面に戻る</a></p>
</div>

<script>
  const $ = (id) => document.getElementById(id);
  const msg = $("msg"), list = $("list"), newName = $("newName");

  // This page is sometimes opened inside a tab of the control page. In that
  // case the heading and the way back are not shown. **The tab label is the
  // heading, and going back means pressing a tab.** When it is opened in its own
  // window, both are needed.
  if (window.self !== window.top) {
    document.querySelectorAll("h1, .back").forEach((e) => { e.hidden = true; });
    // **Inside a tab, use the full width.** The width of the column is set by
    // the user at the divider, so hitting another ceiling at 980px would waste
    // the width they chose. The padding is tightened as well.
    const wrap = document.querySelector(".wrap");
    wrap.style.maxWidth = "none";
    wrap.style.padding = "6px 16px 28px";
  }
  // **Do not redraw while a field is being edited.** A half-typed value would
  // disappear under the user's hands.
  let seen = "";
  const touched = new Set();
  // The meetings whose fields are open. **This is remembered across redraws.**
  // Without it, the refresh every 30 seconds would fold up the fields the user
  // is working in.
  const opened = new Set();

  function say(text, ok) { msg.textContent = text; msg.className = ok ? "ok" : "ng"; }

  // The format datetime-local accepts. **Do not use UTC.** toISOString is off by
  // the time zone offset.
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

    // --- Heading ---
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
    // **Even while a card is folded, the time of the meeting has to be
    // visible.** A list of names alone makes it impossible to tell which one to
    // open.
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = it.start
      ? (it.repeat === "weekly" ? "毎週 " : "") + it.start
        + (it.auto ? "　自動で開始" : "")
      : "予定なし";
    when.textContent += it.route === "tailscale" ? "　経路: Tailscale" : "　経路: Cloudflare";
    head.appendChild(when);

    const made = document.createElement("span");
    made.className = "made"; made.textContent = "作成 " + it.created;
    head.appendChild(made);

    // **The input fields start folded.** As the number of meetings grows,
    // keeping them all open pushes the meetings below off the screen (reported
    // by a user, 2026-09-21). They open only when you edit.
    const edit = document.createElement("button");
    edit.className = "editbtn";
    head.appendChild(edit);
    box.appendChild(head);

    const body = document.createElement("div");
    body.className = "cardbody";
    function setOpen(on) {
      if (on) { opened.add(it.id); } else { opened.delete(it.id); }
      body.style.display = on ? "" : "none";
      // While it is folded, the space under the heading is not needed either.
      head.style.marginBottom = on ? "" : "0";
      edit.textContent = on ? "閉じる" : "編集";
    }
    edit.addEventListener("click", () => setOpen(body.style.display === "none"));
    // **Keep a meeting that has been edited open.** If a half-typed value is
    // folded away and no longer visible, it is easy to forget to save it.
    setOpen(opened.has(it.id) || touched.has(it.id));

    // --- Schedule ---
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
    // **A meeting with no schedule gets the current time.** Correcting a nearby
    // value is faster than typing from an empty field. A meeting is usually one
    // that is still to come.
    start.value = it.start ? it.start.replace(" ", "T") : nowLocal();
    start.addEventListener("input", mark);
    // **Open the calendar and clock wherever in the field you click.** By
    // default they open only when you aim at the small icon at the right end.
    // `showPicker` does not exist in some browsers.
    start.addEventListener("click", () => {
      try { start.showPicker(); } catch (e) { /* leave it to typing by hand */ }
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

    // **The route belongs to the meeting.** A meeting whose QR code is handed
    // out in advance needs Tailscale, because only its URL does not change.
    const route = document.createElement("select");
    for (const [v, text] of [["cloudflare", "Cloudflare（その場で配る）"],
                             ["tailscale", "Tailscale（前もって配る）"]]) {
      const o = document.createElement("option");
      o.value = v; o.textContent = text;
      route.appendChild(o);
    }
    route.value = it.route || "cloudflare";
    route.addEventListener("change", mark);
    row("配信の経路", route);
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
    // **Show the saved value as it is.** The default of the flag is decided
    // when the meeting is created, and a meeting you added yourself has it on
    // from the start (`create` in `meetings.py`). Setting it again here would
    // silently bring back a flag that was turned off.
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

    // **The flag for posting to the Zoom chat.** With it on, every participant
    // of the meeting sees the caption URL. Its default is handled the same way
    // as `auto`.
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

    // --- URLs ---
    const urls = document.createElement("div");
    urls.className = "urls";

    const line = document.createElement("div");
    line.className = "urlline";
    const tag = document.createElement("span");
    tag.className = "tag"; tag.textContent = "参加者用";
    line.appendChild(tag);
    const u = document.createElement("span");
    u.className = "url"; u.id = "u-" + it.id;
    u.textContent = it.url || (it.route === "tailscale"
      ? "URLがまだ決まらない。Tailscale に繋がっているか確かめること。"
      : "Cloudflare ではURLが配信のたびに変わる。配信を始めると出る。"
        + "前もって配るなら、経路を Tailscale にする。");
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
        || "この会議の経路を Tailscale にすると出る（Cloudflare では出さない）。";
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

    // --- Buttons ---
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
          route: route.value,
        }});
        // **Clear the "edited" mark only after the request goes through.**
        // Clearing it first would redraw the card when the request is refused,
        // and it would look as if the saved values had reverted.
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
    } catch (e) { /* the program just stopped; it recovers on the next tick */ }
  }
  refresh();
  setInterval(refresh, 3000);
</script>
</body>
</html>
"""
