"""Minutara test suite: real code, fake audio hardware and fake Whisper."""
import json
import os
import sys
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402  (must come before any minutara import)
from fakes import BT_LOOP, HEADSET, LOOP, MIC, YETI, FakeAudio, FakeWhisperModel, write_wav  # noqa: E402,F401

import numpy as np  # noqa: E402

TMP = fakes.make_app_root()

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_for(pred, timeout=15.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


# ---- 1. config ---------------------------------------------------------------
from minutara import config as config_mod  # noqa: E402

cfg = config_mod.load()
check("config: dirs created", all((TMP / d).is_dir() for d in ("recordings", "transcripts", "notes")))
check("config: server token generated+saved",
      cfg.server_token and json.loads((TMP / "config.json").read_text())["server_token"] == cfg.server_token)
data = json.loads((TMP / "config.json").read_text())
data["whisper_model"] = "base.en"
(TMP / "config.json").write_text(json.dumps(data))
check("config: file value applies", config_mod.load().whisper_model == "base.en")
os.environ["MINUTARA_WHISPER_MODEL"] = "tiny.en"
check("config: env beats file", config_mod.load().whisper_model == "tiny.en")
del os.environ["MINUTARA_WHISPER_MODEL"]
check("config: recordings default", config_mod.load().recordings_dir == TMP / "recordings")
check("config: auto_transcribe defaults on", config_mod.load().auto_transcribe is True)

# ---- 2. capture: streaming to disk, channels, pause, partial failure ----------
from minutara.capture import record, list_input_devices  # noqa: E402

out = TMP / "cap" / "plain.wav"
stop = threading.Event()
threading.Timer(1.0, stop.set).start()
res = record(out, stop)
with wave.open(str(out), "rb") as w:
    ch, rate, frames = w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())
pcm = np.frombuffer(frames, dtype=np.int16).reshape(-1, 2)
check("capture: stereo 16 kHz wav", ch == 2 and rate == 16000)
check("capture: duration tracks wall clock", 0.7 < res.duration_s < 1.4, f"{res.duration_s:.2f}s")
check("capture: left channel is the mic", pcm[:, 0].mean() > 0, str(pcm[:, 0].mean()))
check("capture: right channel is the speakers", pcm[:, 1].mean() < 0, str(pcm[:, 1].mean()))
check("capture: .part files cleaned up", not list((TMP / "cap").glob("*.part")))

# pause drops audio
out = TMP / "cap" / "paused.wav"
stop, pause = threading.Event(), threading.Event()
threading.Timer(0.5, pause.set).start()
threading.Timer(1.3, pause.clear).start()
threading.Timer(1.8, stop.set).start()
res = record(out, stop, pause_event=pause)
check("capture: pause excludes paused audio", 0.6 < res.duration_s < 1.3, f"{res.duration_s:.2f}s of 1.8s wall")

# a stream that dies mid-meeting still leaves the other channel saved
FakeAudio.fail_device_after = {3: 20}  # speakers go quiet after ~0.2 s
out = TMP / "cap" / "partial.wav"
stop = threading.Event()
threading.Timer(1.0, stop.set).start()
res = record(out, stop)
FakeAudio.fail_device_after = {}
check("capture: survives one stream failing", out.exists() and 0.8 < res.duration_s < 1.3, f"{res.duration_s:.2f}s")

def wav_pcm(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2)

# the bug from the field: a device delivering far faster than real time
FakeAudio.speed = {MIC: 40}
out = TMP / "cap" / "runaway.wav"
stop = threading.Event()
threading.Timer(1.5, stop.set).start()
res = record(out, stop)
FakeAudio.speed = {}
check("capture: runaway device can't stretch the recording",
      1.2 < res.duration_s < 1.9, f"{res.duration_s:.2f}s for 1.5s")
pcm = wav_pcm(out)
check("capture: runaway recording keeps both channels", pcm[:, 0].mean() > 0 and pcm[:, 1].mean() < 0)

