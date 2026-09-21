"""予定された会議を、人が居なくても回す見張り。

字幕PCは常時起動している。時刻が来たら、この見張りが順に手を動かす。

    開始 lead_min 分前  会議を選ぶ → 配信を始める →（Zoomに入る）
    開始時刻            記録を会議用に切り替える → 字幕の生成を開始
    その数秒後          **本当に始まったかを確かめる**
    無音が続いたら      生成を停止 → Zoomから出る → 配信を停止 → 記録を閉じる

**この見張りは決して返らないし、例外も外に出さない。**
`app.App.run()` は `asyncio.wait(..., FIRST_COMPLETED)` で待っているので、
どのタスクが終わってもアプリ全体が畳まれる。予定の都合で字幕アプリが落ちてはいけない。

**始まったかどうかは、状態を見て確かめる。** `EngineControl.set_running(True)` は
すぐ返る。入力デバイスを開けなかったときは `_pipeline` が捕まえて停止状態に戻すだけで、
呼び手には何も届かない（再試行も無い）。`generating` が False なのは「まだ」と「失敗」の
両方を意味するので、先に `audio.status()["error"]` を見る。

**畳む判断は、音量ではなく「文になったか」で行う。** Zoomのミックス音声には
暗騒音が常に乗る。振幅で黙っているかを決めると、誰も居ない部屋に繋がったまま止まらない。

**安全上限を必ず持つ。** 文字起こしは流した音声の分だけ課金され、無音でも同じである
（$0.017/分 ≒ 150円/時）。マイクが開きっぱなしの部屋に繋がったままだと止まらないので、
無音の判定とは別に、硬い上限を置く。
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta

from . import config
from . import meetings

# **会議ソフトの操作は、機体によって中身が違う。**
# Windows は窓のクラス名とレジストリ、Linux（コンテナ）は Xvfb の上の窓と
# `xdotool` である。共通にできるのは URL の組み立てだけだったので、
# 実装ごと分けてある。**`zoom_join.py`（Windows 版）は凍結してある。**
# 口は同じ（`running` / `in_meeting` / `join` / `leave` / `JoinError`）。
if os.name == "nt":
    from . import zoom_join
else:
    from . import zoom_join_linux as zoom_join

# 状態。操作画面にもこの名前で出す。
IDLE = "idle"
JOINING = "joining"
ARMING = "arming"
RUNNING = "running"
STOPPING = "stopping"
# **「失敗」という状態は持たない。** 入ったら出られない状態を作ると、
# 一度こけたきり、以後の会議が二度と始まらなくなる。失敗は `failure` に
# 文字として残し、状態は `idle` に戻す。


# **画面に出る一言は、ここに全部並べる。** これらは `/api/status` に載って
# 操作画面へ行くので、訳表に無いと英語表示のときだけ日本語が混ざる。
# ページのマークアップには現れないため、`scripts/check_ui_lang.py` は
# この一覧を見て訳し残しを調べる。文を足したら、ここにも足すこと。
UI_STRINGS = (
    "会議を選んだ",
    "配信を始めた",
    "配信を始められなかった",
    "Zoomに入った",
    "Zoomに入れなかった（手で入れば字幕は出る）",
    "Zoomに入れない: ",
    "Zoomから音が来ない。パスコード違いか、待機室で止まっているか、"
    "更新のダイアログが出ている可能性がある。画面を見ること。",
    "字幕を出している",
    "生成が止められた",
    "操作画面から止めた",
    "例外が出たので片付けた",
    "始まらなかったので片付けた",
    "安全上限で止めた",
    "無音が続いたので止めた",
    "飛ばす予定が無い。",
    "次の予定を飛ばした。",
    "生成が始まらない。",
    "音声の入力を開けない: ",
    "会議を選べない: ",
    "見張りで例外: ",
)


def now_str() -> str:
    return time.strftime("%H:%M:%S")


class Scheduler:
    """予定を見て、会議を1本ずつ回す。

    **同時に回すのは1本だけである。** 重なったときは後から来たほうを飛ばす
    （動いているほうを切らない。会議が延びるのは普通のことで、古い予定のために
    字幕を途中で切るほうが困る）。

    持ち主は `app.App`。呼ばれるのは本体のイベントループからだけである。
    """

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app
        self.state = IDLE
        # いま回している会議と、その回。
        self.meeting_id = ""
        self.meeting_name = ""
        self.occurrence = ""
        # 生成を始めた時刻（`time.monotonic`）。安全上限はここから数える。
        self.started_at = 0.0
        # 最後の失敗。**時間で消さない。** 押して消すまで画面に残す。
        self.failure = ""
        self.failure_at = ""
        # 直前に何をしたか。画面に出す。
        self.note = ""
        # いま回している会議の畳み方。会議ごとに違う。
        self._silence_min = 10.0
        self._max_min = 180
        # 配信をこちらで始めたかどうか。人が始めた配信は止めない。
        self._we_started_tunnel = False
        # Zoomをこちらで起こしたかどうか。**人が開いていた会議は殺さない。**
        self._we_launched_zoom = False
        # この回でZoomに入りに行ったか。音が来ないときの見立てに使う。
        self._joined_zoom = False
        # 音が一度でも届いたか。届いたら、以後は待機室を疑わない。
        self._saw_audio = False
        # 回の始まりの `last_sentence_at`。**これが動いたら「誰かが喋った」。**
        # 振幅（`_saw_audio`）では代用できない。参加者を待っている間の暗騒音でも
        # 立ってしまうからである。
        self._sentence_mark = 0.0
        # チャットへ投げる回の時刻。**空になるまで、来るたびに投げる。**
        self._chat_todo: list[datetime] = []
        self._chat_total = 0
        self._chat_next = 0.0

    # --- 操作画面に返す -----------------------------------------------------

    def status(self) -> dict:
        left_silence = -1.0
        left_max = -1.0
        if self.state == RUNNING:
            # **画面に出す残りは、実際に効いている時計のものにする。**
            # 冒頭はもっと長く待つので、そこで短い数字を見せると、
            # 「もうすぐ切れる」と誤解させる。
            quiet, limit, _why = self._quiet_state()
            left_silence = max(0.0, limit - quiet)
            left_max = max(0.0, self._max_limit() - (time.monotonic() - self.started_at))
        return {
            "state": self.state,
            "meeting": self.meeting_name,
            "occurrence": self.occurrence,
            "note": self.note,
            "failure": self.failure,
            "failure_at": self.failure_at,
            "silence_left_sec": round(left_silence, 1),
            "max_left_sec": round(left_max, 1),
            "upcoming": self._upcoming(),
        }

    def _upcoming(self) -> list[dict]:
        store = getattr(self.app.web, "meetings", None) if self.app.web else None
        if store is None:
            return []
        try:
            return store.upcoming(datetime.now(), limit=3)
        except Exception:  # noqa: BLE001
            return []

    # --- 操作画面からの指示 -------------------------------------------------

    def ack(self) -> dict:
        """失敗の表示を消す。**押すまで消えない。**"""
        self.failure = ""
        self.failure_at = ""
        return self.status()

    def stop_now(self) -> dict:
        """いま回している会議を、こちらから畳む。

        **`App.request_stop` ではない。** あれはプロセスごと終わらせるので、
        常駐が壊れる。
        """
        if self.state == IDLE:
            return self.status()
        self._teardown("操作画面から止めた")
        return self.status()

    def start_now(self, meeting_id: str = "") -> dict:
        """**いま、この会議を1本始める。** 予定の時刻を待たない。

        走る順序は予定の回とまったく同じである（`_begin`）。配信を始め、記録を
        会議の名前で切り替え、Zoomに入り、生成を開始し、チャットに投げる。
        **同じ道を通す。** 手動のために別の順序を書くと、片方だけ直す日が来る。

        予定を入れていない会議を、その場で1本回すためのものである。会議に
        `chat` の印が付いていれば、**押した時と3分後**にチャットへ投げる
        （予定の回では、定刻と3分後）。Zoom のURLが空なら、入りに行かない。

        **待たずに返す。** `_begin` はZoomの起動と配信の立ち上げを含むので、
        数十秒かかる。HTTPの返事をそこまで止めない。進み具合は `status()` の
        `state` と `note` に出る。

        呼ぶのはHTTPサーバのスレッドである。
        """
        if self.state != IDLE:
            raise ValueError("いま会議を回している。先に「いま止める」を押すこと。")
        store = self.app.web.meetings if self.app.web else None
        if store is None:
            raise ValueError("会議の一覧が用意できていない。")
        want = str(meeting_id) or store.status()["active"]
        found = [m for m in store.items() if m.id == want]
        if not found:
            raise ValueError("先に、配信する会議を選ぶこと。")
        loop = self.app.zoom.loop
        if loop is None:
            raise ValueError("本体がまだ動いていない。")
        meeting = found[0]
        # **ここで掴んだことにする。** `_begin` が状態を立てるのは輪の中なので、
        # それまでの数秒に見張りが別の回を始められる。
        self.state = JOINING
        self.note = f"「{meeting.name}」をいま始める"
        occurrence = store.occurrence_key(datetime.now())
        asyncio.run_coroutine_threadsafe(
            self._begin_guarded(meeting, occurrence), loop)
        print(f"[{now_str()}] 予定        操作画面から「{meeting.name}」を始める")
        return self.status()

    def skip_next(self) -> dict:
        """次の回を済ませたことにして飛ばす。「明日は出ない」ときに使う。"""
        store = self.app.web.meetings if self.app.web else None
        if store is None:
            return self.status()
        nxt = store.upcoming(datetime.now(), limit=1)
        if not nxt:
            self.note = "飛ばす予定が無い。"
            return self.status()
        store.mark_fired(nxt[0]["id"], nxt[0]["at"])
        self.note = "次の予定を飛ばした。"
        print(f"[{now_str()}] 予定        {nxt[0]['at']} の"
              f"「{nxt[0]['name']}」を飛ばした")
        return self.status()

    async def _begin_guarded(self, meeting, occurrence: str) -> None:  # noqa: ANN001
        """`start_now` から投げる `_begin`。**例外を外に出さない。**

        `run_coroutine_threadsafe` で投げた輪は誰も待たないので、例外が出ても
        どこにも現れない。**そして状態は JOINING のまま固まる。** そうなると、
        以後どの会議も始まらない（見張りは JOINING を「回している」と見る）。
        `run_forever` が `_tick` に対してやっているのと同じ始末をする。
        """
        try:
            await self._begin(meeting, occurrence)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(f"いま始められない: {type(exc).__name__}: {exc}")
            try:
                self._teardown("始められなかったので片付けた")
            except Exception:  # noqa: BLE001
                self.state = IDLE

    # --- 見張り -------------------------------------------------------------

    async def run_forever(self) -> None:
        """**決して返らない。決して例外を外に出さない。**

        `app.App.run()` の `asyncio.wait(..., FIRST_COMPLETED)` は、どのタスクが
        終わってもアプリ全体を畳む。ここが1回でも返ると、字幕アプリが予定の
        都合で落ちることになる。
        """
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # **掴んだままにしない。** 状態が途中で止まると、以後どの会議も
                # 始まらなくなる。理由を残して待機に戻す。
                self._fail(f"見張りで例外: {type(exc).__name__}: {exc}")
                if self.state != IDLE:
                    try:
                        self._teardown("例外が出たので片付けた")
                    except Exception:  # noqa: BLE001
                        self.state = IDLE
            await asyncio.sleep(config.SCHEDULE_TICK_SEC)

    async def _tick(self) -> None:
        if self.state in (RUNNING, ARMING, JOINING):
            await self._watch_running()
            return
        if self.app.web is None:
            return
        due = self.app.web.meetings.due(datetime.now())
        if not due:
            return
        meeting, when = due[0]
        key = self.app.web.meetings.occurrence_key(when)
        for other, other_when in due[1:]:
            # 重なった回は飛ばす。掴んだままにすると、40分遅れて誰も居ない
            # 部屋に入りに行く。
            other_key = self.app.web.meetings.occurrence_key(other_when)
            self.app.web.meetings.mark_fired(other.id, other_key)
            print(f"[{now_str()}] 予定        {other_key} の「{other.name}」は飛ばした"
                  f"（「{meeting.name}」と重なっている）")
        await self._begin(meeting, key)

    # --- 開始 ---------------------------------------------------------------

    async def _begin(self, meeting, occurrence: str) -> None:  # noqa: ANN001
        """会議を1本始める。

        **いちばん先に「済ませた印」を残す。** 印を残す前に時間のかかる作業を
        すると、5秒後の見張りが同じ回をもう一度掴み、Zoomを何度も起こす。
        始めそこねた回は捨てる。**1本落とすほうが、暴れるよりましである。**
        """
        store = self.app.web.meetings
        store.mark_fired(meeting.id, occurrence)

        self.state = JOINING
        self.meeting_id = meeting.id
        self.meeting_name = meeting.name
        self.occurrence = occurrence
        self._silence_min = meeting.silence_min
        self._max_min = meeting.max_min
        self._we_started_tunnel = False
        self._joined_zoom = bool(meeting.zoom)
        self._saw_audio = False
        self._chat_todo = []
        print(f"[{now_str()}] 予定        「{meeting.name}」を始める（{occurrence}）")

        # 1. 配信する会議を切り替える。
        try:
            store.select(meeting.id)
        except ValueError as exc:
            # **待機に戻す。** JOINING のまま返ると、以後どの会議も始まらない。
            self._fail(f"会議を選べない: {exc}")
            self.state = IDLE
            self.meeting_id = self.meeting_name = self.occurrence = ""
            return
        self.note = "会議を選んだ"

        # 2. 配信を始める。**すでに人が始めていたら、そのままにする。**
        tunnel = self.app.web.tunnel
        if tunnel is not None:
            st = tunnel.status()
            if st["state"] in ("off", "error"):
                st = await asyncio.to_thread(tunnel.start)
                self._we_started_tunnel = True
                if st["state"] == "error":
                    # 配信できなくても続ける。画面共有とZoom字幕は使える。
                    print(f"[{now_str()}] 予定        配信を始められない: {st['error']}")
                    self.note = "配信を始められなかった"
                else:
                    self.note = "配信を始めた"

        # 3. 記録を会議ごとに切り替える。
        self.app.roll_transcript(label=meeting.name)

        # 4. Zoomに入る（Phase 4 で中身を入れる）。
        await self._join_zoom(meeting)

        # 5. 生成を開始して、本当に始まったかを確かめる。
        self.state = ARMING
        self.app.engine.set_running(True)
        self.started_at = time.monotonic()
        self._sentence_mark = self.app.last_sentence_at
        why = await self._arm()
        if why:
            self._fail(why)
            self._teardown("始まらなかったので片付けた")
            return
        self.state = RUNNING
        self.note = "字幕を出している"
        print(f"[{now_str()}] 予定        「{meeting.name}」の字幕を出している")

        # 6. Zoomのチャットに投げるのは、あとで。**ここではまだ投げない。**
        #
        #    **予定の開始時刻を過ぎてから投げる。** Zoomのチャットは、入る前の
        #    発言が見えない。字幕アプリは開始の lead_min 分前に動き出すので、
        #    ここで投げると、定刻に入ってきた人が全員取りこぼす
        #    （麻生の指摘、2026-09-20）。
        #
        #    それに、`zoommtg:` を投げてから会議の窓が出るまで、Zoomは数十秒から
        #    数分かかる（実測: 起動の5秒後にはまだ無かった）。
        #
        #    時刻と窓の両方が揃うのを `_watch_running` が待つ。
        self._chat_next = 0.0
        self._chat_todo = []
        if meeting.chat:
            try:
                start_at = datetime.strptime(occurrence, meetings.TIME_FMT)
            except ValueError:
                start_at = datetime.now()
            self._chat_todo = [start_at + timedelta(minutes=m)
                               for m in config.SCHEDULE_CHAT_AT_MIN]
        self._chat_total = len(self._chat_todo)

    async def _try_chat(self) -> None:
        """会議の窓が出ていたら、チャットに投げる。出るまで何度でも見に来る。

        **字幕が出た時点では、まだZoomが会議に入り終えていない。** そこで一度
        試して諦めると、印を付けた会議でも一度も投げられない（2026-09-20 に
        麻生の実会議でそうなった）。

        **別のスレッドで投げること。** 投げるのに8秒ほどかかる。本体の
        イベントループで待つと、そのあいだ音の取り込みも字幕も止まる。
        """
        if not self._chat_todo:
            return
        # **その回の時刻を過ぎるまで投げない。** 早く投げると、後から入ってきた
        # 人に何も残らない。Zoomのチャットは、入る前の発言が見えない。
        wall = datetime.now()
        if wall < self._chat_todo[0]:
            return
        now = time.monotonic()
        if now < self._chat_next:
            return
        self._chat_next = now + config.SCHEDULE_CHAT_RETRY_SEC
        late = wall > self._chat_todo[0] + timedelta(
            seconds=config.SCHEDULE_CHAT_WAIT_SEC)
        # 何回目か。**記録に残す。** 2回投げるので、どちらが落ちたかが要る。
        which = self._chat_total - len(self._chat_todo) + 1
        round_ = f"（{which}/{self._chat_total}回目）"

        from . import zoom_chat

        if not zoom_chat.meeting_window():
            if late:
                self._chat_todo.pop(0)
                print(f"[{now_str()}] 予定        チャットに投げられない{round_}: "
                      "Zoomの会議の窓が出てこない")
            return

        web = self.app.web
        url = (web.public_url() or web.viewer_url()) if web else ""
        if not url:
            if late:
                self._chat_todo.pop(0)
                print(f"[{now_str()}] 予定        チャットに投げる先のURLが無い{round_}")
            return

        name = self.meeting_name
        try:
            done = await asyncio.to_thread(self._post_chat_now, url, name)
        except Exception as exc:  # noqa: BLE001
            self._chat_todo.pop(0)
            print(f"[{now_str()}] 予定        チャットに投げられない{round_}: "
                  f"{type(exc).__name__}: {exc}")
            return

        if done["text"]:
            self._chat_todo.pop(0)
            self._space_out_next()
            extra = "（QRも）" if done["files"] else ""
            print(f"[{now_str()}] 予定        チャットにURLを投げた{round_}{extra}")
            if not done["files"]:
                # ホストがファイル送信を切っていると、こうなる。**URLは届いている。**
                print(f"[{now_str()}] 予定        QRは送れなかった。URLだけ届いている。")
            return
        # 貼っている途中で前面が入れ替わった、など。**もう一度だけ見に来る。**
        if late:
            self._chat_todo.pop(0)
            why = done["why"]
            print(f"[{now_str()}] 予定        チャットに投げられない{round_}: {why}")

    def _space_out_next(self) -> None:
        """次の回を、いまから少なくとも `SCHEDULE_CHAT_GAP_SEC` 先へずらす。

        **Zoomの参加が遅れると、1回目が押し出される。** そのとき2回目の時刻を
        既に過ぎていると、同じ文が数秒差で2つ並ぶ。壊れているように見える。
        """
        if not self._chat_todo:
            return
        floor = datetime.now() + timedelta(seconds=config.SCHEDULE_CHAT_GAP_SEC)
        if self._chat_todo[0] < floor:
            self._chat_todo[0] = floor

    @staticmethod
    def _post_chat_now(url: str, name: str) -> dict:
        """**別のスレッドで動く。** ここから本体の状態を触らないこと。"""
        from . import zoom_chat

        shot = zoom_chat.qr_file(url, name)
        return zoom_chat.post(zoom_chat.compose(url), [shot] if shot else [])

    async def _join_zoom(self, meeting) -> None:  # noqa: ANN001
        """Zoomに入る。**入れたかどうかは、ここでは分からない。**

        `zoommtg:` はハンドラに渡すだけで、待機室・パスコード違い・更新の
        ダイアログのどれに落ちても何も返ってこない。**確認は音で取る**
        （`_watch_running` が、文字起こしが出ないまま時間が経つのを見る）。
        """
        if not meeting.zoom:
            return
        # **すでに人が会議に入っていたら、後で切らない。** 開いたままの会議を
        # 巻き添えにしないよう、こちらが入れたときだけ覚えておく。
        #
        # **`running()` で見てはいけない。** Zoomは会議を抜けても常駐の窓口を
        # 残すので、常時起動の機体ではほぼいつでも True になる。それを
        # 「会議中」と読むと、こちらが入れた会議から永遠に出なくなる
        # （2026-09-19、実機で踏んだ）。
        already = zoom_join.in_meeting()
        try:
            url = await asyncio.to_thread(
                zoom_join.join, meeting.zoom, config.ZOOM_DISPLAY_NAME)
        except zoom_join.JoinError as exc:
            # **ここで会議を畳まない。** 人が手でZoomに入れば字幕は出せる。
            self._fail(f"Zoomに入れない: {exc}")
            self.note = "Zoomに入れなかった（手で入れば字幕は出る）"
            return
        self._we_launched_zoom = not already
        self.note = "Zoomに入った"
        print(f"[{now_str()}] 予定        Zoomに入る: {url[:90]}")
        if already:
            print(f"[{now_str()}] 予定        Zoomは既に会議に入っていた。"
                  "終わっても終了させない")

    async def _arm(self) -> str:
        """生成が本当に始まったかを確かめる。始まらなければ理由を返す。

        **`generating` だけを見てはいけない。** 入力デバイスを開けなかったときも
        `_pipeline` が黙って False に戻す。False は「まだ」と「失敗」の両方を
        意味するので、先に `audio.status()["error"]` を見る。
        """
        deadline = time.monotonic() + config.SCHEDULE_ARM_SEC
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            err = self.app.audio.status().get("error", "")
            if err:
                return f"音声の入力を開けない: {err}"
            if self.app.engine.status()["generating"]:
                return ""
        return "生成が始まらない。"

    # --- 動いている間 -------------------------------------------------------

    def _heard_sound(self) -> bool:
        """会議が始まってから、音が一度でも届いたか。

        **こちらは振幅で見る。** 文になったかではない。知りたいのは
        「Zoomから音の経路が繋がっているか」であって、誰かが喋ったかではない。
        待機室で止まっていれば、暗騒音すら来ない。
        """
        capture = self.app.capture
        quiet_for = getattr(capture, "quiet_for", None)
        if quiet_for is None:
            return True     # 分からないときは、疑わない
        return quiet_for() < config.SCHEDULE_JOIN_AUDIO_SEC

    def _silence_limit(self) -> float:
        return self._silence_min * 60.0

    def _quiet_state(self) -> tuple[float, float, str]:
        """`(黙っている秒数, 上限, 止めるときの一言)`。

        **「まだ誰も喋っていない」と「会議が終わった」は別である。**
        会議の冒頭は、参加者を待って数分の無言が続くのが普通である
        （麻生の指摘、2026-09-21）。そこで無音の時計を回すと、**始まる前の
        会議を畳んでしまう。**

        だから、最初の1文が出るまでは別の時計で見る。区別に使うのは
        **文が出たかどうか**で、振幅ではない。参加者を待っている間の暗騒音でも
        振幅は立つので、それでは区別にならない。

        始まらないまま置き去りにはしない。上限を過ぎたら畳む。認識の接続は
        黙っていても課金されるので、空の会議に何時間も座らせない。
        """
        now = time.monotonic()
        if self.app.last_sentence_at != self._sentence_mark:
            # 一度は喋った。以後は「途切れてから」を見る。
            return now - self.app.last_sentence_at, self._silence_limit(), \
                "無音が続いたので止めた"
        # まだ一文も出ていない。参加してからの時間で見る。
        # **会議ごとの設定のほうが長ければ、そちらを立てる。**
        limit = max(self._silence_limit(), config.SCHEDULE_OPENING_SEC)
        return now - self.started_at, limit, "会議が始まらないので止めた"

    def _max_limit(self) -> float:
        return self._max_min * 60.0

    async def _watch_running(self) -> None:
        if self.state != RUNNING:
            return
        # 人が操作画面から止めたなら、こちらも片付ける。
        if not self.app.engine.status()["generating"]:
            self._teardown("生成が止められた")
            return
        # チャットへの投稿は、Zoomの窓が出てから。ここで何度でも試す。
        await self._try_chat()
        elapsed = time.monotonic() - self.started_at
        # **音が一度も来ないまま時間が経ったら、入れていない可能性が高い。**
        # Zoomは待機室・パスコード違い・更新のダイアログのどれで止まっても
        # 何も報せてこない。こちらから見えるのは「音が来ない」ことだけである。
        # 会議が始まるのは遅れるものなので、判断は急がない。
        if not self._saw_audio and self._heard_sound():
            self._saw_audio = True
        if (self._joined_zoom and not self._saw_audio
                and elapsed > config.SCHEDULE_JOIN_AUDIO_SEC):
            self._fail("Zoomから音が来ない。パスコード違いか、待機室で止まっているか、"
                       "更新のダイアログが出ている可能性がある。画面を見ること。")
            self._joined_zoom = False   # 一度出したら繰り返さない
        if elapsed > self._max_limit():
            # **無音でなくても必ず止める。** 課金が止まらないのを防ぐ最後の砦。
            self._teardown("安全上限で止めた", detail=f"{self._max_min}分")
            return
        quiet, limit, why = self._quiet_state()
        if quiet > limit:
            self._teardown(why, detail=f"{limit / 60:g}分")

    # --- 片付け -------------------------------------------------------------

    def _teardown(self, why: str, detail: str = "") -> None:
        """会議1本ぶんを畳む。**プロセスは終わらせない。**

        `why` は画面に出す一言で、**数字を混ぜない固定の文にする。**
        訳表は固定の文しか置き換えられない。数字は `detail` に入れて
        端末のログにだけ残す。診断はそちらで足りる。
        """
        self.state = STOPPING
        name = self.meeting_name
        extra = f"（{detail}）" if detail else ""
        print(f"[{now_str()}] 予定        「{name}」を畳む: {why}{extra}")
        try:
            self.app.engine.set_running(False)
        except Exception as exc:  # noqa: BLE001
            print(f"[{now_str()}] 予定        生成を止められない: {exc}")
        self._leave_zoom()
        # Zoom字幕のトークンを捨てる。**会議ごとに別のトークンである。**
        # 残すと、次の会議の字幕が前の会議へ流れる。
        try:
            self.app.zoom.set_enabled(False)
        except Exception:  # noqa: BLE001
            pass
        tunnel = self.app.web.tunnel if self.app.web else None
        if tunnel is not None and self._we_started_tunnel:
            try:
                tunnel.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"[{now_str()}] 予定        配信を止められない: {exc}")
        self.app.roll_transcript()
        self.state = IDLE
        self.note = why
        self.meeting_id = ""
        self.meeting_name = ""
        self.occurrence = ""
        self._we_started_tunnel = False
        self._joined_zoom = False
        self._saw_audio = False
        # チャットへ投げる回。**畳むときに捨てる。**
        self._chat_todo = []
        self._chat_total = 0
        self._chat_next = 0.0

    def _leave_zoom(self) -> None:
        """Zoomから出る。**こちらが起こしたときだけ。**

        会議から出る口は無いので、終了させることになる。`Zoom.exe` を全部
        落とすので、人が開いていた会議まで巻き添えにしてはいけない。
        """
        if not self._we_launched_zoom:
            return
        self._we_launched_zoom = False
        try:
            if zoom_join.leave():
                print(f"[{now_str()}] 予定        Zoomを終了させた")
        except Exception as exc:  # noqa: BLE001
            print(f"[{now_str()}] 予定        Zoomを終了させられない: {exc}")

    # --- 失敗 ---------------------------------------------------------------

    def _fail(self, why: str) -> None:
        """**押して消すまで画面に残す。** 無人の機体では、流れた失敗は見られない。"""
        self.failure = why
        self.failure_at = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now_str()}] 予定        失敗: {why}")
