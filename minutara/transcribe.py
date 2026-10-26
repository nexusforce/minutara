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

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel

from .log import get

log = get("minutara.transcribe")

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


# --- vocabulary ------------------------------------------------------------------

def vocab_terms(text: str) -> list[str]:
    """'Northwind, Priya\nImpact Report' → ['Northwind', 'Priya', 'Impact Report']."""
    seen, out = set(), []
    for t in re.split(r"[,;\n]+", text or ""):
        t = " ".join(t.split())
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:80]


def hotwords(text: str) -> str | None:
    """The vocabulary as Whisper hotwords: hinted in every 30-second window
    (unlike initial_prompt, which only reaches the first)."""
    terms = vocab_terms(text)
    return ", ".join(terms) if terms else None


# --- words into lines ---------------------------------------------------------------

GAP_S = 0.8     # a pause this long starts a new line
LONG_S = 12.0   # past this, the end of a sentence starts a new line too
MAX_S = 30.0    # and nothing runs longer than this


@dataclass
class Word:
    start: float
    end: float
    word: str   # with its leading space, as Whisper gives it


def split_words(words, speaker: str, offset: float = 0.0) -> list[Utterance]:
    """Cut a run of timed words into lines at pauses (and long sentences), so
    the two sides interleave properly instead of one long chunk swallowing
    the other side's short replies."""
    out: list[Utterance] = []
    cur: list = []

    def flush():
        text = "".join(w.word for w in cur).strip()
        if text:
            out.append(Utterance(offset + cur[0].start, offset + cur[-1].end, speaker, text))
        cur.clear()

    for w in words:
        if cur:
            gap = w.start - cur[-1].end
            dur = cur[-1].end - cur[0].start
            sentence_end = cur[-1].word.strip()[-1:] in (".", "?", "!")
            if gap >= GAP_S or (sentence_end and dur >= LONG_S) or dur >= MAX_S:
                flush()
        cur.append(w)
    flush()
    return out


def segment_lines(segments, speaker: str, offset: float = 0.0) -> list[Utterance]:
    """Whisper segments → lines, using word timings where there are any."""
    out: list[Utterance] = []
    for seg in segments:
        words = getattr(seg, "words", None)
        if words:
            out += split_words(words, speaker, offset)
        elif seg.text.strip():
            out.append(Utterance(offset + seg.start, offset + seg.end, speaker, seg.text.strip()))
    return out


def run_whisper(model, audio, speaker: str, offset: float = 0.0, prompt: str = "",
                vocabulary: str = "") -> list[Utterance]:
    """One channel (or piece of one) through Whisper."""
    segments, _info = model.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,  # drops silence — important, each channel is mostly silence
        vad_parameters={"min_silence_duration_ms": 500},
        # Conditioning on its own previous output makes Whisper drift (lost
        # capitals and punctuation, repeated phrases); the vocabulary goes in
        # as hotwords instead, which apply to every window.
        condition_on_previous_text=False,
        word_timestamps=True,
        hotwords=hotwords(vocabulary),
        initial_prompt=prompt or None,
    )
    return segment_lines(segments, speaker, offset)


def _transcribe_channel(model, audio, speaker: str, vocabulary: str = "") -> list[Utterance]:
    return run_whisper(model, audio, speaker, vocabulary=vocabulary)


# --- echo -----------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def remove_echo(utts: list[Utterance], me: str, them: str) -> tuple[list[Utterance], int]:
    """Drop lines on your side that are really the call coming out of your
    speakers into your microphone: they happen at the same moment as a line
    on their side and are mostly the same words."""
    theirs = [u for u in utts if u.speaker != me]
    if not theirs:
        return utts, 0
    kept, dropped = [], 0
    for u in utts:
        if u.speaker == me:
            words = _tokens(u.text)
            near = [t for t in theirs if t.start - 1.0 < u.end and t.end + 1.0 > u.start]
            if near and len(words) >= 3:
                heard = set(_tokens(" ".join(t.text for t in near)))
                if sum(w in heard for w in words) / len(words) >= 0.7:
                    dropped += 1
                    continue
        kept.append(u)
    return kept, dropped


def tidy(utts: list[Utterance], me: str, them: str, echo: bool = True) -> list[Utterance]:
    """In time order, with your mic's echo of the call removed."""
    utts = sorted(utts, key=lambda u: (u.start, u.end))
    if echo:
        utts, n = remove_echo(utts, me, them)
        if n:
            log.info("removed %d line(s) of the call echoing into your microphone", n)
    return utts


def transcribe(
    wav_path: Path,
    me: str = "Me",
    them: str = "Them",
    model_size: str = DEFAULT_MODEL,
    device: str = "cpu",
    vocabulary: str = "",
) -> list[Utterance]:
    try:
        audio, rate = sf.read(str(wav_path), dtype="float32", always_2d=True)
    except Exception:
        # libsndfile can't decode this container (e.g. the .m4a files Pocket
        # serves). Hand the path to faster-whisper, which decodes anything
        # via PyAV — at the cost of the channel-based speaker split.
        audio, rate = None, 0

    # First ever run downloads the model (~500 MB for small.en) — by far the
    # longest "Transcribing" you'll ever see. The download shows up here.
    log.info("loading Whisper %s on %s (first run downloads the model)",
             model_size, device)
    model = WhisperModel(model_size, device=device, compute_type="int8")
    log.info("model ready; transcribing %s", wav_path.name)

    if audio is None:
        utterances = _transcribe_channel(model, str(wav_path), "Speaker", vocabulary)
    elif audio.shape[1] == 2:
        left = _resample(audio[:, 0], rate)
        right = _resample(audio[:, 1], rate)
        utterances = _transcribe_channel(model, left, me, vocabulary)
        utterances += _transcribe_channel(model, right, them, vocabulary)
    else:
        # Mono file (e.g. pulled down from the Pocket device) — no channel
        # separation available, so one unlabeled pass.
        utterances = _transcribe_channel(model, _resample(audio.mean(axis=1), rate), "Speaker",
                                         vocabulary)

    utterances.sort(key=lambda u: u.start)
    return utterances


def to_text(utterances: list[Utterance], with_timestamps: bool = True) -> str:
    lines = []
    for u in utterances:
        prefix = f"{u.stamp()} " if with_timestamps else ""
        lines.append(f"{prefix}{u.speaker}: {u.text}")
    return "\n".join(lines)
