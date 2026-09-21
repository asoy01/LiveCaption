"""中の画面を人が見るための口。`x11vnc` と noVNC を起こす。

**常用しない。** 認証が無く、tailnet の中からは誰でも届く。使うのは、
会議ソフトへのサインインと、自動参加が詰まったときの様子見だけである。
字幕の操作（開始・停止・配信・トークン・記録）は操作画面のほうにある。

**会議中でも開け閉めできる。** `x11vnc` は、既に上がっている X 画面に
あとから貼り付くだけである。会議ソフトも字幕の生成も止まらない。
ここを起動時の設定だけにしてしまうと、**いちばん中を見たい「会議中に
様子がおかしい」ときに、コンテナを作り直すしかなくなり、会議から抜ける。**

**子プロセスは持っておいて回収する。** 入口のシェルから起こすと、シェルが
最後に `exec` で本体になるため、子の親が本体（Python）になる。本体が
`wait()` しないので、止めるたびにゾンビが1つ残る。開け閉めするボタンにする
以上、これは溜まる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config


class Vnc:
    """`x11vnc` ＋ noVNC の開始・停止。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []
        self._error = ""

    # --- 使えるかどうか -----------------------------------------------------

    def _missing(self) -> str:
        """使えない理由。使えるなら空。"""
        if not os.environ.get("DISPLAY"):
            return "画面が無い（DISPLAY が空）。Windows では使わない。"
        for cmd in ("x11vnc", "websockify"):
            if shutil.which(cmd) is None:
                return f"{cmd} が入っていない。"
        if not Path(config.NOVNC_ROOT).is_dir():
            return f"noVNC が {config.NOVNC_ROOT} に無い。"
        return ""

    @property
    def available(self) -> bool:
        return not self._missing()

    # --- 状態 ---------------------------------------------------------------

    def _reap(self) -> None:
        """終わった子を回収し、生きているものだけ残す。**呼ぶ側が lock を持つこと。**"""
        alive = []
        for p in self._procs:
            if p.poll() is None:
                alive.append(p)
        self._procs = alive

    def status(self) -> dict:
        with self._lock:
            self._reap()
            why = self._missing()
            return {
                "on": bool(self._procs),
                "available": not why,
                "error": self._error or why,
                "web_port": config.VNC_WEB_PORT,
                "rfb_port": config.VNC_RFB_PORT,
            }

    # --- 開け閉め -----------------------------------------------------------

    def start(self) -> dict:
        with self._lock:
            self._reap()
            if self._procs:
                return self._status_locked()
            why = self._missing()
            if why:
                self._error = why
                return self._status_locked()
            display = os.environ["DISPLAY"]
            try:
                # `-forever` = 見る人が居なくなっても待ち続ける。
                # `-shared`  = 2人以上が同時に見られる。
                self._procs.append(subprocess.Popen(
                    ["x11vnc", "-display", display, "-forever", "-shared",
                     "-nopw", "-quiet", "-rfbport", str(config.VNC_RFB_PORT)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL))
                # **x11vnc が待ち受けるまで少し待つ。** すぐ websockify を
                # 向けると、繋がらないまま上がったように見える。
                time.sleep(0.5)
                self._procs.append(subprocess.Popen(
                    ["websockify", f"--web={config.NOVNC_ROOT}",
                     str(config.VNC_WEB_PORT),
                     f"localhost:{config.VNC_RFB_PORT}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL))
            except OSError as exc:
                self._error = f"起こせない: {exc}"
                self._stop_locked()
                return self._status_locked()

            time.sleep(0.5)
            self._reap()
            if len(self._procs) < 2:
                # 片方だけ落ちた状態で「上がっている」と言わない。
                self._error = "上がらなかった。ポートが空いているか確かめること。"
                self._stop_locked()
            else:
                self._error = ""
            return self._status_locked()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            self._error = ""
            return self._status_locked()

    def _stop_locked(self) -> None:
        for p in self._procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.monotonic() + 5.0
        for p in self._procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        self._procs = []

    def _status_locked(self) -> dict:
        why = self._missing()
        return {
            "on": bool(self._procs),
            "available": not why,
            "error": self._error or why,
            "web_port": config.VNC_WEB_PORT,
            "rfb_port": config.VNC_RFB_PORT,
        }
