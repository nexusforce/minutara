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

The recording follows the clock, not the devices. Audio devices don't all
deliver at the rate they claim: Windows sends nothing from the speakers
while nothing is playing, and a misbehaving driver can deliver audio far
faster than real time. So each channel is written against the clock:
gaps are filled with silence and anything beyond real time is dropped.
A 10-second recording is always about 10 seconds long.

Requires: pip install pyaudiowpatch numpy
"""

from __future__ import annotations

import base64
import io
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

from .log import get

log = get("scribe.capture")

TARGET_RATE = 16000   # what Whisper wants; we resample down to this
AHEAD_S = 0.3         # how far a stream may run ahead of the clock (buffering)
LAG_S = 0.6           # how far behind the clock before silence is filled in


@dataclass
class CaptureResult:
    path: Path
    duration_s: float
    started_at: float  # unix epoch


# --- devices -----------------------------------------------------------------

def _wasapi(p: pyaudio.PyAudio) -> dict:
    try:
        return p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError as e:
        raise RuntimeError("WASAPI not available — this module is Windows-only.") from e


def _host_name(p: pyaudio.PyAudio, dev: dict) -> str:
    try:
        return str(p.get_host_api_info_by_index(dev["hostApi"])["name"])
    except Exception:
        return "?"


def speaker_label(dev: dict) -> str:
    """'Speakers (Realtek) [Loopback]' -> 'Speakers (Realtek)'."""
    return str(dev["name"]).replace(" [Loopback]", "").strip()


def _find_loopback(p: pyaudio.PyAudio, name: str = "") -> dict:
    """
    The capture side of an output device: the one named in Settings → Speakers,
    else the one matching Windows' current default output.
    """
    # Loopback devices are exposed as separate INPUT devices whose name
    # contains the output device's name.
    loops = list(p.get_loopback_device_info_generator())
    if name:
        want = name.lower().strip()
        for d in loops:
            if speaker_label(d).lower() == want:
                return d
        for d in loops:
            if want in str(d["name"]).lower():
                return d
        log.warning("Configured speakers %r not found; using Windows' default output.", name)

    wasapi = _wasapi(p)
    default_out = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    for dev in loops:
        if default_out["name"] in dev["name"]:
            return dev
    if loops:
        return loops[0]
    raise RuntimeError(
        f"No loopback device found for output {default_out['name']!r}. "
        "Check that audio is actually playing through it."
    )


def _wasapi_mics(p: pyaudio.PyAudio) -> list[dict]:
    """
    Microphones as WASAPI sees them. Windows lists every mic several times,
    once per audio system (MME, DirectSound, WASAPI, WDM-KS); WASAPI is the
    one with full names, and the one the speaker capture uses, so both
    channels run on the same clockwork.
    """
    idx = _wasapi(p)["index"]
    out = []
    for i in range(p.get_device_count()):
        d = p.get_device_info_by_index(i)
        if (d.get("hostApi") == idx and int(d.get("maxInputChannels", 0)) > 0
                and not d.get("isLoopbackDevice")):
            out.append(d)
    return out


def _pick_mic(p: pyaudio.PyAudio, name: str) -> dict:
    """The configured mic if it can be found by name, else the system default."""
    mics = _wasapi_mics(p)
    if name:
        want = name.lower().strip()
        for match in (lambda n: n == want,
                      lambda n: n.startswith(want) or want in n,  # older, shortened names
                      lambda n: n in want):
            for d in mics:
                if match(str(d["name"]).lower()):
                    return d
        log.warning("Configured mic %r not found; using the system default.", name)
    default = _wasapi(p).get("defaultInputDevice", -1)
    if default is not None and default >= 0:
        return p.get_device_info_by_index(default)
    if mics:
        return mics[0]
    return p.get_default_input_device_info()


def list_input_devices() -> dict:
    """Device names for the settings UI. Windows-only, like the rest of capture."""
    p = pyaudio.PyAudio()
    try:
        mics, default, loopback = [], "", ""
        try:
            default = str(_pick_mic(p, "")["name"])
        except Exception:
            pass
        for d in _wasapi_mics(p):
            name = str(d["name"])
            if name not in mics:
                mics.append(name)
        speakers = []
        try:
            for d in p.get_loopback_device_info_generator():
                if speaker_label(d) not in speakers:
                    speakers.append(speaker_label(d))
            loopback = speaker_label(_find_loopback(p))
        except Exception:
            pass
        return {"mics": mics, "default": default, "loopback": loopback,
                "speakers": speakers, "speaker_default": loopback}
    finally:
        p.terminate()


# --- the clock ---------------------------------------------------------------

class _Clock:
    """Seconds of recording so far: wall time minus pauses, frozen at stop."""

    def __init__(self, pause_event: threading.Event | None):
        self.pause_event = pause_event
        self.t0 = time.monotonic()
        self.paused_total = 0.0
        self.paused_since: float | None = None
        self.frozen: float | None = None
        self.lock = threading.Lock()

    def active(self) -> float:
        with self.lock:
            if self.frozen is not None:
                return self.frozen
            now = time.monotonic()
            paused = self.pause_event is not None and self.pause_event.is_set()
            if paused and self.paused_since is None:
                self.paused_since = now
            elif not paused and self.paused_since is not None:
                self.paused_total += now - self.paused_since
                self.paused_since = None
            end = self.paused_since if self.paused_since is not None else now
            return max(0.0, end - self.t0 - self.paused_total)

    @property
    def paused(self) -> bool:
        return self.paused_since is not None

    def freeze(self) -> float:
        value = self.active()
        with self.lock:
            self.frozen = value
        return value


# --- one stream --------------------------------------------------------------

class _Converter:
    """Device audio (int16, any rate, any channel count) -> 16 kHz mono float."""

    def __init__(self, rate: int, channels: int):
        self.rate = rate
        self.channels = max(1, channels)
        self.step = rate // TARGET_RATE if rate % TARGET_RATE == 0 else 0
        self.carry = np.zeros(0, dtype=np.float32)

    def __call__(self, raw: bytes) -> np.ndarray:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        usable = len(arr) - len(arr) % self.channels
        mono = arr[:usable].reshape(-1, self.channels).mean(axis=1)
        if self.rate == TARGET_RATE:
            return mono
        if self.step:
            # 48 kHz, 32 kHz…: average each group of samples. Clean, and
            # carries the leftover samples into the next chunk.
            mono = np.concatenate([self.carry, mono])
            n = len(mono) - len(mono) % self.step
            self.carry = mono[n:]
            return mono[:n].reshape(-1, self.step).mean(axis=1)
        n_out = int(len(mono) * TARGET_RATE / self.rate)
        if n_out == 0:
            return np.zeros(0, dtype=np.float32)
        x_old = np.linspace(0, 1, len(mono), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        return np.interp(x_new, x_old, mono).astype(np.float32)


@dataclass
class StreamStats:
    label: str
    device: str
    host_api: str
    rate: int
    channels: int
    sent_s: float = 0.0      # audio the device delivered, in seconds
    kept_s: float = 0.0      # audio written to the file
    filled_s: float = 0.0    # silence written where the device sent nothing
    dropped_s: float = 0.0   # audio thrown away for running ahead of the clock
    peak: float = 0.0
    sum_sq: float = 0.0
    n: int = 0
    crossings: int = 0       # sign changes in the loud parts: high for static, low for speech
    loud_n: int = 0
    flags: int = 0           # buffer overflow/underflow reports from the driver
    error: str = ""
    elapsed_s: float = 0.0

    @property
    def zcr(self) -> float:
        return self.crossings / self.loud_n if self.loud_n else 0.0

    @property
    def rms(self) -> float:
        return float(np.sqrt(self.sum_sq / self.n)) if self.n else 0.0

    def summary(self) -> str:
        return (f"{self.label} {self.device!r} ({self.host_api}, {self.rate} Hz, "
                f"{self.channels} ch): device sent {self.sent_s:.1f}s in {self.elapsed_s:.1f}s; "
                f"kept {self.kept_s:.1f}s, silence filled {self.filled_s:.1f}s, "
                f"dropped {self.dropped_s:.1f}s; level peak {_db(self.peak)} rms {_db(self.rms)} "
                f"zcr {self.zcr:.2f}"
                + (f"; {self.flags} driver warnings" if self.flags else "")
                + (f"; error: {self.error}" if self.error else ""))


def _db(x: float) -> str:
    return "-inf dB" if x <= 1e-6 else f"{20 * np.log10(x):.0f} dB"


class _Stream(threading.Thread):
    """
    One device, written against the clock to `out` as 16 kHz mono int16.

    The device runs in callback mode: PortAudio hands audio to _cb on its own
    thread, and this thread writes it out. Streams are opened one at a time
    by the caller (PortAudio isn't safe to open from two threads at once).
    """

    def __init__(self, p: pyaudio.PyAudio, device: dict, label: str, clock: _Clock, out):
        super().__init__(daemon=True)
        self.clock = clock
        self.out = out
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        rate = int(device["defaultSampleRate"])
        channels = max(1, int(device["maxInputChannels"]))
        self.convert = _Converter(rate, channels)
        self.stats = StreamStats(label, str(device["name"]), _host_name(p, device),
                                 rate, channels)
        self.written = 0
        self.stream = p.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=max(rate // 100, 64),   # 10 ms, WASAPI's own period
            stream_callback=self._cb,
        )

    def _cb(self, in_data, frame_count, time_info, status):
        if status:
            self.stats.flags += 1
        try:
            self.q.put_nowait(in_data)
        except queue.Full:  # a runaway device; the clock would drop it anyway
            self.stats.dropped_s += frame_count / self.convert.rate
        return (None, pyaudio.paContinue)

    def _write(self, samples: np.ndarray) -> None:
        self.out.write((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
        self.written += len(samples)

    def begin(self) -> None:
        """Start the device (on the caller's thread, one at a time), then the writer."""
        self.stream.start_stream()
        self.start()

    def run(self) -> None:
        try:
            while True:
                try:
                    raw = self.q.get(timeout=0.1)
                except queue.Empty:
                    raw = None
                done = self.clock.frozen is not None
                active = self.clock.active()
                if raw is not None:
                    frames = len(raw) // (2 * self.convert.channels)
                    self.stats.sent_s += frames / self.convert.rate
                    if not self.clock.paused:
                        self._take(self.convert(raw), active)
                # Behind the clock (e.g. nothing playing on the speakers):
                # fill with silence so the two channels stay in step.
                target = int((active - (0 if done else LAG_S)) * TARGET_RATE)
                if target > self.written:
                    gap = target - self.written
                    self._write(np.zeros(gap, dtype=np.float32))
                    self.stats.filled_s += gap / TARGET_RATE
                if done:
                    break
        except Exception as e:  # surfaced by the caller after join()
            self.stats.error = f"{e.__class__.__name__}: {e}"
            log.warning("%s stream failed: %s", self.stats.label, e)
        finally:
            self.close()

    def _take(self, mono: np.ndarray, active: float) -> None:
        room = int((active + AHEAD_S) * TARGET_RATE) - self.written
        if room <= 0:
            self.stats.dropped_s += len(mono) / TARGET_RATE
            return
        if len(mono) > room:
            self.stats.dropped_s += (len(mono) - room) / TARGET_RATE
            mono = mono[:room]
        if len(mono):
            self.stats.peak = max(self.stats.peak, float(np.max(np.abs(mono))))
            self.stats.sum_sq += float(np.sum(mono.astype(np.float64) ** 2))
            size = min(160, len(mono))                     # 10 ms frames
            usable = len(mono) - len(mono) % size
            if usable:
                frames = mono[:usable].reshape(-1, size)
                loud = frames[np.sqrt(np.mean(frames ** 2, axis=1)) > 0.018]   # above -35 dB
                if len(loud):
                    self.stats.crossings += int(np.count_nonzero(np.diff(np.signbit(loud), axis=1)))
                    self.stats.loud_n += loud.size
            self.stats.n += len(mono)
            self.stats.kept_s += len(mono) / TARGET_RATE
            self._write(mono)

    def close(self) -> None:
        try:
            if self.stream.is_active():
                self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass


def _open(p, device, label, clock, out, errors: list) -> _Stream | None:
    try:
        return _Stream(p, device, label, clock, out)
    except Exception as e:
        errors.append(f"{label} ({device.get('name')}): {e}")
        log.warning("couldn't open %s %r: %s", label, device.get("name"), e)
        return None


def _check(stats: StreamStats) -> None:
    """Log what each device did, and warn about the ones that misbehaved."""
    log.info("capture: %s", stats.summary())
    if stats.elapsed_s > 2 and stats.sent_s > stats.elapsed_s * 1.5:
        log.warning("%s %r sent audio %.0fx faster than real time; the extra was discarded. "
                    "This device or its driver is misbehaving — try another one.",
                    stats.label, stats.device, stats.sent_s / stats.elapsed_s)


# --- recording ---------------------------------------------------------------

def record(out_path: Path, stop_event: threading.Event | None = None,
           mic_name: str = "", pause_event: threading.Event | None = None,
           speaker_name: str = "", before_merge=None) -> CaptureResult:
    """
    Record until stop_event is set (or Ctrl+C). Writes a 16kHz stereo WAV.
    mic_name picks a specific microphone by (partial) name; "" = default.
    While pause_event is set, audio is discarded (the recording pauses).

    Saving is unconditional: if one stream dies mid-meeting the other
    channel is still written; it only raises if nothing was captured at all.
    Each channel streams to a .part file as it goes (raw 16 kHz mono s16le),
    so a crash loses at most the last moment; the parts are merged into the
    WAV at the end. before_merge() is called just before that (live
    transcription reads the parts and must let go of them first).
    """
    stop_event = stop_event or threading.Event()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mic_part = Path(str(out_path) + ".mic.part")
    spk_part = Path(str(out_path) + ".spk.part")
    p = pyaudio.PyAudio()
    started_at = time.time()
    errors: list[str] = []
    streams: list[_Stream] = []
    files = []

    try:
        try:
            loopback_dev = _find_loopback(p, speaker_name)
        except Exception as e:
            loopback_dev = None
            errors.append(f"speakers: {e}")
        mic_dev = _pick_mic(p, mic_name)

        log.info("recording: mic=%r speakers=%r -> %s", mic_dev["name"],
                 loopback_dev["name"] if loopback_dev else None, out_path.name)
        print(f"  speakers -> {loopback_dev['name'] if loopback_dev else 'unavailable'}")
        print(f"  mic      -> {mic_dev['name']}")

        clock = _Clock(pause_event)
        mic_file = open(mic_part, "wb")
        spk_file = open(spk_part, "wb")
        files = [mic_file, spk_file]
        mic_s = _open(p, mic_dev, "mic", clock, mic_file, errors)
        spk_s = _open(p, loopback_dev, "speakers", clock, spk_file, errors) if loopback_dev else None
        for s in (mic_s, spk_s):
            if s is None:
                continue
            try:
                s.begin()
                streams.append(s)
            except Exception as e:
                errors.append(f"{s.stats.label} ({s.stats.device}): {e}")
                log.warning("couldn't start %s %r: %s", s.stats.label, s.stats.device, e)
                s.close()
        if not streams:
            raise RuntimeError("Couldn't open any audio device. " + "; ".join(errors))

        try:
            while not stop_event.is_set():
                time.sleep(0.05)
        except KeyboardInterrupt:
            stop_event.set()

        elapsed = clock.freeze()
        for s in streams:
            s.join(timeout=10)
            s.stats.elapsed_s = elapsed
            _check(s.stats)
    finally:
        for f in files:
            f.close()
        for s in streams:
            s.close()
        p.terminate()

    if before_merge is not None:
        try:
            before_merge()
        except Exception as e:
            log.warning("before merging the recording: %s", e)

    def collect(path: Path) -> np.ndarray:
        if not path.exists():
            return np.zeros(0, dtype=np.int16)
        return np.fromfile(path, dtype=np.int16)

    mic, spk = collect(mic_part), collect(spk_part)
    if not mic.any() and not spk.any() and any(s.stats.error for s in streams):
        raise RuntimeError("No audio captured: " + "; ".join(
            s.stats.error for s in streams if s.stats.error))

    # Same length for both channels, and no longer than the time recorded.
    n = min(max(len(mic), len(spk)), int(elapsed * TARGET_RATE) + TARGET_RATE)
    mic = np.pad(mic[:n], (0, n - len(mic[:n])))
    spk = np.pad(spk[:n], (0, n - len(spk[:n])))
    pcm = np.stack([mic, spk], axis=1)

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm.tobytes())
    for part in (mic_part, spk_part):
        try:
            part.unlink(missing_ok=True)
        except OSError as e:   # the WAV is complete; a leftover part is harmless
            log.warning("couldn't remove %s: %s", part.name, e)

    duration = n / TARGET_RATE
    log.info("recording finished: %.1f s -> %s", duration, out_path)
    return CaptureResult(path=out_path, duration_s=duration, started_at=started_at)


# --- device test -------------------------------------------------------------

def test_device(kind: str = "mic", mic_name: str = "", seconds: float = 4.0,
                speaker_name: str = "") -> dict:
    """
    Record a few seconds from one device and report what came in: how loud,
    whether the device kept time, and a WAV of it to play back. For the
    Settings page's "Test" buttons.
    """
    seconds = max(1.0, min(float(seconds), 15.0))
    p = pyaudio.PyAudio()
    buf = io.BytesIO()
    try:
        if kind == "speakers":
            dev = _find_loopback(p, speaker_name)
        else:
            dev = _pick_mic(p, mic_name)
        clock = _Clock(None)
        errors: list[str] = []
        s = _open(p, dev, "speakers" if kind == "speakers" else "mic", clock, buf, errors)
        if not s:
            return {"ok": False, "device": str(dev.get("name")), "verdict": "error",
                    "message": "Couldn't open this device: " + "; ".join(errors)}
        s.begin()
        time.sleep(seconds)
        s.stats.elapsed_s = clock.freeze()
        s.join(timeout=10)
        _check(s.stats)
    finally:
        p.terminate()

    st = s.stats
    from .listen import louder
    clip = np.frombuffer(buf.getvalue(), dtype=np.int16).astype(np.float32) / 32768.0
    pcm = (louder(clip) * 32767).astype(np.int16)   # played back at a normal volume
    wav = io.BytesIO()
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(pcm.tobytes())

    verdict, message = _verdict(kind, st)
    return {
        "ok": verdict == "good",
        "verdict": verdict,
        "message": message,
        "device": st.device,
        "host_api": st.host_api,
        "rate": st.rate,
        "channels": st.channels,
        "seconds": round(st.elapsed_s, 1),
        "sent_s": round(st.sent_s, 1),
        "peak_db": None if st.peak <= 1e-6 else round(20 * float(np.log10(st.peak)), 1),
        "rms_db": None if st.rms <= 1e-6 else round(20 * float(np.log10(st.rms)), 1),
        "audio": "data:audio/wav;base64," + base64.b64encode(wav.getvalue()).decode("ascii"),
    }


def _verdict(kind: str, st: StreamStats) -> tuple[str, str]:
    t = max(st.elapsed_s, 0.1)
    rms_db = 20 * np.log10(st.rms) if st.rms > 1e-6 else -120.0
    if st.error:
        return "error", f"The device stopped with an error: {st.error}"
    if st.sent_s > t * 1.5:
        return "broken", (f"This device sent audio {st.sent_s / t:.0f}× faster than real time, "
                          "which means its driver is misbehaving. Pick a different one.")
    if kind == "speakers":
        if st.sent_s < t * 0.3 or rms_db < -70:
            name = st.device.replace(" [Loopback]", "")
            return "quiet", (f"Nothing came through from {name}. If you heard the chime, your sound "
                             "goes to a different device: pick it under Speakers and test again. "
                             "If you didn't hear it, turn the volume up and unmute.")
        return "good", "Speaker capture works: Scribe can hear what your computer plays."
    if st.sent_s < t * 0.5:
        return "broken", ("The microphone barely sent any audio. Another app may be holding "
                          "it, or try a different microphone.")
    if rms_db < -70:
        return "quiet", ("Silence: no sound reached Scribe. Check the mic isn't muted, and that "
                         "Windows lets desktop apps use it (Settings → Privacy & security → "
                         "Microphone → Let desktop apps access your microphone).")
    if rms_db > -35 and st.zcr > 0.3:
        return "broken", ("This sounds like static, not speech. The device or its driver "
                          "isn't working with Scribe. Pick a different microphone.")
    if rms_db < -50:
        return "low", "The mic works but is very quiet. Speak closer, or raise its level in Windows sound settings."
    return "good", "The microphone works."
