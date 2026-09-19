"""タスクトレイのアイコンと、ログのファイル出力。

**常時起動の機体では、ターミナルを開いておきたくない。** けれども窓を消すと、
生きているのか死んでいるのかが分からなくなる。そこでトレイにアイコンを出す。

    ● 緑   会議の字幕を出している
    ● 青   待機中
    ● 赤   失敗が残っている（操作画面で「了解」を押すまで消えない）

右クリックで、操作画面・ログ・終了。

**ログの行き先を先に決めてから窓を消すこと。** いまのところ、このアプリの
唯一の記録は端末に出る行である（会議1本で40行ほど）。窓を消すなら、
どこかに残さないと、失敗したときに何も分からなくなる。
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

# アイコンの色。状態ごとに描き分ける。
COLORS = {
    "idle": (88, 166, 255),      # 青。待機中
    "running": (63, 185, 80),    # 緑。字幕を出している
    "failed": (248, 81, 73),     # 赤。失敗が残っている
}


# =========================================================================
# ログ
# =========================================================================


class _Tee(io.TextIOBase):
    """端末とファイルの両方に書く。

    **端末があるときは、そちらにも出す。** `pixi run caption` を手で叩いた
    ときの見え方を変えない。窓を消して起動したときは、ファイルだけが残る。
    """

    def __init__(self, stream, handle) -> None:  # noqa: ANN001
        self._stream = stream
        self._handle = handle
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.write(text)
                    self._stream.flush()
                except (OSError, ValueError):
                    self._stream = None
            try:
                self._handle.write(text)
                self._handle.flush()
            except (OSError, ValueError):
                pass
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return bool(self._stream is not None and self._stream.isatty())


def start_logging() -> Path | None:
    """`local/log/` にログを書き始める。書いたファイルの場所を返す。

    **失敗しても落とさない。** ログが残せないことより、字幕が出ないことのほうが
    困る。古いものは `LOG_KEEP` 本だけ残して捨てる。
    """
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = config.LOG_DIR / time.strftime("livecaption_%Y-%m-%d_%H%M%S.log")
        handle = path.open("a", encoding="utf-8", errors="replace")
    except OSError:
        return None

    sys.stdout = _Tee(sys.__stdout__, handle)
    sys.stderr = _Tee(sys.__stderr__, handle)

    try:
        old = sorted(config.LOG_DIR.glob("livecaption_*.log"))
        for stale in old[:-config.LOG_KEEP]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
    return path


def open_in_editor(path: Path | None) -> None:
    """既定のアプリでファイルを開く。"""
    if path is None or not Path(path).exists():
        return
    try:
        os.startfile(str(path))  # noqa: S606
    except OSError:
        subprocess.Popen(["notepad.exe", str(path)], shell=False)


# =========================================================================
# トレイ
# =========================================================================


def _icon_image(color) -> object:  # noqa: ANN001
    """アプリのアイコンに、状態を示す小さな印を重ねる。

    **丸だけを描いてはいけない。** トレイには他のアプリも並ぶので、何のアプリか
    分からなくなる（2026-09-19 の麻生の指摘）。`etc/LiveCaption.ico` を土台にして、
    右下に色の付いた印を置く。

    アイコンが読めないときは、印だけを大きく描いて落とす。**トレイが出ないより、
    形が違うほうがましである。**
    """
    from PIL import Image, ImageDraw

    size = 64
    base = None
    try:
        icon = Image.open(config.APP_ICON)
        # .ico は複数の大きさを持つ。近いものを選んでから合わせる。
        icon.size = min(icon.info.get("sizes", [(size, size)]),
                        key=lambda wh: abs(wh[0] - size))
        icon.load()
        base = icon.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        base = None

    if base is None:
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(image).ellipse((6, 6, size - 6, size - 6),
                                      fill=(*color, 255))
        return image

    # 右下に印を置く。**縁を暗く抜く。** アイコンの絵と重なっても印が読める。
    draw = ImageDraw.Draw(base)
    r = 22
    box = (size - r - 2, size - r - 2, size - 2, size - 2)
    draw.ellipse(box, fill=(13, 17, 23, 255))
    draw.ellipse((box[0] + 3, box[1] + 3, box[2] - 3, box[3] - 3),
                 fill=(*color, 255))
    return base


class Tray:
    """トレイのアイコン1つぶん。**別のスレッドで回る。**

    本体のイベントループとは繋がっていない。状態は `web.status()` を読んで
    自分で拾う。**押されたときの操作も、HTTPの口を叩く**（本体に直接触らない）。
    """

    def __init__(self, web, log_path: Path | None = None) -> None:  # noqa: ANN001
        self.web = web
        self.log_path = log_path
        self._icon = None
        self._state = ""
        self._stop = threading.Event()

    def start(self) -> bool:
        """トレイを出す。出せなければ False（本体は続ける）。"""
        try:
            import pystray
        except ImportError:
            print("  [トレイ] pystray が入っていないので、アイコンは出さない。")
            return False

        menu = pystray.Menu(
            pystray.MenuItem("操作画面を開く", self._open_control, default=True),
            pystray.MenuItem("ログを開く", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了", self._quit),
        )
        self._icon = pystray.Icon(
            "LiveCaption", _icon_image(COLORS["idle"]), "LiveCaption", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass

    # --- 見た目 -------------------------------------------------------------

    def _watch(self) -> None:
        """状態を見て、色と説明を書き換える。**落ちないこと。**"""
        while not self._stop.wait(2.0):
            try:
                self._refresh()
            except Exception:  # noqa: BLE001
                pass

    def _refresh(self) -> None:
        sched = self.web.scheduler
        sc = sched.status() if sched is not None else {}
        if sc.get("failure"):
            state = "failed"
        elif sc.get("state") in ("joining", "arming", "running"):
            state = "running"
        else:
            state = "idle"

        parts = ["LiveCaption"]
        if state == "running":
            parts.append(f"字幕: {sc.get('meeting') or ''}")
        elif sc.get("upcoming"):
            nxt = sc["upcoming"][0]
            parts.append(f"次: {nxt['name']} {nxt['at'][-5:]}")
        else:
            parts.append("待機中")
        if sc.get("failure"):
            parts.append("失敗あり")
        # **説明は128文字までしか出ない**（Windowsの制限）。
        tip = "\n".join(parts)[:127]

        if self._icon is None:
            return
        if state != self._state:
            self._state = state
            self._icon.icon = _icon_image(COLORS[state])
        self._icon.title = tip

    # --- メニュー -----------------------------------------------------------

    def _open_control(self) -> None:
        import webbrowser

        webbrowser.open(self.web.control_url())

    def _open_log(self) -> None:
        open_in_editor(self.log_path)

    def _quit(self) -> None:
        """**本体に直接触らない。** 操作画面と同じ口を叩く。

        終わり方が2通りあると、片方だけ片付け漏れが出る。
        """
        import urllib.request

        try:
            req = urllib.request.Request(
                self.web.control_url() + "/api/shutdown", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:  # noqa: BLE001
            pass
        self.stop()
