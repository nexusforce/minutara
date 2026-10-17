"""
main.py — CLI.

    python -m scribe.main record --title "Board sync"
    python -m scribe.main from-usb            # pull off the Pocket device
    python -m scribe.main from-usb --watch    # wait for it to be plugged in
    python -m scribe.main serve               # phone remote
    python -m scribe.main pull --days 7       # Pocket cloud (needs toggle on)
    python -m scribe.main transcribe file.wav
    python -m scribe.main config              # show what's on and off

Cloud is off by default. Turn it on in config.json.
"""

from __future__ import annotations

import argparse
import threading
from datetime import date, datetime, timedelta, timezone

from . import config as config_mod
from .log import get
from .pipeline import process, stamped_name

log = get("scribe")


def cmd_record(args, cfg) -> None:
    from .capture import record

    name = stamped_name(args.title)
    wav = cfg.recordings_dir / f"{name}.wav"

    stop = threading.Event()
    print(f"Recording '{args.title}'. Ctrl+C to stop.\n")
    result = record(wav, stop, mic_name=cfg.mic_device, speaker_name=cfg.speaker_device)
    print(f"\nStopped after {result.duration_s/60:.1f} min -> {wav.name}")

    if args.no_transcribe:
        return
    print("Transcribing...")
    txt = process(
        wav, cfg,
        title=args.title,
        duration_s=result.duration_s,
        recorded_at=datetime.fromtimestamp(result.started_at, tz=timezone.utc),
    )
    print(f"Transcript -> {txt}")


def cmd_from_usb(args, cfg) -> None:
    from .usb_ingest import find_pocket_drive, ingest, watch

    record_dir = (
        watch(cfg.usb_volume_label, cfg.usb_record_dir)
        if args.watch
        else find_pocket_drive(cfg.usb_volume_label, cfg.usb_record_dir)
    )
    if not record_dir:
        print(f"No {cfg.usb_volume_label} drive found.")
        print("Plug in Pocket, then turn on Settings -> Pocket Web Sync (USB Mode)")
        print("in the phone app. A charge-only cable will not work.")
        return

    result = ingest(record_dir, cfg.recordings_dir, cfg.manifest_path,
                    cfg.delete_from_device)
    if not result.copied:
        print(f"Nothing new. {result.skipped} recording(s) already ingested.")
        return

    print(f"Copied {len(result.copied)} new recording(s).")
    if args.no_transcribe:
        return

    for f in result.copied:
        print(f"  transcribing {f.name}...")
        txt = process(f, cfg, title=f.stem)
        print(f"  -> {txt.name}")


def cmd_pull(args, cfg) -> None:
    if not cfg.pocket_enabled:
        print("The Pocket API is off. Set use_pocket_api and pocket_api_key in config.json.")
        print("For a local-only pull, use: from-usb")
        return

    from .pocket_client import PocketClient

    client = PocketClient(cfg.pocket_api_key, base_url=cfg.pocket_base_url)

    if args.id:
        details = client.get_recording(args.id)
        title = details.get("title") or args.id
        audio = client.download_audio(args.id, cfg.recordings_dir / f"pocket-{args.id}.m4a")
        print(f"Downloaded {audio.name}")
        txt = process(audio, cfg, title=title)
        print(f"Transcript -> {txt}")
        return

    start = (date.today() - timedelta(days=args.days)).isoformat()
    recs = client.list_recordings(start_date=start, limit=args.limit)
    if not recs:
        print("No recordings in that window.")
        return
    print(f"{len(recs)} recording(s) since {start}:\n")
    for r in recs:
        print(f"  {r.id}  {r.recorded_at:<26}  {r.title}")


def cmd_transcribe(args, cfg) -> None:
    from pathlib import Path

    txt = process(Path(args.path), cfg)
    print(f"Transcript -> {txt}")


def cmd_serve(args, cfg) -> None:
    from .server import serve

    if args.port:
        cfg.server_port = args.port
    serve(cfg, open_browser=args.open)


def cmd_config(args, cfg) -> None:
    from .usb_ingest import find_pocket_drive

    key = "set" if cfg.pocket_api_key else "not set"
    drive = find_pocket_drive(cfg.usb_volume_label, cfg.usb_record_dir)

    print(f"""
  App root          {config_mod.APP_ROOT}
  Config file       {config_mod.CONFIG_PATH}

  Whisper           {cfg.whisper_model} on {cfg.whisper_device}
  Pocket drive      {drive or 'not connected'}

  Pocket API        {'ON' if cfg.use_pocket_api else 'off'}   (api key {key})
  Push to Pocket    {'ON' if cfg.push_to_pocket else 'off'}
  Pocket transcripts{'  ON' if cfg.prefer_pocket_transcripts else '  off'}
  Delete from device{'  ON' if cfg.delete_from_device else '  off'}

  Remote            http://<this-pc>:{cfg.server_port}/?token={cfg.server_token}
""")


