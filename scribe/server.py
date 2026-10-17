"""
server.py — Scribe's engine API, and the GUI it serves.

The GUI is a small web app in scribe/web/ (index.html, app.css, app.js),
laid out like Granola: a sidebar, a home list of meetings grouped by day,
and a note per meeting holding your typed notes, the recording and its
transcript. Scribe App.bat opens it in an Edge app window; a phone on the
same Wi-Fi opens the same page as a remote. Nothing routes through the
internet — the browser talks to this process directly.

Every /api request needs the token from config.json. That matters: this API
can start a recording, so anyone on the same network who finds the port
should not be able to drive it.
"""

from __future__ import annotations

import os
import queue
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import config as config_mod
from . import notes
from .config import APP_ROOT, Config
from .log import LOG_PATH, get
from .pipeline import process

log = get("scribe.server")

WEB_DIR = Path(__file__).resolve().parent / "web"
LIVE_STATES = ("recording", "paused", "saving")


class Transcriber:
    """
    A queue and one worker thread, decoupled from capture on purpose: the
    audio is already safe on disk before anything here runs, a failure never
    touches it, and a new recording can start while an old one transcribes.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.pending: list[str] = []
        self.current = ""
        self.current_title = ""
        self.last_message = ""
        threading.Thread(target=self._work, daemon=True).start()

    def enqueue(self, path: Path, title: str | None = None,
                duration_s: float | None = None,
                recorded_at: datetime | None = None) -> bool:
        """Queue a file. False if it's already queued or being transcribed."""
        path = Path(path)
        with self.lock:
            if path.name == self.current or path.name in self.pending:
                return False
            self.pending.append(path.name)
        log.info("queued for transcription: %s", path.name)
        self.q.put((path, title, duration_s, recorded_at))
        return True

    def _work(self) -> None:
        while True:
            path, title, duration_s, recorded_at = self.q.get()
            with self.lock:
                if path.name in self.pending:
                    self.pending.remove(path.name)
                self.current = path.name
                self.current_title = title or path.stem
            try:
                process(path, self.cfg, title=title or path.stem,
                        duration_s=duration_s, recorded_at=recorded_at)
                notes.update(self.cfg, path.stem, error="")
                self.last_message = f"Transcribed {self.current_title}"
            except Exception as e:
                log.exception("transcription failed: %s", path.name)
                notes.update(self.cfg, path.stem, error=f"Transcription failed: {e}")
                self.last_message = f"Transcription of {self.current_title} failed: {e}"
            finally:
                with self.lock:
                    self.current = ""
                    self.current_title = ""

    def status(self) -> dict:
        with self.lock:
            return {
                "busy": bool(self.current),
                "name": self.current,
                "title": self.current_title,
                "queued": len(self.pending),
                "pending": list(self.pending),
                "message": self.last_message,
            }


