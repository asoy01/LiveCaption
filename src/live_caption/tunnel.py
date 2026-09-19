"""参加者に閲覧URLを配るための出口。経路は2つあり、操作画面で選ぶ。

**Cloudflare の一時トンネル**（`Tunnel`）。`cloudflared` を子プロセスで起こし、
出力に現れる `https://<ランダムな語>.trycloudflare.com` を拾う。

    cloudflared tunnel --url http://127.0.0.1:8080

**Tailscale Funnel**（`Funnel`）。`tailscale` に配信を設定させる。

    tailscale funnel --bg 8080

どちらを使うかは `Delivery` が持つ。下の比較が選ぶときの基準である。

| | Cloudflare | Tailscale |
|---|---|---|
| URL | **起動のたびに変わる** | **変わらない** |
| 準備 | 要らない | tailnet の設定が1回 |
| 前もってURLを配れるか | 配れない | **配れる** |

**会議のURLを前もって案内に載せたいなら Tailscale を選ぶ。** ホスト名が
変わらないので、`meetings.py` が作る経路と合わせればURL全体が前日に決まる。

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

import json
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


# =========================================================================
# Tailscale Funnel
# =========================================================================

FUNNEL_HINT = (
    "tailscale が見つからない。字幕PCに Tailscale を入れて、サインインすること。\n"
    "  https://tailscale.com/download/windows"
)
# Funnel を初めて使うときは、tailnet 側で2つ有効にする必要がある。
# どちらも `tailscale funnel` が同意のページを開いて案内する。
FUNNEL_SETUP_HINT = (
    "Tailscale Funnel が有効になっていない。字幕PCで一度だけ次を実行し、\n"
    "ブラウザに出る同意のページを通すこと（tailnet の管理者権限が要る）。\n"
    "  tailscale funnel 8080\n"
    "有効にするのは HTTPS証明書 と、ポリシーの funnel 属性の2つである。"
)


def find_tailscale(explicit: str | None = None) -> str | None:
    """`tailscale` の場所を返す。見つからなければ None。"""
    if explicit:
        return explicit if Path(explicit).exists() else None
    found = shutil.which(config.TAILSCALE_CMD)
    if found:
        return found
    return str(config.TAILSCALE_LOCAL) if config.TAILSCALE_LOCAL.exists() else None


# 直前に調べたホスト名を覚えておく。`(調べた時刻, ホスト名, 失敗の理由)`。
_HOST_CACHE: tuple[float, str, str] = (0.0, "", "")
_HOST_CACHE_LOCK = threading.Lock()


def tailscale_host(command: str | None = None, max_age: float | None = None
                   ) -> tuple[str, str]:
    """このPCの tailnet 上のホスト名を返す。`(ホスト名, 失敗の理由)`。

    **配信していなくても分かる。** これが Tailscale を選ぶ理由である。会議の前日に
    URLを確定して、案内に載せられる。

        tailscale status --json  →  Self.DNSName  →  ms-s1-max.tail1234.ts.net

    **結果を少しのあいだ覚える。** ここは `Funnel.status()` から呼ばれ、
    `status()` は操作画面が2秒ごとに叩く `/api/status` から呼ばれる。覚えないと、
    **画面を開いているあいだ2秒ごとにプロセスを1つ起こす。** 常駐させる機体では
    1日に数万回になる。ホスト名が変わるのは機体の名前を変えたときだけなので、
    古い値で困ることはない。`max_age=0` で必ず取り直す。
    """
    age = config.TAILSCALE_HOST_CACHE_SEC if max_age is None else max_age
    now = time.monotonic()
    with _HOST_CACHE_LOCK:
        at, name, why = _HOST_CACHE
        if at and now - at < age:
            return name, why

    name, why = _tailscale_host_now(command)
    with _HOST_CACHE_LOCK:
        globals()["_HOST_CACHE"] = (time.monotonic(), name, why)
    return name, why


def _tailscale_host_now(command: str | None = None) -> tuple[str, str]:
    """実際に `tailscale status --json` を起こして調べる。"""
    exe = find_tailscale(command)
    if exe is None:
        return "", FUNNEL_HINT
    try:
        out = subprocess.run(
            [exe, "status", "--json"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"tailscale status が動かない: {exc}"
    if out.returncode != 0:
        return "", f"tailscale status が失敗した: {(out.stderr or out.stdout).strip()}"
    try:
        state = json.loads(out.stdout)
    except ValueError as exc:
        return "", f"tailscale status の中身が読めない: {exc}"
    if state.get("BackendState") != "Running":
        return "", ("Tailscale が繋がっていない"
                    f"（{state.get('BackendState', '状態不明')}）。サインインすること。")
    # 末尾の点を落とす。DNSの正式表記のままURLに入れない。
    name = str((state.get("Self") or {}).get("DNSName", "")).rstrip(".")
    if not name:
        return "", "tailscale status にホスト名が無い。MagicDNS を有効にすること。"
    return name, ""


class Funnel:
    """Tailscale Funnel ぶん。`Tunnel` と同じ形で使える。

    **子プロセスは残さない。** `tailscale funnel --bg` は設定を書いて終わる。
    配信そのものは tailscaled が続ける。だから `Tunnel` のような見張りが要らない。

    **URLは始める前から分かる。** `tailscale_host()` がホスト名を返す。
    """

    def __init__(self, port: int, command: str | None = None) -> None:
        self.port = port
        self.command = command
        self._lock = threading.Lock()
        self._state = "off"
        self._url = ""
        self._error = ""
        self.on_change = None

    # --- 状態 ---------------------------------------------------------------

    def base_url(self) -> str:
        """配信していなくても分かる土台のURL。分からなければ空。"""
        host, _ = tailscale_host(self.command)
        return f"https://{host}" if host else ""

    def status(self) -> dict:
        with self._lock:
            state, url, error = self._state, self._url, self._error
        host, why = tailscale_host(self.command)
        if not host and state == "off":
            error = error or why
        return {
            "state": state,
            "url": url,
            "error": error,
            "available": bool(host),
            "base_url": f"https://{host}" if host else "",
        }

    @property
    def url(self) -> str:
        with self._lock:
            return self._url

    # --- 起動と停止 ---------------------------------------------------------

    def start(self) -> dict:
        """配信を始める。**`Tunnel` と違い、戻ったときにはもう繋がっている。**"""
        exe = find_tailscale(self.command)
        if exe is None:
            with self._lock:
                self._state, self._error, self._url = "error", FUNNEL_HINT, ""
            return self.status()
        host, why = tailscale_host(self.command)
        if not host:
            with self._lock:
                self._state, self._error, self._url = "error", why, ""
            return self.status()

        with self._lock:
            self._state, self._error = "starting", ""
        try:
            out = subprocess.run(
                [exe, "funnel", "--bg", "--yes",
                 f"--https={config.FUNNEL_PUBLIC_PORT}", str(self.port)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=config.TUNNEL_TIMEOUT_SEC,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            with self._lock:
                self._state, self._error, self._url = "error", str(exc), ""
            self._changed()
            return self.status()

        if out.returncode != 0:
            # **有効化がまだ、という失敗が一番多い。** そのときは手順を出す。
            text = ((out.stderr or "") + (out.stdout or "")).strip()
            low = text.lower()
            hint = FUNNEL_SETUP_HINT if ("funnel" in low and
                                         ("enable" in low or "not allowed" in low or
                                          "attribute" in low or "https" in low)) else ""
            with self._lock:
                self._state = "error"
                self._url = ""
                self._error = f"{hint}\n\n{text}".strip() if hint else text
            self._changed()
            return self.status()

        with self._lock:
            self._state, self._url, self._error = "on", f"https://{host}", ""
        self._changed()
        return self.status()

    def stop(self) -> dict:
        """配信を畳む。**設定ごと消す。** URLはその場で死ぬ。"""
        exe = find_tailscale(self.command)
        with self._lock:
            was = self._state
            self._state, self._url, self._error = "off", "", ""
        if exe is not None and was != "off":
            try:
                subprocess.run(
                    [exe, "funnel", "--yes", f"--https={config.FUNNEL_PUBLIC_PORT}", "off"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=15,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                with self._lock:
                    self._error = f"止められなかった。手で `tailscale funnel reset` を打つこと: {exc}"
        if was != "off":
            self._changed()
        return self.status()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


# =========================================================================
# 経路の切り替え
# =========================================================================


class Delivery:
    """配信の経路を1つにまとめる。**操作画面からは、これしか見えない。**

    2つの経路を両方とも持っておき、選ばれている方に流す。持ち替えるときは、
    **前の経路を必ず止める。** 止めずに切り替えると、消せないトンネルが残る。
    """

    def __init__(self, port: int, kind: str | None = None,
                 command: str | None = None) -> None:
        self.port = port
        self.cloudflare = Tunnel(port, command)
        self.tailscale = Funnel(port)
        self._kind = kind if kind in config.TUNNEL_KINDS else config.tunnel_kind_selection()
        self._on_change = None

    # --- 経路 ---------------------------------------------------------------

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def active(self):  # noqa: ANN201
        return self.tailscale if self._kind == "tailscale" else self.cloudflare

    @property
    def on_change(self):  # noqa: ANN201
        return self._on_change

    @on_change.setter
    def on_change(self, fn) -> None:  # noqa: ANN001
        self._on_change = fn
        self.cloudflare.on_change = fn
        self.tailscale.on_change = fn

    def select(self, kind: str) -> dict:
        """経路を選び直す。**張ってあるものは先に止める。**"""
        kind = str(kind)
        if kind not in config.TUNNEL_KINDS:
            raise ValueError(f"経路が違う: 「{kind}」。{' / '.join(config.TUNNEL_KINDS)} のどちらか。")
        if kind != self._kind:
            self.active.stop()
            self._kind = kind
            config.remember_tunnel_kind(kind)
            if self._on_change is not None:
                self._on_change()
        return self.status()

    # --- 委譲 ---------------------------------------------------------------

    def base_url(self) -> str:
        """会議のURLを組み立てる土台。**Cloudflare では張るまで空。**"""
        if self._kind == "tailscale":
            return self.tailscale.base_url()
        return self.cloudflare.url

    @property
    def url(self) -> str:
        return self.active.url

    def start(self) -> dict:
        self.active.start()
        return self.status()

    def stop(self) -> dict:
        self.active.stop()
        return self.status()

    def stop_all(self) -> None:
        """終了時に呼ぶ。**選んでいない方も止める。**"""
        self.cloudflare.stop()
        self.tailscale.stop()

    def status(self) -> dict:
        st = dict(self.active.status())
        st["kind"] = self._kind
        st["kinds"] = list(config.TUNNEL_KINDS)
        st.setdefault("base_url", st.get("url", ""))
        # **前もってURLを配れるのは Tailscale だけである。** 画面でそう見せる。
        st["preannounce"] = self._kind == "tailscale"
        return st
