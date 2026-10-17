"""
notes.py — a meeting is a note: a title, your typed notes, the recording and
its transcript.

Each note is one small JSON file in notes/ — one file per note, so a bad
write can only ever damage one meeting. Audio and transcripts stay where
they always lived (recordings/<id>.wav, transcripts/<id>.txt); the note's id
is that shared file stem, which is how they're linked.

sync() adopts any recording or transcript that has no note yet (USB pulls,
CLI recordings, files from before notes existed), so nothing is orphaned.
"""

from __future__ import annotations

import json
import os
import re
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .log import get as get_logger
from .pipeline import stamped_name

log = get_logger("scribe.notes")

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}
FOLDERS_FILE = "_folders.txt"

_lock = threading.RLock()
_STAMP_RE = re.compile(r"^(\d{8}-\d{4})-(.+)$")
_POCKET_RE = re.compile(r"^pocket-(\d{4}-\d{2}-\d{2})-(.+)$")


# --- ids, paths, times -------------------------------------------------------

def valid_id(note_id: str) -> bool:
    """A bare file stem: no path separators, no dot-files, no traversal."""
    return (
        bool(note_id)
        and len(note_id) <= 200
        and "/" not in note_id
        and "\\" not in note_id
        and ".." not in note_id
        and not note_id.startswith(".")
    )


def _path(cfg: Config, note_id: str) -> Path:
    return cfg.notes_dir / f"{note_id}.json"


def audio_path(cfg: Config, note: dict) -> Path | None:
    return cfg.recordings_dir / note["audio"] if note.get("audio") else None


def transcript_path(cfg: Config, note: dict) -> Path:
    return cfg.transcripts_dir / f"{note['id']}.txt"


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _sort_key(note: dict) -> datetime:
    try:
        return datetime.fromisoformat(note.get("created_at", ""))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def unique_id(cfg: Config, title: str) -> str:
    base = stamped_name(title or "meeting")
    cand, n = base, 2
    while _path(cfg, cand).exists() or (cfg.recordings_dir / f"{cand}.wav").exists():
        cand = f"{base}-{n}"
        n += 1
    return cand


# --- storage -----------------------------------------------------------------

def _write(cfg: Config, note: dict) -> None:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    p = _path(cfg, note["id"])
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(note, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)  # atomic: a crash mid-write never leaves half a note


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("unreadable note file %s", p.name)
        return None


def create(cfg: Config, note_id: str, **fields) -> dict:
    note = {
        "id": note_id,
        "title": "",
        "created_at": _iso(datetime.now()),
        "audio": "",
        "notes": "",
        "folder": "",
        "source": "pc",
        "duration_s": None,
        "error": "",
    }
    note.update(fields)
    with _lock:
        _write(cfg, note)
    return note


def load(cfg: Config, note_id: str) -> dict | None:
    if not valid_id(note_id):
        return None
    p = _path(cfg, note_id)
    return _read(p) if p.exists() else None


def update(cfg: Config, note_id: str, **fields) -> dict | None:
    """Merge fields into a note. Read-modify-write under the lock, so the
    recorder updating duration can't clobber notes you're typing."""
    with _lock:
        note = load(cfg, note_id)
        if note is None:
            return None
        note.update(fields)
        _write(cfg, note)
        return note


def delete(cfg: Config, note_id: str) -> bool:
    """Remove the note and its recording and transcript."""
    with _lock:
        note = load(cfg, note_id)
        if note is None:
            return False
        for f in (audio_path(cfg, note), transcript_path(cfg, note)):
            if f is not None and f.exists():
                f.unlink()
        _path(cfg, note_id).unlink(missing_ok=True)
    log.info("deleted note %s", note_id)
    return True


def _all_raw(cfg: Config) -> list[dict]:
    if not cfg.notes_dir.is_dir():
        return []
    return [n for p in cfg.notes_dir.glob("*.json") if (n := _read(p))]


