"""VB-CABLE の配線を確認する。Zoomも会議もAPIも要らない。

CABLE Input にテスト音を流し込みながら、本体と同じやり方で CABLE Output を開く。
音が返ってくれば、仮想ケーブルは通っている。
"""
import asyncio
import sys
import threading
import time

import numpy as np
import sounddevice as sd

sys.path.insert(0, "src")
from live_caption import audio as audio_mod  # noqa: E402
from live_caption import config  # noqa: E402

RATE = 48000


def find_device(name_part, hostapi_name, want_output):
    for i, d in enumerate(sd.query_devices()):
        ch = d["max_output_channels"] if want_output else d["max_input_channels"]
        if ch > 0 and name_part.lower() in d["name"].lower():
            if sd.query_hostapis(d["hostapi"])["name"] == hostapi_name:
                return i
    raise RuntimeError(f"見つからない: {name_part} / {hostapi_name}")


class Tone:
    """別スレッドで CABLE Input にサイン波を出し続ける。"""

    def __init__(self, device):
        self.device = device
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    def _run(self):
        t = np.arange(int(RATE * 0.5)) / RATE
        block = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
        try:
            with sd.OutputStream(
                device=self.device, samplerate=RATE, channels=1, dtype="float32"
            ) as s:
                while not self._stop.is_set():
                    s.write(block)
        except Exception as exc:  # noqa: BLE001
            self.error = exc

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.7)
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)


async def measure(capture, seconds=4.0):
    """`seconds` 秒ぶん取り込んで、区間ごとの最大振幅を返す。

    **本体と同じ `chunks()` で取り出す。** 待ち行列を直接触らない。
    音が1つも来なくても `wait_for` が時間で打ち切るので、必ず戻る。
    """
    capture.start()
    peaks = []

    async def collect():
        async for chunk in capture.chunks():
            s = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
            peaks.append(float(np.abs(s).max()) if s.size else 0.0)

    try:
        await asyncio.wait_for(collect(), seconds)
    except asyncio.TimeoutError:
        pass  # 時間で打ち切るのが正常な終わり方
    finally:
        capture.stop()
    return peaks


def main():
    out_dev = find_device("CABLE Input", "Windows WASAPI", want_output=True)
    print(f"再生先: {out_dev} {sd.query_devices(out_dev)['name']} [WASAPI]")

    default_idx = audio_mod.find_device("CABLE Output")
    wasapi_idx = find_device("CABLE Output", "Windows WASAPI", want_output=False)
    targets = [(default_idx, "本体が既定で選ぶもの")]
    if wasapi_idx != default_idx:
        targets.append((wasapi_idx, "WASAPI 版"))

    for idx, label in targets:
        dev = sd.query_devices(idx)
        api = sd.query_hostapis(dev["hostapi"])["name"]
        print()
        print(f"--- {label}: {idx} {dev['name']!r} [{api}] ---")
        try:
            cap = audio_mod.Capture(device=idx, capture_rate=config.CAPTURE_RATE)
            with Tone(out_dev) as tone:
                peaks = asyncio.run(measure(cap))
                if tone.error:
                    print(f"  再生側で失敗: {tone.error}")
        except Exception as exc:  # noqa: BLE001
            print(f"  開けない: {type(exc).__name__}: {str(exc).splitlines()[0]}")
            continue

        if not peaks:
            print("  音声が1区間も来なかった")
            continue
        loud = sum(1 for p in peaks if p > 0.01)
        print(
            f"  区間 {len(peaks)}、音あり {loud}（{loud / len(peaks):.0%}）"
            f"  最大 peak {max(peaks):.3f}  取りこぼし {cap.dropped}"
        )
        print("  => 通っている" if loud / len(peaks) > 0.5 else "  => 届いていない")


if __name__ == "__main__":
    main()
