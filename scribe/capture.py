"""
capture.py — Windows system-audio + microphone capture.

Records two independent streams and writes them as a stereo WAV:
    LEFT  channel = your microphone (you)
    RIGHT channel = WASAPI loopback of your speakers (everyone else)

Because you use speakers (not headphones) this still works if the mic bleeds
a little of the far end back in — the loopback channel is authoritative for
the far end, and the mic channel is authoritative for you. Keeping them
separate is what gives us free speaker attribution later without a
diarization model.

Requires: pip install pyaudiowpatch numpy
"""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

TARGET_RATE = 16000  # what Whisper wants; we resample down to this
CHUNK = 1024


@dataclass
class CaptureResult:
    path: Path
    duration_s: float
    started_at: float  # unix epoch


def _find_loopback(p: pyaudio.PyAudio) -> dict:
    """Find the loopback device matching the current default output device."""
    try:
        wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as e:
        raise RuntimeError("WASAPI not available — this module is Windows-only.") from e

    default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])

    # Loopback devices are exposed as separate INPUT devices whose name
    # contains the output device's name.
    for dev in p.get_loopback_device_info_generator():
        if default_out["name"] in dev["name"]:
            return dev

    raise RuntimeError(
        f"No loopback device found for output {default_out['name']!r}. "
        "Check that audio is actually playing through it."
    )


def _resample_mono(data: np.ndarray, src_rate: int, src_channels: int) -> np.ndarray:
    """Downmix to mono and linearly resample to TARGET_RATE. Good enough for STT."""
    if src_channels > 1:
        data = data.reshape(-1, src_channels).mean(axis=1)
    if src_rate == TARGET_RATE:
        return data
    n_out = int(len(data) * TARGET_RATE / src_rate)
    if n_out == 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0, 1, len(data), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    return np.interp(x_new, x_old, data).astype(np.float32)


class _StreamRecorder(threading.Thread):
    """Pulls from one device into a list of float32 mono numpy arrays."""

    def __init__(self, p: pyaudio.PyAudio, device: dict, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.p = p
        self.device = device
        self.stop_event = stop_event
        self.frames: list[np.ndarray] = []
        self.error: Exception | None = None

    def run(self) -> None:
        rate = int(self.device["defaultSampleRate"])
        channels = int(self.device["maxInputChannels"])
        try:
            stream = self.p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=self.device["index"],
                frames_per_buffer=CHUNK,
            )
            while not self.stop_event.is_set():
                raw = stream.read(CHUNK, exception_on_overflow=False)
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                self.frames.append(_resample_mono(arr, rate, channels))
            stream.stop_stream()
            stream.close()
        except Exception as e:  # surfaced by the caller after join()
            self.error = e

    def collect(self) -> np.ndarray:
        if self.error:
            raise self.error
        if not self.frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.frames)


def record(out_path: Path, stop_event: threading.Event | None = None) -> CaptureResult:
    """
    Record until stop_event is set (or Ctrl+C). Writes a 16kHz stereo WAV.

    Usage:
        stop = threading.Event()
        # ... set stop from a hotkey / signal handler / UI button
        record(Path("meeting.wav"), stop)
    """
    stop_event = stop_event or threading.Event()
    p = pyaudio.PyAudio()
    started_at = time.time()

    try:
        loopback_dev = _find_loopback(p)
        mic_dev = p.get_default_input_device_info()

        print(f"  speakers -> {loopback_dev['name']}")
        print(f"  mic      -> {mic_dev['name']}")

        mic_rec = _StreamRecorder(p, mic_dev, stop_event)
        spk_rec = _StreamRecorder(p, loopback_dev, stop_event)
        mic_rec.start()
        spk_rec.start()

        try:
            while not stop_event.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            stop_event.set()

        mic_rec.join()
        spk_rec.join()

        mic = mic_rec.collect()
        spk = spk_rec.collect()
    finally:
        p.terminate()

    # Pad the shorter stream so the two channels stay time-aligned.
    n = max(len(mic), len(spk))
    mic = np.pad(mic, (0, n - len(mic)))
    spk = np.pad(spk, (0, n - len(spk)))

    stereo = np.stack([mic, spk], axis=1)
    stereo = np.clip(stereo, -1.0, 1.0)
    pcm = (stereo * 32767).astype(np.int16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm.tobytes())

    duration = n / TARGET_RATE
    return CaptureResult(path=out_path, duration_s=duration, started_at=started_at)
