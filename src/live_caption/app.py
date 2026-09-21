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
import os
import time
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import captions as captions_mod
from . import config, glossary
from . import schedule as schedule_mod
from . import segmenter as segmenter_mod
from . import transcript as transcript_mod
from . import translator as translator_mod
from . import vnc as vnc_mod

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
        print(f"[{now()}] Zoom        トークンを受け取った（会議 {meeting}、"
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
        print(f"[{now()}] Zoom        送信を{'開始' if on else '停止'}した")

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
        print(f"[{now()}] 生成        字幕の生成を{'開始' if on else '停止'}した")
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
        print(f"[{now()}] 音声        入力を {audio_mod.describe_device(index)} にした")
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

    def read(self, name: str) -> tuple[str, str]:
        """表の中身を返す。`(名前, 本文)`。ダウンロードに使う。"""
        return glossary.check_name(name), glossary.read_text(name)

    def upload(self, name: str, text: str) -> dict:
        """表を置く。同じ名前があれば置き換える。

        **使っている表を置き換えたら、その場で入れ直す。** 置き換えたのに
        古いままで会議が進む、というのがいちばん困る。
        """
        stem = glossary.save_text(name, text)
        if stem in self.app.glossary_names:
            self.app.apply_glossary(list(self.app.glossary_names))
        return self.status()

    def remove(self, name: str) -> dict:
        """表を消す。使っていた表なら、選択から外して入れ直す。"""
        stem = glossary.delete_file(name)
        if stem in self.app.glossary_names:
            self.app.apply_glossary(
                [n for n in self.app.glossary_names if n != stem])
        return self.status()


class TuningControl:
    """遅延の調整つまみを、操作画面から変えられるようにする。

    **よく変えるものではない。** 既定値は実測で決めてある。操作画面では畳んでおく。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "items": config.tuning(),
            "warning": config.tuning_warning(),
            "env_path": str(config.ENV_PATH),
        }

    def set(self, values: dict) -> dict:
        """値を変える。**すぐ効く。再起動は要らない。**

        `ValueError` は操作した人に見せる。
        """
        # **全部を検算してから入れる。** 途中で弾かれたときに、半分だけ変わった
        # 状態にしない。入れるのは `config.set_tuning` で、値だけでなく
        # 「人が明示して変えたか」も覚える（向きの切り替えから守るため）。
        checked = {name: config.coerce_tuning(name, raw) for name, raw in values.items()}
        for name, value in checked.items():
            config.set_tuning(name, value)
        # **`Segmenter` は作られたときに値を写している。** そこへ届けないと、
        # 画面の数字だけが変わって、切り方は前のままになる。
        self.app.segmenter.idle_sec = config.IDLE_FLUSH_SEC
        self.app.segmenter.force_cut = config.FORCE_CUT_CHARS
        if checked:
            print(f"[{now()}] 設定        " + "、".join(
                f"{n} を {v} にした" for n, v in checked.items()))
        return self.status()

    def save(self) -> dict:
        """いまの値を `.env` に書く。次に起動したときも同じ値で始まる。"""
        written = {item["env"]: str(item["value"]) for item in config.tuning()}
        path = config.save_env(written)
        print(f"[{now()}] 設定        {path} に保存した")
        st = self.status()
        st["saved"] = str(path)
        return st


class DirectionControl:
    """字幕の向きの切り替え。

    **会議ごとに選ぶ。** 日本語の会議には英語字幕、英語の会議には日本語字幕。
    用語集と違って、切り替えても認識は繋ぎ直さない（`keywords` が向きで変わらない）。

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "current": config.DIRECTION,
            "items": [
                {"name": d.name, "label": d.label, "lang": d.caption_lang}
                for d in config.DIRECTIONS.values()
            ],
        }

    def select(self, name: str) -> dict:
        """選び直す。**その場で効く。再起動も再接続も要らない。**"""
        self.app.apply_direction(str(name))
        return self.status()


