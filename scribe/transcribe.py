"""
transcribe.py — local transcription with faster-whisper.

The trick: we transcribe each channel of the stereo WAV separately.
LEFT is you, RIGHT is them. That means speaker labels come for free from
the wiring, not from a diarization model that has to guess. Then we
interleave the two by timestamp into one readable transcript.

Requires: pip install faster-whisper numpy soundfile
Model runs on CPU by default; set device="cuda" if you have an NVIDIA GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

# "base.en" is fast and fine for meetings. "small.en" is noticeably better
# on accents/crosstalk and still real-time-ish on a modern CPU.
DEFAULT_MODEL = "small.en"

# faster-whisper assumes raw numpy input is already at this rate; anything
# else must be resampled first or the audio plays back at the wrong speed.
WHISPER_RATE = 16000


def _resample(mono: np.ndarray, src_rate: int) -> np.ndarray:
    """Linear resample to WHISPER_RATE. Good enough for STT."""
    if src_rate == WHISPER_RATE or len(mono) == 0:
        return mono.astype(np.float32)
    n_out = int(len(mono) * WHISPER_RATE / src_rate)
    x_old = np.linspace(0, 1, len(mono), endpoint=False)
    x_new = np.linspace(0, 1, n_out, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32)


@dataclass
class Utterance:
    start: float
    end: float
    speaker: str
    text: str

    def stamp(self) -> str:
        m, s = divmod(int(self.start), 60)
        return f"[{m:02d}:{s:02d}]"


def _transcribe_channel(
    model: WhisperModel, audio: np.ndarray | str, speaker: str
) -> list[Utterance]:
    segments, _info = model.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,  # drops silence — important, each channel is mostly silence
        vad_parameters={"min_silence_duration_ms": 500},
    )
    out = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append(Utterance(seg.start, seg.end, speaker, text))
    return out


def transcribe(
    wav_path: Path,
    me: str = "Me",
    them: str = "Them",
    model_size: str = DEFAULT_MODEL,
    device: str = "cpu",
) -> list[Utterance]:
    try:
        audio, rate = sf.read(str(wav_path), dtype="float32", always_2d=True)
    except Exception:
        # libsndfile can't decode this container (e.g. the .m4a files Pocket
        # serves). Hand the path to faster-whisper, which decodes anything
        # via PyAV — at the cost of the channel-based speaker split.
        audio, rate = None, 0

    model = WhisperModel(model_size, device=device, compute_type="int8")

    if audio is None:
        utterances = _transcribe_channel(model, str(wav_path), "Speaker")
    elif audio.shape[1] == 2:
        left = _resample(audio[:, 0], rate)
        right = _resample(audio[:, 1], rate)
        utterances = _transcribe_channel(model, left, me)
        utterances += _transcribe_channel(model, right, them)
    else:
        # Mono file (e.g. pulled down from the Pocket device) — no channel
        # separation available, so one unlabeled pass.
        utterances = _transcribe_channel(model, _resample(audio.mean(axis=1), rate), "Speaker")

    utterances.sort(key=lambda u: u.start)
    return utterances


def to_text(utterances: list[Utterance], with_timestamps: bool = True) -> str:
    lines = []
    for u in utterances:
        prefix = f"{u.stamp()} " if with_timestamps else ""
        lines.append(f"{prefix}{u.speaker}: {u.text}")
    return "\n".join(lines)
