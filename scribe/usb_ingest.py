"""
usb_ingest.py — pull recordings straight off the Pocket over USB-C.

No API, no servers, no protocol work. Once "USB Mode" (Settings -> Pocket Web
Sync) is toggled on in the phone app and the device is plugged in, Pocket
presents itself as a plain drive labelled POCKET with a RECORD folder on it.
We read that folder like any other directory.

Two things worth knowing before you rely on this:

  * The exact file layout inside RECORD is UNVERIFIED. Pocket's docs confirm
    the drive name and the RECORD folder, but not the file naming or the audio
    container. AUDIO_SUFFIXES below is a guess covering the likely candidates.
    Plug the device in once, look at what's actually there, and narrow it.
  * The device must be in USB Mode, which is toggled from the phone app.
    Opening the phone app is also what triggers cloud sync of unsynced
    recordings, so if keeping audio off their servers matters, look for the
    auto-transcription setting first and test with a throwaway recording.

We never delete from the device unless you explicitly opt in — a copy bug that
also deletes the only copy is not a bug you get to fix.
"""

from __future__ import annotations

import ctypes
import json
import shutil
import string
import time
from dataclasses import dataclass
from pathlib import Path

# Best guess — narrow this once you've seen the real drive.
AUDIO_SUFFIXES = {".wav", ".opus", ".m4a", ".mp3", ".flac", ".ogg"}


@dataclass
class IngestResult:
    copied: list[Path]
    skipped: int


def _volume_label(drive: str) -> str:
    """Read a Windows volume label, e.g. 'POCKET'. Empty string on failure."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(f"{drive}:\\"),
            buf, ctypes.sizeof(buf),
            None, None, None, None, 0,
        )
        return buf.value if ok else ""
    except (AttributeError, OSError):
        return ""  # not Windows, or drive not ready


def find_pocket_drive(volume_label: str = "POCKET", record_dir: str = "RECORD") -> Path | None:
    """
    Locate the device. Matches on volume label first, then falls back to
    'any drive with a RECORD folder' in case they rename the volume.
    """
    fallback = None
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        if not root.exists():
            continue
        records = root / record_dir
        if _volume_label(letter).upper() == volume_label.upper() and records.is_dir():
            return records
        if records.is_dir() and fallback is None:
            fallback = records
    return fallback


def _load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _key(f: Path) -> str:
    """Identify a file by name + size + mtime so re-copies are idempotent."""
    st = f.stat()
    return f"{f.name}:{st.st_size}:{int(st.st_mtime)}"


def list_new(record_dir: Path, manifest_path: Path) -> list[Path]:
    seen = _load_manifest(manifest_path)
    out = []
    for f in sorted(record_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if _key(f) not in seen:
            out.append(f)
    return out


def ingest(
    record_dir: Path,
    dest_dir: Path,
    manifest_path: Path,
    delete_from_device: bool = False,
) -> IngestResult:
    """Copy anything new off the device. Returns the local paths."""
    seen = _load_manifest(manifest_path)
    dest_dir.mkdir(parents=True, exist_ok=True)

    new_files = list_new(record_dir, manifest_path)
    copied: list[Path] = []

    for src in new_files:
        dest = dest_dir / f"pocket-{src.name}"
        n = 1
        while dest.exists():
            dest = dest_dir / f"pocket-{src.stem}-{n}{src.suffix}"
            n += 1

        shutil.copy2(src, dest)          # copy2 keeps the original timestamp
        seen[_key(src)] = {
            "local": str(dest.name),
            "ingested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        copied.append(dest)

        if delete_from_device:
            # Only after a verified byte-for-byte size match.
            if dest.stat().st_size == src.stat().st_size:
                src.unlink()

    manifest_path.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    return IngestResult(copied=copied, skipped=len(seen) - len(copied))


def watch(volume_label: str, record_dir_name: str, poll_s: float = 3.0):
    """Block until the Pocket drive shows up, then yield its RECORD path."""
    print(f"Waiting for the {volume_label} drive — plug in Pocket with USB Mode on.")
    while True:
        found = find_pocket_drive(volume_label, record_dir_name)
        if found:
            print(f"Found {found}")
            return found
        time.sleep(poll_s)