# speakers that send nothing while nothing plays: silence, still in step
FakeAudio.silent = {LOOP}
out = TMP / "cap" / "quiet-speakers.wav"
stop = threading.Event()
threading.Timer(1.2, stop.set).start()
res = record(out, stop)
FakeAudio.silent = set()
pcm = wav_pcm(out)
check("capture: silent speakers give a silent channel of full length",
      1.0 < res.duration_s < 1.5 and not pcm[:, 1].any() and (pcm[:, 0] > 0).mean() > 0.9,
      f"{res.duration_s:.2f}s, mic filled {(pcm[:, 0] > 0).mean():.2f}")

# speakers that can't be opened at all still leave a mic recording
FakeAudio.open_fails = {LOOP}
out = TMP / "cap" / "no-speakers.wav"
stop = threading.Event()
threading.Timer(0.8, stop.set).start()
res = record(out, stop)
FakeAudio.open_fails = set()
check("capture: works without the speakers", out.exists() and res.duration_s > 0.6)

# picking a named mic, and only ever WASAPI devices
FakeAudio.opened = []
stop = threading.Event()
threading.Timer(0.3, stop.set).start()
record(TMP / "cap" / "yeti.wav", stop, mic_name="yeti")
check("capture: mic picked by name", YETI in FakeAudio.opened, str(FakeAudio.opened))
FakeAudio.opened = []
stop = threading.Event()
threading.Timer(0.3, stop.set).start()
record(TMP / "cap" / "old-name.wav", stop, mic_name="Microphone Array (Realtek(R) Au")
check("capture: an old shortened name finds the WASAPI mic", MIC in FakeAudio.opened and 0 not in FakeAudio.opened,
      str(FakeAudio.opened))
FakeAudio.opened = []
stop = threading.Event()
threading.Timer(0.3, stop.set).start()
record(TMP / "cap" / "default.wav", stop)
check("capture: default mic is WASAPI's", MIC in FakeAudio.opened and 0 not in FakeAudio.opened, str(FakeAudio.opened))

# 44.1 kHz mono headset
stop = threading.Event()
threading.Timer(1.0, stop.set).start()
res = record(TMP / "cap" / "headset.wav", stop, mic_name="headset")
pcm = wav_pcm(TMP / "cap" / "headset.wav")
check("capture: 44.1 kHz mono mic converts", 0.8 < res.duration_s < 1.3 and pcm[:, 0].mean() > 0,
      f"{res.duration_s:.2f}s mean {pcm[:, 0].mean():.0f}")

d = list_input_devices()
check("devices: lists WASAPI mics only, not loopback/outputs/MME copies",
      d["mics"] == ["Microphone Array (Realtek(R) Audio)", "Blue Yeti USB Microphone",
                    "Headset Mic (44k mono)"], str(d["mics"]))
check("devices: default is the WASAPI mic", d["default"] == "Microphone Array (Realtek(R) Audio)", d["default"])
check("devices: reports the speakers in use", d["loopback"] == "Speakers (Realtek(R) Audio)", d["loopback"])
check("devices: lists every output to capture from",
      d["speakers"] == ["Speakers (Realtek(R) Audio)", "Headphones (WH-1000XM5)"], str(d["speakers"]))

# picking the speakers: the Bluetooth headphones instead of the default output
FakeAudio.opened = []
stop = threading.Event()
threading.Timer(0.8, stop.set).start()
res = record(TMP / "cap" / "bt.wav", stop, speaker_name="Headphones (WH-1000XM5)")
pcm = wav_pcm(TMP / "cap" / "bt.wav")
check("capture: chosen speakers are recorded", BT_LOOP in FakeAudio.opened and LOOP not in FakeAudio.opened
      and pcm[:, 1].mean() < 0, str(FakeAudio.opened))
FakeAudio.opened = []
stop = threading.Event()
threading.Timer(0.3, stop.set).start()
record(TMP / "cap" / "gone.wav", stop, speaker_name="Old USB dock")
check("capture: missing speakers fall back to the default output", LOOP in FakeAudio.opened, str(FakeAudio.opened))

