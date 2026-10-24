"""Tests for the backup folder (Google Drive / OneDrive / Dropbox) and restore."""
import json
import os
import shutil
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-backup-")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_for(pred, timeout=25.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


HOME = TMP / "home"
(HOME / "My Drive").mkdir(parents=True)
(HOME / "Dropbox").mkdir()
os.environ["HOME"] = str(HOME)
os.environ["OneDrive"] = str(HOME / "OneDrive - Contoso")
(HOME / "OneDrive - Contoso").mkdir()

from minutara import backup, config as config_mod, notes  # noqa: E402
from minutara import server as srv  # noqa: E402

cfg = config_mod.load()
check("defaults: backup off, recordings excluded", cfg.backup_dir == "" and cfg.backup_audio is False)

# ---- where to back up -------------------------------------------------------------
sug = backup.suggestions()
labels = [s["label"] for s in sug]
check("suggestions: Google Drive, OneDrive, Dropbox found", {"Google Drive", "OneDrive", "Dropbox"} <= set(labels), str(sug))
check("suggestions: a Minutara backup subfolder", all(s["path"].endswith("Minutara backup") for s in sug))
for bad, why in ((cfg.notes_dir / "x", "inside notes"), (cfg.recordings_dir, "recordings"),
                 (TMP, "the app folder"), (TMP / "nope" / "deeper" / "x", "missing parent")):
    try:
        backup.check_target(cfg, bad)
        check(f"refuses {why}", False)
    except backup.BackupError as e:
        check(f"refuses {why}", True, str(e))
backup.check_target(cfg, HOME / "My Drive" / "Minutara backup")
check("accepts a cloud folder", True)

