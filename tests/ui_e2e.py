"""Drive the real GUI in Chromium against a live Minutara server (fake audio)."""
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import FakeWhisperModel, write_wav  # noqa: E402

TMP = fakes.make_app_root("minutara-ui-")
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

from minutara import config as config_mod, notes  # noqa: E402

cfg = config_mod.load()

# --- seed a realistic library ----------------------------------------------
now = datetime.now()


def seed(stem, title, when, transcript=None, typed="", folder="", source="pc", audio=True, dur=None):
    if audio:
        ext = ".mp3" if source == "pocket" else ".wav"
        if ext == ".wav":
            write_wav(cfg.recordings_dir / f"{stem}.wav", n=16000 * 3)
        else:
            (cfg.recordings_dir / f"{stem}.mp3").write_bytes(b"\xff\xfb" + b"\0" * 400)
    if transcript:
        (cfg.transcripts_dir / f"{stem}.txt").write_text(transcript, encoding="utf-8")
    notes.create(cfg, stem, title=title, created_at=when.astimezone().isoformat(timespec="seconds"),
                 audio=f"{stem}{'.mp3' if source == 'pocket' else '.wav'}" if audio else "",
                 notes=typed, folder=folder, source=source, duration_s=dur)
    if folder:
        notes.add_folder(cfg, folder)


board = """[00:02] Me: Thanks for making time. I want to get through the Q4 budget first and leave the hiring plan for the end.
[00:09] Them: Works for me. Finance sent the revised numbers last night, so we're looking at about eight percent lower than the draft.
[00:18] Them: Most of that comes out of events. Travel is basically flat.
[00:24] Me: That's better than I expected. Can we protect the research line?
[00:31] Them: I think so, if we push the second offsite to January.
[00:38] Me: Let's do that. I'll draft the note to the board this week.
[00:45] Them: Great. Send it to me before Friday and I'll add the finance appendix."""

seed("20261017-1000-board-prep", "Board prep: Q4 budget", now.replace(hour=10, minute=2), board,
     typed="Budget down ~8% vs draft — mostly events\nProtect research line\nMove 2nd offsite → January\n\nTODO: draft board note before Fri",
     folder="Leadership", dur=1862)
seed("20261017-0915-standup", "Team standup", now.replace(hour=9, minute=15),
     "[00:01] Me: Quick one today.\n[00:04] Them: The survey export is ready for review.", folder="Team", dur=540)
seed("pocket-2026-10-16-0003", "Pocket recording 0003", (now - timedelta(days=1)).replace(hour=16, minute=40),
     source="pocket")
seed("20261016-1400-customer-onboarding", "Customer call: onboarding feedback",
     (now - timedelta(days=1)).replace(hour=14, minute=0),
     "[00:00] Them: The setup guide was clear.\n[00:06] Me: Good to hear. Anything confusing?",
     folder="Customers", dur=2710)
seed("20261013-1100-1-1-jordan", "1:1 with Jordan", (now - timedelta(days=4)).replace(hour=11, minute=3),
     "[00:00] Me: How's the new project going?", dur=1800)
seed("20261013-0930-planning", "Quarterly planning", (now - timedelta(days=4)).replace(hour=9, minute=30),
     "[00:00] Them: Let's start with last quarter.", folder="Leadership", dur=3605)

# fake Whisper output for the live recording: one pass per channel
FakeWhisperModel.scripts = [
    [(1.0, 3.0, "Okay, let's kick off. First item is the survey timeline."),
     (9.0, 11.0, "Agreed, Tuesday works.")],
    [(4.0, 8.0, "We can have the cleaned data by Tuesday if nothing else slips.")],
]

import minutara.server as srv  # noqa: E402

app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8799, threaded=True), daemon=True).start()
time.sleep(1.2)

from playwright.sync_api import sync_playwright  # noqa: E402

