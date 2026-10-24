"""Meeting detection, the floating-window controller, and their API."""
import threading
import time

import fakes  # noqa: F401
TMP = fakes.make_app_root("minutara-meet-")

from minutara import config as config_mod  # noqa: E402
from minutara import meetings, server as srv  # noqa: E402
from minutara.widget import WidgetController  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


ZOOM = r"C:\Users\arman\AppData\Roaming\Zoom\bin\Zoom.exe"
TEAMS = "MSTeams_8wekyb3d8bbwe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
pats = meetings.patterns(meetings.DEFAULT_APPS, False)
check("detect: Zoom", meetings.find_meeting([ZOOM], pats) == "Zoom")
check("detect: new Teams (packaged app)", meetings.find_meeting([TEAMS], pats) == "Teams")
check("detect: classic Teams", meetings.find_meeting([r"C:\Users\a\AppData\Local\Microsoft\Teams\current\Teams.exe"], pats) == "Teams")
check("detect: Webex", meetings.find_meeting([r"C:\Program Files\Cisco Spark\CiscoCollabHost.exe"], pats) == "Webex")
check("detect: browsers off by default", meetings.find_meeting([CHROME], pats) == "")
check("detect: browsers when turned on", meetings.find_meeting([CHROME], meetings.patterns(meetings.DEFAULT_APPS, True)) == "Chrome")
check("detect: other mic users ignored", meetings.find_meeting([r"C:\Windows\SoundRecorder.exe", "Minutara.exe"], pats) == "")
check("detect: custom app list", meetings.find_meeting([r"D:\Apps\GoTo\g2mcomm.exe"], meetings.patterns("g2mcomm", False)) == "G2mcomm")
check("detect: no registry off Windows", meetings.mic_users() == [])

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TP = [t.strip() for t in meetings.DEFAULT_TITLES.split(",")]
win = lambda *t: (lambda: list(t))  # noqa: E731
check("browser: Teams on the web (the user's case)",
      meetings.find_meeting([EDGE], pats, win(("msedge.exe", "Minutara"), ("msedge.exe", "Chat | Microsoft Teams")), TP) == "Teams")
check("browser: Teams free / personal",
      meetings.find_meeting([EDGE], pats, win(("msedge.exe", "Microsoft Teams (free)")), TP) == "Teams")
check("browser: Google Meet (en dash title)",
      meetings.find_meeting([CHROME], pats, win(("chrome.exe", "Meet \u2013 abc-defg-hij")), TP) == "Google Meet")
check("browser: Zoom web client",
      meetings.find_meeting([CHROME], pats, win(("chrome.exe", "Zoom Meeting - Google Chrome")), TP) == "Zoom")
check("browser: mic on some other page doesn't count",
      meetings.find_meeting([EDGE], pats, win(("msedge.exe", "Voice typing - Google Docs")), TP) == "")
check("browser: a Teams tab in a browser that isn't using the mic doesn't count",
      meetings.find_meeting([CHROME], pats, win(("msedge.exe", "Chat | Microsoft Teams"), ("chrome.exe", "Inbox")), TP) == "")
check("browser: window titles off Windows", meetings.window_titles() == [])
WV2 = r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\msedgewebview2.exe"
check("webview: new Teams recording through WebView2",
      meetings.find_meeting(["msedgewebview2.exe"], pats, win(("ms-teams.exe", "Chat | Microsoft Teams")), TP) == "Teams")
check("webview: WebView2 recording without a meeting window doesn't count",
      meetings.find_meeting(["msedgewebview2.exe"], pats, win(("ms-teams.exe", "Activity")), TP) == "")
check("audio sessions: new Teams process", meetings.find_meeting(["ms-teams.exe"], pats) == "Teams")
check("audio sessions: Zoom process name", meetings.find_meeting(["zoom.exe"], pats) == "Zoom")
check("sources: nothing off Windows", meetings.audio_session_users() == [] and meetings.registry_mic_users() == [])
_orig = meetings.audio_session_users
meetings.audio_session_users = lambda: (_ for _ in ()).throw(OSError("COM unavailable"))
check("sources: one failing source doesn't stop the other", meetings.mic_users() == [])
meetings.audio_session_users = _orig

