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
from dataclasses import dataclass
from datetime import date

from . import config

# 名前の長さの上限。操作画面の一覧に収めるためで、中身の制約ではない。
NAME_MAX = 60


@dataclass(frozen=True)
class Meeting:
    """1つの会議ぶん。`id` がURLに載る。"""

    id: str
    name: str
    created: str

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "created": self.created}


def _new_id() -> str:
    return secrets.token_urlsafe(config.VIEWER_SECRET_BYTES)


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
                Meeting(str(m["id"]), str(m.get("name", "")), str(m.get("created", "")))
                for m in saved.get("items", [])
                if m.get("id")
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
