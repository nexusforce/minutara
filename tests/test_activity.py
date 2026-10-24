import fakes, time
fakes.make_app_root("minutara-act-")
from minutara import config as config_mod, server as srv
cfg = config_mod.load(); app, rec = srv.create_app(cfg); c = app.test_client()
a = app.config["MINUTARA"]["activity"]; a["t"] = 0
c.get(f"/api/status?token={cfg.server_token}", headers={"X-Minutara-Widget": "1"})
print("PASS" if a["t"] == 0 else "FAIL", "floating window polling isn't activity")
c.get(f"/api/status?token={cfg.server_token}")
print("PASS" if a["t"] > 0 else "FAIL", "the main window's polling is")
