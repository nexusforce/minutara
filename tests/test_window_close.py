import fakes, json, threading, time, urllib.request
TMP = fakes.make_app_root("minutara-win-")
from minutara import config as config_mod, server as srv, window
cfg = config_mod.load(); cfg.server_port = 8799; cfg.auto_transcribe = False; config_mod.save(cfg)
events = {}
def fake_run(url, on_closing=None, widget=None):
    events["widget"] = widget
    # window open: start a recording through the API, then the user closes the window
    t = cfg.server_token
    def post(path):
        req = urllib.request.Request(f"http://127.0.0.1:8799{path}?token={t}", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req).read())
    events["start"] = post("/api/start")
    time.sleep(1.0)
    events["closing_at"] = time.monotonic()
    on_closing()             # what pywebview calls as the window closes
    return True
window.run = fake_run
class Exited(Exception): pass
def fake_exit(code):
    events["exit"] = (code, time.monotonic()); raise Exited
srv.os._exit = fake_exit
try:
    srv.serve(cfg, open_browser=True)
except Exited:
    pass
nid = events["start"]["note_id"]
note = json.loads((TMP / "notes" / f"{nid}.json").read_text())
wav = TMP / "recordings" / f"{nid}.wav"
ok1 = wav.exists() and 0.8 < (note.get("duration_s") or 0) < 1.6
ok2 = events.get("exit", (None,))[0] == 0
waited = events["exit"][1] - events["closing_at"]
print("PASS" if ok1 else "FAIL", "closing mid-recording saves it:", note.get("duration_s"))
print("PASS" if ok2 else "FAIL", "process exits after the window closes, waited %.1fs" % waited)
log = (TMP / "minutara.log").read_text()
print("PASS" if "stopping and saving" in log and "exiting" in log else "FAIL", "log explains it")
print("PASS" if events.get("widget") is not None and events["widget"].url.endswith(cfg.server_token) else "FAIL",
      "the floating window is set up with the app window")
