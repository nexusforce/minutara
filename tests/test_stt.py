"""Tests for live transcription, cloud/server transcription and the version."""
import json
import sys
import threading
import time
import wave
from pathlib import Path

import requests  # the real one

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import FakeWhisperModel  # noqa: E402

import numpy as np  # noqa: E402

TMP = fakes.make_app_root("minutara-stt-")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_for(pred, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


# Record what the fake Whisper model was built with.
BUILT = []
_orig_init = FakeWhisperModel.__init__


def _init(self, *a, **k):
    BUILT.append(k)
    _orig_init(self, *a, **k)


FakeWhisperModel.__init__ = _init
KW = []
_orig_tr = FakeWhisperModel.transcribe


def _tr(self, audio, **k):
    KW.append(k)
    return _orig_tr(self, audio, **k)


FakeWhisperModel.transcribe = _tr

from minutara import config as config_mod, stt  # noqa: E402
from minutara.transcribe import Utterance  # noqa: E402

cfg = config_mod.load()
R = stt.RATE

# ---- defaults ------------------------------------------------------------------
check("defaults: live transcription off", cfg.live_transcribe is False)
check("defaults: built-in transcription (nothing leaves the PC)", stt.provider(cfg) == "local")
check("defaults: groq/openai/server presets", set(cfg.stt["providers"]) == {"groq", "openai", "server"})
check("defaults: saved in config.json", "stt" in json.loads((TMP / "config.json").read_text())
      and json.loads((TMP / "config.json").read_text())["live_transcribe"] is False)

# ---- audio helpers ---------------------------------------------------------------
t = np.arange(R * 10) / R
speechy = (0.2 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
speechy[int(6.0 * R):int(6.6 * R)] = 0.0            # a pause at 6.0–6.6 s
cut = stt.quiet_cut(speechy, int(4 * R), int(9 * R))
check("quiet_cut: cuts in the pause", 6.0 * R <= cut <= 6.6 * R, cut / R)
check("quiet_cut: tiny window returns its end", stt.quiet_cut(speechy, 0, 100) == 100)
check("has_speech: tone yes", stt.has_speech(speechy))
check("has_speech: silence no", not stt.has_speech(np.zeros(R * 5, np.float32)))
check("has_speech: faint hiss no", not stt.has_speech(np.random.default_rng(1).normal(0, 0.0005, R * 5).astype(np.float32)))
check("has_speech: quiet laptop mic (-40 dB) yes", stt.has_speech((0.014 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)))
sp = stt._spread("Hello there. How are you? Fine!", 10.0, 6.0, "Them")
check("spread: one line per sentence, timed by length", [u.text for u in sp] == ["Hello there.", "How are you?", "Fine!"]
      and sp[0].start == 10.0 and abs(sp[-1].end - 16.0) < 1e-6 and sp[0].speaker == "Them")
check("spread: empty text", stt._spread("", 0, 5, "x") == [])
data, name, mime = stt._encode(speechy[:R], "wav")
import io  # noqa: E402
with wave.open(io.BytesIO(data)) as w:
    check("encode: 16 kHz mono WAV", (w.getframerate(), w.getnchannels(), w.getnframes()) == (16000, 1, R) and mime == "audio/wav")
data2, name2, _ = stt._encode(speechy[:R], "flac")
check("encode: FLAC falls back to WAV without FLAC support", name2.endswith((".flac", ".wav")) and len(data2) > 100)
check("tail: last words", stt._tail([Utterance(0, 1, "Me", "a " * 150)]).strip().startswith("a"))

# ---- a fake transcription service ---------------------------------------------------
from flask import Flask, jsonify, request  # noqa: E402

svc = Flask("fake-stt")
CALLS = []
MODE = {"m": "verbose"}


@svc.post("/v1/audio/transcriptions")
@svc.post("/inference")
def fake_transcribe():
    f = request.files.get("file")
    raw = f.read()
    with wave.open(io.BytesIO(raw)) as w:
        n = w.getnframes()
    CALLS.append({"form": dict(request.form), "auth": request.headers.get("Authorization", ""),
                  "filename": f.filename, "seconds": n / R, "path": request.path})
    m = MODE["m"]
    if m == "401":
        return jsonify({"error": {"message": "Invalid API Key"}}), 401
    if m == "429-once":
        MODE["m"] = "verbose"
        return jsonify({"error": {"message": "slow down"}}), 429, {"retry-after": "0"}
    if m == "text-only" and request.form.get("response_format") == "verbose_json":
        return jsonify({"error": {"message": "response_format 'verbose_json' is not compatible with this model"}}), 400
    if request.form.get("response_format") == "json":
        return jsonify({"text": "First sentence. Second one."})
    dur = n / R
    return jsonify({"text": "hi", "segments": [{"start": 0.5, "end": min(2.0, dur), "text": " piece"},
                                               {"start": 2.5, "end": dur, "text": " "}]})


threading.Thread(target=lambda: svc.run(port=8823, threaded=True), daemon=True).start()
time.sleep(0.8)
BASE = "http://127.0.0.1:8823/v1"

# ---- engines -------------------------------------------------------------------------
try:
    stt.CloudEngine(cfg, "groq")
    check("cloud: no key is an error", False)
except stt.STTError as e:
    check("cloud: no key is an error", "No API key for Groq" in str(e), str(e))

cfg.stt["providers"]["groq"].update(base_url=BASE, api_key="gsk_test", upload_format="wav")
eng = stt.CloudEngine(cfg, "groq")
us = eng.run(speechy[: 4 * R], 30.0, "Me", prompt="earlier words")
c = CALLS[-1]
check("cloud: OpenAI-style request", c["form"].get("model") == "whisper-large-v3-turbo"
      and c["form"].get("response_format") == "verbose_json" and c["form"].get("language") == "en"
      and c["form"].get("prompt") == "earlier words" and c["auth"] == "Bearer gsk_test", str(c))
check("cloud: segments timed from the piece's start", [(u.start, u.text, u.speaker) for u in us] == [(30.5, "piece", "Me")], str(us))
MODE["m"] = "text-only"
us = eng.run(speechy[: 4 * R], 10.0, "Them")
check("cloud: model without timings -> plain text, spread over the piece",
      [u.text for u in us] == ["First sentence.", "Second one."] and us[0].start == 10.0 and not eng.verbose, str(us))
MODE["m"] = "verbose"
eng2 = stt.CloudEngine(cfg, "groq")
MODE["m"] = "429-once"
n0 = len(CALLS)
t0 = time.time()
eng2.run(speechy[:R], 0, "Me")
check("cloud: busy (429) is retried", len(CALLS) == n0 + 2 and time.time() - t0 < 10)
MODE["m"] = "401"
try:
    eng2.run(speechy[:R], 0, "Me")
    check("cloud: refusal reported", False)
except stt.STTError as e:
    check("cloud: refusal reported with the service's message", "401" in str(e) and "Invalid API Key" in str(e), str(e))
MODE["m"] = "verbose"
cfg.stt["providers"]["server"].update(base_url="http://127.0.0.1:8823", path="/inference")
srv_eng = stt.CloudEngine(cfg, "server")
srv_eng.run(speechy[:R], 0, "Me")
check("server: custom path, no key needed", CALLS[-1]["path"] == "/inference" and CALLS[-1]["auth"] == "")
cfg.stt["providers"]["server"]["base_url"] = "http://127.0.0.1:9"
try:
    stt.CloudEngine(cfg, "server").run(speechy[:R], 0, "Me")
    check("server: unreachable explained", False)
except stt.STTError as e:
    check("server: unreachable explained", "Is the server running?" in str(e), str(e))

# ---- whole recordings through a service -----------------------------------------------
def stereo(path, left, right):
    pcm = (np.stack([left, right], axis=1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(R); w.writeframes(pcm.tobytes())


n = R * 60
left = np.zeros(n, np.float32); right = np.zeros(n, np.float32)
tt = np.arange(n) / R
left[: 8 * R] = 0.2 * np.sin(2 * np.pi * 220 * tt[: 8 * R])          # you: first 8 s
right[40 * R: 50 * R] = 0.2 * np.sin(2 * np.pi * 330 * tt[40 * R: 50 * R])   # them: 40–50 s
wav = TMP / "recordings" / "cloud-test.wav"
stereo(wav, left, right)
stt.CloudEngine.max_chunk_s = 20
cfg.stt["provider"] = "groq"
n0 = len(CALLS)
utts = stt.transcribe_file(cfg, wav, "Me", "Them")
sent = CALLS[n0:]
check("file: silent pieces aren't sent (costs nothing)", len(sent) == 2, [c["seconds"] for c in sent])
check("file: speaker split kept, times offset per piece",
      [(round(u.start, 1), u.speaker) for u in utts] == [(0.5, "Me"), (40.5, "Them")] or
      [u.speaker for u in utts] == ["Me", "Them"], str(utts))
check("file: pieces at most the limit", all(c["seconds"] <= 20.01 for c in sent))
stt.CloudEngine.max_chunk_s = 600

from minutara import pipeline  # noqa: E402

cfg.stt["provider"] = "groq"
FakeWhisperModel.calls.clear()
out = pipeline.process(wav, cfg, title="cloud")
check("pipeline: uses the service, not the built-in model", not FakeWhisperModel.calls and "piece" in out.read_text())
MODE["m"] = "401"
out = pipeline.process(wav, cfg, title="cloud")
check("pipeline: service failing falls back to the built-in model", len(FakeWhisperModel.calls) == 2
      and "hello world" in out.read_text())
MODE["m"] = "verbose"
cfg.stt["provider"] = "local"

# ---- live transcription (unit) --------------------------------------------------------
stt.LiveTranscriber.MIN_CHUNK_S = 1.0
stt.LiveTranscriber.POLL_S = 0.05
cfg.stt["live_chunk_s"] = 1
lwav = TMP / "recordings" / "live-unit.wav"
mic_p, spk_p = Path(str(lwav) + ".mic.part"), Path(str(lwav) + ".spk.part")
FakeWhisperModel.calls.clear(); BUILT.clear(); KW.clear()
FakeWhisperModel.scripts = None
lt = stt.LiveTranscriber(cfg, lwav, "Me", "Them")
lt.start()
tone = (0.2 * np.sin(2 * np.pi * 250 * np.arange(R // 10) / R) * 32767).astype(np.int16)
with open(mic_p, "wb") as fm, open(spk_p, "wb") as fs:
    for i in range(45):           # 4.5 s, written as capture does
        fm.write(tone.tobytes()); fs.write(np.zeros_like(tone).tobytes())
        fm.flush(); fs.flush()
        time.sleep(0.02)
    wait_for(lambda: lt.status()["lines"] >= 2, 10)
    st = lt.status()
check("live: transcribes during the recording", st["lines"] >= 2 and st["done_s"] > 1.0 and st["behind_s"] < 4.5, str(st))
check("live: the built-in model uses 2 cores", BUILT and BUILT[0].get("cpu_threads") == 2, str(BUILT))
check("live: their silent side never reaches the model", all(np.abs(a).max() > 0.1 for a in FakeWhisperModel.calls))
check("live: pieces get the previous words as context", any(k.get("initial_prompt") == "hello world" for k in KW), str(KW[:3]))
check("live: text so far", "Me: hello world" in lt.text())
# Stop: capture calls halt(), merges the parts into the WAV, deletes them.
lt.halt()
mic = np.fromfile(mic_p, dtype=np.int16); spk = np.fromfile(spk_p, dtype=np.int16)
with wave.open(str(lwav), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(R); w.writeframes(np.stack([mic, spk], 1).tobytes())
mic_p.unlink(); spk_p.unlink()
check("live: let go of the raw files (Windows can delete them)", not mic_p.exists())
before = len(FakeWhisperModel.calls)
done_mic = lt.done[0] / R
utts = lt.finish(lwav)
left_calls = len(FakeWhisperModel.calls) - before
check("live: only the rest is transcribed at stop", left_calls == 1 and done_mic >= 2.0, f"{left_calls} calls, {done_mic}s done")
check("live: full transcript in order", [u.speaker for u in utts] == ["Me"] * len(utts) and len(utts) == st["lines"] + 1
      and all(utts[i].start <= utts[i + 1].start for i in range(len(utts) - 1)), str(utts))
check("live: times follow the recording", utts[-1].start >= done_mic - 0.01, str([u.start for u in utts]))

# ---- live through the app ---------------------------------------------------------------
from minutara import server as srv  # noqa: E402

app, rec = srv.create_app(cfg)
c = app.test_client()
H = {"X-Token": cfg.server_token}
conf = c.get("/api/config", headers=H).get_json()
check("api: version and transcription settings", conf["version"] == "dev" and conf["live_transcribe"] is False
      and conf["stt"]["provider"] == "local" and conf["stt"]["providers"]["groq"]["api_key_set"] is True
      and "api_key" not in conf["stt"]["providers"]["groq"])
s = c.get("/api/status", headers=H).get_json()
check("api: status says who transcribes, and the version", s["stt"]["provider"] == "local" and s["version"] == "dev")

# Off by default: a recording has no live part.
r = c.post("/api/start", headers=H, json={"title": "no live"})
wait_for(lambda: c.get("/api/status", headers=H).get_json()["elapsed"] > 1.5)
s = c.get("/api/status", headers=H).get_json()
check("off: no live transcription", s["live"] is None and rec.live is None)
c.post("/api/stop", headers=H)
wait_for(lambda: c.get("/api/status", headers=H).get_json()["transcriber"]["busy"] is False
         and c.get("/api/status", headers=H).get_json()["state"] == "idle", 20)
time.sleep(0.5)

c.post("/api/config", headers=H, json={"live_transcribe": True})
check("api: live switch saved", cfg.live_transcribe is True and json.loads((TMP / "config.json").read_text())["live_transcribe"] is True)
FakeWhisperModel.calls.clear()
r = c.post("/api/start", headers=H, json={"title": "Live one"}).get_json()
nid = r["note_id"]
ok = wait_for(lambda: (c.get("/api/status", headers=H).get_json().get("live") or {}).get("lines", 0) >= 1, 15)
s = c.get("/api/status", headers=H).get_json()
check("app: live lines while recording", ok and s["live"]["on"], str(s.get("live")))
nv = c.get(f"/api/notes/{nid}", headers=H).get_json()
check("app: note shows the transcript so far", nv["live"] is True and "hello world" in nv["live_transcript"]
      and nv["has_transcript"] is False, str({k: nv.get(k) for k in ("live", "live_transcript", "status")}))
time.sleep(1.5)
c.post("/api/stop", headers=H)
ok = wait_for(lambda: c.get(f"/api/notes/{nid}", headers=H).get_json()["has_transcript"], 20)
nv = c.get(f"/api/notes/{nid}", headers=H).get_json()
check("app: transcript ready after stop", ok and "hello world" in nv["transcript"] and nv["live"] is False)
log_text = (TMP / "minutara.log").read_text(errors="replace") if (TMP / "minutara.log").exists() else ""
check("app: log says how much was left at stop", "live transcription finished" in log_text)
check("app: no raw files left behind", not list((TMP / "recordings").glob("*.part")))

# Live on but auto-transcribe off: nothing transcribes at all.
c.post("/api/config", headers=H, json={"auto_transcribe": False})
c.post("/api/start", headers=H, json={"title": "manual"})
time.sleep(1.0)
check("live needs auto-transcribe", rec.live is None)
c.post("/api/stop", headers=H)
wait_for(lambda: c.get("/api/status", headers=H).get_json()["state"] == "idle", 10)
c.post("/api/config", headers=H, json={"auto_transcribe": True})

# A live engine that breaks never stops the recording; the rest is done after.
c.post("/api/config", headers=H, json={"stt": {"provider": "groq"}})
MODE["m"] = "401"
FakeWhisperModel.calls.clear()
r = c.post("/api/start", headers=H, json={"title": "broken live"}).get_json()
time.sleep(2.5)
s = c.get("/api/status", headers=H).get_json()
check("broken live: still recording", s["state"] == "recording")
c.post("/api/stop", headers=H)
ok = wait_for(lambda: c.get(f"/api/notes/{r['note_id']}", headers=H).get_json()["has_transcript"], 20)
check("broken live: built-in model finishes the job", ok and FakeWhisperModel.calls)
MODE["m"] = "verbose"

# ---- settings API ------------------------------------------------------------------------
c.post("/api/config", headers=H, json={"stt": {"provider": "nonsense"}})
check("settings: unknown service -> built-in", cfg.stt["provider"] == "local")
c.post("/api/config", headers=H, json={"stt": {"providers": {"openai": {"api_key": "sk-x", "model": "gpt-4o-transcribe"},
                                                             "evil": {"x": 1}}}})
check("settings: key and model saved, unknown services ignored", cfg.stt["providers"]["openai"]["api_key"] == "sk-x"
      and cfg.stt["providers"]["openai"]["model"] == "gpt-4o-transcribe" and "evil" not in cfg.stt["providers"])
c.post("/api/config", headers=H, json={"stt": {"providers": {"openai": {"api_key": ""}}}})
check("settings: blank key keeps the saved one", cfg.stt["providers"]["openai"]["api_key"] == "sk-x")
c.post("/api/config", headers=H, json={"stt": {"providers": {"openai": {"api_key": None}}}})
check("settings: null removes the key", cfg.stt["providers"]["openai"]["api_key"] == "")
r = c.post("/api/stt/test", headers=H, json={"provider": "groq"}).get_json()
check("test: service answers", r["ok"] and "Groq answered" in r["message"], str(r))
MODE["m"] = "401"
r = c.post("/api/stt/test", headers=H, json={"provider": "groq"})
check("test: failure explained", r.status_code == 400 and "Invalid API Key" in r.get_json()["message"])
MODE["m"] = "verbose"
check("test: built-in isn't testable", c.post("/api/stt/test", headers=H, json={"provider": "local"}).status_code == 400)
c.post("/api/config", headers=H, json={"stt": {"language": "", "live_threads": 4}})
check("settings: language and cores", cfg.stt["language"] == "" and cfg.stt["live_threads"] == 4)

from minutara.version import VERSION  # noqa: E402
check("version: dev from source", VERSION == "dev")

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
