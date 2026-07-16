"""
config.py — one place for every toggle, and portable-aware paths.

Everything the app writes lives under APP_ROOT, which is the folder the app
was launched from. On a USB stick that means recordings, transcripts, the
Whisper model, and the ingest manifest all travel with the stick and nothing
is left behind on the host machine.

Settings resolve in this order: config.json next to the app > environment
variable > default. Cloud is off unless you turn it on.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _app_root() -> Path:
    """Folder the app lives in — works frozen (PyInstaller) or as source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


APP_ROOT = _app_root()
CONFIG_PATH = APP_ROOT / "config.json"


@dataclass
class Config:
    # --- Pocket cloud. All three default OFF. Nothing leaves the machine
    #     unless you deliberately turn one on. ---
    use_pocket_api: bool = False       # master switch for anything network-facing
    push_to_pocket: bool = False       # upload PC-captured audio to Pocket
    prefer_pocket_transcripts: bool = False  # use their transcript instead of local Whisper
    pocket_api_key: str = ""

    # --- Local transcription ---
    whisper_model: str = "small.en"
    whisper_device: str = "cpu"        # "cuda" if you have an NVIDIA GPU
    me_label: str = "Me"
    them_label: str = "Them"

    # --- USB ingest ---
    usb_volume_label: str = "POCKET"
    usb_record_dir: str = "RECORD"
    delete_from_device: bool = False   # never delete by default

    # --- Phone remote ---
    server_port: int = 8765
    server_token: str = ""             # generated on first run

    # --- Paths (all under APP_ROOT so the stick stays self-contained) ---
    recordings_dir: Path = field(default_factory=lambda: APP_ROOT / "recordings")
    transcripts_dir: Path = field(default_factory=lambda: APP_ROOT / "transcripts")
    model_dir: Path = field(default_factory=lambda: APP_ROOT / "models")
    manifest_path: Path = field(default_factory=lambda: APP_ROOT / "ingested.json")

    def ensure_dirs(self) -> None:
        for d in (self.recordings_dir, self.transcripts_dir, self.model_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def pocket_enabled(self) -> bool:
        return self.use_pocket_api and bool(self.pocket_api_key)


def _coerce(value, default):
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    return value


def load() -> Config:
    cfg = Config()

    file_data = {}
    if CONFIG_PATH.exists():
        try:
            file_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"config.json is not valid JSON ({e}). Using defaults.", file=sys.stderr)

    for key in (
        "use_pocket_api", "push_to_pocket", "prefer_pocket_transcripts",
        "pocket_api_key", "whisper_model", "whisper_device", "me_label",
        "them_label", "usb_volume_label", "usb_record_dir",
        "delete_from_device", "server_port", "server_token",
    ):
        default = getattr(cfg, key)
        if key in file_data:
            setattr(cfg, key, _coerce(file_data[key], default))
        env = os.environ.get(f"MS_{key.upper()}")
        if env is not None:
            setattr(cfg, key, _coerce(env, default))

    # POCKET_API_KEY is the conventional name; honour it too.
    if not cfg.pocket_api_key:
        cfg.pocket_api_key = os.environ.get("POCKET_API_KEY", "")

    # Faster-whisper caches models here instead of %USERPROFILE% — keeps the
    # stick portable and leaves nothing on the host.
    os.environ.setdefault("HF_HOME", str(cfg.model_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(cfg.model_dir))

    cfg.ensure_dirs()

    if not cfg.server_token:
        cfg.server_token = secrets.token_urlsafe(12)
        save(cfg)

    return cfg


def save(cfg: Config) -> None:
    data = {
        "use_pocket_api": cfg.use_pocket_api,
        "push_to_pocket": cfg.push_to_pocket,
        "prefer_pocket_transcripts": cfg.prefer_pocket_transcripts,
        "pocket_api_key": cfg.pocket_api_key,
        "whisper_model": cfg.whisper_model,
        "whisper_device": cfg.whisper_device,
        "me_label": cfg.me_label,
        "them_label": cfg.them_label,
        "usb_volume_label": cfg.usb_volume_label,
        "usb_record_dir": cfg.usb_record_dir,
        "delete_from_device": cfg.delete_from_device,
        "server_port": cfg.server_port,
        "server_token": cfg.server_token,
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
