"""
listen.py — make recordings comfortable to listen to.

A Minutara recording keeps you on the left channel and the other side on the
right, at whatever level each device delivered. That's ideal for
transcription and terrible for listening: one voice per ear, and a laptop
mic often sits 20–30 dB below normal playback level.

So the player gets a listening copy: each channel brought to a normal
speech level on its own, mixed to mono (both voices in both ears), with a
soft limiter so loud moments don't clip. The original recording is never
changed; transcription and downloads use it as it is. Copies are cached in
cache/listen/ and rebuilt whenever the recording changes.
"""

from __future__ import annotations

import os
import re
import threading
import wave
from pathlib import Path

import numpy as np

from .log import get

log = get("minutara.listen")

TARGET = 0.2          # loud-speech level to aim for (about -14 dBFS)
MAX_GAIN = 40.0       # +32 dB at most, so near-silence isn't turned into hiss
SILENT = 3e-4         # a channel quieter than this (about -70 dBFS) is left out
BLOCK = 16000 * 30    # frames per pass: 30 s at 16 kHz keeps memory small

_lock = threading.Lock()


def _copies(out_dir: Path, stem: str) -> list[Path]:
    """Cached copies of exactly this recording (not of 'stem-something-else')."""
    pat = re.compile(re.escape(stem) + r"-\d+-\d+\.wav")
    return [f for f in out_dir.glob("*.wav") if pat.fullmatch(f.name)] if out_dir.is_dir() else []


def _frame_levels(x: np.ndarray, size: int = 320) -> np.ndarray:
    n = len(x) - len(x) % size
    if n == 0:
        return np.zeros(0)
    return np.sqrt(np.mean(x[:n].reshape(-1, size) ** 2, axis=1))


def gain_for(levels: np.ndarray) -> float:
    """Gain that brings the loud parts of a channel (its speech) to TARGET."""
    if len(levels) == 0:
        return 0.0
    loud = float(np.percentile(levels, 95))
    if loud < SILENT:
        return 0.0
    return float(min(MAX_GAIN, TARGET / loud))


def limit(x: np.ndarray) -> np.ndarray:
    """Soft limiter: untouched below 0.7, smoothly squeezed towards 1.0 above."""
    a = np.abs(x)
    over = a > 0.7
    if over.any():
        x = x.copy()
        x[over] = np.sign(x[over]) * (0.7 + 0.3 * np.tanh((a[over] - 0.7) / 0.3))
    return x


def louder(mono: np.ndarray) -> np.ndarray:
    """One clip (e.g. a mic test), brought to a normal listening level."""
    g = gain_for(_frame_levels(mono))
    return limit(mono * g) if g else mono


def listening_copy(src: Path, cache_dir: Path) -> Path:
    """
    The cached listening copy of a WAV recording, made if needed. Anything
    that isn't a 16-bit PCM WAV (Pocket's mp3s, imports) is returned as is.
    """
    if src.suffix.lower() != ".wav":
        return src
    st = src.stat()
    out_dir = cache_dir / "listen"
    out = out_dir / f"{src.stem}-{int(st.st_mtime)}-{st.st_size}.wav"
    if out.exists():
        return out
    with _lock:
        if out.exists():
            return out
        try:
            with wave.open(str(src), "rb") as w:
                if w.getsampwidth() != 2:
                    return src
                ch, rate, frames = w.getnchannels(), w.getframerate(), w.getnframes()

                # Pass 1: how loud is each channel?
                levels = [[] for _ in range(ch)]
                for _ in range(0, frames, BLOCK):
                    x = np.frombuffer(w.readframes(BLOCK), dtype=np.int16).reshape(-1, ch)
                    for c in range(ch):
                        levels[c].append(_frame_levels(x[:, c].astype(np.float32) / 32768.0))
                gains = [gain_for(np.concatenate(lv) if lv else np.zeros(0)) for lv in levels]
                if not any(gains):
                    return src   # nothing but silence: nothing to improve

                # Pass 2: write the mix.
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".tmp")
                w.rewind()
                with wave.open(str(tmp), "wb") as o:
                    o.setnchannels(1)
                    o.setsampwidth(2)
                    o.setframerate(rate)
                    for _ in range(0, frames, BLOCK):
                        x = np.frombuffer(w.readframes(BLOCK), dtype=np.int16).reshape(-1, ch)
                        x = x.astype(np.float32) / 32768.0
                        mix = sum(x[:, c] * gains[c] for c in range(ch))
                        o.writeframes((limit(mix) * 32767).astype(np.int16).tobytes())
            os.replace(tmp, out)
        except (wave.Error, EOFError, OSError) as e:
            log.warning("couldn't make a listening copy of %s: %s", src.name, e)
            return src
        for old in _copies(out_dir, src.stem):   # earlier versions of this recording
            if old != out:
                old.unlink(missing_ok=True)
        log.info("listening copy of %s: gains %s", src.name,
                 ", ".join(f"{20 * np.log10(g):+.0f} dB" if g else "silent" for g in gains))
        return out


def forget(cache_dir: Path, stem: str) -> None:
    """Drop the listening copies of a deleted recording."""
    for f in _copies(cache_dir / "listen", stem):
        f.unlink(missing_ok=True)
