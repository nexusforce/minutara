"""
stt.py — speech to text beyond the one-shot local pass: transcribing while
you record, and transcription by a cloud service or your own server.

Two things live here:

* Engines. LocalEngine is the built-in Whisper model (nothing leaves the
  machine). CloudEngine speaks the OpenAI-style /audio/transcriptions API
  that Groq, OpenAI and self-hosted servers (Speaches, whisper.cpp) share.
  Either one turns a piece of mono 16 kHz audio into timed utterances.

* LiveTranscriber. While a meeting records, each side streams to its own
  raw file (capture.py). Every half minute or so this reads the new audio,
  cuts it at a pause so no word is split, and transcribes it. When you press
  Stop only the last piece is left, so the transcript is ready in seconds.
  It only reads those files; it never touches the recording. If it fails or
  falls behind, the rest is simply transcribed after the meeting, as before.

Your side and their side are transcribed separately, so speaker labels come
from the wiring, exactly as in transcribe.py.
"""

from __future__ import annotations

import io
import os
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import requests

from .log import get
from .transcribe import WHISPER_RATE, Utterance, to_text

log = get("minutara.stt")

RATE = WHISPER_RATE
PROVIDERS = {
    "local": "Built-in (this computer)",
    "groq": "Groq",
    "openai": "OpenAI",
    "server": "Transcription server",
}
CLOUD = ("groq", "openai")
ENV_KEYS = {"groq": ("GROQ_API_KEY",), "openai": ("OPENAI_API_KEY",), "server": ()}


class STTError(RuntimeError):
    """A transcription-service problem worth showing as-is."""


# --- settings -----------------------------------------------------------------

def provider(cfg) -> str:
    name = (getattr(cfg, "stt", None) or {}).get("provider", "local")
    return name if name in PROVIDERS else "local"


def provider_cfg(cfg, name: str | None = None) -> tuple[str, dict]:
    name = name or provider(cfg)
    return name, dict(cfg.stt.get("providers", {}).get(name, {}))


def api_key(name: str, pc: dict) -> str:
    if pc.get("api_key"):
        return pc["api_key"]
    for env in ENV_KEYS.get(name, ()):
        if os.environ.get(env):
            return os.environ[env]
    return ""


def describe(cfg) -> str:
    name, pc = provider_cfg(cfg)
    if name == "local":
        return f"{PROVIDERS[name]} · {cfg.whisper_model}"
    return f"{PROVIDERS[name]} · {pc.get('model') or 'default model'}"


# --- audio helpers ------------------------------------------------------------

FRAME = RATE // 100   # 10 ms


def _frame_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    f = x[: n * FRAME].astype(np.float32).reshape(n, FRAME)
    return np.sqrt(np.mean(f * f, axis=1))


def has_speech(x: np.ndarray, min_s: float = 0.3) -> bool:
    """Anything louder than a quiet room for at least min_s? (-50 dBFS frames)"""
    return int(np.count_nonzero(_frame_rms(x) > 0.00316)) >= int(min_s * 100)