# the Settings page's Test buttons
from minutara.capture import test_device  # noqa: E402
r = test_device("mic", "", 1.0)
check("test mic: a working mic passes", r["verdict"] == "good" and r["audio"].startswith("data:audio/wav;base64,"),
      f"{r['verdict']}: {r['message']}")
check("test mic: reports level and timing", r["peak_db"] is not None and 0.7 < r["sent_s"] < 1.3, str(r))
import base64, io  # noqa: E401,E402
FakeAudio.tones[MIC] = 300          # a quiet laptop mic, about -40 dB
rq = test_device("mic", "", 1.0)
FakeAudio.tones[MIC] = 3000
with wave.open(io.BytesIO(base64.b64decode(rq["audio"].split(",", 1)[1]))) as w:
    clip = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
check("test mic: a quiet mic plays back at a normal volume",
      abs(clip).max() > 3000, f"recorded at 300, plays at {abs(clip).max()} ({rq['verdict']})")
FakeAudio.noise = {MIC}
r = test_device("mic", "", 1.0)
FakeAudio.noise = set()
check("test mic: static is called out", r["verdict"] == "broken" and "static" in r["message"], r["message"])
FakeAudio.speed = {MIC: 30}
r = test_device("mic", "", 1.0)
FakeAudio.speed = {}
check("test mic: a runaway driver is called out", r["verdict"] == "broken" and "faster" in r["message"], r["message"])
FakeAudio.silent = {MIC}
r = test_device("mic", "", 1.0)
FakeAudio.silent = set()
check("test mic: a mic sending nothing is called out", r["verdict"] == "broken", r["message"])
FakeAudio.tones[MIC] = 0
r = test_device("mic", "", 1.0)
FakeAudio.tones[MIC] = 3000
check("test mic: silence is called out with the privacy hint", r["verdict"] == "quiet" and "Privacy" in r["message"], r["message"])
FakeAudio.silent = {LOOP}
r = test_device("speakers", "", 1.0)
FakeAudio.silent = set()
check("test speakers: nothing heard points to the Speakers setting",
      r["verdict"] == "quiet" and "under Speakers" in r["message"], r["message"])
r = test_device("speakers", "", 1.0, speaker_name="Headphones (WH-1000XM5)")
check("test speakers: tests the chosen output", "WH-1000XM5" in r["device"] and r["verdict"] == "good", r["device"])
r = test_device("speakers", "", 1.0)
check("test speakers: playing audio passes", r["verdict"] == "good", r["message"])

# ---- 3. transcribe -----------------------------------------------------------
from minutara.transcribe import transcribe, to_text  # noqa: E402

w16 = TMP / "st16.wav"
write_wav(w16, 16000, 2, 16000)
FakeWhisperModel.calls = []
utts = transcribe(w16, me="Me", them="Them")
check("transcribe: one pass per channel", len(FakeWhisperModel.calls) == 2)
check("transcribe: speakers labelled", sorted(u.speaker for u in utts) == ["Me", "Them"])
w44 = TMP / "mono44.wav"
write_wav(w44, 44100, 1, 44100)
FakeWhisperModel.calls = []
transcribe(w44)
check("transcribe: 44.1k resampled to 16k", len(FakeWhisperModel.calls[0]) == 16000)
m4a = TMP / "x.m4a"
m4a.write_bytes(b"\x00\x00\x00\x20ftypM4A ")
FakeWhisperModel.calls = []
check("transcribe: undecodable falls back to path",
      to_text(transcribe(m4a)) == "[00:00] Speaker: hello world" and FakeWhisperModel.calls == [str(m4a)])

# ---- 4. notes ----------------------------------------------------------------
from minutara import notes  # noqa: E402

cfg = config_mod.load()
write_wav(cfg.recordings_dir / "20260716-0930-test1.wav")
(cfg.transcripts_dir / "20260716-0930-test1.txt").write_text("[00:01] Me: budget talk")
(cfg.recordings_dir / "pocket-2026-01-15-0001.mp3").write_bytes(b"\xff\xfb" + b"\0" * 100)
(cfg.transcripts_dir / "20260710-1100-old-call.txt").write_text("[00:00] Them: hi")
(cfg.recordings_dir / "x.wav.mic.part").write_bytes(b"\0" * 10)
all_n = notes.all_notes(cfg)
by_id = {n["id"]: n for n in all_n}
check("notes: adopts recordings and orphan transcripts",
      set(by_id) >= {"20260716-0930-test1", "pocket-2026-01-15-0001", "20260710-1100-old-call"}, str(set(by_id)))
