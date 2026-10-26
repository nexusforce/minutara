"""Transcript accuracy: names-and-terms hints, no drift, word-level lines in
time order, the mic's echo of the call removed, and the glossary in AI notes."""
import json
import sys
import threading
import time
import wave
from pathlib import Path

import requests  # the real one, before fakes stub anything

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import FakeWhisperModel  # noqa: E402

import numpy as np  # noqa: E402

TMP = fakes.make_app_root("minutara-acc-")
failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


from minutara import ai, config as config_mod, pipeline, stt, transcribe as tr  # noqa: E402

cfg = config_mod.load()
VOCAB = "Northwind, Priya\nTomas; Impact Report, S&P 500,  priya , Magnificent 7"

# ---- settings -----------------------------------------------------------------------
check("defaults: no vocabulary, echo removal on", cfg.vocabulary == "" and cfg.remove_echo is True)
check("vocab: split on commas, lines, semicolons; duplicates dropped",
      tr.vocab_terms(VOCAB) == ["Northwind", "Priya", "Tomas", "Impact Report", "S&P 500", "Magnificent 7"],
      str(tr.vocab_terms(VOCAB)))
check("vocab: as hotwords", tr.hotwords(VOCAB) == "Northwind, Priya, Tomas, Impact Report, S&P 500, Magnificent 7")
check("vocab: empty means no hotwords", tr.hotwords("  ,\n ") is None)

# ---- Whisper options ---------------------------------------------------------------------
FakeWhisperModel.kwargs.clear()
model = FakeWhisperModel()
tr.run_whisper(model, np.zeros(16000, np.float32), "Me", vocabulary=VOCAB)
k = FakeWhisperModel.kwargs[-1]
check("whisper: no conditioning on its own output (stops drift)", k.get("condition_on_previous_text") is False)
check("whisper: word timestamps on", k.get("word_timestamps") is True)
check("whisper: vocabulary as hotwords in every window", k.get("hotwords", "").startswith("Northwind, Priya"))
check("whisper: silence still skipped", k.get("vad_filter") is True)
tr.run_whisper(model, np.zeros(16000, np.float32), "Me", prompt="earlier words")
check("whisper: previous words still passed as the opening prompt",
      FakeWhisperModel.kwargs[-1].get("initial_prompt") == "earlier words" and FakeWhisperModel.kwargs[-1].get("hotwords") is None)


# ---- words into lines ----------------------------------------------------------------------
def W(*triples):
    return [tr.Word(a, b, w) for a, b, w in triples]


lines = tr.split_words(W((0.0, 0.4, " Hi"), (0.4, 0.9, " there."), (2.0, 2.3, " So,"), (2.3, 2.8, " budget?")), "Them", 10)
check("split: a pause starts a new line", [(l.start, l.text) for l in lines] == [(10.0, "Hi there."), (12.0, "So, budget?")],
      str(lines))
long = W(*[(i * 0.5, i * 0.5 + 0.45, " word" + ("." if i == 29 else "")) for i in range(40)])
lines = tr.split_words(long, "Them")
check("split: a long run breaks at a sentence end", len(lines) == 2 and lines[0].text.endswith("word."), [l.text[-12:] for l in lines])
lines = tr.split_words(W(*[(i * 0.5, i * 0.5 + 0.45, " word") for i in range(80)]), "Them")
check("split: nothing longer than 30 s", all(l.end - l.start <= 30.5 for l in lines) and len(lines) >= 2)
segs = [fakes.FakeSegment(1.0, 3.0, " No word timings here. ")]
check("segments without word timings stay whole",
      [(l.start, l.text) for l in tr.segment_lines(segs, "Me", 5)] == [(6.0, "No word timings here.")])

# ---- order and echo on a real file --------------------------------------------------------------
wav = TMP / "recordings" / "order.wav"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x10\x00" * 2 * 16000 * 20)
FakeWhisperModel.scripts = [
    # you (left): a short reply mid-way through their long turn, plus their words echoing in
    [(5.0, 5.6, "Yes, agreed.", [(5.0, 5.2, " Yes,"), (5.2, 5.6, " agreed.")]),
     (12.1, 13.0, "and the budget is down", [(12.1, 12.3, " and"), (12.3, 12.5, " the"), (12.5, 12.7, " budget"),
                                             (12.7, 12.8, " is"), (12.8, 13.0, " down")])],
    # them (right): one long Whisper segment spanning 0–14 s
    [(0.0, 14.0, "Q4 is set. Next, the plan. And the budget is down 8%.",
      [(0.0, 0.5, " Q4"), (0.5, 0.8, " is"), (0.8, 1.2, " set."),
       (7.0, 7.4, " Next,"), (7.4, 7.6, " the"), (7.6, 8.0, " plan."),
       (12.0, 12.2, " And"), (12.2, 12.4, " the"), (12.4, 12.7, " budget"), (12.7, 12.8, " is"),
       (12.8, 13.1, " down"), (13.1, 14.0, " 8%.")])],
]
cfg.vocabulary = VOCAB
out = pipeline.process(wav, cfg, title="order")
text = out.read_text()
lines = text.splitlines()
check("order: your reply sits between their lines, not after their whole turn",
      [l.split("] ", 1)[1] for l in lines] == ["Them: Q4 is set.", "Me: Yes, agreed.", "Them: Next, the plan.",
                                               "Them: And the budget is down 8%."], text)
check("echo: your mic repeating them is dropped", "Me: and the budget" not in text)
check("vocabulary reaches the model in the full pass", all(k.get("hotwords", "").startswith("Northwind")
                                                           for k in FakeWhisperModel.kwargs[-2:]))
