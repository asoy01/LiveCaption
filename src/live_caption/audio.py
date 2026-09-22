"""Audio capture.

On the caption PC, the Zoom output is sent to `CABLE Input` of VB-CABLE. Here
we open `CABLE Output` as an ordinary recording device. We do not use a
loopback API, so volume and mute settings have no effect on us.

The device is opened at 48 kHz and we down-sample to 24 kHz here (required by
gpt-live-transcribe). 48000 to 24000 is exactly 2:1, so we take the mean of
every two samples. This is simple, but it gives less aliasing than dropping
every other sample.
"""

from __future__ import annotations

import asyncio
import time

import numpy as np
import sounddevice as sd

from . import config


def list_devices() -> list[tuple[int, str, int, str]]:
    """A list of (index, name, input channel count, host API), for devices that
    have an input.

    **The host API is included because the same name shows up several times.**
    On Windows, `CABLE Output` appears under MME, DirectSound and WASAPI. The
    name alone cannot tell them apart. (Measured on real meeting audio: MME is
    fine for the input.)
    """
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            api = apis[d["hostapi"]]["name"] if d["hostapi"] < len(apis) else "?"
            out.append((i, d["name"], d["max_input_channels"], api))
    return out


def describe_device(index: int | None) -> str:
    """Build "name (host API)" from an index. For showing on screen."""
    if index is None:
        return "既定の入力"
    for i, name, _, api in list_devices():
        if i == index:
            return f"{name}（{api}）"
    return f"番号 {index}（見つからない）"


def find_device(name: str | None) -> int | None:
    """Find an input device from part of its name. None if not found (the
    default device is then used).
    """
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
    """Take 24 kHz, mono, 16-bit PCM out of an input device."""

    def __init__(self, device: int | None = None, capture_rate: int = config.CAPTURE_RATE) -> None:
        self.device = device
        self.capture_rate = capture_rate
        self.decim = capture_rate // config.ASR_RATE
        if capture_rate % config.ASR_RATE != 0:
            raise ValueError(
                f"取り込みレート {capture_rate} が {config.ASR_RATE} の整数倍ではない。"
                " Windowsのサウンド設定で CABLE Output を 48000 Hz にすること。"
            )
        # Capture runs at the device rate; we send in units of 100 ms.
        self.blocksize = capture_rate * config.CHUNK_MS // 1000
        # **The queue is an asyncio one. Do not use a thread pool.**
        # It used to be a `queue.Queue` waited on through `run_in_executor`,
        # but that wait cannot be cancelled. Every time caption generation was
        # stopped, one thread was left inside `get()`, and
        # (1) the next time audio flowed, those leftover threads took one chunk
        #     each and threw it away
        # (2) quitting while generation was stopped left the application unable
        #     to exit (the interpreter joins non-daemon threads at the end)
        # The device thread puts, the event loop takes, and the hand-off goes
        # through `call_soon_threadsafe`. The waiter is inside the loop, so
        # cancellation works.
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        # Which loop the device callback hands to. Remembered in start().
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream: sd.InputStream | None = None
        self.dropped = 0
        # The peak amplitude since the last read. Used by the meter on the
        # control page. The reader resets it to 0.
        # **This exists so you can see with your eyes that audio is coming
        # in.** If you pick the wrong device, noticing only when somebody
        # speaks is too late.
        self._peak = 0.0
        # The time when sound that looks like sound last arrived
        # (`time.monotonic`). **Reading it does not clear it.**
        # It is the hint for "the meeting seems to be over" in unattended runs.
        self._voice_at = time.monotonic()

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            # Record an overflow and carry on. Never crash during a meeting.
            self.dropped += 1
        mono = indata.mean(axis=1) if indata.ndim > 1 else indata
        # Down-sample to 24 kHz by taking the mean of every two samples
        n = (len(mono) // self.decim) * self.decim
        reduced = mono[:n].reshape(-1, self.decim).mean(axis=1)
        pcm = np.clip(reduced * 32767.0, -32768, 32767).astype(np.int16)
        if reduced.size:
            level = float(np.abs(reduced).max())
            self._peak = max(self._peak, level)
            # **This one is not consumed.** `_peak` is reset to 0 every time
            # the meter on the control page reads it, so anything else that
            # looks at it would fight over the value. The silence watcher gets
            # its own field.
            if level > config.VOICE_PEAK:
                self._voice_at = time.monotonic()
        loop = self._loop
        if loop is None:
            # start() was never called. There is nothing to do but drop it.
            self.dropped += 1
            return
        try:
            loop.call_soon_threadsafe(self._push, pcm.tobytes())
        except RuntimeError:
            # A callback can arrive after the loop is closed. Do not crash.
            self.dropped += 1

    def _push(self, chunk: bytes) -> None:
        """**Runs on the event loop thread.** This is the only place that puts
        into the queue.

        Drop what piles up too far. The cap is 10 seconds' worth (100 items).
        Playing late audio afterwards only produces captions that no longer
        match the conversation.
        """
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            self.dropped += 1

    def start(self) -> None:
        # **Open the device from inside the event loop.** This is where we
        # remember the loop that the device callback hands to through
        # `call_soon_threadsafe`.
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
        # Keep a callback that arrives after the close from handing to the old
        # loop.
        self._loop = None

    def take_peak(self) -> float:
        """Return the peak amplitude since the last read, and reset it to 0.

        **Called from the HTTP thread.**
        """
        peak, self._peak = self._peak, 0.0
        return peak

    def quiet_for(self) -> float:
        """How many seconds since sound last arrived.

        **Unlike `take_peak()`, reading does not clear the value.** The silence
        watcher calls this over and over, so it has its own field and does not
        steal the value from the meter on the control page.
        """
        return max(0.0, time.monotonic() - self._voice_at)

    def drain(self) -> int:
        """Throw away the audio that has piled up. Called after reconnecting to
        speech recognition.

        Holding audio back and then sending it all at once makes the captions
        late, and they no longer match the conversation.
        """
        n = 0
        while True:
            try:
                self._queue.get_nowait()
                n += 1
            except asyncio.QueueEmpty:
                return n

    async def chunks(self):
        """Yield 100 ms of PCM at a time.

        **The wait happens inside the event loop only.** Cancellation works
        directly, so no waiter is left behind when generation is stopped or the
        device is swapped.
        """
        while True:
            yield await self._queue.get()


async def check_level(capture, seconds: float = 20.0) -> bool:
    """A mode that only shows the level. Used to check the audio path.

    It calls no API at all, so it needs neither a key nor a meeting. **Start
    testing on real hardware here.** If the meter does not move here, neither
    speech recognition nor captions will work. This is the starting point when
    narrowing down a problem.
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
            if peak > config.VOICE_PEAK:
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
    """Play a WAV file in real time. Used to test the whole system without
    opening a meeting.

    The file must be 24 kHz, 16-bit, mono. Make one with ffmpeg:
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

    def quiet_for(self) -> float:
        """File playback counts as "sound is always present".

        **If this does not return 0, the silence watcher closes the meeting at
        once.** Tests run with `--from-file`, so anything other than 0.0 here
        makes the test itself impossible.
        """
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
                # Keep feeding silence until the last sentence is settled
                silence = b"\x00" * self.bytes_per_chunk
                while True:
                    yield silence
                    await asyncio.sleep(config.CHUNK_MS / 1000)