check("notes: ignores .part files", not any("part" in k for k in by_id))
check("notes: title from stem", by_id["20260716-0930-test1"]["title"] == "Test1")
check("notes: pocket title", by_id["pocket-2026-01-15-0001"]["title"] == "Pocket recording 0001")
check("notes: pocket source", by_id["pocket-2026-01-15-0001"]["source"] == "pocket")
check("notes: created_at from stamp",
      by_id["20260716-0930-test1"]["created_at"].startswith("2026-07-16T09:30"))
check("notes: wav duration measured", by_id["20260716-0930-test1"]["duration_s"] == 1.0)
check("notes: newest first", [n["id"] for n in all_n].index("20260716-0930-test1")
      < [n["id"] for n in all_n].index("20260710-1100-old-call"))
check("notes: second sync adopts nothing new", len(notes.all_notes(cfg)) == len(all_n))
check("notes: search hits transcript text", notes.matches(cfg, by_id["20260716-0930-test1"], "BUDGET"))
check("notes: search misses", not notes.matches(cfg, by_id["20260716-0930-test1"], "zebra"))
check("notes: rejects traversal ids", notes.load(cfg, "../config") is None and not notes.valid_id("a/b"))
uid = notes.unique_id(cfg, "Test1")
notes.create(cfg, uid, title="x")
check("notes: unique_id avoids collisions", notes.unique_id(cfg, "Test1") != uid)
notes.update(cfg, uid, notes="typed")
notes.update(cfg, uid, duration_s=12.0)
n = notes.load(cfg, uid)
check("notes: updates merge, not clobber", n["notes"] == "typed" and n["duration_s"] == 12.0)
notes.add_folder(cfg, "Team")
notes.update(cfg, uid, folder="Team")
f = {x["name"]: x["count"] for x in notes.folders(cfg)}
check("notes: folders with counts", f.get("Team") == 1, str(f))
notes.remove_folder(cfg, "Team")
check("notes: removing folder keeps the meeting",
      notes.load(cfg, uid) is not None and notes.load(cfg, uid)["folder"] == ""
      and "Team" not in {x["name"] for x in notes.folders(cfg)})
check("notes: clean_folder rejects slashes", notes.clean_folder("a/b") == "" and notes.clean_folder("  A   B ") == "A B")
notes.delete(cfg, "20260716-0930-test1")
check("notes: delete removes audio + transcript",
      not (cfg.recordings_dir / "20260716-0930-test1.wav").exists()
      and not (cfg.transcripts_dir / "20260716-0930-test1.txt").exists()
      and notes.load(cfg, "20260716-0930-test1") is None)

# ---- 5. server API -----------------------------------------------------------
import minutara.server as srv  # noqa: E402

cfg = config_mod.load()
app, rec = srv.create_app(cfg)
c = app.test_client()
T = cfg.server_token
U = lambda p: p + ("&" if "?" in p else "?") + "token=" + T  # noqa: E731

check("api: rejects missing token", c.get("/api/status").status_code == 401)
check("api: rejects bad token", c.get("/api/status?token=nope").status_code == 401)
check("web: index served without token", c.get("/").status_code == 200 and b"/static/app.js" in c.get("/").data)
for asset in ("app.js", "app.css"):
    check(f"web: /static/{asset}", c.get(f"/static/{asset}").status_code == 200)
s = c.get(U("/api/status")).get_json()
check("api: status shape", s["state"] == "idle" and "note_id" in s and "model_ready" in s["transcriber"])

