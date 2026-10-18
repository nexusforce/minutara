"""
server.py — Scribe's engine API, and the GUI it serves.

The GUI is a small web app in scribe/web/ (index.html, app.css, app.js),
laid out like Granola: a sidebar, a home list of meetings grouped by day,
and a note per meeting holding your typed notes, the recording and its
transcript. Scribe.exe opens it in its own window (window.py); a phone on the
same Wi-Fi opens the same page as a remote. Nothing routes through the
internet — the browser talks to this process directly.

Every /api request needs the token from config.json. That matters: this API
can start a recording, so anyone on the same network who finds the port
should not be able to drive it.
"""

from __future__ import annotations

import os
import queue
import re
import socket
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from . import ai, granola, notes
from . import config as config_mod
from .config import AI_DEFAULTS, APP_ROOT, GRANOLA_DEFAULTS, Config
from .log import LOG_PATH, get
from .meetings import MeetingWatcher
from .pipeline import process
from .widget import WidgetController

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
        self.after = None   # called with the note id after each successful transcript
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
                if self.after:
                    self.after(path.stem)
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


class Summarizer:
    """
    Writes AI notes, one meeting at a time, with whichever provider is set in
    Settings → AI. Like transcription it runs in the background, and a
    failure is recorded on the note instead of disappearing.
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

    def enqueue(self, note_id: str) -> bool:
        with self.lock:
            if note_id == self.current or note_id in self.pending:
                return False
            self.pending.append(note_id)
        log.info("queued for AI notes: %s", note_id)
        self.q.put(note_id)
        return True

    def auto(self, note_id: str) -> None:
        if self.cfg.ai_enabled and self.cfg.ai.get("auto_summarize", True):
            self.enqueue(note_id)

    def _work(self) -> None:
        while True:
            nid = self.q.get()
            n = notes.load(self.cfg, nid)
            with self.lock:
                if nid in self.pending:
                    self.pending.remove(nid)
                self.current = nid if n else ""
                self.current_title = (n or {}).get("title") or nid
            if not n:
                continue
            try:
                tpath = notes.transcript_path(self.cfg, n)
                transcript = tpath.read_text(encoding="utf-8", errors="replace") if tpath.exists() else ""
                text = ai.summarize(
                    self.cfg, n.get("title") or "", n.get("created_at", "")[:16].replace("T", " "),
                    transcript, n.get("notes") or "", ", ".join(n.get("attendees") or []))
                name, pc = ai.provider_cfg(self.cfg)
                fields = {"summary": text, "summary_error": "",
                          "summary_meta": {"provider": name, "model": pc.get("model") or "",
                                           "at": datetime.now().isoformat(timespec="seconds")}}
                # Keep Granola's original AI notes if these replace them.
                if (n.get("summary_meta") or {}).get("provider") == "granola" and not n.get("granola_summary"):
                    fields["granola_summary"] = n.get("summary", "")
                notes.update(self.cfg, nid, **fields)
                self.last_message = f"Wrote AI notes for {self.current_title}"
            except ai.AIError as e:
                notes.update(self.cfg, nid, summary_error=str(e))
                self.last_message = f"AI notes for {self.current_title} failed: {e}"
                log.warning("AI notes failed for %s: %s", nid, e)
            except Exception as e:
                log.exception("AI notes failed for %s", nid)
                notes.update(self.cfg, nid, summary_error=str(e))
                self.last_message = f"AI notes for {self.current_title} failed: {e}"
            finally:
                with self.lock:
                    self.current = ""
                    self.current_title = ""

    def status(self) -> dict:
        with self.lock:
            return {"busy": bool(self.current), "id": self.current, "title": self.current_title,
                    "queued": len(self.pending), "pending": list(self.pending),
                    "message": self.last_message}


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
        self.meeting_app = ""  # the meeting app this recording was started for, if any

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

    def start(self, title: str, meeting_app: str = "") -> tuple[bool, str, str]:
        with self.lock:
            if self.state in LIVE_STATES:
                return False, f"Already {self.state}.", self.note_id
            self.title = (title or "").strip()
            self.meeting_app = meeting_app
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
                            speaker_name=self.cfg.speaker_device,
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
    "me_label", "them_label", "mic_device", "speaker_device", "auto_transcribe",
    "meeting_detect", "meeting_apps", "meeting_titles", "meeting_browsers", "floating_recorder",
    "delete_from_device", "pocket_base_url", "usb_volume_label", "usb_record_dir",
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
    summ = Summarizer(cfg)
    trans.after = summ.auto
    rec = Recorder(cfg, trans)
    importer = granola.Importer(cfg)
    pocket_job = {"state": "idle", "message": ""}
    activity = {"t": time.monotonic()}   # last API request, from any window or phone
    test_lock = threading.Lock()

    def busy() -> str:
        """What Scribe is in the middle of, if anything (closing the window waits for it)."""
        if rec.state in LIVE_STATES:
            return "saving the recording"
        t = trans.status()
        if t["busy"] or t["queued"]:
            return "transcribing"
        s = summ.status()
        if s["busy"] or s["queued"]:
            return "writing AI notes"
        if importer.snapshot().get("state") in ("listing", "importing"):
            return "importing from Granola"
        if pocket_job.get("state") == "running":
            return "importing from Pocket"
        return ""

    watcher = MeetingWatcher(cfg)
    watcher.start()
    widget = WidgetController(cfg, rec, watcher)
    app.config["SCRIBE"] = {"rec": rec, "busy": busy, "activity": activity,
                            "watcher": watcher, "widget": widget}

    def authed() -> bool:
        token = request.args.get("token") or request.headers.get("X-Token", "")
        return bool(cfg.server_token) and token == cfg.server_token

    @app.before_request
    def guard():
        if request.path.startswith("/api"):
            if not authed():
                return jsonify({"error": "Bad or missing token."}), 401
            # The floating window polls constantly; it doesn't count as you using Scribe.
            if not request.headers.get("X-Scribe-Widget"):
                activity["t"] = time.monotonic()

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
        s["summarizer"] = summ.status()
        s["ai"] = {"enabled": cfg.ai_enabled,
                   "provider": cfg.ai.get("provider", "off"),
                   "label": ai.describe(cfg) if cfg.ai_enabled else "",
                   "local": cfg.ai.get("provider") in ai.LOCAL}
        imp = importer.snapshot()
        s["granola"] = {k: imp[k] for k in ("state", "done", "total", "message")}
        s["meeting"] = watcher.snapshot()
        s["widget"] = widget.state()
        s["meeting_app"] = rec.meeting_app if rec.state in LIVE_STATES else ""
        return jsonify(s)

    @app.post("/api/start")
    def start():
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", ""))[:200]
        ok, msg, note_id = rec.start(title, meeting_app=str(data.get("meeting_app") or "")[:40])
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
        s = summ.status()
        d.update(has_audio=has_audio, has_transcript=has_t,
                 status=note_status(n, has_audio, has_t, t),
                 has_summary=bool(n.get("summary")),
                 ai_status="writing" if s["id"] == n["id"]
                 else "queued" if n["id"] in s["pending"] else "")
        if full:
            d["notes"] = n.get("notes") or ""
            d["transcript"] = (tpath.read_text(encoding="utf-8", errors="replace")
                               if has_t else "")
            d["me_label"] = cfg.me_label
            d["summary"] = n.get("summary") or ""
            d["summary_meta"] = n.get("summary_meta") or {}
            d["summary_error"] = n.get("summary_error") or ""
            d["attendees"] = n.get("attendees") or []
            d["granola"] = {k: (n.get("granola") or {}).get(k) for k in ("url", "transcript_error")}
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
        from .listen import forget
        forget(cfg.cache_dir, note_id)
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
        download = bool(request.args.get("download"))
        original = audio.name
        if not download and not (note_id == rec.note_id and rec.state in LIVE_STATES):
            # The player gets a listening copy at a normal volume; downloads
            # (and transcription) use the recording exactly as captured.
            from .listen import listening_copy
            audio = listening_copy(audio, cfg.cache_dir)
        # conditional=True gives Range support, which <audio> needs to seek.
        return send_from_directory(audio.parent, audio.name, conditional=True,
                                   as_attachment=download,
                                   download_name=original)

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
            "speaker_device": cfg.speaker_device,
            "meeting_detect": cfg.meeting_detect,
            "meeting_apps": cfg.meeting_apps,
            "meeting_titles": cfg.meeting_titles,
            "meeting_browsers": cfg.meeting_browsers,
            "floating_recorder": cfg.floating_recorder,
            "auto_transcribe": cfg.auto_transcribe,
            "recordings_dir": str(cfg.recordings_dir),
            "use_pocket_api": cfg.use_pocket_api,
            "push_to_pocket": cfg.push_to_pocket,
            "prefer_pocket_transcripts": cfg.prefer_pocket_transcripts,
            "delete_from_device": cfg.delete_from_device,
            # Never echo the key itself back to a browser — only whether one
            # is stored, so the form can say "saved".
            "pocket_key_set": bool(cfg.pocket_api_key),
            "pocket_base_url": cfg.pocket_base_url,
            "usb_volume_label": cfg.usb_volume_label,
            "usb_record_dir": cfg.usb_record_dir,
            "server_port": cfg.server_port,
            "app_root": str(APP_ROOT),
            "ai": config_mod.redacted(cfg.ai),
            "ai_env_keys": {p: bool(ai.api_key(p, {})) for p in ai.PROVIDERS},
            "granola": config_mod.redacted(cfg.granola),
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
        if isinstance(data.get("ai"), dict):
            update = dict(data["ai"])
            if "providers" in update:  # only providers we know, only as objects
                given = update["providers"] if isinstance(update["providers"], dict) else {}
                update["providers"] = {k: v for k, v in given.items()
                                       if k in ai.PROVIDERS and isinstance(v, dict)}
            config_mod.apply_update(cfg.ai, AI_DEFAULTS, update)
            if cfg.ai.get("provider") not in ("off", *ai.PROVIDERS):
                cfg.ai["provider"] = "off"
        if isinstance(data.get("granola"), dict):
            config_mod.apply_update(cfg.granola, GRANOLA_DEFAULTS, data["granola"])
            if cfg.granola.get("method") not in ("mcp", "api"):
                cfg.granola["method"] = "mcp"
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
        info["selected_speaker"] = cfg.speaker_device
        return jsonify(info)

    @app.post("/api/devices/test")
    def devices_test():
        """Record a few seconds from the mic (or the speakers) and report back."""
        if rec.state in LIVE_STATES:
            return fail(409, "Stop the recording before testing.")
        data = request.get_json(silent=True) or {}
        kind = "speakers" if data.get("kind") == "speakers" else "mic"
        mic = str(data.get("mic", cfg.mic_device) or "")
        speaker = str(data.get("speaker", cfg.speaker_device) or "")
        try:
            seconds = float(data.get("seconds") or 4)
        except (TypeError, ValueError):
            seconds = 4.0
        if not test_lock.acquire(blocking=False):
            return fail(409, "A test is already running.")
        try:
            from .capture import test_device
            result = test_device(kind, mic, seconds, speaker_name=speaker)
        except Exception as e:
            log.exception("%s test failed", kind)
            return fail(500, f"Test failed: {e}")
        finally:
            test_lock.release()
        log.info("%s test: %s (%s)", kind, result.get("verdict"), result.get("device"))
        return jsonify(result)

    # --- meetings and the floating window ----------------------------------

    @app.get("/widget")
    def widget_page() -> Response:
        return send_from_directory(WEB_DIR, "widget.html")

    @app.post("/api/meeting/dismiss")
    def meeting_dismiss():
        watcher.dismiss()
        return jsonify({"ok": True})

    @app.post("/api/meeting/keep")
    def meeting_keep():
        watcher.clear_ended()
        return jsonify({"ok": True})

    @app.post("/api/meeting/preview")
    def meeting_preview():
        if rec.state in LIVE_STATES:
            return fail(409, "Stop the recording first.")
        watcher.preview(20)
        return jsonify({"ok": True, "widget": widget.available(),
                        "message": "Showing the meeting pop-up for 20 seconds."})

    @app.post("/api/widget/preview")
    def widget_preview():
        """Settings → Floating recorder → Show me."""
        widget.preview(str((request.get_json(silent=True) or {}).get("face", "recording")), 10)
        if not widget.available():
            return fail(409, "The floating window isn't available: " +
                        (widget.state().get("reason") or "Scribe is open in a browser window."))
        return jsonify({"ok": True, "message": "Showing the floating recorder for 10 seconds."})

    @app.post("/api/widget/hide")
    def widget_hide():
        widget.hide_for_recording()
        return jsonify({"ok": True})

    @app.post("/api/widget/seen")
    def widget_seen():
        widget.seen(str((request.get_json(silent=True) or {}).get("mode", ""))[:20])
        return jsonify({"ok": True})

    @app.post("/api/show")
    def show():
        """A second launch of Scribe asks this one to show its window."""
        from . import window
        return jsonify({"ok": True, "shown": window.bring_to_front()})

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
        return Response("\n".join(lines[-600:]), mimetype="text/plain")

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

    # --- AI notes and Ask -----------------------------------------------------

    @app.post("/api/notes/<note_id>/summarize")
    def summarize_note(note_id: str):
        n = notes.load(cfg, note_id)
        if n is None:
            return fail(404, "No such note.")
        if not cfg.ai_enabled:
            return fail(400, "AI is turned off. Pick a provider in Settings → AI.")
        has_t = notes.transcript_path(cfg, n).exists()
        if not has_t and not (n.get("notes") or "").strip():
            return fail(400, "There's no transcript or notes to write AI notes from yet.")
        queued = summ.enqueue(note_id)
        return jsonify({"ok": True, "message": "Writing AI notes…" if queued else "Already writing."})

    _STOP = set("the a an and or of to in on for with about what when who whom which how did do does "
                "is are was were be been my me i we our you your it this that these those from at by "
                "any all have has had can could should would will there their them they list tell "
                "show give find any recent meeting meetings".split())

    def gather_context(question: str, budget: int) -> tuple[str, list[str]]:
        """Pick meetings for a question across all notes: keyword hits first,
        then the most recent ones, within the provider's input budget."""
        all_n = notes.all_notes(cfg)
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9'’-]{2,}", question.lower()) if w not in _STOP]
        texts, scored = {}, []
        for n in all_n:
            t = notes.transcript_path(cfg, n)
            transcript = t.read_text(encoding="utf-8", errors="replace") if t.exists() else ""
            texts[n["id"]] = transcript
            if words:
                hay = " ".join([n.get("title") or "", n.get("notes") or "",
                                n.get("summary") or "", transcript]).lower()
                score = sum(hay.count(w) for w in words) + \
                    5 * sum((n.get("title") or "").lower().count(w) for w in words)
                if score:
                    scored.append((score, n))
        picks = [n for _, n in sorted(scored, key=lambda x: -x[0])[:8]]
        recent = all_n[: int(cfg.ai.get("ask_recent_meetings") or 15)]
        chosen, seen = [], set()
        for n in picks + recent:
            if n["id"] not in seen:
                seen.add(n["id"])
                chosen.append(n)
        if not chosen:
            return "(No meetings yet.)", []
        per = max(3000, budget // len(chosen))
        blocks, used, sources = [], 0, []
        for n in chosen:
            block = ai.meeting_block(n, texts.get(n["id"], ""), min(per, budget - used))
            if used + len(block) > budget:
                break
            blocks.append(block)
            used += len(block)
            sources.append({"id": n["id"], "title": n.get("title") or "Untitled meeting"})
        return "\n\n".join(blocks), sources

    @app.post("/api/ask")
    def ask_():
        data = request.get_json(silent=True) or {}
        question = str(data.get("question", "")).strip()[:4000]
        if not question:
            return fail(400, "Ask a question first.")
        if not cfg.ai_enabled:
            return fail(400, "AI is turned off. Pick a provider in Settings → AI.")
        history = data.get("history") if isinstance(data.get("history"), list) else []
        budget = ai.context_budget(cfg)
        nid = data.get("note_id")
        if nid:
            n = notes.load(cfg, str(nid))
            if n is None:
                return fail(404, "No such note.")
            t = notes.transcript_path(cfg, n)
            context = ai.meeting_block(
                n, t.read_text(encoding="utf-8", errors="replace") if t.exists() else "", budget)
            sources = [{"id": n["id"], "title": n.get("title") or "Untitled meeting"}]
        else:
            context, sources = gather_context(question, budget)
        try:
            answer = ai.ask(cfg, question, context, history)
        except ai.AIError as e:
            return fail(502, str(e))
        except Exception as e:
            log.exception("ask failed")
            return fail(502, f"Something went wrong asking the AI: {e}")
        return jsonify({"ok": True, "answer": answer, "sources": sources,
                        "provider": ai.describe(cfg)})

    @app.get("/api/ai/defaults")
    def ai_defaults():
        return jsonify({"providers": ai.PROVIDERS, "local": sorted(ai.LOCAL),
                        "summary_prompt": ai.DEFAULT_SUMMARY_PROMPT,
                        "chat_prompt": ai.DEFAULT_CHAT_PROMPT,
                        "defaults": config_mod.redacted(AI_DEFAULTS["providers"])})

    @app.post("/api/ai/models")
    def ai_models():
        provider = str((request.get_json(silent=True) or {}).get("provider", ""))
        if provider not in ai.PROVIDERS:
            return fail(400, "Unknown provider.")
        try:
            return jsonify({"ok": True, "models": ai.list_models(cfg, provider)})
        except ai.AIError as e:
            return fail(502, str(e))

    @app.post("/api/ai/test")
    def ai_test():
        provider = str((request.get_json(silent=True) or {}).get("provider", ""))
        if provider not in ai.PROVIDERS:
            return fail(400, "Unknown provider.")
        try:
            reply = ai.test(cfg, provider)
        except ai.AIError as e:
            return fail(502, str(e))
        except Exception as e:
            log.exception("AI test failed")
            return fail(502, str(e))
        return jsonify({"ok": True, "message": f"Connected to {ai.describe(cfg, provider)}. "
                                               f"It replied: “{reply}”"})

    # --- Granola import ------------------------------------------------------

    def redirect_uri() -> str:
        host = cfg.granola.get("redirect_host") or "127.0.0.1"
        return f"http://{host}:{cfg.server_port}/oauth/granola/callback"

    @app.get("/api/granola/status")
    def granola_status():
        auth = granola.load_auth()
        return jsonify({**importer.snapshot(),
                        "signed_in": bool(auth.get("access_token")),
                        "signed_in_at": auth.get("signed_in_at", ""),
                        "method": cfg.granola.get("method", "mcp"),
                        "api_key_set": bool(cfg.granola.get("api_key")
                                            or os.environ.get("GRANOLA_API_KEY")),
                        "imported_total": sum(1 for _ in cfg.notes_dir.glob("granola-*.json"))})

    @app.post("/api/granola/signin")
    def granola_signin():
        try:
            url = granola.begin_sign_in(cfg, redirect_uri())
        except granola.GranolaError as e:
            return fail(502, str(e))
        return jsonify({"ok": True, "url": url})

    @app.get("/oauth/granola/callback")
    def granola_callback():
        # Not under /api: Granola's sign-in page redirects the browser here.
        # The one-time state value ties it to a sign-in this app started.
        page = ("<!doctype html><meta charset=utf-8><title>Scribe</title>"
                "<body style='font:16px system-ui;padding:40px;background:#262624;color:#ecebe6'>"
                "<h2 style='font-family:Georgia,serif;font-weight:500'>{title}</h2><p>{body}</p>")
        if request.args.get("error"):
            return Response(page.format(title="Granola sign-in didn't finish",
                                        body=f"Granola said: {request.args.get('error')}. "
                                             "Go back to Scribe and try again."), mimetype="text/html")
        try:
            granola.finish_sign_in(request.args.get("code", ""), request.args.get("state", ""))
        except granola.GranolaError as e:
            return Response(page.format(title="Granola sign-in didn't finish", body=str(e)),
                            mimetype="text/html"), 400
        importer.start()
        return Response(page.format(
            title="Signed in to Granola",
            body="Scribe is importing your meetings now. You can close this window and "
                 "watch the progress in Scribe's settings."), mimetype="text/html")

    @app.post("/api/granola/signout")
    def granola_signout():
        granola.clear_auth()
        return jsonify({"ok": True})

    @app.post("/api/granola/import")
    def granola_import():
        refresh = bool((request.get_json(silent=True) or {}).get("refresh"))
        if cfg.granola.get("method", "mcp") == "mcp" and not granola.load_auth().get("access_token"):
            return fail(400, "Sign in to Granola first.")
        ok, msg = importer.start(refresh=refresh)
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 409)

    @app.post("/api/granola/stop")
    def granola_stop():
        importer.stop()
        return jsonify({"ok": True})

    # --- Pocket --------------------------------------------------------------

    @app.get("/api/pocket/status")
    def pocket_status():
        from .usb_ingest import find_pocket_drive
        drive = find_pocket_drive(cfg.usb_volume_label, cfg.usb_record_dir)
        return jsonify({"usb": str(drive) if drive else "",
                        "cloud_enabled": cfg.pocket_enabled,
                        "key_set": bool(cfg.pocket_api_key),
                        "cloud_job": dict(pocket_job)})

    def pocket_cloud_import(days: int) -> None:
        from .pocket_client import PocketClient, PocketError
        try:
            client = PocketClient(cfg.pocket_api_key, base_url=cfg.pocket_base_url)
            start = (date.today() - timedelta(days=days)).isoformat()
            recs, page = [], 1
            while True:
                batch = client.list_recordings(start_date=start, limit=100, page=page)
                recs += batch
                if len(batch) < 100:
                    break
                page += 1
            new = 0
            for r in recs:
                nid = "pocket-cloud-" + re.sub(r"[^A-Za-z0-9_-]", "", r.id)
                if notes.load(cfg, nid):
                    continue
                pocket_job["message"] = f"Downloading “{r.title}”…"
                dest = client.download_audio(r.id, cfg.recordings_dir / f"{nid}.m4a")
                notes.create(cfg, nid, title=r.title,
                             created_at=granola._local_iso(r.recorded_at),
                             audio=dest.name, source="pocket")
                new += 1
                if cfg.auto_transcribe:
                    trans.enqueue(dest, title=r.title)
            pocket_job.update(state="done", message=f"Imported {new} new recording"
                              f"{'s' if new != 1 else ''} from Pocket's cloud "
                              f"({len(recs) - new} already here).")
        except PocketError as e:
            pocket_job.update(state="error", message=f"Pocket: {e}")
        except Exception as e:
            log.exception("pocket cloud import failed")
            pocket_job.update(state="error", message=str(e))

    @app.post("/api/pocket/cloud-import")
    def pocket_cloud():
        if not cfg.pocket_enabled:
            return fail(400, "Turn on Pocket cloud and add your Pocket API key first.")
        if pocket_job.get("state") == "running":
            return fail(409, "Already importing from Pocket.")
        days = max(1, min(int((request.get_json(silent=True) or {}).get("days") or 30), 3650))
        pocket_job.update(state="running", message="Asking Pocket for your recordings…")
        threading.Thread(target=pocket_cloud_import, args=(days,), daemon=True).start()
        return jsonify({"ok": True, "message": "Importing from Pocket's cloud…"})

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


