"""Zoomの会議に入る・出る。Windows専用。

Zoomは `zoommtg:` という経路を OS に登録する。そこへURLを投げると、
Zoomクライアントが起きて会議に入る。

    zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=<ハッシュ>

**入れたかどうかは、こちらには分からない。** 投げるのは一方通行で、
待機室・パスコード違い・更新のダイアログのどれに落ちても、返ってくるものが無い。
**入れたことの確認は音でしか取れない**（`schedule.py` が、文字起こしが出たかで見る）。

**出るには終了させるしかない。** Zoomに「会議から出る」を外から頼む口は無い。
字幕専用のPCなので許容できるが、**こちらが起こしたZoom以外は殺さない。**
麻生が開いたままの会議を巻き添えにしてはいけない。

Zoom側の設定（1回きり。毎回は要らない）:

- 音声の出力先を `CABLE Input` に、マイクはミュート
- 表示名を `Live Captions` などにする
- 「参加時にマイクをミュート」「参加時にビデオをオフ」
- **「コンピューターオーディオに参加」の窓を出さない設定にする。**
  出たままだと、無人で入っても音が繋がらず、原因の分からない失敗になる
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

# 会議番号は9〜11桁。人が貼ると空白やハイフンが混ざる。
_DIGITS = re.compile(r"[0-9]")
# 招待URLの形。`/j/<番号>` を拾う。ホストは `zoom.us` かその下。
_JOIN_PATH = re.compile(r"/j/(\d{9,12})")
# 個人リンク。**番号が入っていないので、こちらでは会議番号に直せない。**
_PERSONAL = re.compile(r"/my/([A-Za-z0-9._-]+)")
# Windows の既定の置き場。レジストリが読めなかったときに見る。
_FALLBACK_EXE = os.path.expandvars(r"%APPDATA%\Zoom\bin\Zoom.exe")


class JoinError(ValueError):
    """会議の指定として受け付けられない文字列。"""


def parse_meeting(text: str) -> tuple[str, str]:
    """会議の指定から `(会議番号, パスコード)` を取り出す。

    受け付ける形:

        https://zoom.us/j/1234567890?pwd=abc
        https://u-tokyo-ac-jp.zoom.us/j/1234567890?pwd=abc
        zoommtg://zoom.us/join?action=join&confno=1234567890&pwd=abc
        1234567890
        123 4567 890        （人が貼るとこうなる）

    **`pwd` はURLに載っているハッシュをそのまま使う。** 人が読むパスコード
    （6桁の数字など）ではない。招待URLから切り取ってくること。
    """
    raw = str(text or "").strip()
    if not raw:
        raise JoinError("会議の指定が空である。")
    if len(raw) > 500:
        raise JoinError("会議の指定が長すぎる。招待URLをそのまま貼ること。")

    # 数字と区切りだけなら、会議番号そのものとみなす。
    if re.fullmatch(r"[0-9 \-]+", raw):
        digits = "".join(_DIGITS.findall(raw))
        if not (9 <= len(digits) <= 12):
            raise JoinError(f"会議番号の桁数がおかしい: 「{raw}」。9〜11桁である。")
        return digits, ""

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https", "zoommtg"):
        raise JoinError(f"知らない書き方: 「{raw[:80]}」。招待URLか会議番号を入れること。")

    # **Zoom の宛先かどうかを確かめる。** 危険だからではなく（番号しか取らない）、
    # 貼り間違いを黙って飲み込まないためである。関係の無いURLから数字だけ拾って
    # 別の会議に入りに行く、という失敗がいちばん分かりにくい。
    host = (parsed.hostname or "").lower()
    if host and host != "zoom.us" and not host.endswith(".zoom.us"):
        raise JoinError(
            f"Zoom の招待URLではない: 「{raw[:80]}」"
            f"（宛先が {host}）。zoom.us のURLか、会議番号を入れること。"
        )

    query = urllib.parse.parse_qs(parsed.query)
    pwd = (query.get("pwd") or [""])[0]

    # zoommtg: は confno をそのまま持っている。
    confno = (query.get("confno") or [""])[0]
    if confno:
        digits = "".join(_DIGITS.findall(confno))
        if not (9 <= len(digits) <= 12):
            raise JoinError(f"会議番号の桁数がおかしい: 「{confno}」。")
        return digits, pwd

    if _PERSONAL.search(parsed.path or ""):
        # **個人リンクは会議番号に直せない。** 開くたびに違う会議になりうるし、
        # URLの中に番号が無い。黙って失敗させず、何をすればよいかを言う。
        raise JoinError(
            "個人リンク（/my/…）には対応していない。"
            "会議を始めたときに出る、番号入りの招待URL（/j/…）を貼ること。"
        )

    found = _JOIN_PATH.search(parsed.path or "")
    if not found:
        raise JoinError(f"会議番号が見つからない: 「{raw[:80]}」")
    return found.group(1), pwd


def join_url(confno: str, pwd: str = "", name: str = "") -> str:
    """`zoommtg:` のURLを組み立てる。**値は必ず符号化する。**

    元になるのは人が貼った文字列なので、そのまま繋げてはいけない。
    """
    parts = ["action=join", "confno=" + urllib.parse.quote(str(confno))]
    if pwd:
        parts.append("pwd=" + urllib.parse.quote(str(pwd)))
    if name:
        parts.append("uname=" + urllib.parse.quote(str(name)))
    return "zoommtg://zoom.us/join?" + "&".join(parts)


def zoom_exe() -> str | None:
    """`Zoom.exe` の場所。見つからなければ None。

    **決め打ちにしない。** Zoomは更新のたびに自分を置き直す。OSに登録されている
    `zoommtg:` の開き方を先に見て、無ければ既定の置き場を見る。
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"zoommtg\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, "")
        # `"C:\...\Zoom.exe" "--url=%1"` という形で入っている。
        found = re.match(r'\s*"([^"]+)"', str(command))
        path = found.group(1) if found else str(command).split()[0]
        if os.path.exists(path):
            return path
    except (ImportError, OSError, IndexError, AttributeError):
        pass
    return _FALLBACK_EXE if os.path.exists(_FALLBACK_EXE) else None