url = f"http://127.0.0.1:8799/?token={cfg.server_token}"
problems = []
console_errors = []


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        problems.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    ctx = browser.new_context(viewport={"width": 1180, "height": 800}, color_scheme="dark")
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("response", lambda r: r.status >= 400 and print("HTTP", r.status, r.url.split("?")[0]))
    page.on("dialog", lambda d: d.accept("Projects") if d.type == "prompt" else d.accept())

    page.goto(url)
    page.wait_for_selector(".row")
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "01-home-dark.png"))
    rows = page.locator(".row").count()
    expect("home: lists all seeded meetings", rows == 6)
    expect("home: grouped by day", page.locator(".day").first.inner_text() == "Today")
    expect("home: untranscribed pocket row offers Transcribe",
           page.locator(".row", has_text="Pocket recording 0003").locator("[data-transcribe]").count() == 1)
    expect("sidebar: folders listed", page.locator("#folders .nav").count() == 3)

    # open an existing note → transcript as chat
    page.click("text=Board prep: Q4 budget")
    page.wait_for_selector("#n-title")
    page.wait_for_timeout(300)
    expect("note: title loaded", page.input_value("#n-title") == "Board prep: Q4 budget")
    expect("note: typed notes loaded", "Protect research line" in page.input_value("#n-body"))
    page.screenshot(path=str(OUT / "02-note-notes.png"))
    page.click(".seg [data-tab=transcript]")
    page.wait_for_timeout(200)
    expect("note: transcript drawn as chat", page.locator(".t-msg.me").count() == 3
           and page.locator(".t-msg.them").count() == 4)
    expect("note: player in dock", page.locator("#d-play").count() == 1)
    page.screenshot(path=str(OUT / "03-note-transcript.png"))

    # live flow through the UI: start from the home card, type, pause, stop
    page.click(".back")
    page.wait_for_selector("#now-title")
    page.fill("#now-title", "Survey timeline check-in")
    page.click("#now-start")
    page.wait_for_selector("#d-stop")
    page.wait_for_timeout(300)
    expect("live: navigated into the new note", page.input_value("#n-title") == "Survey timeline check-in")
    page.click("#n-body")
    page.keyboard.type("Data cleaned by Tuesday?\n- check export\n")
    page.wait_for_timeout(1500)
    expect("live: autosaved typed notes", "check export" in (notes.load(cfg, rec.note_id) or {}).get("notes", ""))
    page.screenshot(path=str(OUT / "04-live-recording.png"))
    page.click("#d-pause")
    page.wait_for_timeout(1300)
    expect("live: paused label", page.inner_text("#d-label") == "Paused")
    page.click("#d-pause")
    page.wait_for_timeout(600)
    page.click(".back")
    page.wait_for_selector("#now-live:not([hidden])")
    page.wait_for_timeout(1100)
    expect("live: home card shows recording", "Survey timeline" in page.inner_text("#live-title"))
    page.screenshot(path=str(OUT / "05-home-live.png"))
    page.click("#live-open")
    page.wait_for_selector("#d-stop")
    page.click("#d-stop")
    page.wait_for_selector("#d-play", timeout=20000)
    expect("stop: player appears once saved", True)
    page.click(".seg [data-tab=transcript]")
    page.wait_for_selector(".t-msg", timeout=20000)
    page.wait_for_timeout(400)
    expect("auto-transcript shows up without a reload",
           "cleaned data by Tuesday" in page.inner_text("#n-transcript"))
    page.screenshot(path=str(OUT / "06-after-stop-transcript.png"))

    # folder chip + new folder via prompt
    page.select_option("#n-folder", "__new__")
    page.wait_for_timeout(800)
    expect("folder: new folder created from chip", page.locator("#folders .nav", has_text="Projects").count() == 1)

    # search
    page.fill("#q", "offsite")
    page.wait_for_timeout(800)
    expect("search: finds transcript text", page.locator(".row").count() == 1
           and "Board prep" in page.inner_text(".row"))
    page.fill("#q", "")
    page.keyboard.press("Escape")

    # settings in light mode
    page.goto(url + "#/settings")
    page.wait_for_selector("#s-mic option:nth-child(3)", state="attached")
    page.select_option("#s-theme", "light")
    page.wait_for_timeout(300)
    expect("settings: mic list populated", page.locator("#s-mic option").count() == 4)
    page.screenshot(path=str(OUT / "07-settings-light.png"), full_page=False)
    page.click("#t-mic")
    page.wait_for_selector("#t-result:not([hidden])", timeout=15000)
    expect("settings: mic test reports " + page.inner_text("#t-msg"), "works" in page.inner_text("#t-msg").lower())
    expect("settings: mic test has playback", page.eval_on_selector("#t-audio", "a => a.src.startsWith('data:audio/wav')"))
    page.locator("#t-result").scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "07b-mic-test.png"))
    from fakes import FakeAudio, MIC
    FakeAudio.noise = {MIC}
    page.click("#t-mic")
    page.wait_for_function("!document.querySelector('#t-mic').disabled && document.querySelector('#t-msg').className === 'warn'", timeout=15000)
    FakeAudio.noise = set()
    expect("settings: static mic is flagged", "static" in page.inner_text("#t-msg"))
    page.screenshot(path=str(OUT / "07c-mic-test-static.png"))
    expect("settings: speakers list populated", page.locator("#s-spk option").count() == 3)
    page.select_option("#s-spk", "Headphones (WH-1000XM5)")
    page.wait_for_timeout(500)
    expect("settings: speakers choice saves", config_mod.load().speaker_device == "Headphones (WH-1000XM5)")
    page.click("#t-spk")
    page.wait_for_function("!document.querySelector('#t-spk').disabled && document.querySelector('#t-msg').className", timeout=15000)
    expect("settings: speakers test uses the chosen output", "WH-1000XM5" in page.inner_text("#t-facts"))
    expect("settings: speakers test works", "Speaker capture works" in page.inner_text("#t-msg"))
    page.locator("#t-result").scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "07d-speakers.png"))
    page.locator("[data-key=auto_transcribe]").click()
    page.wait_for_timeout(500)
    expect("settings: toggle saves", config_mod.load().auto_transcribe is False)
    page.goto(url)
    page.wait_for_selector(".row")
    page.wait_for_timeout(400)
    page.screenshot(path=str(OUT / "08-home-light.png"))
    page.click("text=Customer call: onboarding feedback")
    page.wait_for_selector("#n-title")
    page.click(".seg [data-tab=transcript]")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "09-note-light.png"))
    page.click("#phone-btn")
    page.wait_for_selector(".qr svg")
    page.screenshot(path=str(OUT / "10-phone-qr.png"))
    page.keyboard.press("Escape")

    # phone layout
    phone = browser.new_context(viewport={"width": 390, "height": 820}, color_scheme="dark",
                                device_scale_factor=2).new_page()
    phone.on("pageerror", lambda e: console_errors.append(str(e)))
    phone.goto(url)
    phone.wait_for_selector(".row")
    phone.wait_for_timeout(400)
    phone.screenshot(path=str(OUT / "11-phone-home.png"))
    phone.click("[data-open-side]")
    phone.wait_for_timeout(400)
    phone.screenshot(path=str(OUT / "12-phone-sidebar.png"))

    expect("no JS errors in the console", not console_errors)
    if console_errors:
        print("\n".join(console_errors))
    browser.close()

print()
print("UI FAILURES:", problems if problems else "none")
