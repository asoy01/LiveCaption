"""会議のチャットに、字幕のURLを投げる。

**APIでは出来ない。** Zoom の REST API に「会議中のチャットへ投稿する」口は
無い（`/chat/messages` は会議の外の Team Chat で、別物である）。Meeting SDK か
Zoom Apps を作れば出来るが、アプリの審査と大学側の承認が要る。字幕トークンを
手動にしているのと同じ理由で、いまは採らない。

**そこで、字幕PCの Zoom クライアントを画面操作で動かす。**

実測で確かめた窓（Zoom 7.0.6、Windows、2026-09-20）:

    会議       ConfMultiTabContentWndClass
    チャット   ZConfChatPopupContainerWndClass   Alt+H で開閉する**別の**窓

**チャットが開いているかは、この窓があるかどうかで見る。** Alt+H は開閉の
切り替えなので、状態を見ずに押してはいけない。開いているときに押すと閉じて
しまい、その後の貼り付けと Enter が会議の窓へ落ちる。害はないが、投稿されない
まま「送った」と記録することになる。**無人で回す以上、それが一番困る。**

## 危ないことをしている自覚を持つこと

**これは、別の窓にキーを打ち込む道具である。** 前面の窓が入れ替わった隙に
打つと、どこへ何が入るか分からない。各段の前に「いま前面にあるのが目当ての
窓か」を必ず確かめ、違えば中止する。`_focused` がそれをやっている。

## クリップボードを奪う

貼り付けで送るので、クリップボードを使う。**中身は退避して戻す。**
ただし戻せるのは文字だけである。画像やファイルを入れていた場合は失われる。
文字を打ち込む方法（1文字ずつキーを送る）は、URLと日本語で取りこぼすので
採らない。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time
from pathlib import Path

from . import config, zoom_join

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# チャットの窓のクラス名。**実測**（上の説明を見ること）。
CHAT_WINDOW_CLASS = "ZConfChatPopupContainerWndClass"

# 参加者に出す文面。**英語で固定する。** 読むのは会議の参加者であって、
# 操作画面を開いている人ではない。`i18n` を通さないのはそのためである。
DIRECTION_EN = {
    "ja2en": "Japanese to English",
    "en2ja": "English to Japanese",
}
MESSAGE = (
    "Live captions for this meeting ({way}):\n"
    "{url}\n"
    "Open the link in any browser. No app or sign-in needed."
)

# --- 待ち時間 ---------------------------------------------------------------
# **短くしないこと。** Zoom は貼り付けの中身を作るのに間を置く。詰めると、
# 中身が出来る前に Enter を打って、空のまま送ることになる。
FOCUS_SEC = 0.35        # 窓を前面に出してから落ち着くまで
OPEN_CHAT_SEC = 4.0     # Alt+H のあと、チャットの窓が出るまで待つ上限
PASTE_SEC = 0.9         # 文字を貼ってから Enter まで
PASTE_FILE_SEC = 2.0    # ファイルを貼ってから Enter まで
SENT_SEC = 1.5          # Enter のあと、次へ進むまで


# =========================================================================
# Windows の下回り
# =========================================================================

CF_UNICODETEXT = 13
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
SW_RESTORE = 9

VK = {"RETURN": 0x0D, "MENU": 0x12, "CONTROL": 0x11, "H": 0x48, "V": 0x56}

# **引数と戻り値の型を必ず宣言すること。** 既定は 32bit int なので、64bit の
# ハンドルが切り詰められ、`OverflowError` になる（2026-09-20 に踏んだ）。
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class _DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD), ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]


def _key(vk: int, up: bool = False) -> _INPUT:
    item = _INPUT(type=INPUT_KEYBOARD)
    item.u.ki = _KEYBDINPUT(wVk=vk, wScan=0,
                            dwFlags=KEYEVENTF_KEYUP if up else 0,
                            time=0, dwExtraInfo=None)
    return item


def _send(*names: str, hold: tuple[str, ...] = ()) -> None:
    """キーを叩く。`hold` は押しっぱなしにする修飾キー。"""
    seq: list[_INPUT] = [_key(VK[h]) for h in hold]
    for name in names:
        seq.append(_key(VK[name]))
        seq.append(_key(VK[name], up=True))
    seq.extend(_key(VK[h], up=True) for h in reversed(hold))
    arr = (_INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(_INPUT))


def _class_of(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _find_window(classes: frozenset[str] | set[str]) -> int:
    """Zoom の持ち物のうち、そのクラス名で見えている窓を1つ返す。無ければ 0。"""
    pids = zoom_join._zoom_pids()
    if not pids:
        return 0
    found: list[int] = []

    def visit(hwnd, _lparam):  # noqa: ANN001
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if (pid.value in pids and user32.IsWindowVisible(hwnd)
                and _class_of(hwnd) in classes):
            found.append(hwnd)
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(proto(visit), 0)
    return found[0] if found else 0


def _raise(hwnd: int) -> bool:
    """前面に出す。**Windows は横取りを嫌がる。** 入力スレッドを繋いでから頼む。"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    cur = kernel32.GetCurrentThreadId()
    target = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(user32.AttachThreadInput(cur, target, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur, target, False)
    time.sleep(FOCUS_SEC)
    return _focused(hwnd)


def _focused(hwnd: int) -> bool:
    """いま前面にあるのがこの窓か。**打つ前に必ず確かめること。**"""
    return user32.GetForegroundWindow() == hwnd


# --- クリップボード ---------------------------------------------------------


_owner_hwnd = 0


def _owner() -> int:
    """クリップボードの持ち主になる、見えない窓を1つ作る。

    **`OpenClipboard(NULL)` で開いてはいけない。** 持ち主が NULL になると、
    `SetClipboardData` が成功を返すのに中身が入らない。読み返すと空である
    （2026-09-20 に踏んだ。`EnumClipboardFormats` が何も返さないので分かる）。
    """
    global _owner_hwnd
    if _owner_hwnd:
        return _owner_hwnd
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    hwnd_message = wintypes.HWND(-3)   # HWND_MESSAGE。画面には出ない。
    _owner_hwnd = user32.CreateWindowExW(
        0, "STATIC", "LiveCaptionClipboard", 0, 0, 0, 0, 0,
        hwnd_message, None, None, None) or 0
    return _owner_hwnd


def _clip_open(tries: int = 10) -> bool:
    owner = _owner()
    for _ in range(tries):
        if user32.OpenClipboard(owner if owner else None):
            return True
        time.sleep(0.05)
    return False


def _clip_put(fmt: int, blob: bytes) -> bool:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(blob))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, blob, len(blob))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def clip_get_text() -> str | None:
    """いまの文字。**戻せるのはこれだけである**（画像やファイルは戻せない）。"""
    if not _clip_open():
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
    except OSError:
        return None
    finally:
        user32.CloseClipboard()


