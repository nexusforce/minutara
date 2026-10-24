"""Fake Windows audio stack + Whisper so Minutara's real code runs on Linux.

Import this BEFORE importing minutara. It copies the repo's package into a
temp app folder (so config.json, notes/, recordings/ land there) and puts
that on sys.path.
"""
import shutil
import sys
import tempfile
import threading
import time
import types
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]


def chromium() -> dict:
    """Launch options for Playwright: a preinstalled Chromium if there is one."""
    import os
    path = os.environ.get("CHROMIUM_PATH") or "/opt/pw-browsers/chromium"
    return {"executable_path": path} if Path(path).exists() else {}


def make_app_root(prefix="minutara-test-"):
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    shutil.copytree(REPO / "minutara", tmp / "minutara")
    sys.path.insert(0, str(tmp))
    return tmp


# --- faster-whisper ----------------------------------------------------------
class FakeSegment:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeWhisperModel:
    calls = []
    fail = False
    delay = 0.0
    scripts = None  # optional list of segment lists, consumed per call

    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio, **k):
        if FakeWhisperModel.delay:
            time.sleep(FakeWhisperModel.delay)
        if FakeWhisperModel.fail:
            raise RuntimeError("model exploded")
        FakeWhisperModel.calls.append(audio)
        if FakeWhisperModel.scripts:
            return [FakeSegment(*s) for s in FakeWhisperModel.scripts.pop(0)], None
        return [FakeSegment(0.0, 1.0, "hello world")], None


fw = types.ModuleType("faster_whisper")
fw.WhisperModel = FakeWhisperModel
sys.modules["faster_whisper"] = fw


# --- soundfile (reads WAVs via the stdlib; anything else is "undecodable") ----
sf = types.ModuleType("soundfile")


def sf_read(path, dtype="float32", always_2d=True):
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return (data.astype(np.float32) / 32768.0).reshape(-1, ch), rate


sf.read = sf_read
sys.modules["soundfile"] = sf


# --- pyaudiowpatch: callback-mode fake devices ---------------------------------
# hostApi 0 = MME (shortened names, as Windows really does), 1 = WASAPI.
DEVICES = [
    {"index": 0, "name": "Microphone Array (Realtek(R) Au", "maxInputChannels": 2,
     "isLoopbackDevice": False, "defaultSampleRate": 44100, "hostApi": 0},
    {"index": 1, "name": "Microphone Array (Realtek(R) Audio)", "maxInputChannels": 2,
     "isLoopbackDevice": False, "defaultSampleRate": 48000, "hostApi": 1},
    {"index": 2, "name": "Blue Yeti USB Microphone", "maxInputChannels": 2,
     "isLoopbackDevice": False, "defaultSampleRate": 48000, "hostApi": 1},
    {"index": 3, "name": "Speakers (Realtek(R) Audio) [Loopback]", "maxInputChannels": 2,
     "isLoopbackDevice": True, "defaultSampleRate": 48000, "hostApi": 1},
    {"index": 4, "name": "Speakers (Realtek(R) Audio)", "maxInputChannels": 0,
     "isLoopbackDevice": False, "defaultSampleRate": 48000, "hostApi": 1},
    {"index": 5, "name": "Headset Mic (44k mono)", "maxInputChannels": 1,
     "isLoopbackDevice": False, "defaultSampleRate": 44100, "hostApi": 1},
    {"index": 6, "name": "Headphones (WH-1000XM5) [Loopback]", "maxInputChannels": 2,
     "isLoopbackDevice": True, "defaultSampleRate": 44100, "hostApi": 1},
    {"index": 7, "name": "Headphones (WH-1000XM5)", "maxInputChannels": 0,
     "isLoopbackDevice": False, "defaultSampleRate": 44100, "hostApi": 1},
]
MIC, YETI, LOOP, HEADSET, BT_LOOP = 1, 2, 3, 5, 6


class FakeAudio:
    # Mic = positive tone, speakers = negative, so channel mapping is checkable.
    tones = {0: 3000, 1: 3000, 2: 2000, 3: -3000, 5: 1500, 6: -2500}
    fail_device_after = {}   # device index -> callbacks before the device goes quiet
    speed = {}               # device index -> x real time (runaway driver)
    silent = set()           # devices that send nothing (loopback with nothing playing)
    noise = set()            # devices that send full-scale static
    open_fails = set()
    opened = []


class FakeStream:
    def __init__(self, idx, rate, channels, cb):
        self.idx, self.rate, self.channels, self.cb = idx, rate, channels, cb
        self.active = False
        self.calls = 0

    def start_stream(self):
        self.active = True
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        n = self.rate // 100   # 10 ms buffers, like WASAPI
        rng = np.random.default_rng(self.idx)
        while self.active:
            time.sleep(n / self.rate / FakeAudio.speed.get(self.idx, 1))
            if not self.active:
                break
            self.calls += 1
            limit = FakeAudio.fail_device_after.get(self.idx)
            if self.idx in FakeAudio.silent or (limit is not None and self.calls > limit):
                continue
            if self.idx in FakeAudio.noise:
                data = rng.integers(-32000, 32000, n * self.channels, dtype=np.int16)
            else:
                data = np.full(n * self.channels, FakeAudio.tones.get(self.idx, 0), dtype=np.int16)
            self.cb(data.tobytes(), n, {}, 0)

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.active = False


class FakePyAudio:
    def get_device_count(self):
        return len(DEVICES)

    def get_device_info_by_index(self, i):
        return DEVICES[i]

    def get_default_input_device_info(self):
        return DEVICES[0]   # MME's default, as on Windows

    def get_host_api_info_by_type(self, t):
        return {"index": 1, "name": "Windows WASAPI", "defaultOutputDevice": 4,
                "defaultInputDevice": 1}

    def get_host_api_info_by_index(self, i):
        return {"name": ["MME", "Windows WASAPI"][i]}

    def get_loopback_device_info_generator(self):
        return iter([DEVICES[3], DEVICES[6]])

    def open(self, format, channels, rate, input, input_device_index, frames_per_buffer=0,
             stream_callback=None):
        if input_device_index in FakeAudio.open_fails:
            raise OSError(f"[Errno -9996] Invalid device {input_device_index}")
        assert stream_callback is not None, "capture should use callback mode"
        FakeAudio.opened.append(input_device_index)
        return FakeStream(input_device_index, rate, channels, stream_callback)

    def terminate(self):
        pass


pa = types.ModuleType("pyaudiowpatch")
pa.PyAudio = FakePyAudio
pa.paWASAPI = 13
pa.paInt16 = 8
pa.paContinue = 0
pa.paFramesPerBufferUnspecified = 0
sys.modules["pyaudiowpatch"] = pa


def write_wav(path, rate=16000, ch=2, n=16000):
    pcm = (np.random.default_rng(0).uniform(-0.1, 0.1, n * ch) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
