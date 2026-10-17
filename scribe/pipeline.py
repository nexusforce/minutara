"""
pipeline.py — the one path every recording takes, whatever started it.

CLI, phone remote, and USB ingest all call process(). Local Whisper is the
default and the only thing that runs unless a Pocket toggle is switched on in
config.json.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .log import get

log = get("scribe.pipeline")


def slug(s: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_ " else "" for c in s)
    return keep.strip().replace(" ", "-").lower() or "meeting"


def stamped_name(title: str) -> str:
    return f"{datetime.now():%Y%m%d-%H%M}-{slug(title)}"


def process(
    audio_path: Path,
    cfg: Config,
    title: str | None = None,
    duration_s: float | None = None,
    recorded_at: datetime | None = None,
) -> Path:
    """
    Transcribe audio and write a .txt next to the other transcripts.
    Pushes to Pocket only if config says so. Returns the transcript path.
    """
    from .transcribe import to_text, transcribe

    title = title or audio_path.stem
    name = audio_path.stem

    text = ""

    log.info("processing %s (title=%r)", audio_path.name, title)

    if cfg.prefer_pocket_transcripts and cfg.pocket_enabled:
        # Opt-in: let Pocket transcribe instead of doing it locally. Costs you
        # the free channel-based speaker split and sends audio to their
        # servers, so it's off by default.
        try:
            text = _pocket_transcribe(audio_path, cfg, title, duration_s, recorded_at)
        except Exception as e:
            log.warning("Pocket transcription failed (%s); falling back to local Whisper.", e)
            print(f"Pocket transcription failed ({e}). Falling back to local Whisper.")

    if not text:
        utterances = transcribe(
            audio_path,
            me=cfg.me_label,
            them=cfg.them_label,
            model_size=cfg.whisper_model,
            device=cfg.whisper_device,
        )
        text = to_text(utterances)

    out = cfg.transcripts_dir / f"{name}.txt"
    out.write_text(text, encoding="utf-8")
    lines = text.count("\n") + 1 if text.strip() else 0
    log.info("transcript saved: %s (%d lines%s)", out.name, lines,
             "; no speech found" if not lines else "")

    if cfg.push_to_pocket and cfg.pocket_enabled and not cfg.prefer_pocket_transcripts:
        try:
            _pocket_push(audio_path, cfg, title, duration_s, recorded_at)
            log.info("pushed %s to Pocket", audio_path.name)
        except Exception as e:
            log.warning("Pocket upload failed (%s); transcript is saved locally regardless.", e)
            print(f"Pocket upload failed ({e}). Transcript is saved locally regardless.")

    return out


def _pocket_push(audio_path, cfg, title, duration_s, recorded_at) -> dict:
    from .pocket_client import PocketClient

    client = PocketClient(cfg.pocket_api_key, base_url=cfg.pocket_base_url)
    return client.upload_recording(
        audio_path,
        title=title,
        duration_s=duration_s or 0,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


def _pocket_transcribe(audio_path, cfg, title, duration_s, recorded_at) -> str:
    """Upload, poll for Pocket's transcript, return it. UNVERIFIED response shape."""
    import time

    from .pocket_client import PocketClient

    client = PocketClient(cfg.pocket_api_key, base_url=cfg.pocket_base_url)
    data = client.upload_recording(
        audio_path,
        title=title,
        duration_s=duration_s or 0,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )
    rec_id = data.get("recording_id") or data.get("id")
    if not rec_id:
        raise RuntimeError(f"No recording id in upload response: {data}")

    for _ in range(60):  # ~5 min
        details = client.get_recording(str(rec_id))
        text = details.get("transcript") or details.get("transcription")
        if text:
            return text if isinstance(text, str) else _flatten_segments(text)
        time.sleep(5)

    raise TimeoutError("Pocket did not return a transcript in time.")


def _flatten_segments(segments) -> str:
    """Their MCP docs show transcriptSegments with text/start/end/speaker."""
    lines = []
    for s in segments:
        speaker = s.get("speaker", "Speaker")
        start = float(s.get("start", 0))
        m, sec = divmod(int(start), 60)
        lines.append(f"[{m:02d}:{sec:02d}] {speaker}: {s.get('text', '').strip()}")
    return "\n".join(lines)
