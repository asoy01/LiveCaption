"""音声の取り込み。

字幕専用PCでは、Zoomの出力を VB-CABLE の `CABLE Input` に向けてある。
こちらは `CABLE Output` を普通の録音デバイスとして開く。
ループバックAPIは使わないので、音量やミュートの影響を受けない。

装置は 48 kHz で開き、こちらで 24 kHz に落とす（gpt-live-transcribe の要求）。
48000 → 24000 はちょうど 2:1 なので、2サンプルの平均を取る。単純だが、
そのまま間引くよりは折り返しが小さい。
"""

from __future__ import annotations

import asyncio

import numpy as np
import sounddevice as sd

from . import config


def list_devices() -> list[tuple[int, str, int, str]]:
    """(番号, 名前, 入力チャンネル数, ホストAPI) の一覧。入力を持つものだけ。

    **ホストAPIまで返すのは、同じ名前が何度も出てくるためである。** Windowsでは
    `CABLE Output` が MME・DirectSound・WASAPI の3つに現れる。名前だけでは
    選び分けられない（local/HANDOFF.md「入力は MME のままでよい」）。
    """
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else "?"
            out.append((i, d["name"], d["max_input_channels"], api))
    return out


def describe_device(index: int | None) -> str:
    """番号から「名前（ホストAPI）」を作る。画面に出すためのもの。"""
    if index is None:
        return "既定の入力"
    for i, name, _, api in list_devices():
        if i == index:
            return f"{name}（{api}）"
    return f"番号 {index}（見つからない）"


def find_device(name: str | None) -> int | None:
    """名前の一部から入力デバイスを探す。見つからなければ None（既定を使う）。"""
    if not name:
        return None
    lowered = name.lower()
    for i, dev_name, _, _api in list_devices():
        if lowered in dev_name.lower():
            return i
    raise RuntimeError(
        f"入力デバイスが見つからない: {name}\n"
        "  --list-devices で一覧を出して、名前の一部を指定すること。"
    )


class Capture:
    """入力デバイスから 24 kHz・モノラル・16 bit の PCM を取り出す。"""

    def __init__(self, device: int | None = None, capture_rate: int = config.CAPTURE_RATE) -> None:
        self.device = device
        self.capture_rate = capture_rate
        self.decim = capture_rate // config.ASR_RATE
        if capture_rate % config.ASR_RATE != 0:
            raise ValueError(
                f"取り込みレート {capture_rate} が {config.ASR_RATE} の整数倍ではない。"
                " Windowsのサウンド設定で CABLE Output を 48000 Hz にすること。"
            )
        # 取り込みは装置のレートで、送る単位は 100 ms ぶん。
        self.blocksize = capture_rate * config.CHUNK_MS // 1000
        # **待ち行列は asyncio のものである。スレッドプールは使わない。**
        # `queue.Queue` を `run_in_executor` で待つ形にしていたが、この待ちは
        # 取り消せない。生成を止めるたびにスレッドが1本 `get()` の中に残り、
        # (1) 次に音が流れたとき、残った本数ぶんのチャンクを持ち去って捨てる
        # (2) 生成を止めた状態で終了すると、アプリが終われなくなる
        # （非デーモンのスレッドをインタプリタが最後に join するため）
        # 入れるのは装置のスレッド、取り出すのはイベントループ、という受け渡しは
        # `call_soon_threadsafe` で行う。待ち手がループの中にいるので取り消しが効く。
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        # 装置のコールバックから、どのループへ渡すか。start() で覚える。
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None
        self.dropped = 0
        # 直近の最大振幅。操作画面のメーターに使う。読んだ側が0に戻す。
        # **音が来ているかを目で見るためのものである。** デバイスを選び違えても、
        # 誰かが喋るまで気づけないのでは遅い。
        self._peak = 0.0

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            # オーバーフローは記録だけして続ける。会議中に落とさない。
            self.dropped += 1
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        # 2サンプルの平均で 24 kHz に落とす
        n = (len(mono) // self.decim) * self.decim
        reduced = mono[:n].reshape(-1, self.decim).mean(axis=1)
        pcm = np.clip(reduced * 32767.0, -32768, 32767).astype(np.int16)
        if reduced.size:
            self._peak = max(self._peak, float(np.abs(reduced).max()))
        loop = self._loop
        if loop is None:
            # start() を通っていない。捨てるしかない。
            self.dropped += 1
            return
        try:
            loop.call_soon_threadsafe(self._push, pcm.tobytes())
        except RuntimeError:
            # ループが閉じた後にコールバックが来ることがある。落とさない。
            self.dropped += 1

    def _push(self, chunk: bytes) -> None:
        """**イベントループのスレッドで動く。** 待ち行列に入れるのはここだけ。

        溜まりすぎたら捨てる。10秒ぶん（100個）で頭打ちにしてある。
        遅れた音を後から流しても、字幕は会話と噛み合わない。
        """
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            self.dropped += 1

    def start(self) -> None:
        # **開くのはイベントループの中からである。** 装置のコールバックが
        # `call_soon_threadsafe` で渡す先を、ここで覚える。
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "Capture.start() はイベントループの中から呼ぶこと"
                "（asyncio.run(...) の内側）。"
            ) from exc
        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=self.capture_rate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        # 閉じた後に来たコールバックが、古いループへ渡さないようにする。
        self._loop = None

    def take_peak(self) -> float:
        """前に読んでからの最大振幅を返し、0に戻す。**HTTPのスレッドから呼ぶ。**"""
        peak, self._peak = self._peak, 0.0
        return peak

    def drain(self) -> int:
        """溜まっている音声を捨てる。認識に繋ぎ直したときに呼ぶ。

        溜め込んで一気に流すと、遅れた字幕が会話と噛み合わなくなる。
        """
        n = 0
        while True:
            try:
                self._queue.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                return n

    async def chunks(self):
        """100 ms ぶんの PCM を順に返す。

        **待つのはイベントループの中だけである。** 取り消しがそのまま効くので、
        生成を止めても、デバイスを差し替えても、待ち手は残らない。
        """
        while True:
            yield await self._queue.get()


