"""
backup.py — a copy of your meetings in a folder you choose.

Point it at your Google Drive for desktop folder (or OneDrive, Dropbox, a
USB drive, a network share) and that app does the uploading. Minutara keeps
the copy current: a few seconds after anything changes, and every 15
minutes regardless. In the backup folder:

    notes/            one .json per meeting: your notes, AI notes, title, folder…
    transcripts/      the transcripts
    recordings/       the audio, only if "Include recordings" is on
    Meeting notes/    every meeting as a readable document, to open on any device

Settings and API keys (config.json) are never copied. Deleting a meeting in
Minutara deletes it from the backup too (cloud drives keep a trash for a
while); files you put in the backup folder yourself are left alone, since
only files listed in the backup's own manifest are ever removed.

Restore copies back whatever is missing here: on a new laptop, point
Minutara at the same folder and press Restore.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import export, notes
from .log import get

log = get("minutara.backup")

MANIFEST = ".minutara-backup.json"
DOCS = "Meeting notes"
PERIOD_S = 15 * 60


class BackupError(RuntimeError):
    """A problem worth showing as-is."""


def target(cfg) -> Path | None:
    raw = str(getattr(cfg, "backup_dir", "") or "").strip()
    return Path(raw).expanduser() if raw else None


def check_target(cfg, path: Path) -> None:
    """Refuse folders that would back up into themselves, or that aren't there."""
    p = path.resolve()
    own = [cfg.notes_dir, cfg.transcripts_dir, cfg.recordings_dir, cfg.cache_dir, cfg.model_dir]
    for d in own:
        d = d.resolve()
        if p == d or d in p.parents:
            raise BackupError("Pick a folder outside Minutara's own data folders.")
    app_root = cfg.notes_dir.resolve().parent
    if p == app_root:
        raise BackupError("Pick a folder outside Minutara's own folder.")
    anchor = Path(p.anchor) if p.anchor else None
    if anchor and not anchor.exists():
        raise BackupError(f"{anchor} isn't available. If it's Google Drive, is Google Drive running?")
    if not p.parent.exists():
        raise BackupError(f"{p.parent} doesn't exist. If it's a cloud drive, is its app running?")


_suggested: tuple[float, list] = (0.0, [])


def suggestions() -> list[dict]:
    """Likely backup places on this computer: Google Drive, OneDrive, Dropbox.
    Looked up at most once a minute (a disconnected drive can be slow to check)."""
    global _suggested
    if time.monotonic() - _suggested[0] < 60:
        return _suggested[1]
    out = _find_places()
    _suggested = (time.monotonic(), out)
    return out


def _find_places() -> list[dict]:
    out, seen = [], set()

    def add(label, base: Path):
        try:
            if base.is_dir() and str(base).lower() not in seen:
                seen.add(str(base).lower())
                out.append({"label": label, "path": str(base / "Minutara backup")})
        except OSError:
            pass

    home = Path.home()
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase[3:]:   # Google Drive for desktop mounts a drive (G: by default)
            for name in ("My Drive", "Mi unidad", "Mon Drive", "Meine Ablage"):
                add("Google Drive", Path(f"{letter}:\\") / name)
    for p in (home / "My Drive", home / "Google Drive" / "My Drive", home / "Google Drive"):
        add("Google Drive", p)
    for env in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
        if os.environ.get(env):
            add("OneDrive (work)" if env == "OneDriveCommercial" else "OneDrive", Path(os.environ[env]))
    add("Dropbox", home / "Dropbox")
    return out


