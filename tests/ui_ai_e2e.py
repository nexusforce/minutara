"""Drive the new AI / Ask / Granola / Pocket UI in Chromium against fake services."""
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests  # noqa: F401  (real requests before fakes)

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402
from fakes import write_wav  # noqa: E402
import fake_cloud  # noqa: E402

TMP = fakes.make_app_root("minutara-uiai-")
BASE = fake_cloud.start(8813)
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

from minutara import config as config_mod, notes  # noqa: E402

cfg = config_mod.load()
cfg.server_port = 8798
cfg.ai["provider"] = "anthropic"
cfg.ai["providers"]["anthropic"].update(api_key="sk-ant-good", base_url=BASE + "/anthropic")
cfg.ai["providers"]["ollama"].update(base_url=BASE + "/ollama")
cfg.granola.update(mcp_url=BASE + "/mcp", since="2025-01-01")
config_mod.save(cfg)

now = datetime.now()
board = ("[00:02] Me: Thanks for making time. Budget first, hiring last.\n"
         "[00:09] Them: Finance sent revised numbers, about eight percent lower.\n"
         "[00:24] Me: Can we protect the research line?\n"
         "[00:31] Them: Yes, if we push the second offsite to January.")
write_wav(cfg.recordings_dir / "20261017-1000-board-prep.wav", n=16000 * 3)
(cfg.transcripts_dir / "20261017-1000-board-prep.txt").write_text(board)
notes.create(cfg, "20261017-1000-board-prep", title="Board prep: Q4 budget",
             created_at=now.replace(hour=10, minute=2).astimezone().isoformat(timespec="seconds"),
             audio="20261017-1000-board-prep.wav", notes="Protect research line\nOffsite → January",
             duration_s=1862, folder="Leadership")
notes.add_folder(cfg, "Leadership")

import minutara.server as srv  # noqa: E402

app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8798, threaded=True), daemon=True).start()
time.sleep(1.2)

from playwright.sync_api import sync_playwright  # noqa: E402

url = f"http://127.0.0.1:8798/?token={cfg.server_token}"
problems, console_errors = [], []


