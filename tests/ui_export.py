"""Export buttons in the app page, in Chromium: copy and download each part,
everything, and Export all from Settings."""
import io
import sys
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-uix-")
OUT = Path(__file__).parent / "shots"
OUT.mkdir(exist_ok=True)

from minutara import config as config_mod, notes  # noqa: E402
import minutara.server as srv  # noqa: E402

cfg = config_mod.load()
n = notes.create(cfg, "2026-10-18_1410_weekly", title="Weekly sync", created_at="2026-10-18T14:10:00+02:00",
                 summary="# Summary\n\n* **Ship** on Friday", notes="my own words", folder="Work")
notes.add_folder(cfg, "Work")
cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
notes.transcript_path(cfg, n).write_text("[00:00] Me: hello there\n", encoding="utf-8")
app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8798, threaded=True), daemon=True).start()
time.sleep(1.2)
from playwright.sync_api import sync_playwright  # noqa: E402

base = "http://127.0.0.1:8798"
T = cfg.server_token
problems, errors = [], []


def expect(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        problems.append(name)


def clip(pg):
    return pg.evaluate("navigator.clipboard.readText()")


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    ctx = browser.new_context(accept_downloads=True, permissions=["clipboard-read", "clipboard-write"],
                              viewport={"width": 1100, "height": 800})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{base}/?token={T}#/note/{n['id']}")
    pg.wait_for_selector("#n-title")
    pg.wait_for_timeout(600)
    pg.click('[data-tab="notes"]')

    # My notes: type, then Copy includes what was just typed; Download saves it first.
    pg.fill("#n-body", "my own words, edited")
    pg.click('#tab-notes [data-copy-part]')
    pg.wait_for_timeout(200)
    expect("my notes: copy", clip(pg) == "my own words, edited", clip(pg))
    with pg.expect_download() as d:
        pg.click('#tab-notes [data-dl-part]')
    dl = d.value
    expect("my notes: download name", dl.suggested_filename == "2026-10-18 1410 Weekly sync - My notes.txt",
           dl.suggested_filename)
    expect("my notes: download has the latest typing", Path(dl.path()).read_text().strip() == "my own words, edited")
    pg.screenshot(path=str(OUT / "x-notes.png"))

    # AI notes
    pg.click('[data-tab="ai"]')
    pg.wait_for_selector('[data-ai-act="download"]')
    with pg.expect_download() as d:
        pg.click('[data-ai-act="download"]')
    txt = Path(d.value.path()).read_text()
    expect("AI notes: download is readable text", txt.startswith("SUMMARY") and "- Ship on Friday" in txt, txt)
    pg.click('[data-ai-act="copy"]')
    pg.wait_for_timeout(200)
    expect("AI notes: copy", "Ship" in clip(pg))
    pg.screenshot(path=str(OUT / "x-ai.png"))

    # Transcript
    pg.click('[data-tab="transcript"]')
    pg.wait_for_timeout(300)
    expect("transcript: tools shown", pg.is_visible("#t-tools [data-copy-part]"))
    pg.click("#t-tools [data-copy-part]")
    pg.wait_for_timeout(400)
    expect("transcript: copy", clip(pg).strip() == "[00:00] Me: hello there", clip(pg))
    with pg.expect_download() as d:
        pg.click("#t-tools [data-dl-part]")
    expect("transcript: download name", d.value.suggested_filename.endswith("Transcript.txt"), d.value.suggested_filename)
    pg.screenshot(path=str(OUT / "x-transcript.png"))

    # Everything, from the ⋯ menu
    pg.click("#more-btn")
    pg.screenshot(path=str(OUT / "x-menu.png"))
    with pg.expect_download() as d:
        pg.click("#m-all")
    doc = Path(d.value.path()).read_text()
    expect("everything: one document with all three parts",
           all(s in doc for s in ("WEEKLY SYNC", "AI notes", "My notes", "edited", "Transcript", "hello there")), doc)
    expect("everything: menu closes", pg.is_hidden("#more-menu"))
    pg.click("#more-btn")
    pg.click("#m-copy-all")
    pg.wait_for_timeout(500)
    expect("everything: copy", "WEEKLY SYNC" in clip(pg) and "hello there" in clip(pg))

    # Settings → Export
    pg.goto(f"{base}/?token={T}#/settings")
    pg.wait_for_selector("#x-export")
    pg.wait_for_timeout(600)
    opts = pg.eval_on_selector_all("#x-folder option", "os => os.map(o => o.value)")
    expect("export all: folders listed", opts == ["", "Work"], str(opts))
    pg.select_option("#x-folder", "Work")
    pg.uncheck("#x-transcript")
    with pg.expect_download() as d:
        pg.click("#x-export")
    z = zipfile.ZipFile(io.BytesIO(Path(d.value.path()).read_bytes()))
    names = z.namelist()
    expect("export all: zip of the chosen folder", names == ["2026-10-18 1410 Weekly sync.txt"], str(names))
    expect("export all: chosen parts", "Transcript" not in z.read(names[0]).decode())
    for b in ("#x-summary", "#x-notes"):
        pg.uncheck(b)
    pg.click("#x-export")
    pg.wait_for_timeout(300)
    expect("export all: needs a part", "at least one" in pg.inner_text("body"))
    pg.click("#x-save-all")
    pg.wait_for_function("document.querySelector('#x-msg').textContent.startsWith('Saved')")
    expect("save all now: reports", "Saved 1 meeting" in pg.inner_text("#x-msg"), pg.inner_text("#x-msg"))
    expect("save all now: file on disk", (TMP / "Meeting notes" / "2026-10-18 1410 Weekly sync.txt").exists())
    expect("settings: format and auto-save shown",
           pg.eval_on_selector('[data-key="export_format"]', "e => e.value") == "txt"
           and pg.is_checked('[data-key="auto_export"]'))
    pg.locator("#x-export").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "x-settings.png"))
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.wait_for_timeout(200)
    wide = pg.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    expect("settings: no sideways scroll on a phone", not wide)
    pg.locator("#x-export").scroll_into_view_if_needed()
    pg.screenshot(path=str(OUT / "x-settings-phone.png"))
    browser.close()

expect("no page errors", not errors, str(errors))
print("UI FAILURES:", problems or "none")
sys.exit(1 if problems else 0)
