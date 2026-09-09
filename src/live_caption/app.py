"""全体の組み立て。

    CABLE Output（または WAVファイル）
       ↓ 24 kHz mono PCM、100 ms ずつ
    gpt-live-transcribe（delta を連続で返す）
       ↓
    Segmenter（文の切り出し。モデルは区切りを返さない）
       ↓
    Translator（gpt-4.1-mini。用語表＋置換規則。英語はそのまま）
       ↓ 1行ずつ
    Zoom字幕API（seq を保存しながらPOST）

翻訳は1文あたり約0.9秒（実測、2026-09-09）。会議では文がそれより短い間隔で
出ることがあるので、**訳しながら次の文の翻訳を始める。ただし送る順序は守る。**
順序が入れ替わると読めなくなる。

**無音で確定する文（約8.6%）は、確定を待たずに先回りで訳しておく。**
`IDLE_FLUSH_SEC` は下げられないので（delta 間隔の p99 が閾値より上）、
待っている間に訳しておくのが、この経路を速くする唯一の手である。

ホストの画面には字幕が出ないので、麻生が動作を確認する手段はこの画面のログだけである。
送った内容を必ず表示する。

字幕の出口は2つある。**どちらか一方でも、両方同時でもよい。**

1. Zoom字幕API（`captions.py`）。ホスト権限で取ったトークンが要る
2. ブラウザ字幕（`web.py`）。権限は要らない。画面共有して見せる
"""

from __future__ import annotations

import asyncio
import time

from . import asr as asr_mod
from . import audio as audio_mod
from . import captions as captions_mod
from . import config, glossary
from . import segmenter as segmenter_mod
from . import transcript as transcript_mod
from . import translator as translator_mod

# 同時に走らせる翻訳の数。多くしても順序は守るが、遅れが見えにくくなる。
MAX_INFLIGHT = 4


def now() -> str:
    return time.strftime("%H:%M:%S")


def _bare(text: str) -> str:
    """末尾の文末記号と空白を落とす。先回りの翻訳の照合に使う。"""
    return text.rstrip("。！？.!? 　")


class ZoomControl:
    """Zoom字幕の開始・停止を、ブラウザの操作画面から扱えるようにする。

    **呼ぶのはHTTPサーバのスレッドである。** 本体のイベントループとは別なので、
    コルーチン（捨て字幕の送信）は `run_coroutine_threadsafe` で渡す。

    トークンは会議が始まらないと取れない。したがって起動時には決められず、
    ここで後から差し替えられるようにしてある。
    """

    def __init__(self, sender: captions_mod.CaptionSender) -> None:
        self.sender = sender
        self.loop: asyncio.AbstractEventLoop | None = None
        # 状態が変わったときに呼ぶ。ブラウザ側の表示を揃えるために使う。
        self.on_change = None

    def status(self) -> dict:
        return self.sender.status()

    def set_token(self, url: str) -> dict:
        """トークンを入れる。不正なら captions.TokenError を投げる。"""
        meeting = self.sender.set_token(url)
        print(f"[{now()}] Zoom  トークンを受け取った（会議 {meeting}、"
              f"seq {self.sender.seq} から）")
        self._changed()
        return self.status()

    def set_enabled(self, on: bool) -> dict:
        """Zoomへの送信を開始・停止する。"""
        if on and self.sender.dry_run:
            raise captions_mod.TokenError(
                "--dry-run で起動しているので、Zoomへは送らない。"
                "送るなら --dry-run を外して起動し直すこと。"
            )
        if on and not self.sender.base_url:
            raise captions_mod.TokenError("先にトークンを入れること。")

        was_active = self.sender.active
        self.sender.set_enabled(on)
        print(f"[{now()}] Zoom  送信を{'開始' if on else '停止'}した")

        # 停止していたものを開始したときは、捨て字幕から始める。
        # 受信側は字幕が流れ始めるまで「字幕を表示」を有効にできない。
        if on and not was_active and self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.sender.warmup(), self.loop)
        self._changed()
        return self.status()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.status())