def expect(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        problems.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    ctx = browser.new_context(viewport={"width": 1240, "height": 820}, color_scheme="dark")
    page = ctx.new_page()
    page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    page.goto(url)
    page.wait_for_selector(".row")
    page.wait_for_timeout(1300)
    expect("home: Ask box shows when AI is on", page.is_visible("#home-dock .askbox"))
    expect("sidebar: shows the AI provider", "Claude" in page.inner_text("#cloud-state"))
    page.click("#home-dock .ask-chip")
    page.wait_for_selector(".chat-a.md", timeout=15000)
    expect("home ask: answer + sources", "ANSWER" in page.inner_text("#chat-body")
           and "Board prep" in page.inner_text(".chat-src"))
    page.screenshot(path=str(OUT / "20-home-ask.png"))
    page.keyboard.press("Escape")

    # AI notes on a note
    page.click("text=Board prep: Q4 budget")
    page.wait_for_selector("#n-title")
    page.click(".seg [data-tab=ai]")
    page.wait_for_selector("[data-ai-act=gen]")
    page.screenshot(path=str(OUT / "21-ai-empty.png"))
    page.click("[data-ai-act=gen]")
    page.wait_for_selector("#n-summary h2", timeout=20000)
    page.wait_for_timeout(400)
    expect("ai notes: rendered as headings + bullets",
           page.inner_text("#n-summary h2") == "Budget" and page.locator("#n-summary li").count() >= 2)
    expect("ai notes: bold rendered", page.locator("#n-summary strong").count() >= 1)
    expect("ai notes: credited to the model", "claude-opus-5-5" in page.inner_text("#ai-head"))
    page.screenshot(path=str(OUT / "22-ai-notes.png"))

    # ask about this meeting
    page.fill("#dock .askbox input", "What did we decide about the offsite?")
    page.press("#dock .askbox input", "Enter")
    page.wait_for_selector(".chat-a.md", timeout=15000)
    expect("note ask: answer in panel", "ANSWER" in page.inner_text("#chat-body")
           and page.inner_text("#chat-title") == "Ask about this meeting")
    page.screenshot(path=str(OUT / "23-note-ask.png"))
    page.keyboard.press("Escape")

    # settings: AI
    page.goto(url + "#/settings")
    page.wait_for_selector("[data-ai-fetch]")
    page.click("[data-ai-fetch]")
    page.wait_for_selector("#ai-result.ok", timeout=10000)
    expect("settings: fetch models", "3 models" in page.inner_text("#ai-result"))
    page.click("[data-ai-test]")
    page.wait_for_function("document.querySelector('#ai-result').textContent.includes('Connected')", timeout=15000)
    expect("settings: connection test", "OK" in page.inner_text("#ai-result"))
    page.locator("#s-ai").scroll_into_view_if_needed()
    page.evaluate("document.querySelector('#main').scrollTop = document.querySelector('#s-ai').offsetTop - 70")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "24-settings-ai.png"))
    page.select_option("#ai-provider", "ollama")
    page.wait_for_selector("[data-aip=num_ctx]", state="attached")
    expect("settings: switching provider shows its fields",
           page.locator("[data-aip=base_url]").first.input_value() == BASE + "/ollama"
           and "Nothing leaves" in page.inner_text("#ai-where"))
    expect("settings: provider saved", config_mod.load().ai["provider"] == "ollama")
    page.select_option("#ai-provider", "anthropic")
    page.wait_for_timeout(500)

    # settings: Granola sign-in + import through a real popup
    page.evaluate("document.querySelector('#main').scrollTop = document.querySelector('#s-granola').offsetTop - 70")
    with ctx.expect_page() as pop:
        page.click("#gr-signin")
    popup = pop.value
    popup.wait_for_load_state()
    popup.wait_for_selector("text=Signed in to Granola", timeout=15000)
    expect("granola: popup lands on Minutara's 'Signed in' page", "importing your meetings" in popup.inner_text("body"))
    popup.close()
    page.wait_for_function("document.querySelector('#gr-msg').textContent.includes('Done')", timeout=30000)
    expect("granola: progress reported in settings", "3 imported" in page.inner_text("#gr-msg"))
    expect("granola: shows signed in", "Signed in" in page.inner_text("#gr-account"))
    expect("granola: partial-transcript warning listed", "without a transcript" in page.inner_text("#gr-errors"))
    page.screenshot(path=str(OUT / "25-settings-granola.png"))
    page.evaluate("document.querySelector('#main').scrollTop = document.querySelector('#s-pocket').offsetTop - 70")
    page.wait_for_timeout(300)
    expect("pocket: USB status shown", "Not connected" in page.inner_text("#pk-usb"))
    page.screenshot(path=str(OUT / "26-settings-pocket.png"))

    # imported meetings on Home and in a note
    page.goto(url + "#/")
    page.wait_for_selector("text=Customer call: onboarding")
    page.wait_for_timeout(500)
    expect("home: Granola meetings listed with source", "Granola" in page.inner_text("#list"))
    page.click("text=Customer call: onboarding")
    page.wait_for_selector("#n-summary h2")
    expect("granola note: opens on AI notes, credited to Granola", "Imported from Granola" in page.inner_text("#ai-head"))
    page.click(".seg [data-tab=transcript]")
    page.wait_for_timeout(300)
    expect("granola note: transcript bubbles with real names",
           "Dana Lee" in page.inner_text("#n-transcript") and page.locator(".t-msg.me").count() == 1)
    page.screenshot(path=str(OUT / "27-granola-transcript.png"))
    page.click(".seg [data-tab=notes]")
    expect("granola note: your private notes imported", "ask about SSO" in page.input_value("#n-body"))

    # markdown escapes from Granola render cleanly
    html = page.evaluate("md('- S\\\\&P asked for \\\\~12 guides\\n- **Bold** and *em*')")
    expect("markdown: Granola escapes cleaned", "S&amp;P" in html and "~12" in html and "<strong>Bold</strong>" in html
           and "<em>em</em>" in html, html)

    # turning AI off hides Ask
    config = {"ai": {"provider": "off"}}
    page.evaluate("cfg => fetch('/api/config?token=' + token, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(cfg)})", config)
    page.goto(url + "#/")
    page.wait_for_timeout(1500)
    expect("ai off: Ask box hidden", not page.is_visible("#home-dock .askbox"))

    phone = browser.new_context(viewport={"width": 390, "height": 820}, color_scheme="light",
                                device_scale_factor=2).new_page()
    phone.on("pageerror", lambda e: console_errors.append(str(e)))
    phone.goto(url + "#/note/granola-11111111-aaaa-4bbb-8ccc-000000000001")
    phone.wait_for_selector("#n-summary h2")
    phone.wait_for_timeout(400)
    phone.screenshot(path=str(OUT / "28-phone-ai-notes.png"))

    expect("no JS errors", not console_errors, "; ".join(console_errors[:3]))
    browser.close()

print()
print("UI FAILURES:", problems or "none")
