"""README screenshots from made-up demo meetings (no real data)."""
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fakes  # noqa: E402

TMP = fakes.make_app_root("minutara-shots-")
OUT = fakes.REPO / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

from minutara import config as config_mod, notes  # noqa: E402
import minutara.server as srv  # noqa: E402

cfg = config_mod.load()
now = datetime.now().astimezone().replace(second=0, microsecond=0)


def at(days, h, m):
    return (now - timedelta(days=days)).replace(hour=h, minute=m).isoformat()


ROADMAP_AI = """# Q4 priorities
- Ship **offline mode** before the November release; it's the top request from field teams
  - Sync conflicts resolved last-write-wins for now, revisit in Q1
- Pause the analytics dashboard rebuild until hiring closes
- Mobile parity work moves to Q1 (Priya to confirm scope with design)

# Customer feedback
- Three enterprise accounts asked for SSO audit logs; Marcus estimates two sprints
- Onboarding drop-off is at the workspace-invite step (41% of new teams stall there)

# Risks
- Offline mode depends on the storage migration finishing by Oct 30
- One backend engineer out for most of November

# Next steps
- **Draft the offline-mode spec and circulate by Friday** (Me)
- **Confirm mobile parity scope with design** (Priya)
- **Estimate SSO audit logs in detail** (Marcus)
- **Book a follow-up on onboarding drop-off** (Me)"""

ROADMAP_T = """[00:04] Me: Thanks for making time. The goal today is to lock the Q4 list so we can stop relitigating it every week.
[00:12] Them: Works for me. Priya and I went through the request tracker yesterday.
[00:19] Them: Offline mode is still the top ask from the field teams, by a wide margin.
[00:27] Me: Agreed. If we only ship one big thing this quarter, that's it.
[00:35] Them: The catch is the storage migration. It has to land by the thirtieth or offline mode slips.
[00:44] Me: Let's make that the explicit dependency in the spec. I'll draft it and send it round by Friday.
[00:53] Them: On the dashboard rebuild, I'd pause it until the new hires start. We can't staff both.
[01:02] Me: Fine by me. What about mobile parity?
[01:07] Them: Priya thinks it's a Q1 job. She wants to confirm the scope with design first.
[01:15] Me: Good. And the SSO audit logs?
[01:19] Them: Three enterprise customers have asked. My rough guess is two sprints.
[01:26] Me: Can you firm that up? I'd like a real estimate before we promise anything.
[01:31] Them: Will do."""

demo = [
    ("q4-roadmap", "Q4 roadmap review", at(0, 10, 30), 2860, "Product", ROADMAP_AI,
     "- offline mode = top priority\n- storage migration by 10/30??\n- ask Priya re: mobile", ROADMAP_T,
     ["Priya Shah", "Marcus Lee"]),
    ("northwind", "Customer call: Northwind", at(0, 9, 0), 1925, "Clients",
     "# Their setup\n- 40 seats, moving off spreadsheets\n\n# Next steps\n- **Send pricing for 50 seats** (Me)",
     "", "[00:03] Me: Thanks for joining.\n[00:06] Them: Happy to.", ["Dana Ortiz"]),
    ("one-on-one", "1:1 with Sam", at(1, 15, 0), 1710, "", "# Topics\n- Conference talk proposal\n- Team rotation",
     "talk proposal due 11/8", "[00:02] Me: How's the week going?", []),
    ("design-crit", "Design crit: onboarding", at(1, 11, 0), 3300, "Product", "# Findings\n- Invite step is confusing",
     "", "[00:05] Them: Let's start with the invite screen.", []),
    ("weekly-sync", "Weekly team sync", at(3, 9, 30), 2400, "", "# Updates\n- Release on track", "",
     "[00:01] Me: Morning all.", []),
]
for nid, title, created, dur, folder, summary, mine, transcript, attendees in demo:
    notes.create(cfg, nid, title=title, created_at=created, duration_s=dur, folder=folder,
                 summary=summary, notes=mine, attendees=attendees, audio="",
                 summary_meta={"provider": "anthropic", "model": "claude-opus-5-5", "at": created})
    (cfg.transcripts_dir / f"{nid}.txt").write_text(transcript, encoding="utf-8")
    if folder:
        notes.add_folder(cfg, folder)
cfg.ai["provider"] = "anthropic"
cfg.ai["providers"]["anthropic"]["api_key"] = "demo"

app, rec = srv.create_app(cfg)
threading.Thread(target=lambda: app.run(host="127.0.0.1", port=8803, threaded=True), daemon=True).start()
time.sleep(1.2)
from playwright.sync_api import sync_playwright  # noqa: E402

base, T = "http://127.0.0.1:8803", cfg.server_token


def fake_status(route, extra):
    s = json.loads(route.fetch().text())
    s.update(extra)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(s))


with sync_playwright() as p:
    browser = p.chromium.launch(**fakes.chromium())
    for theme in ("light", "dark"):
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, color_scheme=theme,
                                  device_scale_factor=1)
        pg = ctx.new_page()
        pg.goto(f"{base}/?token={T}#/")
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / f"home-{theme}.png"))
        pg.goto(f"{base}/?token={T}#/note/q4-roadmap")
        pg.wait_for_timeout(1000)
        pg.click('[data-tab="ai"]')
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / f"ai-notes-{theme}.png"))
        pg.click('[data-tab="transcript"]')
        pg.wait_for_timeout(400)
        pg.screenshot(path=str(OUT / f"transcript-{theme}.png"))
        ctx.close()

    # The floating window's faces, with a made-up status.
    for face, size, extra in (
        ("prompt", (184, 164), {"widget": {"mode": "prompt", "app": "Zoom"}, "meeting": {"app": "Zoom"}}),
        ("recording", (300, 60), {"state": "recording", "elapsed": 754, "title": "Q4 roadmap review",
                                  "widget": {"mode": "recording"}}),
    ):
        ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme="dark",
                                  device_scale_factor=2)
        pg = ctx.new_page()
        pg.route("**/api/status*", (lambda e: (lambda route: fake_status(route, e)))(extra))
        pg.goto(f"{base}/widget?token={T}")
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / f"recorder-{face}.png"))
        ctx.close()

    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme="light")
    pg = ctx.new_page()
    pg.goto(f"{base}/?token={T}#/settings")
    pg.wait_for_timeout(1200)
    pg.locator("#stt-provider").scroll_into_view_if_needed()
    pg.evaluate("window.scrollBy(0, -60)")
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / "settings-transcription.png"))
    browser.close()

for f in sorted(OUT.iterdir()):
    print(f.name, f.stat().st_size // 1024, "KB")