async def check_level(capture, seconds: float = 20.0) -> bool:
    """音量を表示するだけのモード。音声経路の確認に使う。

    APIを一切呼ばないので、鍵も会議も要らない。**実機の試験はここから始めること。**
    ここで振れなければ、認識も字幕も動かない。切り分けの起点になる。
    """
    capture.start()
    print(f"{seconds:.0f} 秒間、入力の音量を表示する。Zoomで音が鳴っている状態で見ること。")
    print("無音のまま動かないなら、Zoomのスピーカー設定と CABLE の配線を疑う。")
    print()

    loud = 0
    total = 0
    t0 = asyncio.get_running_loop().time()
    try:
        async for chunk in capture.chunks():
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            peak = float(np.abs(samples).max()) if samples.size else 0.0
            rms = float(np.sqrt((samples ** 2).mean())) if samples.size else 0.0
            total += 1
            if peak > 0.01:
                loud += 1
            bar = "#" * min(int(rms * 200), 50)
            print(f"\r  peak {peak:5.3f}  rms {rms:5.3f}  |{bar:<50}|", end="", flush=True)
            if asyncio.get_running_loop().time() - t0 > seconds:
                break
    finally:
        capture.stop()

    print()
    print()
    ratio = loud / total if total else 0.0
    print(f"音のあった割合: {ratio:.0%}（{loud} / {total} 区間）")
    if ratio < 0.02:
        print("**ほぼ無音。経路が切れている。**")
        print("  - Zoomのスピーカーが `CABLE Input` になっているか")
        print("  - このアプリの入力が `CABLE Output` になっているか（--device）")
        print("  - Windowsのサウンド設定で両方 48000 Hz か")
        return False
    print("音は届いている。次の段階に進んでよい。")
    return True


class FileCapture:
    """WAVファイルを実時間で流す。会議を開かずに全体を試すために使う。

    ファイルは 24 kHz・16 bit・モノラルであること。ffmpeg で作る:
        ffmpeg -i in.m4a -ac 1 -ar 24000 -c:a pcm_s16le out.wav
    """

    def __init__(self, path, loop_forever: bool = False) -> None:
        import wave

        with wave.open(str(path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (config.ASR_RATE, 1, 2):
                raise ValueError(
                    f"{path} は 24000 Hz・モノラル・16 bit ではない。ffmpeg で変換すること。"
                )
            self.data = w.readframes(w.getnframes())
        self.bytes_per_chunk = config.ASR_RATE * 2 * config.CHUNK_MS // 1000
        self.loop_forever = loop_forever
        self.dropped = 0
        self.finished = asyncio.Event()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def take_peak(self) -> float:
        return 0.0

    def drain(self) -> int:
        return 0

    async def chunks(self):
        import time as _time

        while True:
            t0 = _time.perf_counter()
            total = len(self.data) // self.bytes_per_chunk
            for i in range(total):
                yield self.data[i * self.bytes_per_chunk:(i + 1) * self.bytes_per_chunk]
                target = t0 + (i + 1) * config.CHUNK_MS / 1000
                await asyncio.sleep(max(0.0, target - _time.perf_counter()))
            if not self.loop_forever:
                self.finished.set()
                # 最後の文が確定するまで無音を流し続ける
                silence = b"\x00" * self.bytes_per_chunk
                while True:
                    yield silence
                    await asyncio.sleep(config.CHUNK_MS / 1000)
