"""会議のチャットに、字幕のURLを投げる。**Linux（コンテナ）専用。**

Windows 版は `zoom_chat.py` にある。**あちらは凍結してある。触らない。**
文面とQRの組み立て（`compose` / `qr_file`）は機体を選ばないので、
あちらから借りる。**複製しないこと。**

**APIでは出来ない。** Zoom の REST API に「会議中のチャットへ投稿する」口は
無い。だから画面を操作する。Windows 版と同じ理由である。

## Windows 版との違い

**チャットは別の窓ではなく、会議の窓の中の欄である。** Windows では
`ZConfChatPopupContainerWndClass` という独立した窓が出るが、Linux 版の Zoom は
会議の窓の右側に差し込む。**だから窓のクラスでは開閉を判定できない。**

判定は**会議の窓の幅**で行う。チャットを開くと窓が横に広がる（実測で
1188 → 1600 前後）。Alt+H は開閉の切り替えなので、状態を見ずに押してはいけない。
開いているときに押すと閉じ、その後の貼り付けと Enter が会議の窓へ落ちる。
害はないが、**投稿されないまま「送った」と記録することになる。無人で回す
以上、それが一番困る。**

## クリップボードを使う

貼り付けで送る。1文字ずつ打つと、URLと日本語で取りこぼす。
`xclip` で入れて `ctrl+v` で貼る。**中身は退避して戻す。**

## 危ないことをしている自覚を持つこと

**別の窓にキーを打ち込む道具である。** 前面の窓が入れ替わった隙に打つと、
どこへ何が入るか分からない。各段の前に「いま目当ての窓が前面か」を確かめる。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import zoom_join_linux as zoom_join
# **文面とQRは借りる。** `zoom_chat.py` は Linux でも import できるように
# してある（`ctypes.windll` を持たない機体では窓の関数だけが死ぬ）。
from .zoom_chat import DIRECTION_EN, MESSAGE, compose, qr_file  # noqa: F401

#: 幅が「変わった」と見なす差。実測では **1188 → 1629** と大きく動く
#: （2026-09-21、1600x1200 の画面）。揺らぎを拾わない程度に取る。
CHAT_WIDTH_DELTA = 50

#: チャットの入力欄の位置。**窓の右下からの距離で持つ。**
#: チャットの欄は窓の右端に付くので、右からの距離が動かない。
#: 実測（2026-09-21、会議の窓 1629x800 のとき画面の (1400, 890)）。
INPUT_FROM_RIGHT = 229
INPUT_FROM_BOTTOM = 110

#: それぞれの段の待ち。
FOCUS_SEC = 0.4         # 窓を前面に出してから落ち着くまで
OPEN_CHAT_SEC = 6.0     # Alt+H のあと、窓が広がるまで待つ上限
PASTE_SEC = 0.9         # 貼ってから Enter まで
SENT_SEC = 1.5          # Enter のあと、次へ進むまで
IMAGE_PASTE_SEC = 2.0   # 画像を貼ってから、下絵が出るまで


def _run(args: list[str], timeout: float = 10.0, text_in: str | None = None) -> str:
    try:
        out = subprocess.run(
            args, input=text_in, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            stdin=None if text_in is not None else subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


# --- クリップボード ---------------------------------------------------------


def clip_get_text() -> str | None:
    """いまのクリップボード。取れなければ None。"""
    out = _run(["xclip", "-o", "-selection", "clipboard"], timeout=5)
    return out if out else None


def clip_set_text(text: str) -> bool:
    """クリップボードに入れる。

    **`xclip` は入れたあと居座る。** 選択の持ち主として残らないと、貼る前に
    中身が消えるためである。だから待たずに投げっぱなしにする。
    """
    try:
        subprocess.Popen(
            ["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).communicate(text.encode("utf-8"), timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


# --- 窓 ---------------------------------------------------------------------


def meeting_window() -> int:
    """会議の窓のID。無ければ 0。"""
    known = {n.lower() for n in zoom_join.MEETING_WINDOW_NAMES}
    for wid, _pid, name in zoom_join._windows():
        if name.lower() in known:
            try:
                return int(wid, 16)
            except ValueError:
                return 0
    return 0


def _meeting_id() -> str:
    """会議の窓のID（`xdotool` に渡す形）。無ければ空。"""
    known = {n.lower() for n in zoom_join.MEETING_WINDOW_NAMES}
    for wid, _pid, name in zoom_join._windows():
        if name.lower() in known:
            return wid
    return ""


def _width(win_id: str) -> int:
    geo = zoom_join._geometry(win_id)
    return geo[2] if geo else 0


def _raise(win_id: str) -> bool:
    """窓を前面に出す。出せたら True。"""
    _run(["xdotool", "windowactivate", "--sync", win_id], timeout=8)
    time.sleep(FOCUS_SEC)
    return _focused(win_id)


def _focused(win_id: str) -> bool:
    """いま前面にあるのがこの窓か。**打ち込む前に必ず確かめる。**"""
    now = _run(["xdotool", "getactivewindow"], timeout=5).strip()
    if not now:
        return False
    try:
        return int(now) == int(win_id, 16)
    except ValueError:
        return False


def _toggle(win_id: str, before: int) -> int:
    """Alt+H を押して、幅が変わるまで待つ。変わった後の幅（変わらなければ 0）。"""
    _run(["xdotool", "key", "--window", win_id, "alt+h"], timeout=8)
    deadline = time.monotonic() + OPEN_CHAT_SEC
    while time.monotonic() < deadline:
        time.sleep(0.25)
        now = _width(win_id)
        if abs(now - before) >= CHAT_WIDTH_DELTA:
            return now
    return 0


def open_chat() -> str:
    """チャットの欄を出して、会議の窓のIDを返す。出せなければ空。

    **「開いているか」を絶対値で判定しない。** Linux 版の Zoom はチャットを
    会議の窓の中に差し込むので、Windows 版のように別の窓のクラスでは見分け
    られない。幅で見ることになるが、**閉じた幅がいくつかは画面の大きさや
    版で変わる。** 決め打ちにすると、ある日静かに外れる。

    そこで、押してみて**幅の動いた向き**で判断する。縮んだなら、開いていた
    ものを閉じてしまったということなので、押し直す。基準値が要らない。

    実測（2026-09-21、1600x1200 の画面）: 閉 1188 → 開 1629。
    """
    win_id = _meeting_id()
    if not win_id:
        return ""
    if not _raise(win_id):
        return ""

    w0 = _width(win_id)
    w1 = _toggle(win_id, w0)
    if not w1:
        return ""           # 反応が無い。チャットが使えない会議かもしれない
    if w1 > w0:
        return win_id       # 開いた

    # 縮んだ＝開いていたのを閉じた。**押し直して元に戻す。**
    w2 = _toggle(win_id, w1)
    return win_id if w2 and w2 > w1 else ""


# --- 添付 -------------------------------------------------------------------


def _clip_set_image(path: Path) -> bool:
    """画像をクリップボードに載せる。`xclip` は選択の持ち主として居座る。"""
    try:
        subprocess.Popen(
            ["xclip", "-selection", "clipboard", "-t", "image/png",
             "-i", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except OSError:
        return False
    time.sleep(0.8)
    return True


def _click_input(win_id: str) -> bool:
    """チャットの入力欄を押して、焦点を入れる。"""
    geo = zoom_join._geometry(win_id)
    if geo is None:
        return False
    x, y, w, h = geo
    _run(["xdotool", "mousemove",
          str(x + w - INPUT_FROM_RIGHT), str(y + h - INPUT_FROM_BOTTOM)],
         timeout=8)
    time.sleep(0.3)
    _run(["xdotool", "click", "1"], timeout=8)
    time.sleep(0.5)
    return True


def attach(win_id: str, path: Path) -> bool:
    """QRをチャットに載せる。載せられたら True。

    **添付ボタンは使わない。クリップボードから貼る。**
    添付ボタンを押すと Qt のファイルダイアログが開く道もあるのだが、
    **一度は開いたきり、以後どう押しても反応しなかった**（2026-09-21）。
    原因を掴めていないものを、無人で回す経路には置かない。

    貼る形のほうが結果も良い。**チャットに画像として直に出る**ので、受け取った
    人は落とさずにその場で読み取れる。Windows 版で問題になった「ファイル名が
    乱数になって何のファイルか分からない」も起きない。
    """
    if not _clip_set_image(path):
        return False
    if not _focused(win_id):
        return False
    _click_input(win_id)
    _run(["xdotool", "key", "--window", win_id, "ctrl+v"], timeout=8)
    time.sleep(IMAGE_PASTE_SEC)
    _run(["xdotool", "key", "--window", win_id, "Return"], timeout=8)
    time.sleep(SENT_SEC)
    return True


# --- 投稿 -------------------------------------------------------------------


def post(text: str, files: list[Path] | None = None) -> dict:
    """チャットに投げる。何を投げられたかを返す。**例外は投げない。**

    無人で回す道具なので、失敗しても呼び出し側を巻き込まない。
    """
    done = {"text": False, "files": 0, "error": ""}
    win_id = open_chat()
    if not win_id:
        done["error"] = "会議のチャットを開けない。会議に入っているか確かめること。"
        return done

    saved = clip_get_text()
    try:
        if not _raise(win_id):
            done["error"] = "会議の窓を前面に出せない。"
            return done
        if not clip_set_text(text):
            done["error"] = "クリップボードに入れられない。"
            return done
        time.sleep(PASTE_SEC)
        # **前面を撃つ前にもう一度確かめる。** 前面が入れ替わった隙に打つと、
        # どこへ何が入るか分からない。
        if not _focused(win_id):
            done["error"] = "打ち込む直前に、別の窓が前面に出た。中止した。"
            return done
        _run(["xdotool", "key", "--window", win_id, "ctrl+v"], timeout=8)
        time.sleep(PASTE_SEC)
        _run(["xdotool", "key", "--window", win_id, "Return"], timeout=8)
        time.sleep(SENT_SEC)
        done["text"] = True

        # **QRは文面のあと。** 先に送ると、URLより前に絵だけが並ぶ。
        for path in files or []:
            if not Path(path).is_file():
                continue
            if attach(win_id, Path(path)):
                done["files"] += 1
            else:
                # **「送った」と嘘をつかない。** 文面は届いているので、
                # URLは読み手に渡っている。
                done["error"] = "QRを載せられなかった。文面は投げた。"
    finally:
        # **奪ったクリップボードは戻す。** 戻せるのは文字だけである。
        if saved is not None:
            clip_set_text(saved)
    return done