class EngineControl:
    """**字幕の生成そのもの**（録音・認識・翻訳）の開始と停止。

    Zoomへの送信とは別の関門である。こちらを止めると、音声デバイスを閉じ、
    認識のWebSocketも切る。**音は取り込まれず、APIも呼ばれない。**

    操作画面がある起動（`--web`）では、**停止した状態から始める。** 会議に入る前に
    アプリを立ち上げておけるようにするためである。準備中の雑談を認識に流さない。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {"generating": self.app.generating}

    def set_running(self, on: bool) -> dict:
        self.app.set_generating(on)
        print(f"[{now()}] 生成  字幕の生成を{'開始' if on else '停止'}した")
        return self.status()


class AudioControl:
    """入力デバイスの一覧と差し替え。

    **選び違えても、誰かが喋るまで気づけないのでは遅い。** 一覧には
    ホストAPIも出す（Windowsでは同じ名前が MME・DirectSound・WASAPI に現れる）。
    生成中は直近の最大振幅も返すので、音が来ているかを目で見られる。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def devices(self) -> dict:
        """選べる入力の一覧。--from-file のときは選べない。"""
        capture = self.app.capture
        if not isinstance(capture, audio_mod.Capture):
            return {"selectable": False, "devices": [], "index": None,
                    "name": "ファイル（--from-file）"}
        return {
            "selectable": True,
            "index": capture.device,
            "name": audio_mod.describe_device(capture.device),
            "devices": [
                {"index": i, "name": name, "channels": ch, "api": api}
                for i, name, ch, api in audio_mod.list_devices()
            ],
        }

    def status(self) -> dict:
        capture = self.app.capture
        st = {
            "selectable": isinstance(capture, audio_mod.Capture),
            "name": "—",
            "index": None,
            "level": 0.0,
            "dropped": 0,
            "error": self.app.audio_error,
        }
        if capture is None:
            return st
        st["dropped"] = getattr(capture, "dropped", 0)
        if st["selectable"]:
            st["index"] = capture.device
            st["name"] = audio_mod.describe_device(capture.device)
            # 生成中でなければデバイスは閉じているので、メーターは動かない。
            if self.app.generating:
                st["level"] = round(capture.take_peak(), 4)
        else:
            st["name"] = "ファイル（--from-file）"
        return st

    def set_device(self, index: int | None) -> dict:
        """入力を差し替える。**生成中なら、いったん閉じて開き直す。**

        会議の最中に「音が来ていない」と気づくことがある。そのときに
        止めて・選んで・また開始する、という3手を踏ませたくない。
        """
        capture = self.app.capture
        if not isinstance(capture, audio_mod.Capture):
            raise ValueError("--from-file で起動しているので、入力は選べない。")
        if index is not None:
            known = {i for i, _n, _c, _a in audio_mod.list_devices()}
            if index not in known:
                raise ValueError(f"番号 {index} の入力デバイスが無い。一覧を取り直すこと。")

        capture.device = index
        self.app.audio_error = ""
        print(f"[{now()}] 音声  入力を {audio_mod.describe_device(index)} にした")
        if self.app.generating:
            # 生成は続けたまま、音声デバイスと認識だけを開き直す。
            self.app.request_restart()
        return self.status()