def cmd_doctor(args, cfg) -> None:
    """Check that every part Scribe needs loads on this machine."""
    import platform
    import sys

    results = []

    def step(name, fn):
        try:
            results.append((True, name, fn() or "ok"))
        except Exception as e:  # report everything; this is a diagnostic
            results.append((False, name, f"{e.__class__.__name__}: {e}"))

    def soundfile_check():
        import soundfile
        return soundfile.__libsndfile_version__

    def av_check():
        import av
        return av.__version__

    def ct2_check():
        import ctranslate2
        return f"{ctranslate2.__version__}, {ctranslate2.get_cuda_device_count()} NVIDIA GPU(s)"

    def vad_check():
        import numpy as np
        from faster_whisper.vad import get_speech_timestamps
        get_speech_timestamps(np.zeros(16000, dtype=np.float32))
        return "Silero VAD model loads"

    def capture_check():
        if platform.system() != "Windows":
            return "skipped (Windows only)"
        from .capture import list_input_devices
        d = list_input_devices()
        return f"{len(d['mics'])} microphone(s); speakers via {d['loopback'] or 'none found'}"

    def web_check():
        from .server import WEB_DIR
        missing = [f for f in ("index.html", "app.js", "app.css", "widget.html")
                   if not (WEB_DIR / f).exists()]
        if missing:
            raise FileNotFoundError(f"missing {', '.join(missing)} in {WEB_DIR}")
        return str(WEB_DIR)

    def window_check():
        if platform.system() != "Windows":
            return "skipped (Windows only)"
        from . import window
        ok, detail = window.available()
        if ok:
            return f"opens in its own window ({detail})"
        if "not found" in detail:  # Edge's app window stands in; not a failure
            return f"{detail}; Scribe will use an Edge window instead"
        raise RuntimeError(detail)

    def transcribe_check():
        import tempfile
        import wave
        from pathlib import Path

        import numpy as np

        from .transcribe import transcribe
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            t = np.arange(16000 * 2) / 16000
            pcm = (0.2 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(np.repeat(pcm, 2).tobytes())
            transcribe(wav, model_size=cfg.whisper_model, device=cfg.whisper_device)
        return f"{cfg.whisper_model} loaded and ran"

    step("Python", lambda: f"{platform.python_version()} ({platform.machine()}, "
                           f"{'packaged app' if getattr(sys, 'frozen', False) else 'source'})")
    step("audio files (libsndfile)", soundfile_check)
    step("audio decoding (PyAV)", av_check)
    step("speech engine (CTranslate2)", ct2_check)
    step("voice detection", vad_check)
    step("recording devices", capture_check)
    step("app interface files", web_check)
    step("app window", window_check)
    if args.transcribe:
        step("transcription", transcribe_check)

    for ok, name, detail in results:
        print(f"  {'OK  ' if ok else 'FAIL'}  {name}: {detail}")
    failed = [name for ok, name, _ in results if not ok]
    log.info("doctor: %s", "all OK" if not failed else f"failed: {', '.join(failed)}")
    if failed:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scribe")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="capture a Zoom/Teams call from system audio")
    r.add_argument("--title", default="Meeting")
    r.add_argument("--no-transcribe", action="store_true")
    r.set_defaults(func=cmd_record)

    u = sub.add_parser("from-usb", help="pull recordings off the Pocket over USB-C")
    u.add_argument("--watch", action="store_true", help="wait for the drive to appear")
    u.add_argument("--no-transcribe", action="store_true")
    u.set_defaults(func=cmd_from_usb)

    l = sub.add_parser("pull", help="fetch from Pocket's cloud (needs the API toggle)")
    l.add_argument("--days", type=int, default=7)
    l.add_argument("--limit", type=int, default=20)
    l.add_argument("--id")
    l.set_defaults(func=cmd_pull)

    t = sub.add_parser("transcribe", help="transcribe an audio file you already have")
    t.add_argument("path")
    t.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("serve", help="start the app (GUI + phone remote)")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true",
                   help="also open the GUI in a window on this machine")
    s.set_defaults(func=cmd_serve)

    c = sub.add_parser("config", help="show current settings")
    c.set_defaults(func=cmd_config)

    d = sub.add_parser("doctor", help="check that every part Scribe needs works here")
    d.add_argument("--transcribe", action="store_true",
                   help="also load the speech model and transcribe a test tone")
    d.set_defaults(func=cmd_doctor)

    return p


def main() -> None:
    args = build_parser().parse_args()
    cfg = config_mod.load()
    log.info("command: %s", args.cmd)
    try:
        args.func(args, cfg)
    except Exception:
        log.exception("command %r failed", args.cmd)
        raise


if __name__ == "__main__":
    main()