cfg = config_mod.load()
mic = []
w = meetings.MeetingWatcher(cfg, reader=lambda: list(mic), titles=lambda: [])
w.tick()
check("watcher: quiet", w.snapshot()["app"] == "")
mic.append(ZOOM)
w.tick()
check("watcher: meeting starts", w.snapshot()["app"] == "Zoom")
w.dismiss()
w.tick()
check("watcher: not now sticks for this meeting", w.snapshot()["dismissed"] is True)
mic.clear()
w.tick()
s = w.snapshot()
check("watcher: meeting ends", s["app"] == "" and s["ended"] == "Zoom")
mic.append(ZOOM)
w.tick()
check("watcher: next meeting asks again", w.snapshot()["dismissed"] is False and w.snapshot()["ended"] == "")
cfg.meeting_detect = False
mic.clear(); w.tick(); mic.append(ZOOM); w.tick()
check("watcher: off in settings", w.snapshot()["app"] == "")
cfg.meeting_detect = True
wb = meetings.MeetingWatcher(cfg, reader=lambda: [EDGE], titles=lambda: [("msedge.exe", "Standup | Microsoft Teams")])
wb.tick()
check("watcher: Teams in a browser", wb.snapshot()["app"] == "Teams")
check("watcher: snapshot reports what holds the mic", wb.snapshot()["mic_users"] == "msedge.exe")
log_text = (TMP / "minutara.log").read_text()
check("watcher: logs which apps hold the mic", "microphone in use by: msedge.exe" in log_text)
w2 = meetings.MeetingWatcher(cfg, reader=lambda: (_ for _ in ()).throw(OSError("registry gone")))
w2.tick()
check("watcher: a failing registry read is survived", w2.snapshot()["app"] == "" and "registry" in w2.error)
w2.preview(5)
check("watcher: preview pretends Zoom started", w2.snapshot()["app"] == "Zoom" and w2.snapshot()["preview"])


# --- controller ------------------------------------------------------------------
class FakeRec:
    state, note_id, meeting_app = "idle", "", ""


class FakeUI:
    def __init__(self):
        self.ops, self.front, self.ok = [], False, True

    def available(self):
        return self.ok

    def main_in_front(self):
        return self.front

    def show(self, mode, size):
        self.ops.append(("show", mode, size))

    def hide(self):
        self.ops.append(("hide",))


mic.clear()
w = meetings.MeetingWatcher(cfg, reader=lambda: list(mic))
rec, ui = FakeRec(), FakeUI()
c = WidgetController(cfg, rec, w, ui)
c.tick()
check("widget: hidden when nothing's happening", c.mode == "" and ui.ops == [])
mic.append(TEAMS); w.tick(); c.tick()
check("widget: meeting → pop-up", c.mode == "prompt" and ui.ops[-1][:2] == ("show", "prompt"))
rec.state, rec.note_id, rec.meeting_app = "recording", "teams-1", "Teams"
c.tick()
check("widget: recording → floating recorder", c.mode == "recording" and ui.ops[-1][2] == (300, 60))
ui.front = True; c.tick()
check("widget: hidden while Minutara's window is in front", c.mode == "")
ui.front = False; c.tick()
check("widget: back when you switch away", c.mode == "recording")
c.hide_for_recording(); c.tick()
check("widget: – hides it for this recording", c.mode == "")
mic.clear(); w.tick(); c.tick()
check("widget: call ended → asks to stop", c.mode == "ended")
w.clear_ended(); c.tick()
check("widget: keep recording → stays tucked away", c.mode == "")
rec.state, rec.meeting_app = "idle", ""; c.tick()
rec.state, rec.note_id = "recording", "manual-2"; c.tick()
check("widget: a new recording shows it again", c.mode == "recording")
cfg.floating_recorder = False; c.tick()
check("widget: floating recorder off in settings", c.mode == "")
cfg.floating_recorder = True
rec.state = "idle"; mic.append(ZOOM); w.tick(); w.dismiss(); c.tick()
check("widget: not now hides the pop-up", c.mode == "")


