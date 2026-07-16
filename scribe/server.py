"""
server.py — Scribe's GUI: a local web app served over your own LAN.

One page, two audiences. Opened on the Windows box (Scribe App.bat launches
it in an Edge app window) it's the desktop app: record, browse recordings and
transcripts, change settings, read the log. Opened on a phone on the same
Wi-Fi it's the remote. Nothing routes through the internet — the browser
talks to this process directly.

Every request needs the token from config.json. That matters: this endpoint
can start a recording, so anyone on the same network who finds the port
should not be able to drive it.
"""

from __future__ import annotations

import os
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import config as config_mod
from .config import Config
from .log import LOG_PATH, get
from .pipeline import process, stamped_name

log = get("scribe.server")

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}


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
            log.info("recording started: %r", self.title)
            return True, "Recording."

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if self.state != "recording":
                return False, "Not recording."
            self.stop_event.set()
            self.state = "transcribing"
            log.info("recording stopped; transcribing")
            return True, "Stopped. Transcribing."

    def _run(self) -> None:
        from .capture import record

        try:
            name = stamped_name(self.title)
            wav = self.cfg.recordings_dir / f"{name}.wav"
            result = record(wav, self.stop_event, mic_name=self.cfg.mic_device)

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
            log.exception("recording session failed")
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


# Settings the GUI may change. server_port and server_token are deliberately
# not here: the port needs a restart, and the token guards this very API.
# recordings_dir is handled separately (it's a path, not a scalar).
EDITABLE_KEYS = (
    "use_pocket_api", "push_to_pocket", "prefer_pocket_transcripts",
    "pocket_api_key", "whisper_model", "whisper_device",
    "me_label", "them_label", "mic_device", "delete_from_device",
)