def quiet_cut(x: np.ndarray, lo: int, hi: int) -> int:
    """The quietest quarter-second between lo and hi: a pause between words."""
    lo, hi = max(0, lo), min(len(x), hi)
    k = 25   # frames in a quarter second
    if hi - lo < (k + 1) * FRAME:
        return hi
    e = _frame_rms(x[lo:hi])
    smooth = np.convolve(e, np.ones(k) / k, mode="valid")
    i = len(smooth) - 1 - int(np.argmin(smooth[::-1]))   # the latest of equal minima
    return lo + (i + k // 2) * FRAME


def _tail(utts: list[Utterance], chars: int = 200) -> str:
    """The last words said, as context for the next piece (names, spelling)."""
    text = " ".join(u.text for u in utts)[-chars:]
    return text.split(" ", 1)[-1] if len(text) == chars else text


def _spread(text: str, offset: float, duration: float, speaker: str) -> list[Utterance]:
    """Text without timings: one line per sentence, timed by length."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    total = sum(len(p) for p in parts) or 1
    out, pos = [], 0
    for p in parts:
        start = offset + duration * pos / total
        pos += len(p)
        out.append(Utterance(start, offset + duration * pos / total, speaker, p))
    return out


def _encode(x: np.ndarray, fmt: str) -> tuple[bytes, str, str]:
    """Mono 16 kHz audio as FLAC (half the size) or WAV (what every server reads)."""
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
    if fmt == "flac":
        try:
            import soundfile as sf
            buf = io.BytesIO()
            sf.write(buf, pcm, RATE, format="FLAC", subtype="PCM_16")
            return buf.getvalue(), "audio.flac", "audio/flac"
        except Exception as e:   # no FLAC support: WAV works everywhere
            log.info("FLAC encoding unavailable (%s); sending WAV", e)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue(), "audio.wav", "audio/wav"


# --- engines ------------------------------------------------------------------

class LocalEngine:
    """The built-in Whisper model. threads=0 lets it use the usual number."""
    max_chunk_s = None   # Whisper reads any length itself

    def __init__(self, cfg, threads: int = 0):
        self.size, self.device, self.threads = cfg.whisper_model, cfg.whisper_device, threads
        self._model = None

    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            log.info("loading Whisper %s on %s (%s)", self.size, self.device,
                     f"{self.threads} cores" if self.threads else "all cores")
            self._model = WhisperModel(self.size, device=self.device, compute_type="int8",
                                       cpu_threads=self.threads)
        return self._model

    def run(self, x: np.ndarray, offset: float, speaker: str, prompt: str = "") -> list[Utterance]:
        segments, _info = self.model().transcribe(
            x.astype(np.float32), beam_size=5, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=prompt or None)
        return [Utterance(offset + s.start, offset + s.end, speaker, s.text.strip())
                for s in segments if s.text.strip()]


class CloudEngine:
    """Groq, OpenAI, or your own server: anything with OpenAI's transcription API."""
    max_chunk_s = 600   # 10 minutes per upload keeps well under every size limit

    def __init__(self, cfg, name: str | None = None):
        self.name, self.pc = provider_cfg(cfg, name)
        if self.name == "local":
            raise STTError("The built-in model isn't a transcription service.")
        self.label = PROVIDERS[self.name]
        self.key = api_key(self.name, self.pc)
        if self.name in CLOUD and not self.key:
            raise STTError(f"No API key for {self.label}. Add it in Settings → Transcription.")
        base = (self.pc.get("base_url") or "").strip().rstrip("/")
        if not base:
            raise STTError(f"No address set for {self.label}. Add it in Settings → Transcription.")
        self.url = base + "/" + (self.pc.get("path") or "/audio/transcriptions").lstrip("/")
        self.language = (cfg.stt.get("language") or "").strip()
        self.verbose = True   # ask for timed segments; dropped if the model can't give them
        self.seconds_sent = 0.0

    def run(self, x: np.ndarray, offset: float, speaker: str, prompt: str = "") -> list[Utterance]:
        data, fname, mime = _encode(x, self.pc.get("upload_format") or "flac")
        body = self._post(data, fname, mime, prompt)
        self.seconds_sent += len(x) / RATE
        out = []
        for s in body.get("segments") or []:
            text = str(s.get("text") or "").strip()
            if text:
                out.append(Utterance(offset + float(s.get("start") or 0),
                                     offset + float(s.get("end") or 0), speaker, text))
        if not out and not body.get("segments"):
            out = _spread(str(body.get("text") or "").strip(), offset, len(x) / RATE, speaker)
        return out

    def _post(self, data: bytes, fname: str, mime: str, prompt: str) -> dict:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        timeout = float(self.pc.get("timeout") or 300)
        for attempt in range(5):
            fields = {"model": self.pc.get("model") or "",
                      "response_format": "verbose_json" if self.verbose else "json",
                      "temperature": "0"}
            if self.language:
                fields["language"] = self.language
            if prompt:
                fields["prompt"] = prompt
            try:
                r = requests.post(self.url, headers=headers, data=fields,
                                  files={"file": (fname, data, mime)}, timeout=timeout)
            except requests.ConnectionError:
                raise STTError(f"Couldn't reach {self.label} at {self.url}. "
                               + ("Is the server running?" if self.name == "server"
                                  else "Check your internet connection.")) from None
            except requests.Timeout:
                raise STTError(f"{self.label} didn't answer within {timeout:.0f} seconds.") from None
            if r.status_code == 400 and self.verbose and re.search(
                    r"response_format|verbose_json", r.text, re.I):
                log.info("%s model %s gives no timings; using plain text", self.label,
                         self.pc.get("model"))
                self.verbose = False
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                try:
                    wait = float(r.headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0
                wait = min(max(wait, 2 ** attempt * 2), 60)
                log.info("%s said %s; trying again in %.0fs", self.label, r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise STTError(f"{self.label} said {r.status_code}: {_message(r)}")
            try:
                body = r.json()
            except ValueError:
                return {"text": r.text}
            return body if isinstance(body, dict) else {"text": str(body)}
        raise STTError(f"{self.label} kept refusing; try again later.")


def _message(r: requests.Response) -> str:
    try:
        body = r.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err or body)[:300]
    except ValueError:
        return r.text[:300] or r.reason


def engine(cfg, name: str | None = None, threads: int = 0):
    name = name or provider(cfg)
    return LocalEngine(cfg, threads) if name == "local" else CloudEngine(cfg, name)


# --- whole recordings -----------------------------------------------------------

def transcribe_samples(eng, x: np.ndarray, speaker: str, start: int = 0,
                       prompt: str = "") -> list[Utterance]:
    """Audio from `start` on, in pieces cut at pauses (or whole, for Whisper)."""
    out: list[Utterance] = []
    limit = int(eng.max_chunk_s * RATE) if eng.max_chunk_s else 0
    pos = start
    while pos < len(x):
        end = len(x) if not limit or len(x) - pos <= limit else \
            quiet_cut(x, pos + int(limit * 0.8), pos + limit)
        piece = x[pos:end]
        if has_speech(piece):
            got = eng.run(piece, pos / RATE, speaker, prompt)
            out += got
            prompt = _tail(got) or prompt
        pos = end
    return out


def transcribe_file(cfg, path: Path, me: str, them: str) -> list:
    """A whole recording through the chosen service (not the built-in model)."""
    import soundfile as sf
    try:
        audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as e:
        raise STTError(f"Can't read {path.name} for upload ({e}).") from None
    if rate != RATE:
        from .transcribe import _resample
        audio = np.stack([_resample(audio[:, i], rate) for i in range(audio.shape[1])], axis=1)
    eng = CloudEngine(cfg)
    started = time.monotonic()
    if audio.shape[1] == 2:
        utts = transcribe_samples(eng, audio[:, 0], me) + transcribe_samples(eng, audio[:, 1], them)
    else:
        utts = transcribe_samples(eng, audio.mean(axis=1), "Speaker")
    utts.sort(key=lambda u: u.start)
    log.info("%s transcribed %s in %.1fs (%.0fs of speech sent)", eng.label, path.name,
             time.monotonic() - started, eng.seconds_sent)
    return utts


def check(cfg, name: str) -> str:
    """Settings → Test: send one second of quiet and see that the service answers."""
    eng = CloudEngine(cfg, name)
    t = np.arange(RATE) / RATE
    eng.run((0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 0.0, "Test")
    return f"{eng.label} answered. Model: {eng.pc.get('model') or 'default'}."


# --- live -----------------------------------------------------------------------

class LiveTranscriber:
    """Transcribes a recording while it's being made (see the module docstring)."""

    POLL_S = 2.0
    MARGIN = RATE // 2     # leave the newest half second; it may still be arriving
    MIN_CHUNK_S = 10.0

    def __init__(self, cfg, wav_path: Path, me: str, them: str):
        self.cfg = cfg
        self.wav = Path(wav_path)
        self.sides = [(Path(str(wav_path) + ".mic.part"), me),
                      (Path(str(wav_path) + ".spk.part"), them)]
        self.name = provider(cfg)
        base = float(cfg.stt.get("live_chunk_s") or 30)
        self.chunk = int(max(self.MIN_CHUNK_S, base if self.name == "local" else base * 2) * RATE)
        self.done = [0, 0]
        self.prompt = ["", ""]
        self.utts: list[Utterance] = []
        self.eng = None
        self.error = ""
        self.lock = threading.Lock()   # utts, done
        self.io = threading.Lock()     # held while a .part file is open
        self.halted = False
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None
        self.busy_s = 0.0
        self._lag_said = 0.0

    # -- reading the growing files
    def _size(self, i: int) -> int:
        try:
            return self.sides[i][0].stat().st_size // 2
        except OSError:
            return 0

    def _read(self, i: int, start: int, count: int) -> np.ndarray | None:
        with self.io:
            if self.halted:
                return None
            try:
                with open(self.sides[i][0], "rb") as f:
                    f.seek(start * 2)
                    raw = f.read(count * 2)
            except OSError:
                return np.zeros(0, dtype=np.float32)
        raw = raw[: len(raw) // 2 * 2]
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def halt(self) -> None:
        """Stop reading (capture is about to merge and delete the raw files)."""
        self.stopping.set()
        with self.io:
            self.halted = True

    # -- the worker
    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True, name="live-transcribe")
        self.thread.start()
        log.info("live transcription on (%s, pieces of about %ds)",
                 describe(self.cfg), self.chunk // RATE)

    def _engine(self):
        if self.eng is None:
            self.eng = engine(self.cfg, self.name, int(self.cfg.stt.get("live_threads") or 2))
        return self.eng

    def _run(self) -> None:
        try:
            while not self.stopping.is_set():
                worked = False
                for i in (0, 1):
                    if not self.stopping.is_set():
                        worked = self._step(i) or worked
                if not worked:
                    self.stopping.wait(self.POLL_S)
        except Exception as e:
            self.error = str(e) or e.__class__.__name__
            log.warning("live transcription stopped (%s); the rest will be transcribed "
                        "after the meeting", self.error)

    def _step(self, i: int) -> bool:
        ready = self._size(i) - self.MARGIN - self.done[i]
        if ready < self.chunk:
            return False
        x = self._read(i, self.done[i], min(ready, int(self.chunk * 1.5)))
        if x is None or len(x) < self.chunk:
            return False
        cut = quiet_cut(x, int(self.chunk * 0.6), len(x))
        piece = x[:cut]
        if has_speech(piece):
            t0 = time.monotonic()
            got = self._engine().run(piece, self.done[i] / RATE, self.sides[i][1], self.prompt[i])
            self.busy_s += time.monotonic() - t0
            with self.lock:
                self.utts += got
                self.prompt[i] = _tail(got) or self.prompt[i]
        with self.lock:
            self.done[i] += cut
        lag = (self._size(i) - self.done[i]) / RATE
        if lag > 3 * self.chunk / RATE and time.monotonic() - self._lag_said > 120:
            self._lag_said = time.monotonic()
            log.info("live transcription is %.0fs behind; it catches up after the meeting", lag)
        return True

    # -- what the app shows
    def status(self) -> dict:
        with self.lock:
            done = min(self.done) / RATE
            lines = len(self.utts)
        heard = max(self._size(0), self._size(1)) / RATE if not self.halted else done
        return {"on": True, "provider": self.name, "done_s": round(done, 1),
                "behind_s": round(max(0.0, heard - done), 1), "lines": lines,
                "error": self.error}

    def text(self) -> str:
        with self.lock:
            utts = sorted(self.utts, key=lambda u: u.start)
        return to_text(utts)

    # -- after Stop
    def finish(self, wav_path: Path | None = None) -> list[Utterance]:
        """Transcribe whatever is left from the saved recording; return everything."""
        self.halt()
        if self.thread:
            self.thread.join()
        import soundfile as sf
        audio, rate = sf.read(str(wav_path or self.wav), dtype="float32", always_2d=True)
        if rate != RATE or audio.shape[1] != 2:
            raise STTError(f"unexpected recording format ({rate} Hz, {audio.shape[1]} channels)")
        left = max(len(audio) - d for d in self.done) / RATE
        eng = self._engine()
        if self.name == "local" and left > 300:
            eng = engine(self.cfg, "local")   # far behind: finish with every core
        started = time.monotonic()
        utts = list(self.utts)
        for i in (0, 1):
            utts += transcribe_samples(eng, audio[:, i], self.sides[i][1],
                                       start=self.done[i], prompt=self.prompt[i])
        utts.sort(key=lambda u: u.start)
        log.info("live transcription finished: %.0fs of audio left at stop, done in %.1fs "
                 "(%.0fs of work during the meeting)", left, time.monotonic() - started, self.busy_s)
        self.eng = None   # let the model go
        return utts

    def cancel(self) -> None:
        self.halt()
        self.eng = None
