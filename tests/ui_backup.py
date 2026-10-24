"""Settings → Backup in Chromium."""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-uib-")
OUT = Path(__file__).parent / "shots"
HOME = TMP / "home"
(HOME / "My Drive").mkdir(parents=True)
os.environ["HOME"] = str(HOME)

from minutara import config as config_mod, notes  # noqa: E402
import minutara.server as srv  # noqa: E402

cfg = config_mod.load()
notes.create(cfg, "20261020-1000-weekly", title="Weekly sync", notes="hello", created_at="2026-10-20T10:00:00+00:00")
app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8802, threaded=True), daemon=True).start()
time.sleep(1.2)
from playwright.sync_api import sync_playwright  # noqa: E402

base, T = "http://127.0.0.1:8802", cfg.server_token
problems, errors = [], []


def expect(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        problems.append(name)


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    pg = browser.new_page(viewport={"width": 1100, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{base}/?token={T}#/settings")
    pg.wait_for_selector("#b-dir")
    pg.wait_for_timeout(800)
    expect("off by default", "Off" in pg.inner_text("#b-status") and pg.is_disabled("#b-run"))
    expect("Google Drive suggested", "Use Google Drive" in pg.inner_text("#b-suggest"), pg.inner_text("#b-suggest"))
    pg.fill("#b-dir", str(cfg.notes_dir))
    pg.click("#b-save")
    pg.wait_for_timeout(500)
    expect("bad folder explained", "outside" in pg.inner_text("#b-msg") and cfg.backup_dir == "")
    pg.click("#b-suggest [data-bpath]")
    pg.wait_for_function("document.querySelector('#b-status').textContent.startsWith('Last backup')", timeout=15000)
    dest = HOME / "My Drive" / "Minutara backup"
    expect("suggestion used and backed up", cfg.backup_dir == str(dest) and (dest / "notes" / "20261020-1000-weekly.json").exists())
    expect("status says when", "today at" in pg.inner_text("#b-status"), pg.inner_text("#b-status"))
    expect("error cleared", "err" not in (pg.get_attribute("#b-msg", "class") or "") and "Google Drive" in pg.inner_text("#b-msg"))
    pg.check('[data-key="backup_audio"]')
    pg.wait_for_timeout(400)
    expect("recordings switch saved", cfg.backup_audio is True)
    pg.click("#b-run")
    pg.wait_for_function("document.querySelector('#b-status').textContent.startsWith('Last backup')")
    pg.click("#b-restore")
    pg.wait_for_function("document.querySelector('#b-status').textContent.startsWith('Restored')")
    expect("restore reports", "Restored 0 meetings" in pg.inner_text("#b-status"))
    pg.locator("#b-dir").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "backup.png"))
    pg.click("#b-off")
    pg.wait_for_timeout(600)
    expect("turned off", cfg.backup_dir == "" and "Off" in pg.inner_text("#b-status"))
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(300)
    expect("phone: no sideways scroll", not pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
    browser.close()

expect("no page errors", not errors, str(errors))
print("UI FAILURES:", problems or "none")
sys.exit(1 if problems else 0)