FakeWhisperModel.scripts = [
    [(12.1, 13.0, "and the budget is down", [(12.1, 12.5, " and the"), (12.5, 13.0, " budget is down")])],
    [(12.0, 14.0, "And the budget is down 8%.", [(12.0, 14.0, " And the budget is down 8%.")])],
]
cfg.remove_echo = False
text = pipeline.process(wav, cfg, title="order").read_text()
check("echo: kept when the setting is off", "Me: and the budget is down" in text, text)
cfg.remove_echo = True

U = tr.Utterance
kept, n = tr.remove_echo([U(1, 2, "Them", "Yeah right."), U(1.2, 1.8, "Me", "Yeah right.")], "Me", "Them")
check("echo: short replies are never dropped", n == 0)
kept, n = tr.remove_echo([U(1, 3, "Them", "the budget is down"), U(20, 22, "Me", "the budget is down")], "Me", "Them")
check("echo: repeating them later isn't echo", n == 0)
kept, n = tr.remove_echo([U(1, 3, "Me", "we should ship it friday")], "Me", "Them")
check("echo: nothing to compare with, nothing dropped", n == 0)

# ---- live transcription uses the same options --------------------------------------------------------
eng = stt.LocalEngine(cfg, threads=2)
FakeWhisperModel.scripts = [[(0.0, 1.0, "Hello Priya.", [(0.0, 0.4, " Hello"), (0.5, 1.0, " Priya.")])]]
got = eng.run(np.zeros(16000, np.float32), 30.0, "Me", prompt="before")
check("live: vocabulary, no drift, word lines with offsets",
      FakeWhisperModel.kwargs[-1].get("hotwords", "").startswith("Northwind")
      and FakeWhisperModel.kwargs[-1].get("condition_on_previous_text") is False
      and [(u.start, u.text) for u in got] == [(30.0, "Hello Priya.")], str(got))

# ---- transcription services ------------------------------------------------------------------------------
from flask import Flask, jsonify, request  # noqa: E402

svc = Flask("fake-stt-words")
SEEN, MODE = [], {"m": "words"}


@svc.post("/v1/audio/transcriptions")
def fake():
    SEEN.append({k: request.form.getlist(k) for k in request.form})
    if MODE["m"] == "no-granularity" and request.form.getlist("timestamp_granularities[]"):
        return jsonify({"error": {"message": "timestamp_granularities is not supported"}}), 400
    seg = {"start": 0.0, "end": 9.0, "text": " Hi Priya. Budget, please."}
    words = [{"word": "Hi", "start": 0.0, "end": 0.3}, {"word": "Priya", "start": 0.3, "end": 0.8},
             {"word": "Budget", "start": 6.0, "end": 6.5}, {"word": "please", "start": 6.5, "end": 7.0}]
    return jsonify({"text": seg["text"], "segments": [seg], "words": words if MODE["m"] == "words" else []})


threading.Thread(target=lambda: svc.run(port=8826, threaded=True), daemon=True).start()
time.sleep(0.8)
cfg.stt["providers"]["groq"].update(base_url="http://127.0.0.1:8826/v1", api_key="gsk_x", upload_format="wav")
ce = stt.CloudEngine(cfg, "groq")
got = ce.run(np.full(16000 * 9, 0.1, np.float32), 100.0, "Them", prompt="last words")
f = SEEN[-1]
check("service: asks for word timings", f.get("timestamp_granularities[]") == ["segment", "word"], str(f))
check("service: names and terms lead the prompt", f.get("prompt", [""])[0].startswith("Northwind, Priya, Tomas")
      and f["prompt"][0].endswith("last words"), str(f.get("prompt")))
check("service: punctuation kept, lines split at the pause, times offset",
      [(u.start, u.text) for u in got] == [(100.0, "Hi Priya."), (106.0, "Budget, please.")], str(got))
MODE["m"] = "no-granularity"
ce2 = stt.CloudEngine(cfg, "groq")
got = ce2.run(np.full(16000 * 9, 0.1, np.float32), 0.0, "Them")
check("service: one without word timings still works (whole segments)",
      not ce2.words and [u.text for u in got] == ["Hi Priya. Budget, please."], str(got))
check("service: prompt kept short", len(stt.CloudEngine(cfg, "groq")._prompt("x" * 2000)) <= 800)

# ---- AI notes ----------------------------------------------------------------------------------------------
p = ai.summary_prompt(cfg)
check("AI notes: glossary of correct spellings", "Correct spellings" in p and "Impact Report" in p)
check("Ask: glossary too", "Priya" in ai.chat_prompt(cfg))
cfg.vocabulary = ""
check("AI notes: no glossary without a vocabulary", "Correct spellings" not in ai.summary_prompt(cfg))

# ---- settings API ----------------------------------------------------------------------------------------------
from minutara import server as srv  # noqa: E402

app, rec = srv.create_app(cfg)
c = app.test_client()
H = {"X-Token": cfg.server_token}
conf = c.get("/api/config", headers=H).get_json()
check("api: settings listed", conf["vocabulary"] == "" and conf["remove_echo"] is True)
c.post("/api/config", headers=H, json={"vocabulary": "Dana, Marcus", "remove_echo": False})
saved = json.loads((TMP / "config.json").read_text())
check("api: saved", cfg.vocabulary == "Dana, Marcus" and cfg.remove_echo is False
      and saved["vocabulary"] == "Dana, Marcus" and saved["remove_echo"] is False)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL PASS")