def running() -> bool:
    """`Zoom.exe` が動いているか。**会議に入っているかではない。**

    起きているのに待機室で止まっている、という状態も True になる。
    区別する手立ては無い。
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Zoom.exe", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "Zoom.exe" in (out.stdout or "")


def join(text: str, name: str = "") -> str:
    """Zoomを起こして会議に入らせる。投げたURLを返す。

    **入れたかどうかは分からない。** `zoommtg:` はハンドラに渡すだけで、
    待機室・パスコード違い・更新のダイアログのどれに落ちても何も返らない。
    確認は音で取ること。
    """
    confno, pwd = parse_meeting(text)
    url = join_url(confno, pwd, name)
    exe = zoom_exe()
    try:
        if exe:
            # **シェルを通さない。** `pwd` は人が貼った文字列から来る。
            subprocess.Popen(
                [exe, f"--url={url}"], shell=False, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            # 実行ファイルが見つからないときは、OSの関連付けに任せる。
            os.startfile(url)  # noqa: S606
    except OSError as exc:
        raise JoinError(
            f"Zoomを起こせない: {exc}\n"
            f"  探した場所: {exe or _FALLBACK_EXE}\n"
            "  Zoomが入っているか確かめること。"
        ) from exc
    return url


def leave() -> bool:
    """Zoomを終了させる。終了させたなら True。

    **会議から出る口は無い。** 終了させるしかない。
    `Zoom.exe` を全部落とすので、**こちらが起こしたときだけ呼ぶこと**
    （`schedule.py` が覚えている）。
    """
    if not running():
        return False
    try:
        subprocess.run(
            ["taskkill", "/IM", "Zoom.exe", "/T", "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True