# record → note → saved audio → auto-transcript
FakeWhisperModel.calls = []
r = c.post(U("/api/start"), json={"title": "Weekly sync"})
nid = r.get_json().get("note_id")
check("start: creates a note", r.status_code == 200 and nid and notes.load(cfg, nid)["title"] == "Weekly sync")
check("start: second start refused", c.post(U("/api/start")).status_code == 409)
s = c.get(U("/api/status")).get_json()
check("status: recording with note id", s["state"] == "recording" and s["note_id"] == nid)
lst = c.get(U("/api/notes")).get_json()
check("list: live note shows recording", next(x for x in lst if x["id"] == nid)["status"] == "recording")
c.put(U(f"/api/notes/{nid}"), json={"title": "Weekly sync (renamed)", "notes": "- agenda"})
check("save: title syncs to live recorder", c.get(U("/api/status")).get_json()["title"] == "Weekly sync (renamed)")
check("delete: refused while recording", c.delete(U(f"/api/notes/{nid}")).status_code == 409)
check("device test: refused while recording", c.post(U("/api/devices/test"), json={}).status_code == 409)
check("busy: recording counts", app.config["MINUTARA"]["busy"]() == "saving the recording")
time.sleep(0.6)
check("pause", c.post(U("/api/pause")).status_code == 200)
e1 = c.get(U("/api/status")).get_json()["elapsed"]
time.sleep(0.5)
e2 = c.get(U("/api/status")).get_json()["elapsed"]
check("pause: timer frozen", abs(e2 - e1) < 0.05, f"{e1:.2f} → {e2:.2f}")
check("resume", c.post(U("/api/resume")).status_code == 200)
time.sleep(0.5)
check("stop", c.post(U("/api/stop")).status_code == 200)
check("stop: saving state", c.get(U("/api/status")).get_json()["state"] in ("saving", "idle"))
check("stop: wav saved and recorder idle",
      wait_for(lambda: c.get(U("/api/status")).get_json()["state"] == "idle"))
wav = cfg.recordings_dir / f"{nid}.wav"
check("stop: audio file exists", wav.exists())
check("auto-transcribe: transcript written",
      wait_for(lambda: (cfg.transcripts_dir / f"{nid}.txt").exists()))
time.sleep(0.2)
g = c.get(U(f"/api/notes/{nid}")).get_json()
check("note: full view", g["status"] == "transcribed" and g["has_audio"] and "hello world" in g["transcript"]
      and g["notes"] == "- agenda" and g["me_label"] == "Me", str({k: g[k] for k in ("status", "has_audio")}))
check("note: duration recorded", g["duration_s"] and 0.6 < g["duration_s"] < 1.6, str(g["duration_s"]))

# audio + transcript downloads
r = c.get(U(f"/api/notes/{nid}/audio"))
check("audio: served with range support", r.status_code == 200 and "bytes" in (r.headers.get("Accept-Ranges") or ""))
play = r.get_data()
r = c.get(U(f"/api/notes/{nid}/audio?download=1"))
check("audio: download is an attachment", "attachment" in (r.headers.get("Content-Disposition") or "")
      and f"{nid}.wav" in r.headers.get("Content-Disposition", ""))
