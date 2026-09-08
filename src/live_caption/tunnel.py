"""一時トンネル（TryCloudflare）の起動と、閲覧URLの取り出し。

参加者に閲覧URLを配るための出口である。`cloudflared` を子プロセスで起こし、
出力に現れる `https://<ランダムな語>.trycloudflare.com` を拾う。

    cloudflared tunnel --url http://127.0.0.1:8080

**接続は字幕PCから外向きに張られる。** 着信は要らない。ポート開放も、ルータの
設定も、管理者への申請もいらない。学内LAN・会議室のWiFi・テザリングのどれでも通る。

**アカウントもドメインも要らない。** 代わりに次の制約がある（Cloudflareが明記）。

- URLは起動のたびに変わる。前のURLは死ぬ
- SLAが無い。「テストと開発用」と書かれている
- 同時のリクエストは200まで。長ポーリングは1人が1本占めるので、これが人数の上限になる
- **Server-Sent Events が使えない。** だから `web.py` は長ポーリングにしてある

**既定では張らない。** 会議の字幕は Cloudflare を通り、TLSもそこで終わる。
未公開の観測結果を扱う会議では、画面共有（外に出ない）に留めること。
使うときだけ、操作画面から開始する。

`cloudflared` が無くても本体は止めない。画面共有とZoom字幕APIは使えるためである。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config

# cloudflared が出す一時トンネルのURL。
URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
# 失敗したときに見せる出力の行数。
TAIL_LINES = 40

INSTALL_HINT = (
    "cloudflared が見つからない。次のどちらかで用意すること。\n"
    "  1. 実行ファイルを1つ置く（管理者権限は要らない）:\n"
    "     https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe\n"
    f"     を {config.TUNNEL_LOCAL} に置く\n"
    "  2. winget install --id Cloudflare.cloudflared"
)


def find_cloudflared(explicit: str | None = None) -> str | None:
    """`cloudflared` の場所を返す。見つからなければ None。"""
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which(config.TUNNEL_CMD)
    if found:
        return found
    return str(config.TUNNEL_LOCAL) if config.TUNNEL_LOCAL.exists() else None


class Tunnel:
    """`cloudflared` の子プロセス1つぶん。

    状態は4つ。`off` → `starting` → `on`、または `error`。
    **操作画面（HTTPサーバのスレッド）から開始・停止される。** `_lock` で守る。
    """

    def __init__(self, port: int, command: str | None = None) -> None:
        self.port = port
        self.command = command
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._state = "off"
        self._url = ""
        self._error = ""
        self._tail: list[str] = []
        # URLが出たときに呼ぶ。画面の表示を揃えるために使う。
        self.on_change = None

    # --- 状態 ---------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "url": self._url,
                "error": self._error,
                "available": find_cloudflared(self.command) is not None,
            }

    @property
    def url(self) -> str:
        with self._lock:
            return self._url

    # --- 起動と停止 ---------------------------------------------------------

    def start(self) -> dict:
        """トンネルを張る。**すぐ返る。** URLは後から出てくる。"""
        with self._lock:
            if self._state in ("starting", "on"):
                return self._status_locked()
            exe = find_cloudflared(self.command)
            if exe is None:
                self._state, self._error, self._url = "error", INSTALL_HINT, ""
                return self._status_locked()

            try:
                proc = subprocess.Popen(
                    [exe, "tunnel", "--url", f"http://127.0.0.1:{self.port}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    # 端末の Ctrl+C を子に飛ばさない。停止はこちらから行う。
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            except OSError as exc:
                self._state, self._error, self._url = "error", str(exc), ""
                return self._status_locked()

            self._proc = proc
            self._state, self._url, self._error, self._tail = "starting", "", "", []
            started = time.monotonic()

        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        threading.Thread(target=self._watch, args=(proc, started), daemon=True).start()
        return self.status()

    def stop(self) -> dict:
        """トンネルを畳む。URLはその場で死ぬ。"""
        with self._lock:
            was = self._state
            proc, self._proc = self._proc, None
            self._state, self._url, self._error = "off", "", ""
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # 張っていなかったなら黙って戻る。終了のたびに「止めた」と出したくない。
        if was != "off":
            self._changed()
        return self.status()

    # --- 子プロセスの見張り -------------------------------------------------

    def _read(self, proc: subprocess.Popen) -> None:
        """出力からURLを拾う。失敗したときのために末尾も溜める。"""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            with self._lock:
                if self._proc is not proc:
                    return  # stop() 済み。もう関係ない
                self._tail.append(line)
                if len(self._tail) > TAIL_LINES:
                    del self._tail[: len(self._tail) - TAIL_LINES]
                m = URL_RE.search(line)
                found = bool(m) and self._state != "on"
                if m:
                    self._url, self._state, self._error = m.group(0), "on", ""
            if found:
                self._changed()

        # 出力が終わった＝プロセスが終わった。
        with self._lock:
            if self._proc is not proc:
                return
            self._proc = None
            if self._state != "off":
                self._state = "error"
                self._url = ""
                self._error = "cloudflared が終了した。\n" + "\n".join(self._tail[-8:])
        self._changed()

    def _watch(self, proc: subprocess.Popen, started: float) -> None:
        """URLが出ないまま時間切れになったら、失敗として見せる。"""
        time.sleep(config.TUNNEL_TIMEOUT_SEC)
        with self._lock:
            if self._proc is not proc or self._state != "starting":
                return
            elapsed = time.monotonic() - started
            self._state = "error"
            self._error = (
                f"{elapsed:.0f}秒たってもURLが出てこない。\n" + "\n".join(self._tail[-8:])
            )
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _status_locked(self) -> dict:
        return {
            "state": self._state,
            "url": self._url,
            "error": self._error,
            "available": True,
        }
