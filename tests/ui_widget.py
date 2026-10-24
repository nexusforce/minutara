"""The floating window's page and the meeting settings, in Chromium."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-uiw-")
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

from minutara import config as config_mod  # noqa: E402
import minutara.server as srv  # noqa: E402

cfg = config_mod.load()
app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8797, threaded=True), daemon=True).start()
time.sleep(1.2)
from playwright.sync_api import sync_playwright  # noqa: E402

base = "http://127.0.0.1:8797"
T = cfg.server_token
problems, errors = [], []


def expect(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        problems.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    for theme in ("dark", "light"):
        ctx = browser.new_context(color_scheme=theme)
        from minutara.widget import SIZES
        for mode, (w, h) in SIZES.items():
            pg = ctx.new_page()
            pg.set_viewport_size({"width": w, "height": h})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{base}/widget?token={T}")
            pg.evaluate(f"window.minutaraMode('{mode}')")
            pg.wait_for_timeout(200)
            # nothing may overflow the small window
            over = pg.evaluate("""() => [...document.querySelectorAll('.face button, .t2, .t1, .time')]
                .filter(e => e.offsetParent).some(e => { const r = e.getBoundingClientRect();
                return r.right > innerWidth + 0.5 || r.bottom > innerHeight + 0.5 || r.left < 0 || r.top < 0; })""")
            expect(f"widget {mode} ({theme}): fits its window", not over)
            pg.screenshot(path=str(OUT / f"w-{mode}-{theme}.png"))
            pg.close()
        ctx.close()

    # Driving it: preview a meeting, start from the pop-up, pause, stop.
    ctx = browser.new_context(color_scheme="dark")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.set_viewport_size({"width": 184, "height": 164})
    pg.goto(f"{base}/widget?token={T}")
    import json
    import urllib.request

    def post(path, body=None):
        req = urllib.request.Request(f"{base}{path}?token={T}", data=json.dumps(body or {}).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())

    def status():
        return json.loads(urllib.request.urlopen(f"{base}/api/status?token={T}").read())

    post("/api/meeting/preview")
    pg.evaluate("window.minutaraMode('prompt')")
    pg.wait_for_function("document.querySelector('#p-app').textContent.includes('Zoom')")
    expect("widget: names the meeting app", "Zoom meeting" in pg.inner_text("#p-app"))
    pg.click("#p-start")
    pg.wait_for_timeout(800)
    s = status()
    expect("widget: Start records", s["state"] == "recording" and s["title"] == "Zoom meeting")
    expect("widget: a preview isn't tied to a real meeting", s["meeting_app"] == "")
    pg.set_viewport_size({"width": 300, "height": 60})
    pg.evaluate("window.minutaraMode('recording')")
    pg.wait_for_timeout(1300)
    expect("widget: timer runs", pg.inner_text("#r-time") not in ("0:00", ""))
    pg.click("#r-pause")
    pg.wait_for_timeout(700)
    expect("widget: pause", status()["state"] == "paused" and "Paused" in pg.inner_text("#r-lbl"))
    pg.screenshot(path=str(OUT / "w-recording-paused.png"))
    pg.click("#r-pause")
    pg.wait_for_timeout(600)
    expect("widget: resume", status()["state"] == "recording")
    pg.click("#r-hide")
    pg.wait_for_timeout(300)
    expect("widget: – tucks it away", app.config["MINUTARA"]["widget"].hidden_for == s["note_id"])
    pg.click("#r-stop")
    end = time.time() + 10
    while time.time() < end and status()["state"] != "idle":
        time.sleep(0.2)
    expect("widget: stop saves", status()["state"] == "idle")

    # Ended face buttons
    rec.meeting_app = ""
    pg.set_viewport_size({"width": 236, "height": 148})
    pg.evaluate("window.minutaraMode('ended')")
    pg.click("#e-keep")
    pg.wait_for_timeout(300)
    expect("widget: keep recording acknowledged", status()["meeting"]["ended"] == "")

    # In the app: no floating window here (not Windows), so the prompt shows in-page.
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1180, "height": 800})
    page.goto(f"{base}/?token={T}")
    post("/api/meeting/preview")
    page.wait_for_selector("#meet-card:not([hidden])", timeout=5000)
    expect("app: meeting card when there's no floating window", "Zoom meeting" in page.inner_text("#mc-title"))
    page.screenshot(path=str(OUT / "w-app-card.png"))
    page.click("#mc-skip")
    page.wait_for_selector("#meet-card[hidden]", state="attached", timeout=5000)
    expect("app: not now hides it", status()["meeting"]["dismissed"] is True)

    # Settings
    page.goto(f"{base}/?token={T}#/settings")
    page.wait_for_selector("[data-key=meeting_detect]", state="attached")
    page.wait_for_timeout(500)
    expect("settings: detect on by default", page.is_checked("[data-key=meeting_detect]"))
    expect("settings: floating recorder on by default", page.is_checked("[data-key=floating_recorder]"))
    page.locator("text=Meetings").first.scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "w-settings.png"))
    page.locator("[data-key=floating_recorder]").click()
    page.wait_for_timeout(500)
    expect("settings: floating recorder toggle saves", config_mod.load().floating_recorder is False)
    page.click("#m-preview")
    page.wait_for_timeout(500)
    expect("settings: preview button", "20 seconds" in page.inner_text("#m-msg"))
    expect("no JS errors", not errors)
    if errors:
        print(errors)
    browser.close()

print("UI FAILURES:", problems or "none")