class Recorder:
    """Owns the single recording session. Every recording is a note."""

    def __init__(self, cfg: Config, transcriber: Transcriber):
        self.cfg = cfg
        self.transcriber = transcriber
        self.lock = threading.Lock()
        self.stop_event: threading.Event | None = None
        self.pause_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.note_id = ""
        self.title = ""
        self.started_at: float | None = None
        self.paused_at: float | None = None
        self.paused_total = 0.0
        self.final_elapsed = 0.0
        self.state = "idle"  # idle | recording | paused | saving | error
        self.last_message = ""

    def _elapsed(self, now: float) -> float:
        if not self.started_at:
            return 0.0
        if self.state == "recording":
            return now - self.started_at - self.paused_total
        if self.state == "paused":
            return self.paused_at - self.started_at - self.paused_total
        if self.state == "saving":
            return self.final_elapsed
        return 0.0

    def start(self, title: str) -> tuple[bool, str, str]:
        with self.lock:
            if self.state in LIVE_STATES:
                return False, f"Already {self.state}.", self.note_id
            self.title = (title or "").strip()
            self.note_id = notes.unique_id(self.cfg, self.title)
            notes.create(self.cfg, self.note_id, title=self.title,
                         audio=f"{self.note_id}.wav", source="pc")
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()
            self.started_at = datetime.now(timezone.utc).timestamp()
            self.paused_at = None
            self.paused_total = 0.0
            self.state = "recording"
            self.last_message = ""
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            log.info("recording started: %s", self.note_id)
            return True, "Recording.", self.note_id

    def pause(self) -> tuple[bool, str]:
        with self.lock:
            if self.state != "recording":
                return False, "Not recording."
            self.pause_event.set()
            self.paused_at = datetime.now(timezone.utc).timestamp()
            self.state = "paused"
            log.info("recording paused")
            return True, "Paused."

    def resume(self) -> tuple[bool, str]:
        with self.lock:
            if self.state != "paused":
                return False, "Not paused."
            self.paused_total += datetime.now(timezone.utc).timestamp() - self.paused_at
            self.paused_at = None
            self.pause_event.clear()
            self.state = "recording"
            log.info("recording resumed")
            return True, "Recording."

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if self.state not in ("recording", "paused"):
                return False, "Not recording."
            self.final_elapsed = self._elapsed(datetime.now(timezone.utc).timestamp())
            self.pause_event.clear()
            self.stop_event.set()
            self.state = "saving"
            log.info("recording stopping")
            return True, "Stopped. Saving."

    def _run(self) -> None:
        from .capture import record

        note_id = self.note_id
        wav = self.cfg.recordings_dir / f"{note_id}.wav"
        try:
            result = record(wav, self.stop_event, mic_name=self.cfg.mic_device,
                            pause_event=self.pause_event)
            # The audio is safe on disk from here on, whatever happens next.
            notes.update(self.cfg, note_id, duration_s=round(result.duration_s, 1),
                         error="")
            note = notes.load(self.cfg, note_id) or {}
            title = note.get("title") or "Untitled meeting"
            if self.cfg.auto_transcribe:
                self.transcriber.enqueue(
                    wav, title=title,
                    duration_s=result.duration_s,
                    recorded_at=datetime.fromtimestamp(result.started_at, tz=timezone.utc),
                )
                self.last_message = f"Saved “{title}”. Transcribing."
            else:
                self.last_message = f"Saved “{title}”."
            self.state = "idle"
        except Exception as e:
            log.exception("recording session failed")
            notes.update(self.cfg, note_id, error=f"Recording failed: {e}")
            self.state = "error"
            self.last_message = str(e)

    def status(self) -> dict:
        now = datetime.now(timezone.utc).timestamp()
        return {
            "state": self.state,
            "note_id": self.note_id,
            "title": self.title,
            "elapsed": max(self._elapsed(now), 0.0),
            "message": self.last_message,
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
    "me_label", "them_label", "mic_device", "auto_transcribe",
    "delete_from_device",
)


def _shutdown() -> None:
    """Exit shortly after the response flushes. Blunt, but right for this app."""
    threading.Timer(0.4, os._exit, args=(0,)).start()


def _model_cached(cfg: Config) -> bool:
    """Has the Whisper model been downloaded yet? (The first run is slow.)"""
    try:
        return any((cfg.model_dir / "hub").glob(f"models--*faster-whisper-{cfg.whisper_model}"))
    except OSError:
        return False