class RecordControl:
    """会議の記録。**字幕PCに溜めて、操作画面から落とす**（2026-09-20）。

    置き場は `local/transcripts/`。**画面からは選び直せない。** 字幕PCは常時
    起動で、操作は tailnet 越しである。記録を取りに行くのに RustDesk を起こす
    のが面倒だ、というのが元の問題なので、**落とせるようにすれば置き場を
    動かす理由が無くなる。** 変えたい人は `.env` の `LIVECAPTION_SAVE_DIR`。

    （2026-09-20 の午前には、置き場を画面から選ぶ作りにしていた。フォルダを
    選ぶ窓と、遠隔用のフォルダ一覧の2通り。落とせるようになったので両方消した。）

    呼ぶのはHTTPサーバのスレッドである。
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "dir": str(self.app.settings.transcript_dir),
            "saving": self.app.settings.save,
        }

    def latest_path(self) -> Path | None:
        """落とせる記録の `.jsonl`。**中身のあるもののうち、いちばん新しい1本。**

        **空のものは飛ばす。** 会議が終わると、本体は次の記録を開いて待つ
        （`roll_transcript`）。いちばん新しいファイルをそのまま返すと、
        会議のあとに落としたとき、1文も入っていない記録が出てくる。

        画面からファイルを指す文字列は受け取らない。**どれを返すかはここで
        決める。** 操作画面には認証が無いので、そこへパスを渡す口を作らない。
        """
        directory = Path(self.app.settings.transcript_dir)
        for item in transcript_mod.scan(directory):
            if item["lines"]:
                return directory / f"{item['stem']}.jsonl"
        return None

    def latest(self) -> dict:
        """落とせる記録が何かを画面に知らせる。無ければ `item` は None。"""
        path = self.latest_path()
        item = None
        if path is not None:
            for found in transcript_mod.scan(Path(self.app.settings.transcript_dir)):
                if found["stem"] == path.stem:
                    item = found
                    break
        return {"saving": self.app.settings.save, "item": item}


class App:
    def __init__(self, settings: config.Settings, web=None) -> None:  # noqa: ANN001
        self.settings = settings
        self.web = web
        # **向きは他の何よりも先に決める。** `Segmenter` は作られたときに
        # `FORCE_CUT_CHARS` を写し、`Translator` は向きでプロンプトを選ぶ。
        # 後から差し替えると、その2つだけが古い向きのままになる。
        self.direction = config.apply_direction(
            settings.direction or config.direction_selection()
        )
        # 操作画面の言語。**字幕の向きとは無関係である。** 英語の会議に日本語字幕を
        # 出しながら、操作画面を英語にすることもある。
        config.apply_ui_lang(config.ui_lang_selection())
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
        self.tuning = TuningControl(self)
        self.dir_control = DirectionControl(self)
        # 予定された会議を無人で回す見張り（schedule.py）。
        self.scheduler = schedule_mod.Scheduler(self)
        # VNC（vnc.py）。**コンテナで動かすときだけ使える。**
        # Windows では DISPLAY が無いので、操作画面には「使えない」と出る。
        self.vnc = vnc_mod.Vnc()
        # run() で受け取る。操作画面から入力を差し替えるために持っておく。
        self.capture = None
        # 音声デバイスを開けなかったときの理由。開けたら消す。
        self.audio_error = ""
        # 最後に1文が確定した時刻（`time.monotonic`）。無音の見張りが使う。
        # **音量ではなく文で見る。** 理由は `_dispatch` に書いた。
        self.last_sentence_at = time.monotonic()
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
        # 書きかけの文字起こしを、最後に閲覧画面へ渡した時刻（`_show_partial`）。
        self._partial_at = 0.0
        # 会議の記録。**日本語の認識文と英語の字幕を対にして残す。**
        # 対にするために、翻訳のタスクと元の文を一緒に持ち回る（_dispatch / _post）。
        self.transcript = (
            transcript_mod.Transcript(settings.transcript_dir, meta=self._transcript_meta())
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
            # 遅延の調整つまみ。よく変えないので、画面では畳んである。
            web.tuning = self.tuning
            # 字幕の向き。会議ごとに選ぶ。
            web.direction = self.dir_control
            # 同じ画面から字幕アプリそのものを終わらせる。
            web.on_shutdown = self.request_stop
            # 記録が溜まっていることを操作画面に出す。
            web.transcript = self.transcript
            # 記録の置き場。フォルダ選択の窓もここから開く。
            web.records = RecordControl(self)
            # 予定の状態と、止める・飛ばす・失敗を消す、の操作。
            web.scheduler = self.scheduler
            # VNC。**会議中でも起動・停止できる**ようにしてある。
            web.vnc = self.vnc
            # **既定では上げない。** 認証が無いので、要るときだけ画面から開ける。
            if os.environ.get(config.VNC_ENV, "").strip().lower() in (
                    "1", "true", "yes", "on"):
                st = self.vnc.start()
                if st["on"]:
                    print(f"VNC:        起動した（ポート {st['web_port']}）。"
                          "**認証は無い。**")
                else:
                    print(f"VNC:        起動できない（{st['error']}）")
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
        print(f"[{now()}] 終了        {reason}から終了を要求された")
        loop = self.zoom.loop
        if loop is None:
            self.stop_requested.set()
        else:
            loop.call_soon_threadsafe(self.stop_requested.set)

    def _transcript_meta(self) -> dict:
        """記録の先頭に書く情報。

        **呼ぶたびに今の状態から作る。** 会議ごとに記録を切り替えるので、
        起動時の値を使い回すと、向きや用語集を途中で変えた後の記録が嘘になる。
        """
        return {
            "asr": config.ASR_MODEL,
            "delay": self.settings.delay,
            "languages": ",".join(self.settings.languages),
            "translate": self.settings.translate_model,
            # 始めたときの向き。**途中で変えられるので、1文ごとにも残す。**
            "direction": self.direction.name,
            "glossary": len(self.entries),
            "glossary_sets": ", ".join(self.glossary_names) or "(なし)",
            "dry_run": self.settings.dry_run,
        }

    def roll_transcript(self, label: str = "") -> Path | None:
        """いまの記録を閉じて `.md` を書き、次の記録を開く。閉じたパスを返す。

        **プロセスの終了を待たない。** 常駐させると `report()` が呼ばれるのは
        何週間も先になる。それまで `.md` は1本も書かれず、会議何十本ぶんが
        1つの `.jsonl` に溜まる。会議が終わるたびにここで区切る。

        **`web.transcript` も差し替えること。** 差し替えないと、いま書いている
        記録がどれかを操作画面が取り違える。

        1文も無い記録は `close()` が消すので、会議の前後で2回呼んでも
        空ファイルは残らない。

        呼ぶのは本体のイベントループだけである（スケジューラと、停止から
        少し置いて呼ぶ `_close_record_soon`）。
        """
        if self.transcript is None:
            return None
        done = self.transcript.close()
        if done is not None:
            print(f"[{now()}] 記録        書いた: {done}")
        self.transcript = transcript_mod.Transcript(
            self.settings.transcript_dir, meta=self._transcript_meta(), label=label)
        self.transcript.open()
        if self.web is not None:
            self.web.transcript = self.transcript
        return done

    def set_generating(self, on: bool) -> None:
        """字幕の生成を開始・停止する。**別のスレッドから呼ばれる。**

        表示に使う `generating` はここで即座に立てる。操作画面は要求の返事で
        状態を描き直すので、イベントループを待つと1拍ずれて見える。
        実際の開始・停止（音声デバイスと認識の接続）は `_pipeline` が行う。
        """
        if on == self.generating:
            return
        if on:
            # **前の失敗を引きずらない。** `audio_error` は、開けたときと
            # デバイスを差し替えたときにしか消えない。残っていると、画面には
            # ずっと古い失敗が出たままになり、**始まったかどうかを状態から
            # 判定できない**（無人で回すときは、これが唯一の手がかりになる）。
            self.audio_error = ""
            # 無音の見張りの起点。止まっているあいだの時間を数えない。
            self.last_sentence_at = time.monotonic()
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
        print(f"[{now()}] 用語        用語集を {label} にした"
              f"（{len(entries)} 語、文字起こしに渡す語 {len(self.keywords)}）")
        if self.dropped_keywords:
            print(f"       **{self.dropped_keywords} 語が上限で切り捨てられた。"
                  "文字起こしの段には届かない。**")
        if self.generating:
            # keywords はセッションの開始時にしか送れない。繋ぎ直す。
            self.request_restart()

    def apply_direction(self, name: str) -> None:
        """字幕の向きを切り替える。**別のスレッドから呼ばれる。**

        **認識は繋ぎ直さない。** `keywords` は用語表の日本語と英語の両方を常に
        渡しているので、向きを変えても同じ語が同じ並びで行く。用語集の選び直しとは
        ここが違う。

        `config.apply_direction()` が定数を差し替え、ここが、その定数を写して
        持っている3つ（翻訳のプロンプト・`Segmenter`・`CaptionSender`）に届ける。
        """
        d = config.apply_direction(name)
        self.direction = d
        # **走っている先回りの翻訳を捨てる。** 古いプロンプトで訳しているので、
        # 採用すると切り替えた直後の1文だけ逆向きの字幕が出る。
        #
        # **タスクの取り消しは、走らせている輪の中からでないといけない。**
        # ここはHTTPサーバのスレッドなので、輪へ渡す（`set_generating` と同じ形）。
        loop = self.zoom.loop
        if loop is None:
            self._drop_speculation()
        else:
            loop.call_soon_threadsafe(self._drop_speculation)
        self.translator.system = translator_mod.build_system(self.entries)
        # 直前の文脈も捨てる。切り替えの前後で字幕の言語が変わるため。
        self.translator.history.clear()
        # **`Segmenter` は作られたときに値を写している。** 届けないと切り方が前のまま。
        self.segmenter.force_cut = config.FORCE_CUT_CHARS
        self.sender.lang = d.caption_lang
        config.remember_direction(d.name)
        print(f"[{now()}] 向き        字幕を {d.label} にした"
              f"（1行 {config.MAX_CAPTION_CHARS} 文字、強制分割 {config.FORCE_CUT_CHARS} 文字、"
              f"Zoomの lang={d.caption_lang}）")

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
                print(f"[{now()}] 音声        入力を開けない: {self.audio_error}")
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
            # 溜まっていた文字を捨てたので、画面の書きかけの行も消す。
            self._show_partial(force=True)
            # 途中の文字を捨てたので、それを訳していた先回りも捨てる。
            self._drop_speculation()
            # 差し替えで抜けたときは _gen_on が立ったままなので、そのまま開き直す。
            self._restart.clear()
            # **止めて抜けたなら、記録を区切る。** 停止は「この会議は終わり」の
            # 意味なので（配信もZoom字幕も閉じる）、記録もそこで閉じて `.md` を
            # 書く。ここでは待たない。待つと、すぐ再開したときに開き直しが遅れる。
            if not self.generating:
                asyncio.create_task(self._close_record_soon())

    async def _close_record_soon(self) -> None:
        """停止から少し置いて、記録を区切る。

        **すぐには閉じない。** 止めた時点で、最後の1文がまだ翻訳の途中のことが
        ある。即座に区切ると、その1文だけが次の記録に落ちて、**中身1行の記録が
        「最新」になる。**

        **この間に再開したら区切らない。** 休憩で止めただけなら、会議の記録は
        1本のままにする。
        """
        await asyncio.sleep(config.STOP_ROLL_WAIT_SEC)
        if self.generating:
            return
        self.roll_transcript()

    def _on_delta(self, delta: str) -> None:
        cuts = self.segmenter.feed(delta)
        for cut in cuts:
            self.sentences.put_nowait(cut)
        # 確定した直後は間引かない。書きかけが画面に残ったままになる。
        self._show_partial(force=bool(cuts))

    def _show_partial(self, force: bool = False) -> None:
        """書きかけの文字起こしを閲覧画面へ渡す。

        **文が確定するまでの数秒、画面には何も出ない。** 話している人の言葉は
        `Segmenter` に溜まっているだけで、読む側からは止まって見える。届いた分を
        そのまま流しておくと、声とほぼ同時（実測 0.24秒）に文字が出る。

        **翻訳とZoom字幕には流さない。** どちらも出した行を置き換えられないので、
        書きかけを送ると訂正版と二重に残る。置き換えられるのはブラウザだけである。

        delta は中央値 0.01秒 の間隔で来るので、そのまま毎回渡すと長ポーリングが
        回りっぱなしになる。`PARTIAL_INTERVAL_SEC` ごとに間引く。
        **確定と停止のときは `force` で必ず渡す。** 間引きのせいで、書きかけが
        画面に残ったままになるのを防ぐ。
        """
        if self.web is None or not config.WEB_PARTIAL:
            return
        nowt = time.monotonic()
        if not force and nowt - self._partial_at < config.PARTIAL_INTERVAL_SEC:
            return
        self._partial_at = nowt
        self.web.partial(self.segmenter.buffer)

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
                # 無音で確定したときも、書きかけの行を消す。
                self._show_partial(force=True)

    async def _dispatch(self) -> None:
        """文を受け取って翻訳を始める。順序を保つため、タスクを順番に積む。"""
        while True:
            cut = await self.sentences.get()
            # **無音の見張りの基準はここである。** 音量ではなく「文になったか」で見る。
            # Zoomのミックス音声には暗騒音が常に乗るので、振幅で黙っているかを
            # 決めると、誰も居ない部屋に繋がったまま止まらない。
            self.last_sentence_at = time.monotonic()
            self.stats["sentences"] += 1
            self.stats[f"cut_{cut.reason}"] = self.stats.get(f"cut_{cut.reason}", 0) + 1
            print(f"[{now()}] 文字起こし  {cut.text}")
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
                    print(f"[{now()}] 字幕        {mark} {line}")
                else:
                    print(f"[{now()}] 字幕        {mark} {line}   (Zoomへは送っていない)")
            # 翻訳に失敗して lines が空でも記録する。日本語だけでも残す価値がある。
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, waited=cut.waited, took=took, total=total,
                    spec=used_spec, direction=config.DIRECTION,
                )

    # --- 起動 ---------------------------------------------------------------

    async def run(self, capture, start_now: bool = True) -> None:
        """`start_now=False` なら、停止した状態で待つ（操作画面から開始する）。"""
        # 操作画面から入力を差し替えられるように、持っておく。
        self.capture = capture
        print("=" * 70)
        print("LiveCaption")
        print("=" * 70)
        print(f"字幕の向き: {self.direction.label}"
              f"（1行 {config.MAX_CAPTION_CHARS} 文字、強制分割 {config.FORCE_CUT_CHARS} 文字）")
        print(f"用語対訳表: {', '.join(self.glossary_names) or '**選ばれていない**'}"
              f"  {len(self.entries)} 語"
              f"（文字起こしに渡す語 {len(self.keywords)}、上限 {config.ASR_KEYWORD_LIMIT}）")
        if self.dropped_keywords:
            print(f"  **{self.dropped_keywords} 語が上限で切り捨てられた。"
                  "文字起こしの段には届かない。**")
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
            print(f"操作画面:   {self.web.control_url()}  （自分だけ。共有しないこと）")
            # **出ているなら、必ず見せる。** 出ているつもりが無いのに出ている、
            # というのがいちばん困る。認証は無いので、届く範囲が全てである。
            for extra in self.web.control_urls_extra():
                print(f"            {extra}  "
                      "（tailnet の中から。**ACLで絞ること**）")
            print(f"閲覧画面:   {self.web.viewer_url()}"
                  f"  （{self.web.lines}行。外には出ない）")
            state = self.web.tunnel.status()["state"] if self.web.tunnel else "off"
            if state == "on":
                print(f"配信URL:    {self.web.public_url()}  （参加者に配る）")
            elif state == "starting":
                # URLは数秒後に出てくる。出たら run.py が端末に書く。
                print("配信URL:    起動中（URLが出たらここに表示する）")
            else:
                print("配信URL:    **停止中**"
                      "（操作画面の「配信を開始」で、参加者に配るURLが出る）")
        if self.transcript is not None:
            # ここでファイルを作る。会議が始まる前に、書ける場所かどうかが分かる。
            self.transcript.open()
            print(f"記録:       {self.transcript.path}")
            print("  文字起こしと字幕を対にして、確定するたびに書く。")
        else:
            print("記録:       **残さない**（--no-save）")
        if start_now:
            print("字幕の生成: すぐ始める")
        else:
            print("字幕の生成: **停止中**"
                  "（操作画面の「開始」を押すまで、録音も文字起こしも翻訳もしない）")
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
            # 予定された会議を回す見張り。**予定が1つも無くても回しておく。**
            # 動きの分かれ目を減らすためで、費用は寝ているコルーチン1本ぶんである。
            # **この見張りは決して返らない。** 返ると下の wait を抜けてアプリが畳まれる。
            asyncio.create_task(self.scheduler.run_forever()),
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
            # 覗き口を開けたままにしない。認証が無いので、閉じて終わる。
            self.vnc.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_end(self, finished: asyncio.Event) -> None:
        await finished.wait()
        # 最後の文が認識され、訳され、送られるまでの余裕
        await asyncio.sleep(12)
        for cut in self.segmenter.flush():
            print(f"[{now()}] 文字起こし  {cut.text}")
            heard_at = time.time()
            if self.web is not None:
                self.web.asr(cut.text)
            lines, took = await self.translator.translate(cut.text)
            sent = 0
            for line in lines:
                if await self.sender.send(line):
                    self.stats["lines"] += 1
                    sent += 1
                print(f"[{now()}] 字幕        {line}")
                if self.web is not None:
                    self.web.caption(line)
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, took=took, total=time.time() - heard_at,
                    direction=config.DIRECTION,
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
        print(f"文字起こしの再接続: {self.asr.reconnects} 回")
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