import io as _io  # noqa: E402
with wave.open(_io.BytesIO(play)) as w:
    pch, pdat = w.getnchannels(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
with wave.open(_io.BytesIO(r.get_data())) as w:
    dch, ddat = w.getnchannels(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
# (the fake mic and speakers are exact opposite tones, so their mix is silent;
# levels are checked with realistic audio in the listen tests below)
check("audio: player gets a mono mix of both sides", pch == 1 and len(pdat) == len(ddat) // 2,
      f"{pch} ch, {len(pdat)} vs {len(ddat)} samples")
check("audio: download is the untouched stereo original", dch == 2 and (cfg.recordings_dir / f"{nid}.wav").read_bytes() == r.get_data())
check("audio: listening copy cached", len(list((cfg.cache_dir / "listen").glob(f"{nid}-*.wav"))) == 1)
r = c.get(U(f"/api/notes/{nid}/transcript?download=1"))
check("transcript: download", r.status_code == 200 and "attachment" in r.headers.get("Content-Disposition", ""))

# auto off → saved but untranscribed, manual transcribe works, dedupes
c.post(U("/api/config"), json={"auto_transcribe": False})
r = c.post(U("/api/start"), json={"title": "Manual one"})
nid2 = r.get_json()["note_id"]
time.sleep(0.5)
c.post(U("/api/stop"))
wait_for(lambda: c.get(U("/api/status")).get_json()["state"] == "idle")
g2 = c.get(U(f"/api/notes/{nid2}")).get_json()
check("auto off: saved, not transcribed", g2["has_audio"] and g2["status"] == "audio", g2["status"])
FakeWhisperModel.delay = 0.6
r1 = c.post(U(f"/api/notes/{nid2}/transcribe")).get_json()
r2 = c.post(U(f"/api/notes/{nid2}/transcribe")).get_json()
check("transcribe: queued, duplicate refused", "Queued" in r1["message"] and "Already" in r2["message"])
check("transcribe: shows as in progress",
      c.get(U(f"/api/notes/{nid2}")).get_json()["status"] in ("queued", "transcribing"))
check("delete: refused while transcribing", c.delete(U(f"/api/notes/{nid2}")).status_code == 409)
check("transcribe: completes", wait_for(lambda: c.get(U(f"/api/notes/{nid2}")).get_json()["status"] == "transcribed"))
FakeWhisperModel.delay = 0

# a failing transcription is reported on the note, and audio survives
r = c.post(U("/api/start"), json={"title": "Will fail"})
nid3 = r.get_json()["note_id"]
time.sleep(0.4)
c.post(U("/api/stop"))
wait_for(lambda: c.get(U("/api/status")).get_json()["state"] == "idle")
FakeWhisperModel.fail = True
c.post(U(f"/api/notes/{nid3}/transcribe"))
check("failure: note shows error",
      wait_for(lambda: c.get(U(f"/api/notes/{nid3}")).get_json()["status"] == "error"))
g3 = c.get(U(f"/api/notes/{nid3}")).get_json()
check("failure: message and audio kept", "model exploded" in g3["error"] and g3["has_audio"])
FakeWhisperModel.fail = False
c.post(U(f"/api/notes/{nid3}/transcribe"))
check("failure: retry clears the error",
      wait_for(lambda: c.get(U(f"/api/notes/{nid3}")).get_json()["status"] == "transcribed")
      and not c.get(U(f"/api/notes/{nid3}")).get_json()["error"])

# list filters
c.post(U("/api/folders"), json={"name": "Team"})
c.put(U(f"/api/notes/{nid}"), json={"folder": "Team"})
inf = [x["id"] for x in c.get(U("/api/notes?folder=Team")).get_json()]
check("list: folder filter", inf == [nid], str(inf))
hits = [x["id"] for x in c.get(U("/api/notes?q=agenda")).get_json()]
check("list: search finds typed notes", hits == [nid], str(hits))
folders = c.get(U("/api/folders")).get_json()["folders"]
check("folders: listed with count", {"name": "Team", "count": 1} in folders, str(folders))
check("folders: bad name refused", c.post(U("/api/folders"), json={"name": "a/b"}).status_code == 400)
c.delete(U("/api/folders/Team"))
check("folders: removed, note kept",
      c.get(U(f"/api/notes/{nid}")).get_json()["folder"] == ""
      and not c.get(U("/api/folders")).get_json()["folders"])

# save via POST (sendBeacon path) and unknown notes
check("save: POST alias works", c.post(U(f"/api/notes/{nid}"), json={"notes": "beacon"}).status_code == 200
      and notes.load(cfg, nid)["notes"] == "beacon")
check("note: unknown is 404", c.get(U("/api/notes/nope")).status_code == 404)
check("note: traversal is 404", c.get(U("/api/notes/..%2Fconfig")).status_code == 404)

# delete
check("delete: works when idle", c.delete(U(f"/api/notes/{nid2}")).status_code == 200
      and not (cfg.recordings_dir / f"{nid2}.wav").exists())

# settings API
cf = c.get(U("/api/config")).get_json()
check("config api: key never echoed", "pocket_api_key" not in cf and cf["pocket_key_set"] is False)
check("config api: app_root reported", cf["app_root"] == str(TMP))
c.post(U("/api/config"), json={"pocket_api_key": "pk_x", "server_token": "HIJACK", "mic_device": "Yeti"})
saved = json.loads((TMP / "config.json").read_text())
check("config api: key saved, token untouched", saved["pocket_api_key"] == "pk_x" and saved["server_token"] == T)
c.post(U("/api/config"), json={"pocket_api_key": " "})
check("config api: blank key keeps saved", json.loads((TMP / "config.json").read_text())["pocket_api_key"] == "pk_x")
dv = c.get(U("/api/devices")).get_json()
check("devices api", len(dv["mics"]) == 3 and dv["selected"] == "Yeti" and len(dv["speakers"]) == 2)
tr = c.post(U("/api/devices/test"), json={"kind": "mic", "mic": "yeti", "seconds": 1})
tj = tr.get_json()
check("device test api: mic", tr.status_code == 200 and tj["verdict"] == "good" and "Yeti" in tj["device"], str(tj)[:200])
tj = c.post(U("/api/devices/test"), json={"kind": "speakers", "seconds": 1}).get_json()
check("device test api: speakers", "Loopback" in tj["device"], str(tj)[:200])
check("device test api: needs token", app.test_client().post("/api/devices/test").status_code == 401)
show = c.post(U("/api/show")).get_json()
check("show api: no window here", show["ok"] is True and show["shown"] is False)
check("busy: idle at the end", app.config["MINUTARA"]["busy"]() in ("", "transcribing", "writing AI notes"))
ph = c.get(U("/api/phone")).get_json()
check("phone api: QR svg + token url", ph["svg"].startswith("<svg") and T in ph["url"])
check("usb api: no drive → 404", c.post(U("/api/usb")).status_code == 404)
lg = c.get(U("/api/log")).get_data(as_text=True)
check("log api: records activity", "recording started" in lg and "queued for transcription" in lg)
real = srv._shutdown
srv._shutdown = lambda: None
check("quit api", c.post(U("/api/quit")).get_json()["ok"] is True)
srv._shutdown = real
check("port detector", srv._port_in_use(1) is False)

# ---- listening copies ---------------------------------------------------------
from minutara import listen  # noqa: E402
L = TMP / "listen-src"
L.mkdir()
cache = TMP / "listen-cache"
def stereo(path, left, right, n=16000 * 3):
    t = np.arange(n) / 16000
    speech = np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 0.7 * t) > 0)   # bursts, like talking
    pcm = np.stack([speech * left, speech * right], axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes((pcm * 32767).astype(np.int16).tobytes())
stereo(L / "quiet.wav", 0.01, 0.0)            # a -40 dB laptop mic, nothing from the speakers
out = listen.listening_copy(L / "quiet.wav", cache)
with wave.open(str(out)) as w:
    q = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16) / 32768
check("listen: quiet mic brought up to speech level", 0.15 < np.percentile(np.abs(q), 99) < 1.0,
      f"{np.percentile(np.abs(q), 99):.3f}")
stereo(L / "loud.wav", 0.95, 0.9)
out = listen.listening_copy(L / "loud.wav", cache)
with wave.open(str(out)) as w:
    ld = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
check("listen: loud recording doesn't clip", abs(ld).max() < 32767, str(abs(ld).max()))
stereo(L / "silent.wav", 0.0, 0.0)
check("listen: silence is left alone", listen.listening_copy(L / "silent.wav", cache) == L / "silent.wav")
stereo(L / "quiet-two.wav", 0.01, 0.0)
listen.listening_copy(L / "quiet-two.wav", cache)
listen.forget(cache, "quiet")
names = sorted(f.name.rsplit("-", 2)[0] for f in (cache / "listen").glob("*.wav"))
check("listen: forgetting one recording keeps similarly named ones", names == ["loud", "quiet-two"], str(names))
(L / "x.mp3").write_bytes(b"ID3")
check("listen: non-WAV files pass through", listen.listening_copy(L / "x.mp3", cache) == L / "x.mp3")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