def clip_set_text(text: str) -> bool:
    if not _clip_open():
        return False
    try:
        user32.EmptyClipboard()
        return _clip_put(CF_UNICODETEXT, (text + "\x00").encode("utf-16-le"))
    finally:
        user32.CloseClipboard()


def clip_set_files(paths: list[Path]) -> bool:
    """ファイルそのものを載せる。エクスプローラでコピーしたのと同じ形。

    **画像を CF_DIB で載せてはいけない。** Zoom が乱数の名前を付けて送るので、
    受け取った人には何のファイルか分からない。こちらなら名前が残る。
    """
    head = _DROPFILES(pFiles=ctypes.sizeof(_DROPFILES), pt=wintypes.POINT(0, 0),
                      fNC=False, fWide=True)
    names = "".join(str(p) + "\x00" for p in paths) + "\x00"
    if not _clip_open():
        return False
    try:
        user32.EmptyClipboard()
        return _clip_put(CF_HDROP, bytes(head) + names.encode("utf-16-le"))
    finally:
        user32.CloseClipboard()


# =========================================================================
# 本体
# =========================================================================


def compose(url: str, direction: str = "") -> str:
    """参加者に出す文面を作る。**英語で固定。**"""
    way = DIRECTION_EN.get(direction or config.DIRECTION, "")
    if not way:
        return f"Live captions for this meeting:\n{url}\n" \
               "Open the link in any browser. No app or sign-in needed."
    return MESSAGE.format(way=way, url=url)


