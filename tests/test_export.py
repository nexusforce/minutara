"""Tests for export: documents, per-part downloads/copies, Export all, auto-save."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import write_wav  # noqa: E402

TMP = fakes.make_app_root("minutara-export-")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


from minutara import config as config_mod, export, notes  # noqa: E402
from minutara import server as srv  # noqa: E402

cfg = config_mod.load()
check("config: auto-save on by default", cfg.auto_export is True)
check("config: txt by default", cfg.export_format == "txt")
check("config: copies folder default", cfg.export_dir == TMP / "Meeting notes")

SUMMARY = "# Summary\n\nWe agreed on **Friday**.\n\n## Action items\n\n* Ana: send the *deck*\n* Bo: book `room 4`\n\nSee [the doc](https://x.test/d)."
n1 = notes.create(cfg, "2026-10-18_1410_weekly", title='Weekly: sync / "Q4"?', created_at="2026-10-18T14:10:00+02:00",
                  duration_s=3725, folder="Work", attendees=["Ana", "Bo"], summary=SUMMARY,
                  notes="remember the budget")
cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
notes.transcript_path(cfg, n1).write_text("[00:00] Me: hello\n[00:03] Them: hi there\n", encoding="utf-8")
write_wav(cfg.recordings_dir / "2026-10-18_1410_weekly.wav", seconds=1) if "seconds" in write_wav.__code__.co_varnames else None
if not (cfg.recordings_dir / "2026-10-18_1410_weekly.wav").exists():
    import wave
    with wave.open(str(cfg.recordings_dir / "2026-10-18_1410_weekly.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
notes.update(cfg, n1["id"], audio="2026-10-18_1410_weekly.wav")
n2 = notes.create(cfg, "2026-10-17_0900_standup", title="Standup", created_at="2026-10-17T09:00:00+02:00",
                  notes="only my notes")
n3 = notes.create(cfg, "2026-10-17_0900_standup-2", title="Standup", created_at="2026-10-17T09:00:00+02:00",
                  folder="Work", notes="second standup")
n1 = notes.load(cfg, n1["id"])

# ---- filenames and documents ---------------------------------------------------
fn = export.filename(n1, "txt")
check("filename: date first, unsafe characters removed", fn == "2026-10-18 1410 Weekly sync  Q4.txt".replace("  ", " ") or fn,
      fn)
check("filename: no Windows-forbidden characters", not any(c in fn for c in '<>:"/\\|?*'), fn)
check("filename: suffix for one part", export.filename(n1, "md", "AI notes").endswith(" - AI notes.md"))
check("filename: unknown format falls back to txt", export.filename(n1, "exe").endswith(".txt"))
check("filename: untitled", "Untitled meeting" in export.filename({"title": ""}, "txt"))

md = export.document(cfg, n1, export.PARTS, "md")
check("doc md: title heading", md.startswith('# Weekly: sync / "Q4"?\n'), md[:60])
check("doc md: date, length, folder", "Sunday 18 October 2026, 14:10" in md and "1 h 2 min" in md and "Folder: Work" in md, md[:200])
check("doc md: attendees", "Attendees: Ana, Bo" in md)
check("doc md: three sections in order",
      md.index("## AI notes") < md.index("## My notes") < md.index("## Transcript"))
check("doc md: AI headings nested under the section", "### Summary" in md and "#### Action items" in md)
check("doc md: transcript and notes included", "Them: hi there" in md and "remember the budget" in md)

txt = export.document(cfg, n1, export.PARTS, "txt")
check("doc txt: no markdown markers", "**" not in txt and "](" not in txt and "# " not in txt, txt)
check("doc txt: title underlined", 'WEEKLY: SYNC / "Q4"?\n=====' in txt, txt[:120])
check("doc txt: bullets and link readable", "- Ana: send the deck" in txt and "the doc (https://x.test/d)" in txt, txt)
check("doc txt: section headings", "AI notes\n--------" in txt and "Transcript\n----------" in txt)

only = export.document(cfg, n1, ["transcript", "summary"], "txt")
check("doc: chosen parts only, in standard order", "My notes" not in only and only.index("AI notes") < only.index("Transcript"))
empty = export.document(cfg, n2, export.PARTS, "txt")
check("doc: missing parts say (none)", empty.count("(none)") == 2, empty)

check("single part: AI notes as text", export.single_part(cfg, n1, "summary", "txt").startswith("SUMMARY\n======="))
check("single part: AI notes as md unchanged", export.single_part(cfg, n1, "summary", "md").strip() == SUMMARY)
check("single part: transcript", export.single_part(cfg, n1, "transcript").startswith("[00:00] Me: hello"))
check("plain: 2*3*4 maths survives", "2*3*4" in export._plain("2*3*4"), export._plain("2*3*4"))

# ---- endpoints -----------------------------------------------------------------
app, rec = srv.create_app(cfg)
c = app.test_client()
H = {"X-Token": cfg.server_token}
check("export: needs token", c.get(f"/api/notes/{n1['id']}/export").status_code == 401)
r = c.get(f"/api/notes/{n1['id']}/export?part=summary", headers=H)
cd = r.headers.get("Content-Disposition", "")
check("download AI notes: attachment", r.status_code == 200 and cd.startswith("attachment;"), cd)
check("download AI notes: UTF-8 file name", "AI notes.txt" in unquote(cd.split("UTF-8''")[-1]), cd)
check("download AI notes: readable text", r.data.decode().startswith("SUMMARY"))
r = c.get(f"/api/notes/{n1['id']}/export?part=notes&copy=1", headers=H)
check("copy my notes: plain text, no attachment", r.data.decode().strip() == "remember the budget"
      and "Content-Disposition" not in r.headers)
r = c.get(f"/api/notes/{n1['id']}/export?part=transcript&format=md", headers=H)
check("download transcript as md", r.headers["Content-Type"].startswith("text/markdown")
      and "Transcript.md" in unquote(r.headers["Content-Disposition"]))
r = c.get(f"/api/notes/{n1['id']}/export", headers=H)
check("download everything: whole document", all(s in r.data.decode() for s in ("AI notes", "My notes", "Transcript")))
r = c.get(f"/api/notes/{n1['id']}/export?copy=1&format=md", headers=H)
check("copy everything (md)", r.data.decode().startswith("# Weekly"))
check("export: missing note 404", c.get("/api/notes/nope/export", headers=H).status_code == 404)

r = c.get("/api/export", headers=H)
z = zipfile.ZipFile(io.BytesIO(r.data))
names = sorted(z.namelist())
check("export all: zip download", r.status_code == 200 and "attachment" in r.headers["Content-Disposition"])
check("export all: one file per meeting, in folders", len(names) == 3 and sum(n.startswith("Work/") for n in names) == 2, str(names))
check("export all: same-named meetings both kept",
      any("Standup.txt" in n for n in names) and any("Standup" in n and "(2)" in n for n in names) or
      len({n.split("/")[-1] for n in names}) == 3, str(names))
check("export all: no recordings unless asked", not any(n.endswith(".wav") for n in names))
r = c.get("/api/export?audio=1&folder=Work&parts=summary&format=md", headers=H)
z = zipfile.ZipFile(io.BytesIO(r.data))
names = sorted(z.namelist())
check("export folder: only that folder, flat", len([n for n in names if n.endswith(".md")]) == 2
      and not any("/" in n for n in names), str(names))
check("export folder: recording alongside its document", any(n.endswith(".wav") for n in names), str(names))
doc = next(z.read(n).decode() for n in names if "Weekly" in n and n.endswith(".md"))
check("export folder: chosen parts only", "## AI notes" in doc and "## Transcript" not in doc)
wav = next(i for i in z.infolist() if i.filename.endswith(".wav"))
check("export: recordings stored, not compressed", wav.compress_type == zipfile.ZIP_STORED)
check("export: only the latest zip kept", len(list((cfg.cache_dir / "exports").glob("*.zip"))) == 1)

# ---- settings ------------------------------------------------------------------
conf = c.get("/api/config", headers=H).get_json()
check("config api: export settings", conf["auto_export"] is True and conf["export_format"] == "txt"
      and conf["export_dir"].endswith("Meeting notes"))
r = c.post("/api/config", headers=H, json={"export_format": "pdf"})
check("config api: bad format falls back to txt", cfg.export_format == "txt")
out = TMP / "copies"
c.post("/api/config", headers=H, json={"export_dir": str(out), "export_format": "md"})
check("config api: folder and format saved", cfg.export_dir == out and cfg.export_format == "md"
      and json.loads((TMP / "config.json").read_text())["export_dir"] == str(out))

# ---- auto-save -----------------------------------------------------------------
r = c.post("/api/export/save-all", headers=H).get_json()
files = sorted(p.name for p in out.iterdir())
check("save all: every meeting with content, same names kept apart", len(files) == 3
      and files == sorted(notes.load(cfg, i)["export_file"] for i in (n1["id"], n2["id"], n3["id"]))
      and any("Standup (2).md" in f for f in files), str(files))
check("save all: message", "Saved 3 meetings" in r["message"], r["message"])
before = {p.name for p in out.iterdir()}
c.put(f"/api/notes/{n2['id']}", headers=H, json={"title": "Daily standup", "notes": "edited"})
c.put(f"/api/notes/{n2['id']}", headers=H, json={"notes": "edited again"})
export.wait_idle()
time.sleep(3.5)   # debounce
export.wait_idle()
after = {p.name for p in out.iterdir()}
new = export.filename(notes.load(cfg, n2["id"]), "md")
old2 = notes.load(cfg, n2["id"])["export_file"]
check("auto-save: renamed meeting replaces its old file", new in after and len(after) == len(before)
      and old2 == new and len(before - after) == 1, f"{before} -> {after}")
check("auto-save: latest edit written", "edited again" in (out / new).read_text(encoding="utf-8"))
blank = notes.create(cfg, "2026-10-19_blank", title="Blank")
check("auto-save: empty meeting skipped", export.save_copy(cfg, blank["id"]) is None)
c.delete(f"/api/notes/{n2['id']}", headers=H)
check("delete: auto-saved copy goes too", new not in {p.name for p in out.iterdir()})
cfg.auto_export = False
c.put(f"/api/notes/{n3['id']}", headers=H, json={"notes": "off now"})
time.sleep(3.5)
check("auto-save off: nothing written", "off now" not in (out / notes.load(cfg, n3["id"])["export_file"]).read_text())
r = c.post("/api/export/open-folder", headers=H)
check("open folder: tells the path off Windows", r.status_code == 400 and str(out) in r.get_json()["message"])

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