def _wait_for_port(port: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _port_in_use(port):
            return True
        time.sleep(0.1)
    return False


def _show_running(cfg: Config, url: str) -> None:
    """Scribe is already running: bring its window up, or open one onto it."""
    import json
    import urllib.request

    from . import window

    # Only open a second window when the running Scribe says it has none
    # (e.g. it was started with plain `serve`). If it's just slow to answer,
    # keep asking rather than doubling up windows.
    for attempt in range(4):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{cfg.server_port}/api/show?token={cfg.server_token}",
                data=b"{}", method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                shown = json.loads(r.read() or b"{}").get("shown")
            break
        except Exception as e:
            log.info("the running Scribe didn't answer (%s); retrying", e)
            time.sleep(1)
    else:
        log.warning("the running Scribe isn't answering; not opening a second copy")
        return
    if shown:
        log.info("brought the running window to the front")
        return
    log.info("the running Scribe has no window; opening one onto it")
    if not window.run(url):
        _open_ui(url)


def _finish_in_background(state: dict) -> None:
    """
    The window closed. Finish whatever is under way (saving a recording,
    transcribing, AI notes, an import), then exit. Opening Scribe again
    meanwhile reconnects to this process, which keeps it alive.
    """
    busy, activity = state["busy"], state["activity"]
    told = ""
    while True:
        doing = busy()
        recent = time.monotonic() - activity["t"] < 12
        if not doing and not recent:
            break
        if doing and doing != told:
            log.info("window closed; finishing in the background: %s", doing)
            told = doing
        time.sleep(1)
    log.info("nothing left to do; exiting")


def serve(cfg: Config, open_browser: bool = False) -> None:
    local_url = f"http://127.0.0.1:{cfg.server_port}/?token={cfg.server_token}"

    # Opening Scribe twice shouldn't crash on a busy port. If it's already
    # running, just bring up its window.
    if _port_in_use(cfg.server_port):
        log.info("already running on port %s", cfg.server_port)
        print("Scribe is already running.")
        if open_browser:
            _show_running(cfg, local_url)
        return

    app, rec = create_app(cfg)
    lan_url = f"http://{local_ip()}:{cfg.server_port}/?token={cfg.server_token}"

    log.info("serving on port %s (log: %s)", cfg.server_port, LOG_PATH)
    print("\n  Scribe is up. On your phone (same Wi-Fi), open:\n")
    print(f"    {lan_url}\n")
    try:
        if sys.stdout.isatty():   # not into the log, where it's 500 lines of blocks
            import qrcode  # optional
            qr = qrcode.QRCode(border=1)
            qr.add_data(lan_url)
            qr.print_ascii(invert=True)
    except Exception:
        pass
    print("  Windows Firewall will prompt on first run — allow it on Private networks.")
    print("  Quit from the app's settings page (or Ctrl+C here).\n")

    if not open_browser:
        app.run(host="0.0.0.0", port=cfg.server_port, threaded=True)
        return

    # With a window: the server runs on a thread, the window on the main
    # thread (Windows requires that), and closing the window ends the app.
    server = threading.Thread(
        target=app.run, kwargs={"host": "0.0.0.0", "port": cfg.server_port, "threaded": True},
        daemon=True)
    server.start()
    _wait_for_port(cfg.server_port, 15)

    def closing():
        if rec.state in ("recording", "paused"):
            log.info("window closed during a recording; stopping and saving it")
            rec.stop()

    from . import window
    state = app.config["SCRIBE"]
    ui = None
    try:
        widget_url = f"http://127.0.0.1:{cfg.server_port}/widget?token={cfg.server_token}"
        save_timer = {"t": None}

        def moved(x, y):
            # Remember where you dragged it (saved a moment after you let go).
            cfg.widget_pos = f"{x},{y}"
            if save_timer["t"]:
                save_timer["t"].cancel()
            save_timer["t"] = threading.Timer(1.0, config_mod.save, args=(cfg,))
            save_timer["t"].start()

        ui = window.WidgetUI(widget_url, cfg.widget_pos, on_moved=moved)
        state["widget"].ui = ui
        state["widget"].start()
    except Exception:
        log.exception("floating window unavailable")

    if window.run(local_url, on_closing=closing, widget=ui):
        _finish_in_background(app.config["SCRIBE"])
        os._exit(0)

    _open_ui(local_url)   # no native window here: an Edge app window instead
    server.join()
