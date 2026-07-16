"""
server.py — a phone remote for the recorder, over your own LAN.

Not an Android app: a small HTTP server on the Windows box and a page your
phone opens. Add it to your home screen and it behaves like an app. Nothing
routes through the internet — the phone talks to the laptop directly.

Every request needs the token from config.json. That matters: this endpoint can
start a recording, so anyone on the same network who finds the port should not
be able to drive it.
"""

from __future__ import annotations

import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from .config import Config
from .pipeline import process, stamped_name


class Recorder:
    """Owns the single recording session and the worker thread behind it."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.title = ""
        self.started_at: float | None = None
        self.state = "idle"  # idle | recording | transcribing | error
        self.last_message = ""
        self.last_transcript: str | None = None

    def start(self, title: str) -> tuple[bool, str]:
        with self.lock:
            if self.state in ("recording", "transcribing"):
                return False, f"Already {self.state}."
            self.title = title or "Meeting"
            self.stop_event = threading.Event()
            self.state = "recording"
            self.last_message = ""
            self.started_at = datetime.now(timezone.utc).timestamp()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True, "Recording."

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if self.state != "recording":
                return False, "Not recording."
            self.stop_event.set()
            self.state = "transcribing"
            return True, "Stopped. Transcribing."

    def _run(self) -> None:
        from .capture import record

        try:
            name = stamped_name(self.title)
            wav = self.cfg.recordings_dir / f"{name}.wav"
            result = record(wav, self.stop_event)

            txt = process(
                wav, self.cfg,
                title=self.title,
                duration_s=result.duration_s,
                recorded_at=datetime.fromtimestamp(result.started_at, tz=timezone.utc),
            )
            self.last_transcript = txt.name
            self.state = "idle"
            self.last_message = f"Saved {txt.name}"
        except Exception as e:
            self.state = "error"
            self.last_message = str(e)

    def status(self) -> dict:
        elapsed = 0.0
        if self.state == "recording" and self.started_at:
            elapsed = datetime.now(timezone.utc).timestamp() - self.started_at
        return {
            "state": self.state,
            "title": self.title,
            "elapsed": elapsed,
            "message": self.last_message,
            "last_transcript": self.last_transcript,
            "pocket_enabled": self.cfg.pocket_enabled,
            "push_to_pocket": self.cfg.push_to_pocket and self.cfg.pocket_enabled,
        }


def local_ip() -> str:
    """The address the phone should hit. No traffic is actually sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def create_app(cfg: Config) -> tuple[Flask, Recorder]:
    app = Flask(__name__)
    rec = Recorder(cfg)

    def authed() -> bool:
        token = request.args.get("token") or request.headers.get("X-Token", "")
        return bool(cfg.server_token) and token == cfg.server_token

    @app.before_request
    def guard():
        if request.path.startswith("/api") and not authed():
            return jsonify({"error": "Bad or missing token."}), 401

    @app.get("/")
    def index() -> Response:
        return Response(PAGE, mimetype="text/html")

    @app.get("/api/status")
    def status():
        return jsonify(rec.status())

    @app.post("/api/start")
    def start():
        title = (request.json or {}).get("title", "Meeting")
        ok, msg = rec.start(title)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.post("/api/stop")
    def stop():
        ok, msg = rec.stop()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.get("/api/transcripts")
    def transcripts():
        files = sorted(
            cfg.transcripts_dir.glob("*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:25]
        return jsonify([
            {
                "name": f.name,
                "size": f.stat().st_size,
                "when": datetime.fromtimestamp(f.stat().st_mtime).strftime("%b %d, %H:%M"),
            }
            for f in files
        ])

    @app.get("/api/transcripts/<path:name>")
    def transcript(name: str):
        safe = Path(name).name
        f = cfg.transcripts_dir / safe
        if not f.exists():
            return jsonify({"error": "No transcript by that name."}), 404
        return Response(f.read_text(encoding="utf-8"), mimetype="text/plain")

    @app.post("/api/usb")
    def usb():
        from .usb_ingest import find_pocket_drive, ingest

        record_dir = find_pocket_drive(cfg.usb_volume_label, cfg.usb_record_dir)
        if not record_dir:
            return jsonify({
                "ok": False,
                "message": "No Pocket drive. Plug it in and turn on USB Mode in the app.",
            }), 404

        result = ingest(record_dir, cfg.recordings_dir, cfg.manifest_path,
                        cfg.delete_from_device)
        if not result.copied:
            return jsonify({"ok": True, "message": "Nothing new on the device."})

        def work():
            for f in result.copied:
                try:
                    process(f, cfg, title=f.stem)
                except Exception as e:
                    print(f"Could not transcribe {f.name}: {e}")

        threading.Thread(target=work, daemon=True).start()
        n = len(result.copied)
        return jsonify({
            "ok": True,
            "message": f"Copied {n} recording{'s' if n != 1 else ''}. Transcribing now.",
        })

    return app, rec


def serve(cfg: Config) -> None:
    app, _ = create_app(cfg)
    url = f"http://{local_ip()}:{cfg.server_port}/?token={cfg.server_token}"

    print("\n  Phone remote is up. Open this on your phone (same Wi-Fi):\n")
    print(f"    {url}\n")
    try:
        import qrcode  # optional
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (pip install qrcode for a scannable code here)\n")
    print("  Windows Firewall will prompt on first run — allow it on Private networks.")
    print("  Ctrl+C to stop.\n")

    app.run(host="0.0.0.0", port=cfg.server_port, threaded=True)


# --- The remote -------------------------------------------------------------
# Design notes, since they're deliberate:
#   The Pocket has exactly one button and says everything through one LED —
#   solid blue connected, amber recording. This page speaks the same language:
#   one button, one lamp, same colours. That also keeps it discreet on a table
#   mid-meeting — an amber dot reads as nothing to anyone glancing over, where
#   a red REC banner does not.
#   System fonts only: this loads off a USB stick on a LAN with no internet.

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E1113">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Scribe</title>
<style>
  :root {
    --ink:#0E1113; --shell:#191E22; --edge:#2C343A; --edge-hi:#3D474E;
    --text:#C9D1D6; --dim:#6C7A83;
    --lamp-blue:#4C86B8; --lamp-amber:#E8933A; --lamp-red:#B8564C;
    --mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body {
    margin:0; min-height:100dvh; background:var(--ink); color:var(--text);
    font-family:var(--sans);
    display:flex; flex-direction:column;
    padding:max(20px, env(safe-area-inset-top)) 20px max(20px, env(safe-area-inset-bottom));
  }
  header { display:flex; align-items:center; gap:10px; margin-bottom:28px; }
  .lamp {
    width:11px; height:11px; border-radius:50%;
    background:var(--lamp-blue); box-shadow:0 0 10px 1px currentColor; color:var(--lamp-blue);
    flex:none; transition:background .25s, color .25s;
  }
  body[data-state="recording"] .lamp { background:var(--lamp-amber); color:var(--lamp-amber); animation:breathe 2.4s ease-in-out infinite; }
  body[data-state="transcribing"] .lamp { background:var(--lamp-blue); color:var(--lamp-blue); animation:breathe 1s ease-in-out infinite; }
  body[data-state="error"] .lamp { background:var(--lamp-red); color:var(--lamp-red); animation:none; }
  @keyframes breathe { 0%,100%{opacity:1} 50%{opacity:.35} }
  @media (prefers-reduced-motion:reduce) { .lamp { animation:none !important; } }

  header h1 {
    font:600 13px/1 var(--sans); letter-spacing:.16em; text-transform:uppercase;
    margin:0; color:var(--dim);
  }
  .cloud {
    margin-left:auto; font:500 10px/1 var(--sans); letter-spacing:.12em;
    text-transform:uppercase; color:var(--dim);
    border:1px solid var(--edge); border-radius:999px; padding:5px 9px;
  }
  .cloud[data-on="true"] { color:var(--lamp-amber); border-color:#4A3A24; }

  .meter { text-align:center; margin:auto 0; }
  .counter {
    font:300 68px/1 var(--mono); letter-spacing:-.02em;
    font-variant-numeric:tabular-nums; color:var(--text);
  }
  .caption { margin-top:10px; font-size:13px; color:var(--dim); min-height:1.3em; }

  .button {
    width:132px; height:132px; margin:34px auto 0; display:block;
    border-radius:50%; border:1px solid var(--edge-hi); cursor:pointer;
    background:linear-gradient(180deg, #232A2F, #161B1F);
    box-shadow:0 1px 0 #39434A inset, 0 10px 26px rgba(0,0,0,.5);
    color:var(--text); font:600 12px/1 var(--sans);
    letter-spacing:.14em; text-transform:uppercase;
    transition:transform .09s ease, box-shadow .09s ease, color .2s;
  }
  .button:active { transform:translateY(2px); box-shadow:0 1px 0 #39434A inset, 0 4px 12px rgba(0,0,0,.5); }
  .button:focus-visible { outline:2px solid var(--lamp-blue); outline-offset:4px; }
  .button:disabled { opacity:.45; cursor:default; }
  body[data-state="recording"] .button { color:var(--lamp-amber); border-color:#5A452C; }

  .title-input {
    display:block; width:100%; max-width:290px; margin:26px auto 0;
    background:transparent; border:0; border-bottom:1px solid var(--edge);
    color:var(--text); font:400 15px var(--sans); text-align:center;
    padding:9px 4px;
  }
  .title-input:focus { outline:none; border-bottom-color:var(--lamp-blue); }
  .title-input::placeholder { color:var(--dim); }

  footer { margin-top:34px; }
  .row { display:flex; gap:10px; margin-bottom:18px; }
  .ghost {
    flex:1; background:transparent; border:1px solid var(--edge); border-radius:9px;
    color:var(--dim); font:500 12px var(--sans); padding:11px; cursor:pointer;
  }
  .ghost:active { background:var(--shell); }
  .ghost:focus-visible { outline:2px solid var(--lamp-blue); outline-offset:2px; }

  .label { font:600 10px var(--sans); letter-spacing:.14em; text-transform:uppercase;
           color:var(--dim); margin-bottom:9px; }
  ul { list-style:none; margin:0; padding:0; max-height:33dvh; overflow-y:auto; }
  li a {
    display:flex; justify-content:space-between; gap:12px; align-items:baseline;
    padding:11px 12px; margin-bottom:5px; border-radius:8px; background:var(--shell);
    color:var(--text); text-decoration:none; font-size:13px;
  }
  li a:active { background:#212930; }
  li .when { color:var(--dim); font:400 11px/1 var(--mono); flex:none; }
  li .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .empty { color:var(--dim); font-size:13px; padding:11px 0; }
</style>
</head>
<body data-state="idle">
  <header>
    <span class="lamp" aria-hidden="true"></span>
    <h1>Scribe</h1>
    <span class="cloud" id="cloud" data-on="false">Local</span>
  </header>

  <main class="meter">
    <div class="counter" id="counter" role="timer" aria-live="off">00:00</div>
    <div class="caption" id="caption">Ready</div>
    <button class="button" id="btn">Start</button>
    <input class="title-input" id="title" placeholder="What is this meeting?"
           autocomplete="off" enterkeyhint="done">
  </main>

  <footer>
    <div class="row">
      <button class="ghost" id="usb">Pull from Pocket</button>
      <button class="ghost" id="refresh">Refresh</button>
    </div>
    <div class="label">Transcripts</div>
    <ul id="list"><li class="empty">Nothing yet. Press the button to start.</li></ul>
  </footer>

<script>
const token = new URLSearchParams(location.search).get("token") || "";
const $ = id => document.getElementById(id);
let state = "idle";

const api = (path, opts={}) => fetch(path + (path.includes("?") ? "&" : "?") + "token=" + token,
  { headers: {"Content-Type":"application/json"}, ...opts });

const clock = s => {
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return String(m).padStart(2,"0") + ":" + String(sec).padStart(2,"0");
};

async function poll() {
  try {
    const s = await (await api("/api/status")).json();
    state = s.state;
    document.body.dataset.state = state;
    $("counter").textContent = clock(s.elapsed);
    $("cloud").textContent = s.push_to_pocket ? "Pocket on" : "Local";
    $("cloud").dataset.on = s.push_to_pocket;

    if (state === "recording")        { $("btn").textContent = "Stop";  $("btn").disabled = false; $("caption").textContent = s.title; }
    else if (state === "transcribing"){ $("btn").textContent = "Wait";  $("btn").disabled = true;  $("caption").textContent = "Transcribing"; }
    else if (state === "error")       { $("btn").textContent = "Start"; $("btn").disabled = false; $("caption").textContent = s.message || "Something failed"; }
    else                              { $("btn").textContent = "Start"; $("btn").disabled = false; $("caption").textContent = s.message || "Ready"; }
  } catch { $("caption").textContent = "Lost the laptop"; }
}

async function loadList() {
  try {
    const files = await (await api("/api/transcripts")).json();
    $("list").innerHTML = files.length
      ? files.map(f => `<li><a href="/api/transcripts/${encodeURIComponent(f.name)}?token=${token}">
          <span class="name">${f.name}</span><span class="when">${f.when}</span></a></li>`).join("")
      : `<li class="empty">Nothing yet. Press the button to start.</li>`;
  } catch {}
}

$("btn").onclick = async () => {
  const path = state === "recording" ? "/api/stop" : "/api/start";
  const body = state === "recording" ? undefined : JSON.stringify({ title: $("title").value || "Meeting" });
  $("btn").disabled = true;
  await api(path, { method:"POST", body });
  await poll();
  if (path === "/api/stop") setTimeout(loadList, 1500);
};

$("usb").onclick = async () => {
  $("caption").textContent = "Looking for Pocket";
  const r = await (await api("/api/usb", { method:"POST" })).json();
  $("caption").textContent = r.message;
  setTimeout(loadList, 2500);
};

$("refresh").onclick = loadList;

poll(); loadList();
setInterval(poll, 1000);
setInterval(() => { if (state !== "recording") loadList(); }, 20000);
</script>
</body>
</html>
"""
