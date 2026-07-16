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
from .pipeline import process, stamped_name


def cmd_record(args, cfg) -> None:
    from .capture import record

    name = stamped_name(args.title)
    wav = cfg.recordings_dir / f"{name}.wav"

    stop = threading.Event()
    print(f"Recording '{args.title}'. Ctrl+C to stop.\n")
    result = record(wav, stop)
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

    client = PocketClient(cfg.pocket_api_key)

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
    serve(cfg)


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

    s = sub.add_parser("serve", help="start the phone remote")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    c = sub.add_parser("config", help="show current settings")
    c.set_defaults(func=cmd_config)

    return p


def main() -> None:
    args = build_parser().parse_args()
    cfg = config_mod.load()
    args.func(args, cfg)


if __name__ == "__main__":
    main()