class BrokenUI(FakeUI):
    def show(self, mode, size):
        raise RuntimeError("WebView2 crashed")


c2 = WidgetController(cfg, FakeRec(), meetings.MeetingWatcher(cfg, reader=lambda: [ZOOM]), BrokenUI())
c2.watcher.tick()
c2.start(); time.sleep(1.0); c2.stop()
check("widget: a broken window never stops the loop", c2._err == "WebView2 crashed")
c3 = WidgetController(cfg, FakeRec(), w, None)
c3.tick()
check("widget: nothing to do without a window", c3.mode == "" and c3.state()["available"] is False)

# --- server ------------------------------------------------------------------------
app, rec = srv.create_app(cfg)
cl = app.test_client()
T = cfg.server_token
U = lambda p: p + ("&" if "?" in p else "?") + "token=" + T  # noqa: E731
page = cl.get("/widget?token=" + T)
check("api: floating page served", page.status_code == 200 and b"Take notes?" in page.data)
s = cl.get(U("/api/status")).get_json()
check("api: status has meeting and widget", "meeting" in s and s["widget"]["available"] is False)
r = cl.post(U("/api/meeting/preview")).get_json()
s = cl.get(U("/api/status")).get_json()
check("api: preview shows a Zoom meeting", s["meeting"]["app"] == "Zoom" and s["meeting"]["preview"], str(s["meeting"]))
cl.post(U("/api/meeting/dismiss"))
check("api: not now", cl.get(U("/api/status")).get_json()["meeting"]["dismissed"] is True)
r = cl.post(U("/api/start"), json={"title": "Zoom meeting", "meeting_app": "Zoom"}).get_json()
s = cl.get(U("/api/status")).get_json()
check("api: start for a meeting", r["ok"] and s["meeting_app"] == "Zoom" and s["title"] == "Zoom meeting")
check("api: preview refused while recording", cl.post(U("/api/meeting/preview")).status_code == 409)
cl.post(U("/api/widget/hide"))
check("api: hide for this recording", app.config["MINUTARA"]["widget"].hidden_for == r["note_id"])
cl.post(U("/api/widget/seen"), json={"mode": "recording"})
check("api: page check-in", cl.get(U("/api/status")).get_json()["widget"]["page_mode"] == "recording")
time.sleep(0.5)
cl.post(U("/api/stop"))
end = time.time() + 10
while time.time() < end and cl.get(U("/api/status")).get_json()["state"] != "idle":
    time.sleep(0.2)
check("api: meeting_app cleared after stop", cl.get(U("/api/status")).get_json()["meeting_app"] == "")
check("api: widget endpoints need the token", cl.post("/api/meeting/preview").status_code == 401)
cfg2 = config_mod.load()
check("config: meeting settings saved and loaded", cfg2.meeting_detect is True and "zoom.exe" in cfg2.meeting_apps
      and cfg2.floating_recorder is True)
cl.post(U("/api/config"), json={"meeting_browsers": True, "meeting_apps": "zoom.exe, gotomeeting"})
cfg3 = config_mod.load()
check("config: meeting settings editable", cfg3.meeting_browsers is True and cfg3.meeting_apps == "zoom.exe, gotomeeting")
cl.post(U("/api/config"), json={"meeting_titles": "teams, meet, gotomeeting"})
check("config: page titles editable", config_mod.load().meeting_titles == "teams, meet, gotomeeting")
check("config: page titles default", "microsoft teams" in config_mod.Config().meeting_titles)
from minutara import window  # noqa: E402
check("window: unblocking is a no-op off Windows", window.unblock() == 0)
st = cl.get(U("/api/status")).get_json()["widget"]
check("status: says why there's no floating window", st["available"] is False and "reason" in st)

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILURE(S): {failures}")
