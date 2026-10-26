"""Transcription settings, the live transcript and the credit line, in Chromium."""
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-uistt-")
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

from minutara import config as config_mod, stt  # noqa: E402
import minutara.server as srv  # noqa: E402

stt.LiveTranscriber.MIN_CHUNK_S = 1.0
stt.LiveTranscriber.POLL_S = 0.1
cfg = config_mod.load()
cfg.stt["live_chunk_s"] = 1
app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8799, threaded=True), daemon=True).start()

from flask import Flask, jsonify  # noqa: E402
svc = Flask("fake-stt")


@svc.post("/v1/audio/transcriptions")
def tr():
    return jsonify({"text": "ok", "segments": []})


threading.Thread(target=lambda: svc.run(port=8824, threaded=True), daemon=True).start()
time.sleep(1.2)
from playwright.sync_api import sync_playwright  # noqa: E402

base = "http://127.0.0.1:8799"
T = cfg.server_token
problems, errors = [], []


def expect(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        problems.append(name)


def post(path, body=None):
    req = urllib.request.Request(f"{base}{path}?token={T}", data=json.dumps(body or {}).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    ctx = browser.new_context(viewport={"width": 1100, "height": 860})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{base}/?token={T}#/settings")
    pg.wait_for_selector("#stt-provider")
    pg.wait_for_timeout(700)
    expect("defaults: built-in transcription", pg.eval_on_selector("#stt-provider", "e => e.value") == "local")
    expect("defaults: says nothing leaves", "Nothing leaves" in pg.inner_text("#stt-where"))
    expect("defaults: live transcription off", not pg.is_checked('[data-key="live_transcribe"]'))
    expect("defaults: model rows shown for built-in", pg.is_visible('[data-key="whisper_model"]'))
    expect("credit line", pg.inner_text(".credit").strip() == "Minutara  · Made by VanDevs".replace("  ", " ")
           or "Made by VanDevs" in pg.inner_text(".credit"), pg.inner_text(".credit"))
    expect("version shown", "dev" in pg.inner_text("#s-version"), pg.inner_text("#s-version"))
    pg.locator("#stt-provider").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "stt-local.png"))

    # Names and terms, echo removal
    expect("echo removal on by default", pg.is_checked('[data-key="remove_echo"]'))
    pg.fill('[data-key="vocabulary"]', "Priya, Tomas\nImpact Report")
    pg.locator('[data-key="vocabulary"]').blur()
    pg.wait_for_timeout(500)
    expect("names and terms saved", cfg.vocabulary == "Priya, Tomas\nImpact Report", repr(cfg.vocabulary))
    pg.locator('[data-key="vocabulary"]').scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "stt-vocab.png"))

    # Live switch
    pg.check('[data-key="live_transcribe"]')
    pg.wait_for_timeout(400)
    expect("live: saved", cfg.live_transcribe is True)
    expect("live: explains the cost", "processor cores" in pg.inner_text("#live-hint"))

    # Groq
    pg.select_option("#stt-provider", "groq")
    pg.wait_for_selector('[data-sttp="api_key"]')
    expect("groq: saved", cfg.stt["provider"] == "groq")
    expect("groq: warns audio leaves", "sent to Groq" in pg.inner_text("#stt-where")
           and "warn" in pg.get_attribute("#stt-where", "class"))
    expect("groq: model rows hidden", not pg.is_visible('[data-key="whisper_model"]'))
    expect("groq: live hint mentions Groq", "sent to Groq" in pg.inner_text("#live-hint"), pg.inner_text("#live-hint"))
    pg.fill('[data-sttp="api_key"]', "gsk_secret")
    pg.press('[data-sttp="api_key"]', "Tab")
    pg.wait_for_timeout(600)
    expect("groq: key saved, never shown", cfg.stt["providers"]["groq"]["api_key"] == "gsk_secret"
           and pg.get_attribute('[data-sttp="api_key"]', "placeholder").startswith("Saved")
           and "gsk_secret" not in pg.content())
    pg.click(".adv >> text=Advanced (address")
    pg.fill('#stt-fields [data-sttp="base_url"]', "http://127.0.0.1:8824/v1")
    pg.press('#stt-fields [data-sttp="base_url"]', "Tab")
    pg.wait_for_timeout(600)
    pg.click('[data-stt-test="groq"]')
    pg.wait_for_function("document.querySelector('#stt-result') && !document.querySelector('#stt-result').hidden"
                         " && !document.querySelector('#stt-result').textContent.includes('Sending')")
    expect("groq: test passes", "Groq answered" in pg.inner_text("#stt-result"), pg.inner_text("#stt-result"))
    expect("sidebar: says transcripts go to Groq", "Groq" in pg.inner_text("#cloud-state"), pg.inner_text("#cloud-state"))
    pg.locator("#stt-provider").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "stt-groq.png"))

    # Your own server
    pg.select_option("#stt-provider", "server")
    pg.wait_for_selector('#stt-fields [data-sttp="base_url"]')
    expect("server: address first", pg.eval_on_selector('#stt-fields [data-sttp="base_url"]', "e => e.value")
           == "http://localhost:8000/v1")
    pg.locator("#stt-provider").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "stt-server.png"))

    # Back to built-in; live transcript on a real (fake-device) recording.
    pg.select_option("#stt-provider", "local")
    pg.wait_for_timeout(500)
    r = post("/api/start", {"title": "Live UI"})
    pg.goto(f"{base}/?token={T}#/note/{r['note_id']}")
    pg.wait_for_selector("#n-title")
    pg.click('[data-tab="transcript"]')
    pg.wait_for_selector(".t-live", timeout=15000)
    expect("live: banner while recording", "Live transcript" in pg.inner_text(".t-live"))
    expect("live: lines drawn as a conversation", pg.locator("#n-transcript .t-msg").count() >= 1)
    pg.screenshot(path=str(OUT / "stt-live-note.png"))
    post("/api/stop")
    pg.wait_for_function("!document.querySelector('.t-live') && document.querySelectorAll('#n-transcript .t-msg').length", timeout=20000)
    expect("live: final transcript replaces it", pg.locator("#n-transcript .t-msg").count() >= 1)

    # Phone width
    pg.goto(f"{base}/?token={T}#/settings")
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(600)
    expect("phone: no sideways scroll", not pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
    pg.locator(".credit").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "stt-credit-phone.png"))
    browser.close()

expect("no page errors", not errors, str(errors))
print("UI FAILURES:", problems or "none")
sys.exit(1 if problems else 0)