def _shutdown() -> None:
    """Exit shortly after the response flushes. Blunt, but right for this app."""
    threading.Timer(0.4, os._exit, args=(0,)).start()


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
        title = (request.get_json(silent=True) or {}).get("title", "Meeting")
        ok, msg = rec.start(title)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.post("/api/stop")
    def stop():
        ok, msg = rec.stop()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.get("/api/config")
    def get_config():
        return jsonify({
            "whisper_model": cfg.whisper_model,
            "whisper_device": cfg.whisper_device,
            "me_label": cfg.me_label,
            "them_label": cfg.them_label,
            "mic_device": cfg.mic_device,
            "recordings_dir": str(cfg.recordings_dir),
            "use_pocket_api": cfg.use_pocket_api,
            "push_to_pocket": cfg.push_to_pocket,
            "prefer_pocket_transcripts": cfg.prefer_pocket_transcripts,
            "delete_from_device": cfg.delete_from_device,
            # Never echo the key itself back to a browser — only whether one
            # is stored, so the form can say "saved".
            "pocket_key_set": bool(cfg.pocket_api_key),
            "server_port": cfg.server_port,
        })

    @app.post("/api/config")
    def set_config():
        data = request.get_json(silent=True) or {}
        defaults = Config()
        for key in EDITABLE_KEYS:
            if key not in data:
                continue
            # A blank key field means "keep the one already saved".
            if key == "pocket_api_key" and not str(data[key]).strip():
                continue
            setattr(cfg, key, config_mod._coerce(data[key], getattr(defaults, key)))
        if str(data.get("recordings_dir", "")).strip():
            cfg.recordings_dir = config_mod.resolve_dir(data["recordings_dir"])
            cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
        config_mod.save(cfg)
        log.info("settings saved (recordings_dir=%s, mic=%r, model=%s)",
                 cfg.recordings_dir, cfg.mic_device, cfg.whisper_model)
        return jsonify({"ok": True})

    @app.get("/api/devices")
    def devices():
        try:
            from .capture import list_input_devices
            info = list_input_devices()
        except Exception as e:
            info = {"mics": [], "default": "", "loopback": "", "error": str(e)}
        info["selected"] = cfg.mic_device
        return jsonify(info)

    @app.get("/api/recordings")
    def recordings():
        cfg.recordings_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(
            (f for f in cfg.recordings_dir.iterdir()
             if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
        return jsonify([
            {
                "name": f.name,
                "size": f.stat().st_size,
                "when": datetime.fromtimestamp(f.stat().st_mtime).strftime("%b %d, %H:%M"),
            }
            for f in files
        ])

    @app.get("/api/recordings/<path:name>")
    def recording(name: str):
        safe = Path(name).name
        if not (cfg.recordings_dir / safe).exists():
            return jsonify({"error": "No recording by that name."}), 404
        # conditional=True gives Range support, which <audio> needs to seek.
        return send_from_directory(cfg.recordings_dir, safe, conditional=True)

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

    @app.get("/api/phone")
    def phone():
        """The LAN URL for the phone remote, plus a QR of it as inline SVG."""
        lan_url = f"http://{local_ip()}:{cfg.server_port}/?token={cfg.server_token}"
        svg = ""
        try:
            import io
            import qrcode
            import qrcode.image.svg
            img = qrcode.make(lan_url, image_factory=qrcode.image.svg.SvgPathImage,
                              border=2)
            buf = io.BytesIO()
            img.save(buf)
            svg = buf.getvalue().decode("utf-8")
            svg = svg[svg.index("<svg"):]  # drop the XML declaration for innerHTML
        except Exception as e:
            log.warning("QR unavailable (%s) — showing the URL only", e)
        return jsonify({"url": lan_url, "svg": svg})

    @app.get("/api/log")
    def log_tail():
        if not LOG_PATH.exists():
            return Response("(no log yet)", mimetype="text/plain")
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        return Response("\n".join(lines[-300:]), mimetype="text/plain")

    @app.post("/api/quit")
    def quit_():
        log.info("quit requested from the GUI")
        _shutdown()
        return jsonify({"ok": True, "message": "Scribe is shutting down."})

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
        log.info("usb ingest: %d new, %d already seen", len(result.copied), result.skipped)
        if not result.copied:
            return jsonify({"ok": True, "message": "Nothing new on the device."})

        def work():
            for f in result.copied:
                try:
                    process(f, cfg, title=f.stem)
                except Exception:
                    log.exception("could not transcribe %s", f.name)

        threading.Thread(target=work, daemon=True).start()
        n = len(result.copied)
        return jsonify({
            "ok": True,
            "message": f"Copied {n} recording{'s' if n != 1 else ''}. Transcribing now.",
        })

    return app, rec


def _open_ui(url: str) -> None:
    """
    Open the GUI like a desktop app. Edge's --app mode gives a chromeless
    window (no tabs, no address bar) and ships on every Windows 10/11; if it
    isn't where we expect, the default browser is a fine fallback.
    """
    import subprocess
    import webbrowser

    for edge in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ):
        if Path(edge).exists():
            try:
                subprocess.Popen([edge, f"--app={url}"])
                return
            except OSError:
                break
    webbrowser.open(url)


def _port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def serve(cfg: Config, open_browser: bool = False) -> None:
    local_url = f"http://127.0.0.1:{cfg.server_port}/?token={cfg.server_token}"

    # Double-clicking the launcher twice shouldn't crash on a busy port —
    # if Scribe is already running, just bring up its window.
    if _port_in_use(cfg.server_port):
        log.info("already running on port %s; opening the window", cfg.server_port)
        print("Scribe is already running — opening its window.")
        if open_browser:
            _open_ui(local_url)
        return

    app, _ = create_app(cfg)
    lan_url = f"http://{local_ip()}:{cfg.server_port}/?token={cfg.server_token}"

    log.info("serving on port %s (log: %s)", cfg.server_port, LOG_PATH)
    print("\n  Scribe is up. On your phone (same Wi-Fi), open:\n")
    print(f"    {lan_url}\n")
    try:
        import qrcode  # optional
        qr = qrcode.QRCode(border=1)
        qr.add_data(lan_url)
        qr.print_ascii(invert=True)
    except Exception:
        pass
    print("  Windows Firewall will prompt on first run — allow it on Private networks.")
    print("  Quit from the app's settings panel (or Ctrl+C here).\n")

    if open_browser:
        threading.Timer(1.0, _open_ui, args=(local_url,)).start()

    app.run(host="0.0.0.0", port=cfg.server_port, threaded=True)


# --- The GUI -----------------------------------------------------------------
# Design notes, since they're deliberate:
#   The Pocket has exactly one button and says everything through one LED —
#   solid blue connected, amber recording. This page speaks the same language:
#   one button, one lamp, same colours. That also keeps it discreet on a table
#   mid-meeting — an amber dot reads as nothing to anyone glancing over, where
#   a red REC banner does not.
#   Dark and light are both first-class: default follows the OS, the toggle in
#   the header overrides it, and the choice sticks (localStorage).
#   System fonts only: this loads off a USB stick on a LAN with no internet.

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0E1113" id="metatheme">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>Scribe</title>
<script>
  // Before first paint so there's no flash of the wrong theme.
  document.documentElement.dataset.theme =
    localStorage.getItem("scribe-theme") ||
    (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
</script>
<style>
  :root {
    --ink:#0E1113; --shell:#191E22; --edge:#2C343A; --edge-hi:#3D474E;
    --text:#C9D1D6; --dim:#6C7A83;
    --lamp-blue:#4C86B8; --lamp-amber:#E8933A; --lamp-red:#B8564C;
    --btn-a:#232A2F; --btn-b:#161B1F; --btn-inset:#39434A;
    --overlay:rgba(4,6,8,.72);
    --mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
    --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  :root[data-theme="light"] {
    --ink:#EFEDE7; --shell:#FBFAF7; --edge:#DBD5C9; --edge-hi:#C7C0B2;
    --text:#2C3237; --dim:#837E70;
    --lamp-blue:#37678F; --lamp-amber:#BC6F1D; --lamp-red:#A0473B;
    --btn-a:#FFFFFF; --btn-b:#ECE8DF; --btn-inset:#FFFFFF;
    --overlay:rgba(58,54,44,.45);
  }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  [hidden] { display:none !important; }
  body {
    margin:0; min-height:100dvh; background:var(--ink); color:var(--text);
    font-family:var(--sans);
    padding:max(20px, env(safe-area-inset-top)) 20px max(20px, env(safe-area-inset-bottom));
    transition:background .25s, color .25s;
  }
  .wrap { max-width:1020px; margin:0 auto; }

  header { display:flex; align-items:center; gap:10px; margin-bottom:26px; }
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
  .chip {
    font:500 10px/1 var(--sans); letter-spacing:.12em;
    text-transform:uppercase; color:var(--dim);
    border:1px solid var(--edge); border-radius:999px; padding:5px 9px;
  }
  .chip[data-on="true"] { color:var(--lamp-amber); border-color:var(--lamp-amber); }
  .spacer { margin-left:auto; }
  .icon {
    background:transparent; border:1px solid var(--edge); border-radius:9px;
    color:var(--dim); width:34px; height:30px; cursor:pointer; font-size:14px; line-height:1;
  }
  .icon:hover { color:var(--text); border-color:var(--edge-hi); }
  .icon:focus-visible { outline:2px solid var(--lamp-blue); outline-offset:2px; }

  .grid { display:grid; gap:20px; }
  @media (min-width:900px) { .grid { grid-template-columns:380px 1fr; align-items:start; } }

  .panel { background:var(--shell); border:1px solid var(--edge); border-radius:14px; padding:22px; }

  .meter { text-align:center; padding:14px 0 6px; }
  .counter {
    font:300 64px/1 var(--mono); letter-spacing:-.02em;
    font-variant-numeric:tabular-nums; color:var(--text);
  }
  .caption { margin-top:10px; font-size:13px; color:var(--dim); min-height:1.3em; overflow-wrap:anywhere; }

  .button {
    width:128px; height:128px; margin:28px auto 0; display:block;
    border-radius:50%; border:1px solid var(--edge-hi); cursor:pointer;
    background:linear-gradient(180deg, var(--btn-a), var(--btn-b));
    box-shadow:0 1px 0 var(--btn-inset) inset, 0 10px 26px rgba(0,0,0,.35);
    color:var(--text); font:600 12px/1 var(--sans);
    letter-spacing:.14em; text-transform:uppercase;
    transition:transform .09s ease, box-shadow .09s ease, color .2s;
  }
  .button:active { transform:translateY(2px); box-shadow:0 1px 0 var(--btn-inset) inset, 0 4px 12px rgba(0,0,0,.35); }
  .button:focus-visible { outline:2px solid var(--lamp-blue); outline-offset:4px; }
  .button:disabled { opacity:.45; cursor:default; }
  body[data-state="recording"] .button { color:var(--lamp-amber); border-color:var(--lamp-amber); }

  .title-input {
    display:block; width:100%; max-width:290px; margin:24px auto 4px;
    background:transparent; border:0; border-bottom:1px solid var(--edge);
    color:var(--text); font:400 15px var(--sans); text-align:center;
    padding:9px 4px;
  }
  .title-input:focus { outline:none; border-bottom-color:var(--lamp-blue); }
  .title-input::placeholder { color:var(--dim); }

  .row { display:flex; gap:10px; margin-bottom:18px; }
  .ghost {
    flex:1; background:transparent; border:1px solid var(--edge); border-radius:9px;
    color:var(--dim); font:500 12px var(--sans); padding:11px; cursor:pointer;
  }
  .ghost:hover { color:var(--text); border-color:var(--edge-hi); }
  .ghost:active { background:var(--ink); }
  .ghost:focus-visible { outline:2px solid var(--lamp-blue); outline-offset:2px; }

  .tabs { display:flex; gap:16px; margin-bottom:9px; }
  .tab {
    background:none; border:0; padding:0 0 4px; cursor:pointer;
    font:600 10px var(--sans); letter-spacing:.14em; text-transform:uppercase;
    color:var(--dim); border-bottom:2px solid transparent;
  }
  .tab[data-on="true"] { color:var(--text); border-bottom-color:var(--lamp-amber); }

  ul { list-style:none; margin:0; padding:0; max-height:56dvh; overflow-y:auto; }
  li a {
    display:flex; justify-content:space-between; gap:12px; align-items:baseline;
    padding:11px 12px; margin-bottom:5px; border-radius:8px; background:var(--ink);
    color:var(--text); text-decoration:none; font-size:13px; border:1px solid transparent;
  }
  li a:hover { border-color:var(--edge); }
  li .when { color:var(--dim); font:400 11px/1 var(--mono); flex:none; }
  li .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .empty { color:var(--dim); font-size:13px; padding:11px 0; }

  /* Overlays: reader (text or audio) + settings */
  .overlay {
    position:fixed; inset:0; background:var(--overlay); z-index:20;
    display:flex; align-items:center; justify-content:center; padding:18px;
  }
  .overlay[hidden] { display:none; }
  .sheet {
    background:var(--shell); border:1px solid var(--edge); border-radius:14px;
    width:100%; max-width:720px; max-height:88dvh; display:flex; flex-direction:column;
  }
  .sheet header { margin:0; padding:14px 18px; border-bottom:1px solid var(--edge); }
  .sheet header .name { font:600 13px var(--sans); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sheet .body { padding:16px 18px; overflow-y:auto; }
  .sheet pre {
    margin:0; font:400 13px/1.7 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere;
  }
  .sheet audio { width:100%; }
  #p-qr svg { display:block; width:100%; height:auto; }
  .sheet .tools { display:flex; gap:8px; margin-left:auto; }
  .tool {
    background:transparent; border:1px solid var(--edge); border-radius:8px;
    color:var(--dim); font:500 11px var(--sans); padding:6px 10px; cursor:pointer;
    text-decoration:none; display:inline-block;
  }
  .tool:hover { color:var(--text); border-color:var(--edge-hi); }

  /* Settings form */
  .field { margin-bottom:16px; }
  .field label { display:block; font:600 10px var(--sans); letter-spacing:.12em;
                 text-transform:uppercase; color:var(--dim); margin-bottom:6px; }
  .field input[type=text], .field input[type=password], .field select {
    width:100%; background:var(--ink); border:1px solid var(--edge); border-radius:8px;
    color:var(--text); font:400 14px var(--sans); padding:9px 11px;
  }
  .field input:focus, .field select:focus { outline:none; border-color:var(--lamp-blue); }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .check { display:flex; align-items:center; gap:10px; padding:8px 0; font-size:14px; cursor:pointer; }
  .check input { width:16px; height:16px; accent-color:var(--lamp-amber); }
  .hint { font-size:12px; color:var(--dim); line-height:1.5; margin-top:4px; }
  .warn { color:var(--lamp-amber); }
  .save {
    width:100%; margin-top:6px; padding:12px; border-radius:9px; cursor:pointer;
    border:1px solid var(--edge-hi); background:linear-gradient(180deg, var(--btn-a), var(--btn-b));
    color:var(--text); font:600 12px var(--sans); letter-spacing:.12em; text-transform:uppercase;
  }
</style>
</head>
<body data-state="idle">
<div class="wrap">
  <header>
    <span class="lamp" aria-hidden="true"></span>
    <h1>Scribe</h1>
    <span class="chip" id="cloud" data-on="false">Local</span>
    <span class="spacer"></span>
    <button class="icon" id="theme" title="Toggle dark / light" aria-label="Toggle theme">☾</button>
    <button class="icon" id="gear" title="Settings" aria-label="Settings">⚙</button>
  </header>

  <div class="grid">
    <section class="panel">
      <div class="meter">
        <div class="counter" id="counter" role="timer" aria-live="off">00:00</div>
        <div class="caption" id="caption">Ready</div>
        <button class="button" id="btn">Start</button>
        <input class="title-input" id="title" placeholder="What is this meeting?"
               autocomplete="off" enterkeyhint="done">
      </div>
    </section>

    <section class="panel">
      <div class="row">
        <button class="ghost" id="usb">Pull from Pocket</button>
        <button class="ghost" id="refresh">Refresh</button>
      </div>
      <div class="tabs">
        <button class="tab" id="tab-transcripts" data-on="true">Transcripts</button>
        <button class="tab" id="tab-recordings" data-on="false">Recordings</button>
      </div>
      <ul id="list"><li class="empty">Nothing yet. Press the button to start.</li></ul>
    </section>
  </div>
</div>

<!-- Reader: transcripts (text) and recordings (audio) -->
<div class="overlay" id="viewer" hidden>
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Reader">
    <header style="display:flex;align-items:center;gap:12px;">
      <span class="name" id="v-name"></span>
      <span class="tools">
        <button class="tool" id="v-copy">Copy</button>
        <a class="tool" id="v-download" download>Download</a>
        <button class="tool" id="v-close">Close</button>
      </span>
    </header>
    <div class="body">
      <pre id="v-text"></pre>
      <audio id="v-audio" controls preload="metadata" hidden></audio>
    </div>
  </div>
</div>

<!-- Settings -->
<div class="overlay" id="settings" hidden>
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Settings" style="max-width:460px;">
    <header style="display:flex;align-items:center;gap:12px;">
      <span class="name">Settings</span>
      <span class="tools"><button class="tool" id="s-close">Close</button></span>
    </header>
    <div class="body">
      <div class="field">
        <label for="s-mic">Microphone</label>
        <select id="s-mic"></select>
        <div class="hint" id="s-loop"></div>
      </div>

      <div class="field">
        <label for="s-dir">Recordings folder</label>
        <input type="text" id="s-dir" autocomplete="off">
        <div class="hint">Relative to the app folder, or an absolute path. Default: recordings</div>
      </div>

      <div class="field two">
        <span>
          <label for="s-model">Whisper model</label>
          <input type="text" id="s-model" list="models" autocomplete="off">
          <datalist id="models">
            <option value="tiny.en"><option value="base.en">
            <option value="small.en"><option value="medium.en">
          </datalist>
        </span>
        <span>
          <label for="s-device">Device</label>
          <select id="s-device"><option value="cpu">cpu</option><option value="cuda">cuda</option></select>
        </span>
      </div>
      <div class="field two">
        <span><label for="s-me">Your label</label><input type="text" id="s-me"></span>
        <span><label for="s-them">Their label</label><input type="text" id="s-them"></span>
      </div>

      <div class="field">
        <label>Pocket cloud <span class="warn">— these send audio to Pocket's servers</span></label>
        <label class="check"><input type="checkbox" id="s-api"> Enable the Pocket API</label>
        <label class="check"><input type="checkbox" id="s-push"> Push PC recordings to Pocket</label>
        <label class="check"><input type="checkbox" id="s-prefer"> Prefer Pocket's transcripts</label>
      </div>

      <div class="field">
        <label for="s-key">Pocket API key</label>
        <input type="password" id="s-key" autocomplete="off" placeholder="pk_...">
        <div class="hint">Stored only in config.json on this machine. Leave blank to keep the saved key.</div>
      </div>

      <div class="field">
        <label>Device ingest</label>
        <label class="check"><input type="checkbox" id="s-del"> Delete recordings from the Pocket after a verified copy</label>
      </div>

      <div class="hint" id="s-port"></div>
      <button class="save" id="s-save">Save</button>
      <div class="row" style="margin-top:14px; margin-bottom:0;">
        <button class="ghost" id="s-phone">Link phone</button>
        <button class="ghost" id="s-viewlog">View log</button>
        <button class="ghost" id="s-quit">Quit Scribe</button>
      </div>
    </div>
  </div>
</div>

<!-- Link your phone: QR + URL for the remote -->
<div class="overlay" id="phone" hidden>
  <div class="sheet" role="dialog" aria-modal="true" aria-label="Link your phone" style="max-width:380px;">
    <header style="display:flex;align-items:center;gap:12px;">
      <span class="name">Link your phone</span>
      <span class="tools"><button class="tool" id="p-close">Close</button></span>
    </header>
    <div class="body" style="text-align:center;">
      <div id="p-qr" style="background:#fff; border-radius:12px; padding:14px; max-width:280px; margin:0 auto;"></div>
      <div id="p-url" style="font:400 12px/1.6 var(--mono); overflow-wrap:anywhere;
                              color:var(--dim); margin-top:14px; user-select:all;"></div>
      <div class="hint" style="margin-top:10px;">
        Scan with the phone's camera (same Wi-Fi), then add the page to your
        home screen. The link includes your access token — share it with
        nobody but your own phone.
      </div>
    </div>
  </div>
</div>

<script>
const token = new URLSearchParams(location.search).get("token") || "";
const $ = id => document.getElementById(id);
let state = "idle";
let tab = "transcripts";

const api = (path, opts={}) => fetch(path + (path.includes("?") ? "&" : "?") + "token=" + token,
  { headers: {"Content-Type":"application/json"}, ...opts });

const clock = s => {
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return String(m).padStart(2,"0") + ":" + String(sec).padStart(2,"0");
};

// --- theme -------------------------------------------------------------
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("scribe-theme", t);
  $("theme").textContent = t === "dark" ? "☾" : "☀";
  $("metatheme").content = t === "dark" ? "#0E1113" : "#EFEDE7";
}
applyTheme(document.documentElement.dataset.theme);
$("theme").onclick = () =>
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

// --- recorder ----------------------------------------------------------
async function poll() {
  try {
    const s = await (await api("/api/status")).json();
    state = s.state;
    document.body.dataset.state = state;
    $("counter").textContent = clock(s.elapsed);
    $("cloud").textContent = s.push_to_pocket ? "Pocket on" : "Local";
    $("cloud").dataset.on = s.push_to_pocket;

    if (state === "recording")        { $("btn").textContent = "Stop";  $("btn").disabled = false; $("caption").textContent = s.title; }
    else if (state === "transcribing"){ $("btn").textContent = "Wait";  $("btn").disabled = true;  $("caption").textContent = "Transcribing — the very first run downloads the model (~500 MB), so it can take a while"; }
    else if (state === "error")       { $("btn").textContent = "Start"; $("btn").disabled = false; $("caption").textContent = s.message || "Something failed"; }
    else                              { $("btn").textContent = "Start"; $("btn").disabled = false; $("caption").textContent = s.message || "Ready"; }
  } catch { $("caption").textContent = "Lost the connection"; }
}

$("btn").onclick = async () => {
  const path = state === "recording" ? "/api/stop" : "/api/start";
  const body = state === "recording" ? undefined : JSON.stringify({ title: $("title").value || "Meeting" });
  $("btn").disabled = true;
  await api(path, { method:"POST", body });
  await poll();
  if (path === "/api/stop") setTimeout(loadList, 1500);
};

// --- transcripts + recordings list ---------------------------------------
function setTab(t) {
  tab = t;
  $("tab-transcripts").dataset.on = t === "transcripts";
  $("tab-recordings").dataset.on = t === "recordings";
  loadList();
}
$("tab-transcripts").onclick = () => setTab("transcripts");
$("tab-recordings").onclick = () => setTab("recordings");

async function loadList() {
  const endpoint = tab === "transcripts" ? "/api/transcripts" : "/api/recordings";
  const emptyMsg = tab === "transcripts"
    ? "Nothing yet. Press the button to start."
    : "No recordings yet.";
  try {
    const files = await (await api(endpoint)).json();
    $("list").innerHTML = files.length
      ? files.map(f => `<li><a href="${endpoint}/${encodeURIComponent(f.name)}?token=${token}" data-name="${f.name}">
          <span class="name">${f.name}</span><span class="when">${f.when}</span></a></li>`).join("")
      : `<li class="empty">${emptyMsg}</li>`;
  } catch {}
}

$("list").onclick = e => {
  const a = e.target.closest("a[data-name]");
  if (!a) return;
  e.preventDefault();
  if (tab === "transcripts") openTranscript(a.dataset.name);
  else openRecording(a.dataset.name);
};

function showViewer(name, downloadUrl) {
  $("v-name").textContent = name;
  $("v-download").href = downloadUrl;
  $("viewer").hidden = false;
}

async function openTranscript(name) {
  const r = await api("/api/transcripts/" + encodeURIComponent(name));
  if (!r.ok) return;
  const audio = $("v-audio");
  audio.hidden = true; audio.pause(); audio.removeAttribute("src");
  $("v-text").hidden = false;
  $("v-text").textContent = await r.text();
  $("v-copy").hidden = false;
  showViewer(name, "/api/transcripts/" + encodeURIComponent(name) + "?token=" + token);
}

function openRecording(name) {
  const url = "/api/recordings/" + encodeURIComponent(name) + "?token=" + token;
  $("v-text").hidden = true; $("v-text").textContent = "";
  $("v-copy").hidden = true;
  const audio = $("v-audio");
  audio.hidden = false;
  audio.src = url;
  showViewer(name, url);
}

$("v-copy").onclick = async () => {
  const text = $("v-text").textContent;
  try { await navigator.clipboard.writeText(text); }
  catch {  // plain-HTTP LAN pages don't always get the clipboard API
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand("copy"); ta.remove();
  }
  $("v-copy").textContent = "Copied";
  setTimeout(() => $("v-copy").textContent = "Copy", 1200);
};

// --- settings ----------------------------------------------------------
async function openSettings() {
  const c = await (await api("/api/config")).json();
  $("s-model").value = c.whisper_model;
  $("s-device").value = c.whisper_device;
  $("s-me").value = c.me_label;
  $("s-them").value = c.them_label;
  $("s-dir").value = c.recordings_dir;
  $("s-api").checked = c.use_pocket_api;
  $("s-push").checked = c.push_to_pocket;
  $("s-prefer").checked = c.prefer_pocket_transcripts;
  $("s-del").checked = c.delete_from_device;
  $("s-key").value = "";
  $("s-key").placeholder = c.pocket_key_set ? "saved — leave blank to keep" : "pk_...";
  $("s-port").textContent = `Serving on port ${c.server_port}. Changing the port needs a restart.`;
  $("settings").hidden = false;

  // Devices load after the panel opens — enumerating them takes a moment.
  const mic = $("s-mic");
  mic.innerHTML = `<option value="">Loading devices…</option>`;
  try {
    const d = await (await api("/api/devices")).json();
    const def = d.default ? `System default (${d.default})` : "System default";
    mic.innerHTML = `<option value="">${def}</option>` +
      d.mics.map(m => `<option value="${m.replaceAll('"',"&quot;")}">${m}</option>`).join("");
    mic.value = d.mics.includes(c.mic_device) ? c.mic_device : "";
    $("s-loop").textContent = d.loopback
      ? `Speakers captured via: ${d.loopback}`
      : (d.error ? "Device list unavailable: " + d.error : "");
  } catch {
    mic.innerHTML = `<option value="">System default</option>`;
  }
}
$("gear").onclick = openSettings;

$("s-save").onclick = async () => {
  await api("/api/config", { method:"POST", body: JSON.stringify({
    whisper_model: $("s-model").value.trim() || "small.en",
    whisper_device: $("s-device").value,
    me_label: $("s-me").value.trim() || "Me",
    them_label: $("s-them").value.trim() || "Them",
    mic_device: $("s-mic").value,
    recordings_dir: $("s-dir").value.trim() || "recordings",
    use_pocket_api: $("s-api").checked,
    push_to_pocket: $("s-push").checked,
    prefer_pocket_transcripts: $("s-prefer").checked,
    delete_from_device: $("s-del").checked,
    pocket_api_key: $("s-key").value,
  })});
  $("settings").hidden = true;
  poll();
  if (tab === "recordings") loadList();
};

$("s-phone").onclick = async () => {
  const d = await (await api("/api/phone")).json();
  $("settings").hidden = true;
  $("p-url").textContent = d.url;
  $("p-qr").innerHTML = d.svg || "";
  $("p-qr").style.display = d.svg ? "" : "none";
  $("phone").hidden = false;
};

$("s-viewlog").onclick = async () => {
  const r = await api("/api/log");
  $("settings").hidden = true;
  const audio = $("v-audio");
  audio.hidden = true; audio.pause(); audio.removeAttribute("src");
  $("v-text").hidden = false;
  $("v-text").textContent = await r.text();
  $("v-copy").hidden = false;
  showViewer("scribe.log", "/api/log?token=" + token);
  const body = document.querySelector("#viewer .body");
  body.scrollTop = body.scrollHeight;
};

$("s-quit").onclick = async () => {
  if (!confirm("Quit Scribe? A recording in progress would be lost.")) return;
  try { await api("/api/quit", { method:"POST" }); } catch {}
  $("settings").hidden = true;
  $("caption").textContent = "Scribe stopped. You can close this window.";
  setTimeout(() => window.close(), 400);
};

// --- overlays: backdrop click + Escape close ----------------------------
for (const id of ["viewer", "settings", "phone"]) {
  $(id).addEventListener("click", e => { if (e.target === $(id)) $(id).hidden = true; });
}
$("v-close").onclick = () => { const a = $("v-audio"); a.pause(); $("viewer").hidden = true; };
$("s-close").onclick = () => $("settings").hidden = true;
$("p-close").onclick = () => $("phone").hidden = true;
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { $("v-audio").pause(); $("viewer").hidden = true; $("settings").hidden = true; $("phone").hidden = true; }
});

// --- misc --------------------------------------------------------------
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
