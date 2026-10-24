import fakes, threading, time
TMP = fakes.make_app_root("minutara-show-")
from minutara import config as config_mod, server as srv, window
cfg = config_mod.load(); cfg.server_port = 8798; config_mod.save(cfg)
app, rec = srv.create_app(cfg)
threading.Thread(target=app.run, kwargs={"port": 8798}, daemon=True).start()
srv._wait_for_port(8798, 10)
calls = []
class SlowWindow:   # the GUI thread is busy: every call blocks for a while
    def restore(self): time.sleep(8); calls.append("restore")
    def show(self): calls.append("show")
    on_top = False
window._window = SlowWindow(); window._shown.set()
opened = []
window.run = lambda url, on_closing=None: opened.append(url) or True
t0 = time.monotonic(); srv._show_running(cfg, "u"); dt = time.monotonic() - t0
print("PASS" if dt < 2 and not opened else "FAIL", f"second launch hands over fast ({dt:.2f}s), no second window: {opened}")
time.sleep(9)
print("PASS" if calls[:2] == ["restore", "show"] else "FAIL", "window still brought forward later", calls)
window._window = None
srv._show_running(cfg, "u")
print("PASS" if opened == ["u"] else "FAIL", "headless Minutara gets a window", opened)