class GlossaryControl:
    """用語集の一覧と選び直し。

    **会議によって語彙が違う。** サブシステムごとの表を必要なぶんだけ重ねる。
    1つの大きな表を全部の会議で使うと、関係の無い語が認識の keywords を食い、
    上限で本当に要る語が落ちる。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "sets": glossary.available(),
            "selected": list(self.app.glossary_names),
            "terms": len(self.app.entries),
            "keywords": len(self.app.keywords),
            "limit": config.ASR_KEYWORD_LIMIT,
            "dropped": self.app.dropped_keywords,
        }

    def select(self, names: list[str]) -> dict:
        """選び直す。**生成中なら、認識を繋ぎ直して新しい keywords を届ける。**

        keywords はセッションの開始時にしか送れない。繋ぎ直さないと、
        翻訳だけが新しい表になり、認識は古い表のままになる。
        """
        known = {s["name"] for s in glossary.available()}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"用語集が無い: {', '.join(unknown)}")
        self.app.apply_glossary(names)
        return self.status()


class App:
    def __init__(self, settings: config.Settings, web=None) -> None:  # noqa: ANN001
        self.settings = settings
        self.web = web
        # **使う用語集は名前で決まる。** 起動時の指定が無ければ前回の選択。
        self.glossary_names: tuple[str, ...] = tuple(
            settings.glossary_names if settings.glossary_names is not None
            else glossary.selection()
        )
        entries = glossary.load(self.glossary_names)
        self.entries = entries
        # 上限で切り捨てられた語は認識の段に届かない。黙って落とさず、
        # 起動時の画面に数を出す（2026-09-07 に27語が落ちていたのに気づけなかった）。
        every = glossary.keywords(entries, limit=None)
        self.keywords = every[: config.ASR_KEYWORD_LIMIT]
        self.dropped_keywords = len(every) - len(self.keywords)
        self.asr = asr_mod.Asr(
            keywords=self.keywords,
            delay=settings.delay,
            languages=settings.languages,
        )
        self.segmenter = segmenter_mod.Segmenter()
        self.translator = translator_mod.Translator(entries, settings.translate_model)
        self.sender = captions_mod.CaptionSender(
            settings.caption_url or None, dry_run=settings.dry_run
        )
        self.zoom = ZoomControl(self.sender)
        self.engine = EngineControl(self)
        self.audio = AudioControl(self)
        self.glossary = GlossaryControl(self)
        # run() で受け取る。操作画面から入力を差し替えるために持っておく。
        self.capture = None
        # 音声デバイスを開けなかったときの理由。開けたら消す。
        self.audio_error = ""
        # 字幕の生成（録音・認識・翻訳）が回っているか。
        # **操作画面があるときは、停止した状態から始める。** 会議に入る前に
        # アプリを立ち上げておけるようにするため。無いときは押す手段が無いので
        # すぐ始める（run.py が決めて `start_now` で渡す）。
        self.generating = False
        # 開始と停止を _pipeline に伝える。片方が立っているときは他方を倒す。
        self._gen_on = asyncio.Event()
        self._gen_off = asyncio.Event()
        self._gen_off.set()
        # 入力デバイスを差し替えたときに立てる。生成は続けたまま、
        # 音声デバイスと認識だけを開き直す。
        self._restart = asyncio.Event()
        # 会議の記録。**日本語の認識文と英語の字幕を対にして残す。**
        # 対にするために、翻訳のタスクと元の文を一緒に持ち回る（_dispatch / _post）。
        self.transcript = (
            transcript_mod.Transcript(
                settings.transcript_dir,
                meta={
                    "asr": config.ASR_MODEL,
                    "delay": settings.delay,
                    "languages": ",".join(settings.languages),
                    "translate": settings.translate_model,
                    "glossary": len(entries),
                    "glossary_sets": ", ".join(self.glossary_names) or "(なし)",
                    "dry_run": settings.dry_run,
                },
            )
            if settings.save
            else None
        )
        if web is not None:
            # ブラウザの操作画面から Zoom字幕を開始・停止できるようにする。
            web.control = self.zoom
            # 字幕の生成そのものを開始・停止できるようにする。
            web.engine = self.engine
            # 入力デバイスの一覧と差し替え。
            web.audio = self.audio
            # 用語集の一覧と選び直し。
            web.glossary = self.glossary
            # 同じ画面から字幕アプリそのものを終わらせる。
            web.on_shutdown = self.request_stop
            # 記録が溜まっていることを操作画面に出す。
            web.transcript = self.transcript
        self.sentences: asyncio.Queue[segmenter_mod.Cut] = asyncio.Queue()
        self.inflight: asyncio.Queue = asyncio.Queue(maxsize=MAX_INFLIGHT)
        self.stats = {"sentences": 0, "lines": 0}
        # 先回りの翻訳。(投げた文字列, タスク) を1つだけ持つ。
        self._spec: tuple[str, asyncio.Task] | None = None
        # 外から終了を頼まれたら立てる。run() がこれを見て後片付けに入る。
        self.stop_requested = asyncio.Event()

    def request_stop(self, reason: str = "外部") -> None:
        """字幕アプリを終わらせる。**別のスレッドから呼ばれる。**

        ブラウザの操作画面（HTTPサーバのスレッド）から来るので、
        Event は `call_soon_threadsafe` で本体のイベントループに渡す。
        Ctrl+C と同じところへ落ちる。後片付けは run.py が行う。
        """
        print(f"[{now()}] 終了  {reason}から終了を要求された")
        loop = self.zoom.loop
        if loop is None:
            self.stop_requested.set()
        else:
            loop.call_soon_threadsafe(self.stop_requested.set)

    def set_generating(self, on: bool) -> None:
        """字幕の生成を開始・停止する。**別のスレッドから呼ばれる。**

        表示に使う `generating` はここで即座に立てる。操作画面は要求の返事で
        状態を描き直すので、イベントループを待つと1拍ずれて見える。
        実際の開始・停止（音声デバイスと認識の接続）は `_pipeline` が行う。
        """
        if on == self.generating:
            return
        self.generating = on
        loop = self.zoom.loop
        if loop is None:
            self._flip(on)
        else:
            loop.call_soon_threadsafe(self._flip, on)

    def apply_glossary(self, names: list[str] | tuple[str, ...]) -> None:
        """用語集を選び直して、認識と翻訳の両方に反映する。

        **別のスレッドから呼ばれる。**

        翻訳のプロンプトはその場で作り直せる。認識の `keywords` は
        セッションの開始時にしか送れないので、生成中なら繋ぎ直す。
        """
        names = tuple(names)
        entries = glossary.load(names)
        every = glossary.keywords(entries, limit=None)

        self.glossary_names = names
        self.entries = entries
        self.keywords = every[: config.ASR_KEYWORD_LIMIT]
        self.dropped_keywords = len(every) - len(self.keywords)
        self.asr.keywords = self.keywords
        # 翻訳のプロンプトを作り直す。訳の途中の文には影響しない。
        self.translator.system = translator_mod.build_system(entries)
        glossary.remember(names)

        label = ", ".join(names) or "(なし)"
        print(f"[{now()}] 用語  用語集を {label} にした"
              f"（{len(entries)} 語、認識に渡す語 {len(self.keywords)}）")
        if self.dropped_keywords:
            print(f"       **{self.dropped_keywords} 語が上限で切り捨てられた。"
                  "認識の段には届かない。**")
        if self.generating:
            # keywords はセッションの開始時にしか送れない。繋ぎ直す。
            self.request_restart()

    def request_restart(self) -> None:
        """音声デバイスと認識を開き直す。**別のスレッドから呼ばれる。**"""
        loop = self.zoom.loop
        if loop is None:
            self._restart.set()
        else:
            loop.call_soon_threadsafe(self._restart.set)

    def _flip(self, on: bool) -> None:
        if on:
            self._gen_off.clear()
            self._gen_on.set()
        else:
            self._gen_on.clear()
            self._gen_off.set()

    # --- 段ごとの仕事 -------------------------------------------------------

    async def _pipeline(self, capture) -> None:
        """開始されている間だけ、取り込みと認識を回す。

        **停止したら音声デバイスを閉じ、認識のWebSocketも切る。** 止めているのに
        音が取り込まれていたり、APIに送られていたりしてはいけない。

        止めるときは、切り出しの途中まで溜まっている文字を捨てる。出さずに捨てる。
        止めた後に字幕が1行出てくると、読み手には何が起きたのか分からない。
        """
        while True:
            await self._gen_on.wait()
            # **開けないデバイスを選ぶことがある。** 抜けているUSB機器、
            # 48 kHz を受け付けない装置。ここで落とすと、以後この輪は回らず、
            # 画面には何の理由も出ない。捕まえて、止まった状態へ戻す。
            try:
                capture.start()
            except Exception as exc:  # noqa: BLE001 - 会議中に落とさない
                # **開けないデバイスを選ぶことがある。** 抜けているUSB機器、
                # 48 kHz を受け付けない装置。ここで落とすと以後この輪は回らず、
                # 画面には何の理由も出ない。捕まえて、止まった状態へ戻す。
                self.audio_error = f"{type(exc).__name__}: {exc}"
                print(f"[{now()}] 音声  入力を開けない: {self.audio_error}")
                print("       操作画面の「音声の入力」で別のデバイスを選ぶこと。")
                self.generating = False
                self._flip(False)
                continue
            self.audio_error = ""
            self.asr.resume()
            asr = asyncio.create_task(self.asr.run(capture, self._on_delta))
            # Zoomへ送っている最中なら、ここが受信側にとっての「流れ始め」である。
            if self.sender.active:
                await self.sender.warmup()

            # 停止と、入力の差し替えの、どちらが先に来ても抜ける。
            # **イベントの立つ順に頼らない。** 捨て字幕の送信中（約1秒）に
            # 差し替えを頼まれても、取りこぼさないようにするためである。
            waits = [
                asyncio.create_task(self._gen_off.wait()),
                asyncio.create_task(self._restart.wait()),
            ]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # **終了のときもここを通る。** 認識のタスクは別に作ってあるので、
                # 畳まないと接続が残る。ここでは待たない。待つと、取り消しの
                # 途中でもう一度 CancelledError を受けることがある。
                for w in waits:
                    w.cancel()
                self.asr.stop()
                asr.cancel()
            await asyncio.gather(asr, *waits, return_exceptions=True)
            capture.stop()
            self.segmenter.reset()
            # 途中の文字を捨てたので、それを訳していた先回りも捨てる。
            self._drop_speculation()
            # 差し替えで抜けたときは _gen_on が立ったままなので、そのまま開き直す。
            self._restart.clear()

    def _on_delta(self, delta: str) -> None:
        for cut in self.segmenter.feed(delta):
            self.sentences.put_nowait(cut)

    # --- 先回りの翻訳 -------------------------------------------------------

    def _speculate(self) -> None:
        """無音が続いたら、確定を待たずに翻訳を投げておく。

        確定した時点で中身が変わっていなければ、その結果をそのまま使う。
        **`IDLE_FLUSH_SEC` の安全余裕はそのままで、翻訳の時間だけが消える。**
        無音待ちを速くする手は、これしか残っていない（閾値は下げられない）。
        """
        if not config.SPECULATE_AFTER_SEC:
            return
        if self.segmenter.silent_for() < config.SPECULATE_AFTER_SEC:
            return
        text = self.segmenter.buffer.strip()
        if not text or not segmenter_mod.has_content(text):
            return
        if self._spec is not None and self._spec[0] == text:
            return                      # 同じ中身で二度投げない
        self._drop_speculation()
        self.stats["spec_fired"] = self.stats.get("spec_fired", 0) + 1
        self._spec = (
            text,
            asyncio.create_task(self.translator.translate(text, remember=False)),
        )

    def _take_speculation(self, text: str):
        """先回りの翻訳が使えるなら、そのタスクを返す。使えなければ捨てて None。"""
        if self._spec is None:
            return None
        spec_text, task = self._spec
        # 末尾の文末記号だけの違いは許す。認識は「。」を遅れて足すことがあり、
        # 中身は同じなので訳し直す意味が無い。
        if _bare(spec_text) != _bare(text):
            self._drop_speculation()
            return None
        self._spec = None
        self.stats["spec_used"] = self.stats.get("spec_used", 0) + 1
        return task

    def _drop_speculation(self) -> None:
        """使わないと決まった先回りを捨てる。

        取り消しても、走っているHTTPの往復そのものは止まらない
        （`asyncio.to_thread` は途中で割り込めない）。結果を読まなくするだけである。
        """
        if self._spec is not None:
            self._spec[1].cancel()
            self._spec = None

    async def _watch_idle(self) -> None:
        """発話が途切れたら、文末記号が無くても確定させる。"""
        while True:
            await asyncio.sleep(config.IDLE_POLL_SEC)
            self._speculate()
            for cut in self.segmenter.flush_if_idle():
                self.sentences.put_nowait(cut)

    async def _dispatch(self) -> None:
        """文を受け取って翻訳を始める。順序を保つため、タスクを順番に積む。"""
        while True:
            cut = await self.sentences.get()
            self.stats["sentences"] += 1
            self.stats[f"cut_{cut.reason}"] = self.stats.get(f"cut_{cut.reason}", 0) + 1
            print(f"[{now()}] 認識  {cut.text}")
            if self.web is not None:
                self.web.asr(cut.text)
            # 先回りが当たっていれば、それを使う。翻訳の時間がまるごと消える。
            task = self._take_speculation(cut.text)
            used_spec = task is not None
            if task is None:
                task = asyncio.create_task(self.translator.translate(cut.text))
            # 元の文と、認識が確定した時刻を一緒に持ち回る。記録で日本語と英語を
            # 対にするため、そして記録の時刻を「訳せた時刻」にしないためである。
            # 満杯なら待つ。これが翻訳の同時実行数の上限になる。
            await self.inflight.put((cut, time.time(), task, used_spec))

    async def _post(self) -> None:
        """翻訳の完了を順番に待って、字幕として送る。"""
        while True:
            cut, heard_at, task, used_spec = await self.inflight.get()
            lines, took = await task
            # 確定から最初の字幕までの実測。翻訳そのものの時間と分けて出す。
            # 差が待ち行列と諸経費である。**先回りが当たった文では total が
            # took より小さくなる。** 翻訳が確定より前に終わっているからである。
            total = time.time() - heard_at
            if used_spec and lines:
                # 先回りは文脈に足していない。採用が決まったここで足す。
                self.translator.remember(cut.text)
            # **閲覧画面には先に全部渡す。** 下の 0.6秒 の間隔は Zoom の表示制約への
            # 対処で（字幕の窓は最小4行しかなく、まとめて送ると先頭が押し出される）、
            # 8行ある閲覧画面には要らない。ここを一緒にしていたので、閲覧画面が
            # Zoom の都合で遅れていた。
            if self.web is not None:
                for line in lines:
                    self.web.caption(line)
            # 実測は最初の行にだけ出す。2行目からは同じ幅の空白で桁を揃える
            # （「総」「訳」が全角なので、2文字ぶん余分に要る）。
            head = f"(総 {total:.1f}s / 訳 {took:.1f}s{' 先' if used_spec else ''})"
            cont = " " * (len(head) + 2 + (1 if used_spec else 0))
            sent = 0
            for i, line in enumerate(lines):
                if i:
                    # まとめて送ると読み手が追えない。窓は最小4行しかない。
                    await asyncio.sleep(config.LINE_INTERVAL_SEC)
                # Zoomへ送ったかどうかに関わらず、画面には必ず出す。
                # 送っていないことは、行頭の印で分かるようにする。
                mark = head if not i else cont
                if await self.sender.send(line):
                    self.stats["lines"] += 1
                    sent += 1
                    print(f"[{now()}] 字幕  {mark} {line}")
                else:
                    print(f"[{now()}] 字幕  {mark} {line}   (Zoomへは送っていない)")
            # 翻訳に失敗して lines が空でも記録する。日本語だけでも残す価値がある。
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, waited=cut.waited, took=took, total=total,
                    spec=used_spec,
                )

    # --- 起動 ---------------------------------------------------------------

    async def run(self, capture, start_now: bool = True) -> None:
        """`start_now=False` なら、停止した状態で待つ（操作画面から開始する）。"""
        # 操作画面から入力を差し替えられるように、持っておく。
        self.capture = capture
        print("=" * 70)
        print("LiveCaption")
        print("=" * 70)
        print(f"用語対訳表: {', '.join(self.glossary_names) or '**選ばれていない**'}"
              f"  {len(self.entries)} 語"
              f"（認識に渡す語 {len(self.keywords)}、上限 {config.ASR_KEYWORD_LIMIT}）")
        if self.dropped_keywords:
            print(f"  **{self.dropped_keywords} 語が上限で切り捨てられた。"
                  "認識の段には届かない。**")
            print("  config.ASR_KEYWORD_LIMIT を上げること。")
        print(f"音声認識:   {config.ASR_MODEL}  delay={self.settings.delay}"
              f"  languages={list(self.settings.languages)}")
        print(f"翻訳:       {self.settings.translate_model}")
        print(f"音声の入力: {self.audio.status()['name']}")
        if self.settings.dry_run:
            print("**--dry-run: Zoomには送らない。**")
        elif self.sender.base_url:
            print(f"字幕の送信先: {self.sender.base_url[:55]}...")
            print(f"seq は {self.sender.seq} から始める")
        else:
            print("Zoomへの送信: **停止中**（トークンが無い）")
            if self.web is not None:
                print("  ブラウザの操作画面（右上の歯車）からトークンを入れて開始できる。")
        if self.web is not None:
            print(f"操作画面:   {self.web.control_url()}  （麻生だけ。共有しないこと）")
            print(f"閲覧画面:   {self.web.viewer_url()}"
                  f"  （{self.web.lines}行。全画面にして画面共有する）")
            state = self.web.tunnel.status()["state"] if self.web.tunnel else "off"
            if state == "on":
                print(f"配信URL:    {self.web.public_url()}  （参加者に配る）")
            elif state == "starting":
                # URLは数秒後に出てくる。出たら run.py が端末に書く。
                print("配信URL:    起動中（URLが出たらここに表示する）")
            else:
                print("配信URL:    **停止中**"
                      "（操作画面の「トンネルを開始」で、参加者に配るURLが出る）")
        if self.transcript is not None:
            # ここでファイルを作る。会議が始まる前に、書ける場所かどうかが分かる。
            self.transcript.open()
            print(f"記録:       {self.transcript.path}")
            print("  日本語の認識文と英語の字幕を対にして、確定するたびに書く。")
        else:
            print("記録:       **残さない**（--no-save）")
        if start_now:
            print("字幕の生成: すぐ始める")
        else:
            print("字幕の生成: **停止中**"
                  "（操作画面の「開始」を押すまで、録音も認識も翻訳もしない）")
        if self.web is not None:
            print("Ctrl+C か、ブラウザの操作画面の「終了」で終わる。")
        else:
            print("Ctrl+C で終了する。")
        print("-" * 70)

        # ブラウザから開始したときに捨て字幕を送れるよう、ループを渡しておく。
        self.zoom.loop = asyncio.get_running_loop()
        if start_now:
            self.set_generating(True)

        tasks = [
            asyncio.create_task(self._pipeline(capture)),
            asyncio.create_task(self._watch_idle()),
            asyncio.create_task(self._dispatch()),
            asyncio.create_task(self._post()),
            # ブラウザから終了を頼まれたら、これが完了して下の wait を抜ける。
            asyncio.create_task(self.stop_requested.wait()),
        ]
        # --from-file のとき、ファイルを流し終えたら少し待って終わる。
        finished = getattr(capture, "finished", None)
        if finished is not None:
            tasks.append(asyncio.create_task(self._wait_end(finished)))

        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            self.asr.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_end(self, finished: asyncio.Event) -> None:
        await finished.wait()
        # 最後の文が認識され、訳され、送られるまでの余裕
        await asyncio.sleep(12)
        for cut in self.segmenter.flush():
            print(f"[{now()}] 認識  {cut.text}")
            heard_at = time.time()
            if self.web is not None:
                self.web.asr(cut.text)
            lines, took = await self.translator.translate(cut.text)
            sent = 0
            for line in lines:
                if await self.sender.send(line):
                    self.stats["lines"] += 1
                    sent += 1
                print(f"[{now()}] 字幕  {line}")
                if self.web is not None:
                    self.web.caption(line)
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, took=took, total=time.time() - heard_at,
                )

    def report(self, capture=None) -> None:
        """終了時の要約。Ctrl+C のあとでも呼べるように同期で書く。"""
        print("-" * 70)
        print(f"確定した文: {self.stats['sentences']}")
        # 確定の理由ごとの内訳。**無音待ちの割合が、遅延の調整の出発点である。**
        breakdown = [
            (label, self.stats.get(f"cut_{key}", 0))
            for key, label in (
                ("punct", "文末記号"), ("force", "強制分割"),
                ("idle", "無音待ち"), ("flush", "終了時"),
            )
        ]
        total_cuts = sum(n for _, n in breakdown) or 1
        print("  " + "  ".join(
            f"{label} {n}（{100 * n / total_cuts:.1f}%）" for label, n in breakdown if n
        ))
        fired = self.stats.get("spec_fired", 0)
        if fired:
            used = self.stats.get("spec_used", 0)
            # **投げ捨てた分がそのまま余分な料金である。** 割に合うかはここで見る。
            print(f"先回りの翻訳: {fired} 回投げて {used} 回当たった"
                  f"（捨てた {fired - used}）")
        print(f"Zoomへ送った行: {self.sender.sent}（失敗 {self.sender.failed}）")
        print(f"認識の再接続: {self.asr.reconnects} 回")
        if capture is not None:
            print(f"音声の取りこぼし: {getattr(capture, 'dropped', 0)} 回")
        if self.web is not None:
            print(f"ブラウザ字幕を見ていた画面: {self.web.viewers} 個")
        if self.segmenter.buffer.strip():
            print(f"未送信の文字列: {self.segmenter.buffer.strip()[:80]}")
        if self.transcript is not None:
            # **`.md` はここで初めて書かれる。** `.jsonl` は途中まで残っている。
            md = self.transcript.close()
            if md is not None:
                print(f"記録: {self.transcript.path}")
                print(f"      {md}")
            elif self.transcript.error:
                print(f"記録: 残せなかった（{self.transcript.error}）")
            else:
                print("記録: 確定した文が無かったので、何も残していない。")