class Backup:
    """One worker; changes are coalesced so a burst of edits is one pass."""

    def __init__(self, cfg, skip=None):
        self.cfg = cfg
        self.skip = skip or (lambda: "")   # note id being recorded right now (its audio isn't final)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.due = 0.0
        self.running = False
        self.last: dict = {}
        self._stop = threading.Event()

    # -- scheduling
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="backup").start()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()

    def schedule(self, delay: float = 8.0) -> None:
        if target(self.cfg) is None:
            return
        with self.lock:
            when = time.monotonic() + delay
            if not self.due or when < self.due:
                self.due = when
        self.wake.set()

    def _loop(self) -> None:
        next_full = time.monotonic() + 60   # a first pass shortly after start
        while not self._stop.is_set():
            self.wake.wait(timeout=5)
            self.wake.clear()
            now = time.monotonic()
            with self.lock:
                due = self.due and now >= self.due
            if not (due or now >= next_full):
                continue
            with self.lock:
                self.due = 0.0
            next_full = now + PERIOD_S
            if target(self.cfg) is not None:
                try:
                    self.run()
                except Exception as e:   # recorded in self.last; never fatal
                    log.warning("backup failed: %s", e)

    # -- the work
    def run(self) -> dict:
        dest = target(self.cfg)
        if dest is None:
            raise BackupError("Choose a backup folder first.")
        with self.lock:
            if self.running:
                return {"ok": True, "message": "A backup is already running."}
            self.running = True
        started = time.time()
        try:
            check_target(self.cfg, dest)
            stats = self._sync(dest)
            msg = (f"Backed up: {stats['copied']} file{'s' if stats['copied'] != 1 else ''} updated, "
                   f"{stats['removed']} removed, {stats['meetings']} meetings in the backup.")
            self.last = {"ok": True, "at": datetime.now().isoformat(timespec="seconds"),
                         "message": msg, **stats}
            if stats["copied"] or stats["removed"]:
                log.info("backup to %s: %s (%.1fs)", dest, msg, time.time() - started)
            return self.last
        except Exception as e:
            self.last = {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
                         "message": str(e)}
            raise
        finally:
            with self.lock:
                self.running = False

    def _sync(self, dest: Path) -> dict:
        cfg = self.cfg
        dest.mkdir(parents=True, exist_ok=True)
        man = _load_manifest(dest)
        files: dict = man.setdefault("files", {})
        docs: dict = man.setdefault("docs", {})
        live = self.skip() or ""

        sources: dict[str, Path] = {}
        for p in cfg.notes_dir.glob("*.json"):
            sources[f"notes/{p.name}"] = p
        folders = cfg.notes_dir / notes.FOLDERS_FILE
        if folders.exists():
            sources[f"notes/{folders.name}"] = folders
        for p in cfg.transcripts_dir.glob("*.txt"):
            sources[f"transcripts/{p.name}"] = p
        if getattr(cfg, "backup_audio", False) and cfg.recordings_dir.is_dir():
            for p in cfg.recordings_dir.iterdir():
                if p.is_file() and p.suffix.lower() in notes.AUDIO_SUFFIXES and p.stem != live:
                    sources[f"recordings/{p.name}"] = p

        copied, removed, changed_notes = 0, 0, set()
        for rel, src in sources.items():
            try:
                st = src.stat()
            except OSError:
                continue
            sig = [st.st_size, st.st_mtime_ns]
            out = dest / rel
            if files.get(rel) == sig and out.exists():
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            shutil.copyfile(src, tmp)
            os.replace(tmp, out)
            files[rel] = sig
            copied += 1
            if rel.startswith(("notes/", "transcripts/")) and src.suffix in (".json", ".txt"):
                changed_notes.add(src.stem)

        # Gone here (meeting deleted): gone from the backup too. Recordings
        # stay if you only turned "Include recordings" off.
        for rel in list(files):
            if rel in sources:
                continue
            if rel.startswith("recordings/") and (cfg.recordings_dir / rel.split("/", 1)[1]).exists():
                continue
            (dest / rel).unlink(missing_ok=True)
            del files[rel]
            removed += 1

        # Readable documents, rewritten for meetings that changed.
        fmt = cfg.export_format if cfg.export_format in export.FORMATS else "txt"
        current = {n["id"]: n for n in notes.all_notes(cfg)}
        taken: set[str] = {v.lower() for k, v in docs.items()
                           if k in current and k not in changed_notes and v.endswith("." + fmt)}
        for nid, n in current.items():
            if (nid in docs and nid not in changed_notes and docs[nid].endswith("." + fmt)
                    and (dest / DOCS / docs[nid]).exists()):
                continue
            if not (n.get("summary") or n.get("notes") or notes.transcript_path(cfg, n).exists()):
                continue
            name = export.filename(n, fmt)
            base, ext = name.rsplit(".", 1)
            i = 2
            while name.lower() in taken:
                name = f"{base} ({i}).{ext}"
                i += 1
            taken.add(name.lower())
            (dest / DOCS).mkdir(parents=True, exist_ok=True)
            old = docs.get(nid)
            (dest / DOCS / name).write_text(export.document(cfg, n, export.PARTS, fmt), encoding="utf-8")
            if old and old != name:
                (dest / DOCS / old).unlink(missing_ok=True)
            docs[nid] = name
        for nid in [k for k in docs if k not in current]:
            (dest / DOCS / docs.pop(nid)).unlink(missing_ok=True)
            removed += 1

        man["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_manifest(dest, man)
        return {"copied": copied, "removed": removed, "meetings": len(current),
                "size_mb": round(sum(_size(dest / r) for r in files) / 1e6, 1)}

    def restore(self) -> dict:
        """Copy back anything in the backup that's missing here. Never overwrites."""
        cfg = self.cfg
        src = target(cfg)
        if src is None:
            raise BackupError("Choose the backup folder to restore from first.")
        if not (src / "notes").is_dir():
            raise BackupError(f"No Minutara backup in {src}.")
        counts = {"notes": 0, "transcripts": 0, "recordings": 0}
        places = {"notes": cfg.notes_dir, "transcripts": cfg.transcripts_dir,
                  "recordings": cfg.recordings_dir}
        for kind, here in places.items():
            folder = src / kind
            if not folder.is_dir():
                continue
            here.mkdir(parents=True, exist_ok=True)
            for f in folder.iterdir():
                if not f.is_file() or f.name.endswith(".part"):
                    continue
                if kind == "notes" and f.name == notes.FOLDERS_FILE:
                    _merge_folders(cfg, f)
                    continue
                out = here / f.name
                if out.exists():
                    continue
                tmp = out.with_name(out.name + ".part")
                shutil.copyfile(f, tmp)
                os.replace(tmp, out)
                counts[kind] += 1
        msg = (f"Restored {counts['notes']} meeting{'s' if counts['notes'] != 1 else ''}, "
               f"{counts['transcripts']} transcripts and {counts['recordings']} recordings. "
               "Nothing that was already here was changed.")
        log.info("restore from %s: %s", src, msg)
        return {"ok": True, "message": msg, **counts}

    def status(self) -> dict:
        dest = target(self.cfg)
        return {"on": dest is not None, "dir": str(dest or ""), "running": self.running,
                "audio": bool(getattr(self.cfg, "backup_audio", False)), "last": self.last}


current: Backup | None = None   # the app's backup, once it's running


def changed() -> None:
    """Something worth backing up changed (a note, transcript, recording)."""
    if current is not None:
        current.schedule()


def _merge_folders(cfg, f: Path) -> None:
    for name in f.read_text(encoding="utf-8", errors="replace").splitlines():
        name = notes.clean_folder(name)
        if name:
            notes.add_folder(cfg, name)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _load_manifest(dest: Path) -> dict:
    try:
        data = json.loads((dest / MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_manifest(dest: Path, man: dict) -> None:
    p = dest / MANIFEST
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(man, indent=1), encoding="utf-8")
    os.replace(tmp, p)
