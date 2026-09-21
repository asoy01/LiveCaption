"""Zoomの会議に入る・出る。**Linux（コンテナ）専用。**

Windows 版は `zoom_join.py` にある。**あちらは凍結してある。触らない。**
共通の部分（`parse_meeting` / `join_url` / `JoinError`）はあちらから借りる。

Windows との大きな違いは2つある。

1. **プレビュー窓が出る。** `enableShowPreviewWndToJoin=false` は効かない
   （段階0で確かめた）。だから「Join」を押す段が要る。
2. **入室と「音に入る」は別である。** Join のあとに
   「Join with Computer Audio」を押すまで、音は1バイトも来ない。

**ボタンは窓の角からの距離で押す。** 画面の絶対座標で持つと、窓の大きさや
画面の解像度が変わった日に、黙って外れる。距離は実測で決めた（2026-09-21、
1600x1200 の画面）。

    プレビュー窓 638x532 のとき Join は画面 (1073, 1016)
      → 右から 47、下から 49
    音声ダイアログ 570x400 のとき Join with Computer Audio は (799, 563)
      → 横は中央、上から 143
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from . import config
from .zoom_join import JoinError, join_url, parse_meeting  # noqa: F401

#: 会議の窓の名前。**コンテナの locale は固定してあるので英語である。**
#: `zoom.zoom` というクラス名は全部の窓で同じなので、Windows のように
#: クラスでは見分けられない（2026-09-21 に確かめた）。
MEETING_WINDOW_NAMES = frozenset({"Meeting", "Zoom Meeting"})
#: 常駐の窓口。会議とは関係がない。
HOME_WINDOW_NAMES = frozenset({"Zoom Workplace", "Zoom"})
#: 音声ダイアログの見分け方（部分一致）。
AUDIO_DIALOG_HINT = "audio conference options"

#: プレビュー窓と見なす最小の大きさ。
#: **Zoom は細い通知窓を出す**（実測で `zoom` という名前の 850x74 が出た）。
#: 名前だけで選ぶと、そちらを押しにいく。実物は 638x532 だった。
MIN_PREVIEW_W = 300
MIN_PREVIEW_H = 300

#: ボタンの位置（上の実測）。
JOIN_FROM_RIGHT = 47
JOIN_FROM_BOTTOM = 49
AUDIO_FROM_TOP = 143

#: それぞれの段を待つ秒数。
#: **速いときは速い。** プロファイルが温まっていれば、プレビュー窓は 4 秒で
#: 出て、参加から音が来るまで 2〜5 秒で終わる（2026-09-21 の実測）。
#: ここを短くしないのは、冷えた起動と、混んでいる回線のためである。
PREVIEW_WAIT_SEC = 45.0
AUDIO_DIALOG_WAIT_SEC = 45.0
AUDIO_READY_WAIT_SEC = 20.0
_POLL_SEC = 0.5

#: クラッシュ報告の置き場。**空にしてから起こす。**
#: 前回が不正終了だと「Zoom quit unexpectedly」が前面に出て、
#: 座標で押す手順がそこで止まる。
CRASH_REPORT_DIR = Path.home() / ".zoom" / "reports"


def _run(args: list[str], timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


def _pids() -> set[int]:
    """Zoom 本体のプロセス。**`pgrep -f` は使わない。**

    自分を起動した殻の命令行にも `zoom` の字が入るので、拾ってしまう。
    """
    pids = set()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            cmd = (p / "cmdline").read_bytes().split(b"\0")[0].decode(
                "utf-8", "replace")
        except OSError:
            continue
        if cmd == "/opt/zoom/zoom":
            pids.add(int(p.name))
    return pids


def running() -> bool:
    """Zoom が動いているか。

    **これで「会議に入っているか」を判断してはいけない。** Zoom は会議を
    抜けても常駐の窓口を残す。会議中かどうかは `in_meeting()` で見ること。
    """
    return bool(_pids())


def _windows() -> list[tuple[str, int, str]]:
    """`(窓ID, PID, 名前)` の一覧。Zoom のものだけ。"""
    pids = _pids()
    out = []
    for line in _run(["wmctrl", "-lp"]).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5 or not parts[2].isdigit():
            continue
        if int(parts[2]) in pids:
            out.append((parts[0], int(parts[2]), parts[4].strip()))
    return out


def _geometry(win_id: str) -> tuple[int, int, int, int] | None:
    """`(x, y, 幅, 高さ)`。取れなければ None。"""
    vals = {}
    for line in _run(["xdotool", "getwindowgeometry", "--shell", win_id]).splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if v.strip().lstrip("-").isdigit():
                vals[k.strip()] = int(v)
    try:
        return vals["X"], vals["Y"], vals["WIDTH"], vals["HEIGHT"]
    except KeyError:
        return None


def _audio_attached() -> bool:
    """Zoom が音を書き出しているか。**sink-input の有無で見る。**

    PulseAudio の言葉で、`meeting` と `mic` が **sink**（出力先）、
    アプリがそこへ繋いだ音の流れが **sink-input** である。Zoom が会議の音声に
    入ると sink-input が現れ、抜けると消える。

    **これがいちばん確かな「会議中」の印である。** 窓の名前は表示言語で
    変わるが、これは変わらない。この箱で音を出すのは Zoom だけである。
    """
    return bool(_run(["pactl", "list", "short", "sink-inputs"]).strip())


def in_meeting() -> bool:
    """いま会議に入っているか。

    **プロセスの有無では分からない。** Zoom は会議を抜けても常駐の窓口を
    残すので、常時起動の機体ではほぼいつでも動いている。それを「会議中」と
    読むと、**こちらが入れた会議から永遠に出なくなる。**

    音（sink-input）を先に見て、無ければ窓の名前を見る。音はまだ繋がって
    いないが会議の窓は出ている、という途中の状態があるためである。
    """
    if not running():
        return False
    if _audio_attached():
        return True
    known = {n.lower() for n in MEETING_WINDOW_NAMES}
    return any(name.lower() in known for _id, _pid, name in _windows())


def _click(x: int, y: int) -> None:
    _run(["xdotool", "mousemove", str(x), str(y), "click", "1"])


def _wait(predicate, limit: float):  # noqa: ANN001
    """`predicate()` が真を返すまで待つ。返り値をそのまま返す。時間切れなら None。"""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(_POLL_SEC)
    return None


def _preview_window() -> tuple[str, int, str] | None:
    """プレビュー窓。**名前は会議の題名なので、前もっては分からない。**

    だから「常駐の窓口でも、会議の窓でも、音声ダイアログでもないもの」で拾う。
    """
    known = {n.lower() for n in HOME_WINDOW_NAMES | MEETING_WINDOW_NAMES}
    for win in _windows():
        name = win[2].lower()
        if name in known or AUDIO_DIALOG_HINT in name:
            continue
        geo = _geometry(win[0])
        if geo is None or geo[2] < MIN_PREVIEW_W or geo[3] < MIN_PREVIEW_H:
            continue
        return win
    return None


def _audio_dialog() -> tuple[str, int, str] | None:
    for win in _windows():
        if AUDIO_DIALOG_HINT in win[2].lower():
            return win
    return None


def join(text: str, name: str = "") -> str:
    """Zoom を起こして会議に入らせる。投げたURLを返す。

    **入れたかどうかは、ここでは分からない。** 待機室・パスコード違い・
    更新のダイアログのどれに落ちても、Zoom は何も返さない。
    **確認は音で取ること**（`in_meeting()` が sink-input を見ている）。
    """
    confno, pwd = parse_meeting(text)
    url = join_url(confno, pwd, name)

    exe = shutil.which(config.ZOOM_CMD)
    if exe is None:
        raise JoinError(
            f"Zoom が見つからない（{config.ZOOM_CMD}）。\n"
            "  コンテナに Zoom が入っているか確かめること。")

    # **クラッシュ報告を先に捨てる。** 前回が不正終了だと、起動した瞬間に
    # 「Zoom quit unexpectedly」が前面に出て、下の座標クリックがそこで止まる。
    try:
        for f in CRASH_REPORT_DIR.iterdir():
            if f.is_file():
                f.unlink()
    except OSError:
        pass

    try:
        subprocess.Popen(
            [exe, url], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise JoinError(f"Zoom を起こせない: {exc}") from exc

    # --- プレビュー窓の「Join」 ---------------------------------------------
    # **`enableShowPreviewWndToJoin=false` は効かない**（段階0）。
    win = _wait(_preview_window, PREVIEW_WAIT_SEC)
    if win is not None:
        geo = _geometry(win[0])
        if geo is not None:
            x, y, w, h = geo
            _click(x + w - JOIN_FROM_RIGHT, y + h - JOIN_FROM_BOTTOM)

    # --- 音声ダイアログの「Join with Computer Audio」 -----------------------
    # **入室と「音に入る」は別の操作である。** ここを押すまで音は来ない。
    dlg = _wait(_audio_dialog, AUDIO_DIALOG_WAIT_SEC)
    if dlg is not None:
        geo = _geometry(dlg[0])
        if geo is not None:
            x, y, w, h = geo
            _click(x + w // 2, y + AUDIO_FROM_TOP)

    # 音が来るまで待つ。**来なくても例外にしない。** 待機室で待たされている
    # だけかもしれない。見張り（schedule.py）が無音で畳む。
    _wait(_audio_attached, AUDIO_READY_WAIT_SEC)
    return url


def leave() -> bool:
    """Zoom を終了させる。終了させたなら True。

    **会議から出る口は無い。** 終了させるしかない。
    **こちらが会議に入れたときだけ呼ぶこと**（`schedule.py` が覚えている）。
    """
    if not in_meeting():
        return False
    pids = _pids()
    if not pids:
        return False
    _run(["pkill", "-TERM", "-f", "/opt/zoom/zoom"], timeout=15)
    # 素直に落ちなければ切る。**残すと次の会議に入れない。**
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _pids():
        time.sleep(_POLL_SEC)
    if _pids():
        _run(["pkill", "-KILL", "-f", "/opt/zoom/zoom"], timeout=15)
    return True