def create_app(cfg: Config) -> tuple[Flask, Recorder]:
    app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="/static")
    trans = Transcriber(cfg)
    rec = Recorder(cfg, trans)

    def authed() -> bool:
        token = request.args.get("token") or request.headers.get("X-Token", "")
        return bool(cfg.server_token) and token == cfg.server_token

    @app.before_request
    def guard():
        if request.path.startswith("/api") and not authed():
            return jsonify({"error": "Bad or missing token."}), 401

    def fail(code: int, message: str):
        return jsonify({"ok": False, "message": message}), code

    @app.get("/")
    def index() -> Response:
        return send_from_directory(WEB_DIR, "index.html")

    # --- recording ---------------------------------------------------------

    @app.get("/api/status")
    def status():
        s = rec.status()
        s["transcriber"] = trans.status()
        s["transcriber"]["model_ready"] = _model_cached(cfg)
        return jsonify(s)

    @app.post("/api/start")
    def start():
        title = str((request.get_json(silent=True) or {}).get("title", ""))[:200]
        ok, msg, note_id = rec.start(title)
        return jsonify({"ok": ok, "message": msg, "note_id": note_id}), (200 if ok else 409)

    @app.post("/api/pause")
    def pause():
        ok, msg = rec.pause()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.post("/api/resume")
    def resume():
        ok, msg = rec.resume()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.post("/api/stop")
    def stop():
        ok, msg = rec.stop()
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    # --- notes -------------------------------------------------------------

    def note_status(n: dict, has_audio: bool, has_t: bool, t: dict) -> str:
        if n["id"] == rec.note_id and rec.state in LIVE_STATES:
            return rec.state
        name = n.get("audio") or ""
        if name and name == t["name"]:
            return "transcribing"
        if name and name in t["pending"]:
            return "queued"
        if has_t:
            return "transcribed"
        if n.get("error"):
            return "error"
        return "audio" if has_audio else "empty"

    def view(n: dict, t: dict, full: bool = False) -> dict:
        audio = notes.audio_path(cfg, n)
        has_audio = bool(audio and audio.exists())
        tpath = notes.transcript_path(cfg, n)
        has_t = tpath.exists()
        d = {k: n.get(k) for k in
             ("id", "title", "created_at", "folder", "source", "duration_s", "error")}
        d.update(has_audio=has_audio, has_transcript=has_t,
                 status=note_status(n, has_audio, has_t, t))
        if full:
            d["notes"] = n.get("notes") or ""
            d["transcript"] = (tpath.read_text(encoding="utf-8", errors="replace")
                               if has_t else "")
            d["me_label"] = cfg.me_label
        return d

    @app.get("/api/notes")
    def list_notes():
        q = request.args.get("q", "").strip()
        folder = request.args.get("folder", "").strip()
        t = trans.status()
        out = []
        for n in notes.all_notes(cfg):
            if folder and n.get("folder") != folder:
                continue
            if q and not notes.matches(cfg, n, q):
                continue
            out.append(view(n, t))
        return jsonify(out)

    @app.get("/api/notes/<note_id>")
    def get_note(note_id: str):
        n = notes.load(cfg, note_id)
        if n is None:
            return fail(404, "No such note.")
        return jsonify(view(n, trans.status(), full=True))

    # POST too, so the page can save with navigator.sendBeacon as it closes.
    @app.route("/api/notes/<note_id>", methods=["PUT", "POST"])
    def save_note(note_id: str):
        data = request.get_json(silent=True) or {}
        fields = {}
        if "title" in data:
            fields["title"] = str(data["title"])[:200]
        if "notes" in data:
            fields["notes"] = str(data["notes"])[:500_000]
        if "folder" in data:
            fields["folder"] = notes.clean_folder(data["folder"])
        if notes.update(cfg, note_id, **fields) is None:
            return fail(404, "No such note.")
        if "title" in fields and note_id == rec.note_id:
            rec.title = fields["title"]
        if fields.get("folder"):
            notes.add_folder(cfg, fields["folder"])
        return jsonify({"ok": True})

    @app.delete("/api/notes/<note_id>")
    def delete_note(note_id: str):
        n = notes.load(cfg, note_id)
        if n is None:
            return fail(404, "No such note.")
        if note_id == rec.note_id and rec.state in LIVE_STATES:
            return fail(409, "Stop the recording first.")
        t = trans.status()
        name = n.get("audio") or ""
        if name and (name == t["name"] or name in t["pending"]):
            return fail(409, "Wait for the transcription to finish first.")
        notes.delete(cfg, note_id)
        return jsonify({"ok": True})

    @app.post("/api/notes/<note_id>/transcribe")
    def transcribe_note(note_id: str):
        n = notes.load(cfg, note_id)
        if n is None:
            return fail(404, "No such note.")
        if note_id == rec.note_id and rec.state in LIVE_STATES:
            return fail(409, "Still recording.")
        audio = notes.audio_path(cfg, n)
        if not audio or not audio.exists():
            return fail(404, "This note has no recording to transcribe.")
        queued = trans.enqueue(audio, title=n.get("title") or None,
                               duration_s=n.get("duration_s"))
        return jsonify({"ok": True, "message": "Queued for transcription."
                        if queued else "Already transcribing."})

    @app.get("/api/notes/<note_id>/audio")
    def note_audio(note_id: str):
        n = notes.load(cfg, note_id)
        audio = notes.audio_path(cfg, n) if n else None
        if not audio or not audio.exists():
            return fail(404, "No recording.")
        # conditional=True gives Range support, which <audio> needs to seek.
        return send_from_directory(audio.parent, audio.name, conditional=True,
                                   as_attachment=bool(request.args.get("download")))

    @app.get("/api/notes/<note_id>/transcript")
    def note_transcript(note_id: str):
        n = notes.load(cfg, note_id)
        t = notes.transcript_path(cfg, n) if n else None
        if not t or not t.exists():
            return fail(404, "No transcript.")
        return send_from_directory(t.parent, t.name,
                                   as_attachment=bool(request.args.get("download")))

    @app.get("/api/folders")
    def list_folders():
        return jsonify({"folders": notes.folders(cfg)})

    @app.post("/api/folders")
    def add_folder():
        name = notes.clean_folder((request.get_json(silent=True) or {}).get("name", ""))
        if not name:
            return fail(400, "Folder names can't be empty or contain slashes.")
        notes.add_folder(cfg, name)
        return jsonify({"ok": True, "name": name})

    @app.delete("/api/folders/<name>")
    def remove_folder(name: str):
        notes.remove_folder(cfg, name)
        return jsonify({"ok": True})

    # --- settings ----------------------------------------------------------

    @app.get("/api/config")
    def get_config():
        return jsonify({
            "whisper_model": cfg.whisper_model,
            "whisper_device": cfg.whisper_device,
            "me_label": cfg.me_label,
            "them_label": cfg.them_label,
            "mic_device": cfg.mic_device,
            "auto_transcribe": cfg.auto_transcribe,
            "recordings_dir": str(cfg.recordings_dir),
            "use_pocket_api": cfg.use_pocket_api,
            "push_to_pocket": cfg.push_to_pocket,
            "prefer_pocket_transcripts": cfg.prefer_pocket_transcripts,
            "delete_from_device": cfg.delete_from_device,
            # Never echo the key itself back to a browser — only whether one
            # is stored, so the form can say "saved".
            "pocket_key_set": bool(cfg.pocket_api_key),
            "server_port": cfg.server_port,
            "app_root": str(APP_ROOT),
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
        log.info("settings saved (recordings_dir=%s, mic=%r, model=%s, auto=%s)",
                 cfg.recordings_dir, cfg.mic_device, cfg.whisper_model,
                 cfg.auto_transcribe)
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

    # --- misc --------------------------------------------------------------

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
            return fail(404, "No Pocket drive found. Plug it in and turn on USB Mode in the Pocket app.")

        result = ingest(record_dir, cfg.recordings_dir, cfg.manifest_path,
                        cfg.delete_from_device)
        log.info("usb ingest: %d new, %d already seen", len(result.copied), result.skipped)
        if not result.copied:
            return jsonify({"ok": True, "message": "Nothing new on your Pocket."})

        notes.sync(cfg)  # each copied file becomes a note
        n = len(result.copied)
        if cfg.auto_transcribe:
            for f in result.copied:
                note = notes.load(cfg, f.stem) or {}
                trans.enqueue(f, title=note.get("title") or None)
            msg = f"Copied {n} recording{'s' if n != 1 else ''}. Transcribing now."
        else:
            msg = f"Copied {n} recording{'s' if n != 1 else ''} from your Pocket."
        return jsonify({"ok": True, "message": msg})

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
    print("  Quit from the app's settings page (or Ctrl+C here).\n")

    if open_browser:
        threading.Timer(1.0, _open_ui, args=(local_url,)).start()

    app.run(host="0.0.0.0", port=cfg.server_port, threaded=True)