# ---- some meetings ------------------------------------------------------------------------
def wav(path, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\1\0" * 32000 * seconds)


n1 = notes.create(cfg, "20261020-1000-weekly", title="Weekly sync", created_at="2026-10-20T10:00:00+00:00",
                  audio="20261020-1000-weekly.wav", notes="my notes", summary="# Summary\n- ok", folder="Work")
wav(cfg.recordings_dir / n1["audio"])
(cfg.transcripts_dir / f"{n1['id']}.txt").write_text("[00:00] Me: hello\n", encoding="utf-8")
n2 = notes.create(cfg, "20261021-0900-standup", title="Standup", created_at="2026-10-21T09:00:00+00:00",
                  audio="20261021-0900-standup.wav")
wav(cfg.recordings_dir / n2["audio"])
notes.add_folder(cfg, "Work")
notes.add_folder(cfg, "Clients")

DEST = HOME / "My Drive" / "Minutara backup"
cfg.backup_dir = str(DEST)
bk = backup.Backup(cfg)
r = bk.run()
files = sorted(str(p.relative_to(DEST)) for p in DEST.rglob("*") if p.is_file())
check("backup: notes, transcripts, folders, readable docs", {
    "notes/20261020-1000-weekly.json", "notes/20261021-0900-standup.json", "notes/_folders.txt",
    "transcripts/20261020-1000-weekly.txt", "Meeting notes/2026-10-20 1000 Weekly sync.txt"} <= set(files), str(files))
check("backup: no recordings unless asked", not any(f.startswith("recordings/") for f in files))
check("backup: never settings or keys", not any("config" in f for f in files))
check("backup: meeting with nothing to read gets no document", not any("Standup" in f for f in files if f.startswith("Meeting")))
doc = (DEST / "Meeting notes" / "2026-10-20 1000 Weekly sync.txt").read_text()
check("backup: readable document has AI notes, notes, transcript", "Summary\n-------" in doc and "my notes" in doc and "Me: hello" in doc)
check("backup: result message", r["ok"] and r["meetings"] == 2 and r["copied"] == 4, str(r))
r = bk.run()
check("backup: nothing changed, nothing copied", r["copied"] == 0 and r["removed"] == 0, str(r))

time.sleep(0.02)
notes.update(cfg, n1["id"], title="Weekly sync (renamed)", notes="more notes")
r = bk.run()
check("backup: edited meeting copied again", r["copied"] == 1, str(r))
docs = sorted(p.name for p in (DEST / "Meeting notes").iterdir())
check("backup: renamed meeting's document replaced", docs == ["2026-10-20 1000 Weekly sync (renamed).txt"], str(docs))

(DEST / "my own file.txt").write_text("keep me")
cfg.backup_audio = True
r = bk.run()
check("backup: recordings when asked", (DEST / "recordings" / n1["audio"]).exists() and (DEST / "recordings" / n2["audio"]).exists())
cfg.backup_audio = False
bk.run()
check("backup: turning recordings off keeps the copies", (DEST / "recordings" / n1["audio"]).exists())
(cfg.recordings_dir / n2["audio"]).unlink()
notes.delete(cfg, n2["id"])
r = bk.run()
check("backup: deleted meeting leaves the backup", not (DEST / "notes" / f"{n2['id']}.json").exists()
      and not (DEST / "recordings" / n2["audio"]).exists() and r["removed"] >= 2, str(r))
check("backup: your own files are left alone", (DEST / "my own file.txt").read_text() == "keep me")
cfg.export_format = "md"
bk.run()
check("backup: documents follow the file type", sorted(p.name for p in (DEST / "Meeting notes").iterdir())
      == ["2026-10-20 1000 Weekly sync (renamed).md"])
cfg.export_format = "txt"

live = Backup2 = backup.Backup(cfg, skip=lambda: "20261022-live")
wav(cfg.recordings_dir / "20261022-live.wav")
cfg.backup_audio = True
live.run()
check("backup: the recording in progress is skipped", not (DEST / "recordings" / "20261022-live.wav").exists())
cfg.backup_audio = False

# ---- restore on a "new laptop" -----------------------------------------------------------------
NEW = TMP / "newlaptop"
for d in ("notes", "transcripts", "recordings"):
    (NEW / d).mkdir(parents=True)
(NEW / "notes" / f"{n1['id']}.json").write_text(json.dumps({"id": n1["id"], "title": "Local copy wins"}))


class C:   # just the paths restore needs
    notes_dir = NEW / "notes"; transcripts_dir = NEW / "transcripts"; recordings_dir = NEW / "recordings"
    cache_dir = NEW / "cache"; model_dir = NEW / "models"
    backup_dir = str(DEST); backup_audio = True; export_format = "txt"


r = backup.Backup(C).restore()
check("restore: copies back what's missing", (NEW / "transcripts" / f"{n1['id']}.txt").exists()
      and (NEW / "recordings" / n1["audio"]).exists(), str(r))
check("restore: never overwrites", json.loads((NEW / "notes" / f"{n1['id']}.json").read_text())["title"] == "Local copy wins")
check("restore: folders merged", "Clients" in (NEW / "notes" / "_folders.txt").read_text())
check("restore: message", "Nothing that was already here was changed" in r["message"])
C.backup_dir = str(TMP / "home")
try:
    backup.Backup(C).restore()
    check("restore: not a backup folder", False)
except backup.BackupError as e:
    check("restore: not a backup folder", "No Minutara backup" in str(e))

# ---- through the app ------------------------------------------------------------------------------
shutil.rmtree(DEST)
cfg.backup_dir = ""
app, rec = srv.create_app(cfg)
c = app.test_client()
H = {"X-Token": cfg.server_token}
b = c.get("/api/backup", headers=H).get_json()
check("api: off, with suggestions", b["on"] is False and len(b["suggestions"]) >= 3)
r = c.post("/api/config", headers=H, json={"backup_dir": str(cfg.notes_dir)})
check("api: bad folder refused", r.status_code == 400 and "outside" in r.get_json()["message"] and cfg.backup_dir == "")
r = c.post("/api/config", headers=H, json={"backup_dir": str(DEST)})
check("api: folder saved", r.status_code == 200 and cfg.backup_dir == str(DEST)
      and json.loads((TMP / "config.json").read_text())["backup_dir"] == str(DEST))
check("api: first backup soon after choosing", wait_for(lambda: (DEST / "notes" / f"{n1['id']}.json").exists(), 15))
c.put(f"/api/notes/{n1['id']}", headers=H, json={"notes": "typed in the app"})
check("api: edits reach the backup by themselves",
      wait_for(lambda: "typed in the app" in (DEST / "notes" / f"{n1['id']}.json").read_text(), 25))
r = c.post("/api/backup/run", headers=H).get_json()
check("api: back up now", r["ok"] and "Backed up" in r["message"], str(r))
b = c.get("/api/backup", headers=H).get_json()
check("api: status shows the last backup", b["on"] and b["last"]["ok"] and b["dir"] == str(DEST))
r = c.post("/api/backup/restore", headers=H).get_json()
check("api: restore with nothing missing", r["ok"] and r["notes"] == 0, str(r))
c.post("/api/config", headers=H, json={"backup_dir": ""})
check("api: turned off", cfg.backup_dir == "" and c.post("/api/backup/run", headers=H).status_code == 400)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