def all_notes(cfg: Config) -> list[dict]:
    sync(cfg)
    notes = _all_raw(cfg)
    notes.sort(key=_sort_key, reverse=True)
    return notes


def matches(cfg: Config, note: dict, q: str) -> bool:
    q = q.lower()
    if q in (note.get("title") or "").lower() or q in (note.get("notes") or "").lower():
        return True
    t = transcript_path(cfg, note)
    return t.exists() and q in t.read_text(encoding="utf-8", errors="replace").lower()


# --- adopting files that predate notes ----------------------------------------

def _title_from_stem(stem: str) -> str:
    m = _STAMP_RE.match(stem)
    if m:
        return m.group(2).replace("-", " ").strip().capitalize() or "Meeting"
    m = _POCKET_RE.match(stem)
    if m:
        return f"Pocket recording {m.group(2)}"
    return stem.replace("-", " ").replace("_", " ").strip().capitalize() or "Untitled"


def _created_from(stem: str, f: Path) -> str:
    m = _STAMP_RE.match(stem)
    if m:
        try:
            return _iso(datetime.strptime(m.group(1), "%Y%m%d-%H%M"))
        except ValueError:
            pass
    return _iso(datetime.fromtimestamp(f.stat().st_mtime))


def _duration(f: Path) -> float | None:
    if f.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(f), "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 1)
    except Exception:
        return None


def sync(cfg: Config) -> None:
    with _lock:
        cfg.notes_dir.mkdir(parents=True, exist_ok=True)
        known = {p.stem for p in cfg.notes_dir.glob("*.json")}

        if cfg.recordings_dir.is_dir():
            for f in sorted(cfg.recordings_dir.iterdir()):
                if not f.is_file() or f.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                if f.stem in known or not valid_id(f.stem):
                    continue
                create(cfg, f.stem,
                       title=_title_from_stem(f.stem),
                       created_at=_created_from(f.stem, f),
                       audio=f.name,
                       source="pocket" if f.stem.startswith("pocket-") else "pc",
                       duration_s=_duration(f))
                known.add(f.stem)
                log.info("adopted recording %s as a note", f.name)

        if cfg.transcripts_dir.is_dir():
            for t in sorted(cfg.transcripts_dir.glob("*.txt")):
                if t.stem in known or not valid_id(t.stem):
                    continue
                create(cfg, t.stem,
                       title=_title_from_stem(t.stem),
                       created_at=_created_from(t.stem, t))
                known.add(t.stem)
                log.info("adopted transcript %s as a note", t.name)


# --- folders -----------------------------------------------------------------

def clean_folder(name) -> str:
    """Normalise a folder name; "" if it isn't usable."""
    name = " ".join(str(name or "").split())[:60]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""
    return name


def _saved_folders(cfg: Config) -> list[str]:
    p = cfg.notes_dir / FOLDERS_FILE
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [n for n in (clean_folder(x) for x in lines) if n]


def _write_folders(cfg: Config, names: list[str]) -> None:
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.notes_dir / FOLDERS_FILE
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(names), encoding="utf-8")
    os.replace(tmp, p)


def add_folder(cfg: Config, name: str) -> None:
    with _lock:
        names = _saved_folders(cfg)
        if name not in names:
            names.append(name)
            _write_folders(cfg, names)


def remove_folder(cfg: Config, name: str) -> None:
    """Drop the folder; its meetings stay, they just leave the folder."""
    with _lock:
        _write_folders(cfg, [n for n in _saved_folders(cfg) if n != name])
        for note in _all_raw(cfg):
            if note.get("folder") == name:
                note["folder"] = ""
                _write(cfg, note)


def folders(cfg: Config) -> list[dict]:
    with _lock:
        counts: dict[str, int] = {}
        for note in _all_raw(cfg):
            f = note.get("folder")
            if f:
                counts[f] = counts.get(f, 0) + 1
        names = _saved_folders(cfg)
        names += [f for f in counts if f not in names]
        return [{"name": f, "count": counts.get(f, 0)}
                for f in sorted(names, key=str.lower)]
