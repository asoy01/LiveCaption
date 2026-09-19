"""会議ごとの閲覧URL。作る・選ぶ・消す。

閲覧URLは `https://<ホスト名>/v/<経路>` という形で、後半の `<経路>` をここで持つ。

**会議ごとに別の経路を使う。** 参加者が会議ごとに違うので、先週の会議のURLで
今日の字幕が見えてはいけない。

**前もって作れる。** Tailscale Funnel はホスト名が変わらないので、会議の前日でも
URLを確定できる。Zoomのリンクと一緒に案内に載せるための作りである。
Cloudflare の一時トンネルはホスト名が毎回変わるので、経路だけ先に作れても
URL全体は当日まで決まらない。

**配信するのは選んである1つだけ。** 他の会議のURLは、その日には404を返す。
`web.py` の閲覧サーバが見るのは `viewer_path` 1本だけなので、これは自然に成り立つ。

**会議は必ず1つ以上ある。** 0個になると閲覧URLが決まらず、画面共有もできない。
最後の1つを消したときは、今日の日付で代わりを作る。

置き場は `local/meetings.json`。ここは git に入らない。会議の名前が入るためである。
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from . import config

# 名前の長さの上限。操作画面の一覧に収めるためで、中身の制約ではない。
NAME_MAX = 60
# 予定の書き方。**秒も時間帯も持たない。** 1台の機体で、人が手で打つ値である。
TIME_FMT = "%Y-%m-%d %H:%M"
# 繰り返しはこの2つだけ。cron も RRULE も持ち込まない。
REPEATS = ("", "weekly")


@dataclass(frozen=True)
class Meeting:
    """1つの会議ぶん。`id` がURLに載る。

    **下の予定の欄は後から足した。** 古い `local/meetings.json` には入っていないので、
    既定値を持たせてある（`_meeting_from` が補う）。
    """

    id: str
    name: str
    created: str
    # --- 予定（無人で回すための欄） ---
    start: str = ""            # "2026-09-25 09:30"。空なら予定なし
    repeat: str = ""           # "" か "weekly"
    zoom: str = ""             # Zoomの招待URLか会議番号。空なら自分では入らない
    lead_min: int = 2          # 何分前に動き出すか
    silence_min: float = 10.0  # 無音がこれだけ続いたら畳む
    max_min: int = 180         # 安全上限。無音でなくてもここで必ず止める
    auto: bool = False         # **自動で回す印。既定は切り。** 理由は下
    # **Zoomのチャットに字幕のURLを投げる印。既定は切り。**
    # 投げると、会議の参加者全員にURLが見える。外に出せない会議では付けないこと。
    chat: bool = False
    last_fired: str = ""       # 済ませた回の `start`。**時刻ではなく回を書く**
    host_id: str = ""          # ホストがトークンを貼るURLの経路（Phase 6）

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "created": self.created,
            "start": self.start, "repeat": self.repeat, "zoom": self.zoom,
            "lead_min": self.lead_min, "silence_min": self.silence_min,
            "max_min": self.max_min, "auto": self.auto, "chat": self.chat,
            "last_fired": self.last_fired, "host_id": self.host_id,
        }

    @property
    def scheduled(self) -> bool:
        return bool(self.start) and self.auto


def _new_id() -> str:
    return secrets.token_urlsafe(config.VIEWER_SECRET_BYTES)


def _new_host_id() -> str:
    """ホスト用URLの経路。

    **閲覧用の `id` より長くする。** 閲覧用は破られても字幕が漏れるだけで、
    しかも数十人に配るものである。こちらは Zoom へ何を送るかを決めるので、
    桁を変えてある。**閲覧用から導けてはいけない。**
    """
    return secrets.token_urlsafe(config.HOST_SECRET_BYTES)


# --- 読み込みの補正 -----------------------------------------------------------
# **手で直した `meetings.json` で落ちないようにする。** 読めない値は既定に戻す。
# ここで例外を出すと、見張りが5秒ごとに同じ会議で転び続ける。


def _clean_start(raw) -> str:  # noqa: ANN001
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return datetime.strptime(text, TIME_FMT).strftime(TIME_FMT)
    except (ValueError, TypeError):
        return ""


def _num(raw, lo: float, hi: float, default: float) -> float:  # noqa: ANN001
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value or value < lo or value > hi:  # NaN もここで落ちる
        return default
    return value


def _meeting_from(m: dict) -> Meeting:
    """1件ぶんを読む。**足りない欄は既定値で埋める。**"""
    return Meeting(
        id=str(m["id"]),
        name=str(m.get("name", "")),
        created=str(m.get("created", "")),
        start=_clean_start(m.get("start")),
        repeat=str(m.get("repeat", "")) if m.get("repeat") in REPEATS else "",
        zoom=str(m.get("zoom", "")).strip(),
        lead_min=int(_num(m.get("lead_min"), 0, 60, 2)),
        silence_min=_num(m.get("silence_min"), 0.5, 240, 10.0),
        max_min=int(_num(m.get("max_min"), 5, 24 * 60, 180)),
        auto=bool(m.get("auto", False)),
        chat=bool(m.get("chat", False)),
        last_fired=str(m.get("last_fired", "")),
        host_id=str(m.get("host_id", "")),
    )


class Store:
    """会議の一覧と、いま配信している会議。

    **操作画面（HTTPサーバのスレッド）から触られる。** `_lock` で守る。
    書き込みに失敗しても落とさない。会議は続けられるべきである。
    """

    def __init__(self, path=None) -> None:  # noqa: ANN001
        # **既定値引数で MEETINGS_STATE_PATH を捕まえない。** import のときに
        # 1回だけ評価されるので、試験から差し替えられなくなる。
        self.path = config.MEETINGS_STATE_PATH if path is None else path
        self._lock = threading.Lock()
        self._items: list[Meeting] = []
        self._active = ""
        self._error = ""
        # 経路が変わったときに呼ぶ。閲覧サーバの `viewer_path` を合わせるため。
        self.on_change = None
        self._load()

    # --- 読み書き -----------------------------------------------------------

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            items = [
                _meeting_from(m)
                for m in saved.get("items", [])
                if isinstance(m, dict) and m.get("id")
            ]
            active = str(saved.get("active", ""))
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            items, active = [], ""
        self._items = items
        self._active = active if any(m.id == active for m in items) else ""
        if not self._items:
            # **1つも無い状態を作らない。** 閲覧URLが決まらないと、画面もQRも
            # 出せない。今日の日付で1つ作っておく。
            self._items = [Meeting(_new_id(), str(date.today()), str(date.today()))]
            self._active = self._items[0].id
            self._save_locked()
        elif not self._active:
            self._active = self._items[0].id

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"active": self._active,
                       "items": [m.as_dict() for m in self._items]}
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(self.path)
            self._error = ""
        except OSError as exc:
            self._error = f"会議の一覧を保存できない: {exc}"
            print(f"  [会議] {self._error}")

    # --- 読み取り -----------------------------------------------------------

    @property
    def active_id(self) -> str:
        with self._lock:
            return self._active

    @property
    def viewer_path(self) -> str:
        """いま配信している会議の経路。"""
        return f"/v/{self.active_id}"

    def path_of(self, meeting_id: str) -> str:
        return f"/v/{meeting_id}"

    def items(self) -> list[Meeting]:
        with self._lock:
            return list(self._items)

    def status(self) -> dict:
        with self._lock:
            return {
                "active": self._active,
                "error": self._error,
                "items": [dict(m.as_dict(), path=f"/v/{m.id}") for m in self._items],
            }

    # --- 書き換え -----------------------------------------------------------

    def create(self, name: str) -> dict:
        """会議を1つ足す。**足すだけで、配信の相手は変えない。**

        先の会議のURLを作っている最中に、今日の配信が切り替わっては困る。
        """
        name = str(name).strip()[:NAME_MAX]
        if not name:
            raise ValueError("会議の名前を入れること。")
        with self._lock:
            if any(m.name == name for m in self._items):
                raise ValueError(f"同じ名前の会議がある: 「{name}」。")
            self._items.append(Meeting(_new_id(), name, str(date.today())))
            self._save_locked()
            return self._status_locked()

    def select(self, meeting_id: str) -> dict:
        """配信する会議を選ぶ。**その場で効く。**

        **いま開いている閲覧画面は繋がらなくなる。** 経路が変わるためである。
        会議の最中に触るものではない。
        """
        meeting_id = str(meeting_id)
        with self._lock:
            if not any(m.id == meeting_id for m in self._items):
                raise ValueError("その会議は無い。")
            changed = meeting_id != self._active
            self._active = meeting_id
            self._save_locked()
            out = self._status_locked()
        if changed:
            self._changed()
        return out

    def delete(self, meeting_id: str) -> dict:
        """会議を消す。URLはその場で死ぬ。

        **最後の1つを消したら、代わりを1つ作る。** 閲覧URLが決まらないと、画面共有も
        できなくなる。「消せない」と断るのではなく、今日の日付で作り直す。
        終わった会議を全部片付けられるほうが、使う側には自然である。
        """
        meeting_id = str(meeting_id)
        with self._lock:
            if not any(m.id == meeting_id for m in self._items):
                raise ValueError("その会議は無い。")
            self._items = [m for m in self._items if m.id != meeting_id]
            changed = meeting_id == self._active
            if not self._items:
                self._items = [Meeting(_new_id(), str(date.today()), str(date.today()))]
            if changed:
                self._active = self._items[0].id
            self._save_locked()
            out = self._status_locked()
        if changed:
            self._changed()
        return out

    # --- 予定 ---------------------------------------------------------------

    def set_schedule(self, meeting_id: str, **fields) -> dict:  # noqa: ANN003
        """予定の欄を書き換える。読めない値は `ValueError` で断る。

        **ここは人が打った値を受ける入口なので、ここで断る。** 読み込みの側
        （`_meeting_from`）は逆に、何が来ても既定値に落として通す。
        壊れたファイルで見張りが転び続けるほうが困るためである。
        """
        meeting_id = str(meeting_id)
        clean: dict = {}

        if "start" in fields:
            raw = str(fields["start"] or "").strip().replace("T", " ")
            # ブラウザの datetime-local は "2026-09-25T09:30" を返す。
            if raw and _clean_start(raw) == "":
                raise ValueError(f"日時の書き方が違う: 「{raw}」。{TIME_FMT} の形で入れること。")
            clean["start"] = _clean_start(raw)
        if "repeat" in fields:
            rep = str(fields["repeat"] or "")
            if rep not in REPEATS:
                raise ValueError(f"繰り返しが違う: 「{rep}」。空か weekly のどちらか。")
            clean["repeat"] = rep
        if "zoom" in fields:
            clean["zoom"] = str(fields["zoom"] or "").strip()[:500]
        if "lead_min" in fields:
            clean["lead_min"] = int(_num(fields["lead_min"], 0, 60, -1))
            if clean["lead_min"] < 0:
                raise ValueError("何分前に動き出すかは 0〜60 で入れること。")
        if "silence_min" in fields:
            value = _num(fields["silence_min"], 0.5, 240, -1)
            if value < 0:
                raise ValueError("無音で畳むまでの分は 0.5〜240 で入れること。")
            clean["silence_min"] = value
        if "max_min" in fields:
            value = int(_num(fields["max_min"], 5, 24 * 60, -1))
            if value < 0:
                raise ValueError("安全上限は 5〜1440 分で入れること。")
            clean["max_min"] = value
        if "auto" in fields:
            clean["auto"] = bool(fields["auto"])
        if "chat" in fields:
            clean["chat"] = bool(fields["chat"])

        with self._lock:
            found = [m for m in self._items if m.id == meeting_id]
            if not found:
                raise ValueError("その会議は無い。")
            old = found[0]
            # 予定を変えたら、済ませた印を落とす。時刻を動かしたのに
            # 「もう済んだ」と見なされては困る。
            if clean.get("start", old.start) != old.start:
                clean["last_fired"] = ""
            new = replace(old, **clean)
            self._items = [new if m.id == meeting_id else m for m in self._items]
            self._save_locked()
            return self._status_locked()

    def ensure_host_id(self, meeting_id: str, renew: bool = False) -> str:
        """ホスト用URLの経路。無ければ作って保存する。"""
        meeting_id = str(meeting_id)
        with self._lock:
            found = [m for m in self._items if m.id == meeting_id]
            if not found:
                raise ValueError("その会議は無い。")
            if found[0].host_id and not renew:
                return found[0].host_id
            new = replace(found[0], host_id=_new_host_id())
            self._items = [new if m.id == meeting_id else m for m in self._items]
            self._save_locked()
            return new.host_id

    def mark_fired(self, meeting_id: str, occurrence: str) -> None:
        """その回を済ませたことにする。**失敗しても投げない。**

        見張りから呼ばれる。ここで例外を出すと見張りが転び、同じ回を
        何度も掴むことになる。
        """
        try:
            with self._lock:
                found = [m for m in self._items if m.id == meeting_id]
                if not found:
                    return
                new = replace(found[0], last_fired=str(occurrence))
                self._items = [new if m.id == meeting_id else m for m in self._items]
                self._save_locked()
        except Exception as exc:  # noqa: BLE001
            print(f"  [会議] 済ませた印を残せない: {exc}")

    def next_occurrence(self, m: Meeting, now: datetime) -> datetime | None:
        """次に来る回。無ければ None。

        **`weekly` は日付に7日足して組み直す。** 「7日ぶんの秒を足す」ではない。
        夏時間のある土地に機体を動かしても、指定した時刻のままになる。
        """
        if not m.start:
            return None
        try:
            when = datetime.strptime(m.start, TIME_FMT)
        except ValueError:
            return None
        if not m.repeat:
            return when
        # 過去になっていたら、次に来る同じ曜日・同じ時刻まで進める。
        guard = 0
        while when < now and guard < 520:  # 10年ぶんで打ち切る
            when = datetime.combine(
                when.date() + timedelta(days=7), when.time())
            guard += 1
        return when

    def occurrence_key(self, when: datetime) -> str:
        return when.strftime(TIME_FMT)

    def due(self, now: datetime) -> list[tuple[Meeting, datetime]]:
        """いま動き出すべき会議。**新しい順ではなく、開始の早い順に返す。**

        重なっていることが分かるように、1つに絞らず全部返す。選ぶのは見張りである。
        """
        out = []
        for m in self.items():
            if not m.scheduled:
                continue
            when = self.next_occurrence(m, now)
            if when is None:
                continue
            key = self.occurrence_key(when)
            if m.last_fired == key:
                continue
            # 開始の `lead_min` 分前から掴む。過ぎすぎた回は拾わない
            # （止めていた間に流れた回まで、まとめて始めない）。
            begin = when - timedelta(minutes=m.lead_min)
            if begin <= now <= when + timedelta(minutes=config.SCHEDULE_GRACE_MIN):
                out.append((m, when))
        out.sort(key=lambda pair: pair[1])
        return out

    def upcoming(self, now: datetime, limit: int = 3) -> list[dict]:
        """これから来る回を早い順に。操作画面に出す。"""
        out = []
        for m in self.items():
            if not m.scheduled:
                continue
            when = self.next_occurrence(m, now)
            if when is None:
                continue
            key = self.occurrence_key(when)
            if m.last_fired == key and not m.repeat:
                continue
            if m.last_fired == key and m.repeat:
                when = datetime.combine(when.date() + timedelta(days=7), when.time())
            out.append({
                "id": m.id, "name": m.name,
                "at": when.strftime(TIME_FMT),
                "in_sec": int((when - now).total_seconds()),
                "zoom": bool(m.zoom),
            })
        out.sort(key=lambda d: d["at"])
        return out[:limit]

    # --- 内部 ---------------------------------------------------------------

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _status_locked(self) -> dict:
        return {
            "active": self._active,
            "error": self._error,
            "items": [dict(m.as_dict(), path=f"/v/{m.id}") for m in self._items],
        }