def qr_file(url: str, name: str = "") -> Path | None:
    """チャットに添えるQRを書き出す。書けなければ None。

    **受け取った人に分かる名前にする。** クリップボード経由で送るので、
    ファイル名がそのまま相手に見える。`{GUID}.png` では何のファイルか
    分からない。

    置き場は `local/`。会議の記録と混ぜない。**次に投げるときに上書きする。**
    """
    try:
        import segno

        safe = "".join(c for c in name
                       if c.isascii() and (c.isalnum() or c in "-_ ")).strip()
        safe = "-".join(safe.split())[:40]
        out = config.LOG_DIR.parent / (
            f"live-captions-qr-{safe}.png" if safe else "live-captions-qr.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        segno.make(url, error="m").save(
            str(out), scale=8, border=3, dark="#000000", light="#ffffff")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"  [チャット] QRを書けない: {exc}")
        return None


def meeting_window() -> int:
    return _find_window(zoom_join.MEETING_WINDOW_CLASSES)


def chat_window() -> int:
    return _find_window({CHAT_WINDOW_CLASS})


def open_chat() -> int:
    """チャットの窓を出して、その窓を返す。出せなければ 0。

    **既に開いていれば Alt+H を押さない。** 押すと閉じてしまう。
    """
    found = chat_window()
    if found:
        return found
    meeting = meeting_window()
    if not meeting or not _raise(meeting):
        return 0
    _send("H", hold=("MENU",))
    deadline = time.monotonic() + OPEN_CHAT_SEC
    while time.monotonic() < deadline:
        time.sleep(0.25)
        found = chat_window()
        if found:
            return found
    return 0


def post(text: str, files: list[Path] | None = None) -> dict:
    """チャットに投げる。何を投げられたかを返す。**例外は投げない。**

    **文字を先に送る。** ファイルはホストの設定で止められることがあり、
    こちらからは止められたことが分からない。先に文字を送っておけば、
    止められてもURLは届く。

    返すのは `{"text": bool, "files": int, "why": str}`。
    """
    done = {"text": False, "files": 0, "why": ""}
    files = list(files or [])

    chat = open_chat()
    if not chat:
        done["why"] = ("チャットの窓を出せない。会議に入っているか確かめること。"
                       if meeting_window() else "Zoomの会議の窓が無い。")
        return done

    saved = clip_get_text()
    try:
        if text:
            done["text"] = _paste_and_send(chat, lambda: clip_set_text(text),
                                           PASTE_SEC)
            if not done["text"]:
                done["why"] = "貼り付けの途中で、前面の窓が入れ替わった。"
                return done
        for path in files:
            if not Path(path).exists():
                continue
            ok = _paste_and_send(chat, lambda p=path: clip_set_files([Path(p)]),
                                 PASTE_FILE_SEC)
            if not ok:
                # **ここで止めない。** 文字は既に届いている。
                done["why"] = "ファイルを貼れなかった。URLだけは届いている。"
                break
            done["files"] += 1
    finally:
        # **必ず戻す。** 人が入れていたものを黙って持っていかない。
        if saved is not None:
            clip_set_text(saved)
    return done


def _paste_and_send(chat: int, load, wait: float) -> bool:  # noqa: ANN001
    """クリップボードに載せて、貼って、送る。前面が外れたら中止する。"""
    if not _raise(chat):
        return False
    if not load():
        return False
    time.sleep(0.25)
    if not _focused(chat):
        return False
    _send("V", hold=("CONTROL",))
    time.sleep(wait)
    if not _focused(chat):
        return False
    _send("RETURN")
    time.sleep(SENT_SEC)
    return True
