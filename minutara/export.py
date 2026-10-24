"""
export.py — meetings as readable documents.

One document per meeting, with any of its parts: AI notes, your notes and
the transcript, under a heading with the date, length, folder and
attendees. As plain text (.txt, opens anywhere) or Markdown (.md, for
Obsidian, Notion and the like).

Used three ways:
  * per meeting: download or copy one part, or everything together;
  * Export all: every meeting (or one folder's) as a zip, optionally with
    the recordings;
  * auto-save: each meeting's document kept up to date in a folder of your
    choosing (Settings → Export), rewritten whenever its AI notes, your
    notes or its transcript change.
"""

from __future__ import annotations

import re
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import notes
from .log import get

log = get("minutara.export")

PARTS = ("summary", "notes", "transcript")
PART_TITLES = {"summary": "AI notes", "notes": "My notes", "transcript": "Transcript"}
FORMATS = ("txt", "md")


def _when(n: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(n.get("created_at", ""))[:19])
    except ValueError:
        return None


def _duration(seconds) -> str:
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    h, m = divmod(s // 60, 60)
    return f"{h} h {m} min" if h else f"{max(m, 1)} min"


def filename(n: dict, fmt: str = "txt", suffix: str = "") -> str:
    """'2026-10-18 1410 Weekly sync.txt': sorts by date in any file list."""
    when = _when(n)
    stamp = when.strftime("%Y-%m-%d %H%M") if when else ""
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", n.get("title") or "Untitled meeting").strip(" .")
    title = re.sub(r"\s+", " ", title)[:80] or "Untitled meeting"
    base = f"{stamp} {title}".strip() + (f" - {suffix}" if suffix else "")
    return f"{base}.{fmt if fmt in FORMATS else 'txt'}"


def _plain(markdown: str) -> str:
    """Markdown → readable plain text: headings underlined, markers dropped."""
    out = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            text = re.sub(r"[*`]", "", m.group(2)).strip()
            out += ["", text.upper() if len(m.group(1)) == 1 else text,
                    ("=" if len(m.group(1)) == 1 else "-") * len(text)]
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        line = re.sub(r"^(\s*)[*+] ", r"\1- ", line)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip("\n")


def part_text(cfg, n: dict, part: str) -> str:
    """One part of a meeting, as stored (Markdown for AI notes)."""
    if part == "summary":
        return (n.get("summary") or "").strip()
    if part == "notes":
        return (n.get("notes") or "").strip()
    if part == "transcript":
        t = notes.transcript_path(cfg, n)
        return t.read_text(encoding="utf-8", errors="replace").strip() if t and t.exists() else ""
    return ""


def document(cfg, n: dict, parts=PARTS, fmt: str = "txt") -> str:
    """A meeting as one readable document with the chosen parts."""
    parts = [p for p in PARTS if p in parts]
    when = _when(n)
    facts = []
    if when:
        facts.append(f"{when:%A} {when.day} {when:%B %Y}, {when:%H:%M}")
    if _duration(n.get("duration_s")):
        facts.append(_duration(n.get("duration_s")))
    if n.get("folder"):
        facts.append(f"Folder: {n['folder']}")
    attendees = ", ".join(n.get("attendees") or [])

    md = [f"# {n.get('title') or 'Untitled meeting'}", ""]
    if facts:
        md.append(" · ".join(facts))
    if attendees:
        md.append(f"Attendees: {attendees}")
    for part in parts:
        text = part_text(cfg, n, part)
        md += ["", f"## {PART_TITLES[part]}", ""]
        if not text:
            md.append("(none)")
        elif part == "summary":
            # AI notes use their own headings; nest them under this section.
            md.append(re.sub(r"^(#{1,4}) ", r"##\1 ", text, flags=re.M))
        else:
            md.append(text)
    body = "\n".join(md).strip() + "\n"
    return body if fmt == "md" else _plain(body) + "\n"


def single_part(cfg, n: dict, part: str, fmt: str = "txt") -> str:
    """Just one part, without the meeting heading (for Download on a tab)."""
    text = part_text(cfg, n, part)
    if part == "summary" and fmt != "md":
        return _plain(text) + "\n"
    return text + "\n"


def export_all(cfg, parts=PARTS, fmt: str = "txt", audio: bool = False,
               folder: str = "") -> Path:
    """Every meeting (or one folder's) as a zip; returns its path."""
    out_dir = cfg.cache_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.zip"):   # only the latest is kept
        old.unlink(missing_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    zpath = out_dir / f"Minutara export {stamp}{' - ' + folder if folder else ''}.zip"
    used: set[str] = set()
    count = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for n in notes.all_notes(cfg):
            if folder and n.get("folder") != folder:
                continue
            name = filename(n, fmt)
            base, ext = name.rsplit(".", 1)
            i = 2
            while name.lower() in used:
                name = f"{base} ({i}).{ext}"
                i += 1
            used.add(name.lower())
            sub = f"{n['folder']}/" if n.get("folder") and not folder else ""
            z.writestr(sub + name, document(cfg, n, parts, fmt))
            if audio:
                a = notes.audio_path(cfg, n)
                if a and a.exists():
                    z.write(a, sub + name.rsplit(".", 1)[0] + a.suffix.lower(),
                            compress_type=zipfile.ZIP_STORED)   # audio doesn't compress
            count += 1
    log.info("exported %d meeting(s) to %s", count, zpath.name)
    return zpath


# --- auto-save ----------------------------------------------------------------

_timers: dict[str, threading.Timer] = {}
_lock = threading.Lock()


def save_copy(cfg, note_id: str) -> Path | None:
    """Write (or rewrite) one meeting's document in the auto-save folder."""
    n = notes.load(cfg, note_id)
    if not n:
        return None
    if not (n.get("summary") or n.get("notes") or notes.transcript_path(cfg, n).exists()):
        return None   # nothing to read yet
    out_dir = cfg.export_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = cfg.export_format if cfg.export_format in FORMATS else "txt"
    prev = n.get("export_file")
    # Two meetings can share a date, time and title: give the second "(2)".
    taken = {(o.get("export_file") or "").lower() for o in notes._all_raw(cfg) if o.get("id") != note_id}
    name = filename(n, fmt)
    base, ext = name.rsplit(".", 1)
    i = 2
    while name.lower() in taken:
        name = f"{base} ({i}).{ext}"
        i += 1
    target = out_dir / name
    if prev and prev != target.name and (out_dir / prev).exists():
        (out_dir / prev).unlink(missing_ok=True)   # renamed meeting: replace the old file
    target.write_text(document(cfg, n, PARTS, fmt), encoding="utf-8")
    if prev != target.name:
        notes.update(cfg, note_id, export_file=target.name)
    return target


def schedule(cfg, note_id: str, delay: float = 3.0) -> None:
    """Auto-save soon (typing saves every second; write once it settles)."""
    if not getattr(cfg, "auto_export", False) or not note_id:
        return

    def run():
        with _lock:
            _timers.pop(note_id, None)
        try:
            save_copy(cfg, note_id)
        except Exception as e:
            log.warning("auto-save of %s failed: %s", note_id, e)

    with _lock:
        t = _timers.pop(note_id, None)
        if t:
            t.cancel()
        t = threading.Timer(delay, run)
        t.daemon = True
        _timers[note_id] = t
        t.start()


def save_all(cfg) -> int:
    """Write every meeting to the auto-save folder now."""
    count = 0
    for n in notes.all_notes(cfg):
        try:
            if save_copy(cfg, n["id"]):
                count += 1
        except Exception as e:
            log.warning("saving %s failed: %s", n.get("id"), e)
    log.info("saved %d meeting document(s) to %s", count, cfg.export_dir)
    return count


def forget(cfg, n: dict) -> None:
    """A deleted meeting's auto-saved document goes with it."""
    name = (n or {}).get("export_file")
    if name:
        (cfg.export_dir / name).unlink(missing_ok=True)


def wait_idle(timeout: float = 5.0) -> None:
    """For tests: let pending auto-saves finish."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _timers:
                return
        time.sleep(0.05)
