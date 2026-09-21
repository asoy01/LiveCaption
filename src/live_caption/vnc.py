"""中の画面を人が見るための口。TigerVNC と noVNC を起こす。

**常用しない。** 認証が無く、tailnet の中からは誰でも届く。使うのは、
会議ソフトへのサインインと、自動参加が詰まったときの様子見だけである。
字幕の操作（開始・停止・配信・トークン・記録）は操作画面のほうにある。

繋ぎ方は3つある。

    https://<完全名>:6443/vnc.html   ブラウザ（`tailscale serve`）
    http://<機体>:6080/vnc.html      ブラウザ（素）
    <機体>:5900                       VNCクライアント

**クリップボードを自動で同期したいなら、VNCクライアントで繋ぐこと。**
ブラウザは `http` では secure context にならず、`navigator.clipboard` が
生えないので、見ている側のクリップボードを読めない。`https` の口を
用意してあるのはそのためである。

**会議中でも起動・停止できる。** `x0vncserver` は、既に上がっている X 画面に
あとから貼り付くだけである。会議ソフトも字幕の生成も止まらない。
ここを起動時の設定だけにしてしまうと、**いちばん中を見たい「会議中に
様子がおかしい」ときに、コンテナを作り直すしかなくなり、会議から抜ける。**

**子プロセスは持っておいて回収する。** 入口のシェルから起こすと、シェルが
最後に `exec` で本体になるため、子の親が本体（Python）になる。本体が
`wait()` しないので、止めるたびにゾンビが1つ残る。起動・停止を繰り返す
ボタンにする以上、これは溜まる。
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
    """TigerVNC（`x0vncserver`）＋ noVNC の開始・停止。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []
        self._error = ""

    # --- 使えるかどうか -----------------------------------------------------

    def _missing(self) -> str:
        """使えない理由。使えるなら空。

        **`autocutsel` は要求しない。** 無ければクリップボードが繋がらない
        だけで、画面は見られる。
        """
        if not os.environ.get("DISPLAY"):
            return "画面が無い（DISPLAY が空）。Windows では使わない。"
        for cmd in ("x0vncserver", "websockify"):
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
                "https_port": config.VNC_HTTPS_PORT,
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
                # **TigerVNC を使う。** x11vnc の古い cut-text は Latin-1 しか
                # 運べず、クリップボードの日本語が `???` になる。
                # `-AlwaysShared` = 2人以上が同時に見られる。
                # `-SecurityTypes None` = パスワードを持たない。
                # **守っているのは tailnet の中だけという一点である。**
                #
                # **`-fg` を必ず付けること。** Debian の `x0vncserver` は perl の
                # ラッパーで、既定では自分で `setsid` して切り離す。こちらが掴んだ
                # 子は即座に終わるので、**「上がらなかった」と誤判定したうえ、
                # 本物が野良で残る**（2026-09-21 に踏んだ）。
                #
                # **`-localhost no` が要る。** TigerVNC の既定は localhost だけで、
                # そのままだと VNC クライアントから繋げない。ブラウザ（noVNC）は
                # `http` では Windows のクリップボードを読めない（secure context で
                # ないので `navigator.clipboard` が無い）。**手で貼らずに済ませたい
                # なら、VNC クライアントで直に繋ぐしかない。**
                #
                # **`--I-KNOW-THIS-IS-INSECURE` を付けている。** TigerVNC は、
                # 認証なしで localhost 以外に出すことを拒む。もっともな警告だが、
                # **同じ箱の 6080（noVNC）が既に tailnet 全体へ認証なしで出ている**
                # ので、5900 を開けても露出の種類は変わらない。守っているのは
                # 「tailnet の中からしか届かない」の一点である。
                # 既定では動かさず、要るときだけ操作画面から開ける。
                self._procs.append(subprocess.Popen(
                    ["x0vncserver", "-fg", "-display", display,
                     "-rfbport", str(config.VNC_RFB_PORT),
                     "-localhost", "no", "--I-KNOW-THIS-IS-INSECURE",
                     "-SecurityTypes", "None", "-AlwaysShared"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL))
                # **待ち受けるまで少し待つ。** すぐ websockify を向けると、
                # 繋がらないまま上がったように見える。
                time.sleep(1.0)
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
                return self._status_locked()

            # **クリップボードの橋渡しは要らない。** TigerVNC が X の選択を
            # 自分で見張る（`autocutsel` を足していた時期があったが、不要）。
            self._error = ""
            # **https でも出す。** `http` のままだとブラウザが secure context と
            # 見なさず、`navigator.clipboard` が生えない。noVNC が見ている側の
            # クリップボードを読めないのはそのためである。
            # **失敗しても止めない。** http の 6080 は使える。
            from . import tunnel as tunnel_mod
            tunnel_mod.serve_https(config.VNC_HTTPS_PORT, config.VNC_WEB_PORT)
            return self._status_locked()

    def stop(self) -> dict:
        with self._lock:
            self._stop_locked()
            self._error = ""
            return self._status_locked()

    def _stop_locked(self) -> None:
        if self._procs:
            # **https の出口も閉じる。** 中身が死んでいるのに口だけ残すと、
            # 開いた人は「繋がらない」としか分からない。
            from . import tunnel as tunnel_mod
            tunnel_mod.serve_off(config.VNC_HTTPS_PORT)
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
            "https_port": config.VNC_HTTPS_PORT,
            "rfb_port": config.VNC_RFB_PORT,
        }
