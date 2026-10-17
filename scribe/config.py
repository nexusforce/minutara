"""
config.py — one place for every toggle, and portable-aware paths.

Everything the app writes lives under APP_ROOT, which is the folder the app
was launched from. On a USB stick that means recordings, transcripts, the
Whisper model, and the ingest manifest all travel with the stick and nothing
is left behind on the host machine.

Settings resolve in this order: environment variable (SCRIBE_<KEY>) >
config.json next to the app > default. The env override matters because
config.json is written in full on first run — a one-off run like
SCRIBE_WHISPER_DEVICE=cuda would be impossible if the file always won.
Cloud is off unless you turn it on.

Services change their APIs, model names and addresses, so every endpoint,
model and limit that talks to the outside world lives here (and in the
Settings page) rather than in code. The nested "ai" and "granola" sections
merge over the defaults below, so new options appear automatically after an
update while everything you've set is kept.
"""

from __future__ import annotations

import copy
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

# --- AI notes and "Ask" ------------------------------------------------------
# provider "off" means no transcript ever leaves the machine for AI.
# A blank base_url means "the provider's normal address". A blank prompt
# means "use Scribe's built-in prompt" (so prompt improvements still arrive).
AI_DEFAULTS: dict = {
    "provider": "off",          # off | anthropic | openai | gemini | ollama | compatible
    "auto_summarize": True,     # write AI notes after each transcript (when a provider is on)
    "summary_prompt": "",
    "chat_prompt": "",
    "ask_recent_meetings": 15,  # how many recent meetings "Ask" looks across
    "providers": {
        "anthropic": {
            "api_key": "", "base_url": "", "model": "claude-opus-5-5",
            "effort": "medium",        # low | medium | high | xhigh | max | "" (omit)
            "fallbacks": "default",    # server-side fallback on refusals; "" turns it off
            "max_tokens": 16000, "timeout": 600, "max_input_chars": 2_000_000,
        },
        "openai": {
            "api_key": "", "base_url": "", "model": "gpt-6.1-sol",
            "api_style": "responses",  # responses | chat_completions
            "token_param": "max_completion_tokens",
            "max_tokens": 16000, "timeout": 600, "max_input_chars": 1_500_000,
        },
        "gemini": {
            "api_key": "", "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": "gemini-flash-latest",
            "generate_path": "/models/{model}:generateContent",
            "models_path": "/models",
            "max_tokens": 16000, "timeout": 600, "max_input_chars": 2_000_000,
        },
        "ollama": {
            "base_url": "http://localhost:11434", "model": "llama3.2:3b",
            "num_ctx": 16384,          # Ollama's own default is tiny; long meetings need room
            "timeout": 1800, "max_input_chars": 40_000,
        },
        "compatible": {                # LM Studio, OpenRouter, vLLM, Azure-style gateways…
            "api_key": "", "base_url": "", "model": "",
            "api_style": "chat_completions",
            "token_param": "max_tokens",
            "max_tokens": 4096, "timeout": 600, "max_input_chars": 100_000,
        },
    },
}

# --- Granola import ----------------------------------------------------------
GRANOLA_DEFAULTS: dict = {
    "method": "mcp",            # mcp (sign in with Granola) | api (Personal API key)
    "mcp_url": "https://mcp.granola.ai/mcp",
    "api_base": "https://public-api.granola.ai/v1",
    "api_key": "",
    "since": "2018-01-01",      # import meetings from this date on
    "scope": "mine",            # mine (recorded or attended) | all (everything visible)
    "client_id": "",            # only if Granola issues you a fixed OAuth client id
    "redirect_host": "127.0.0.1",
}

SECRET_KEYS = {"api_key"}
GRANOLA_AUTH_PATH = APP_ROOT / "granola_auth.json"


@dataclass
class Config:
    # --- Pocket cloud. All three default OFF. Nothing leaves the machine
    #     unless you deliberately turn one on. ---
    use_pocket_api: bool = False       # master switch for anything network-facing
    push_to_pocket: bool = False       # upload PC-captured audio to Pocket
    prefer_pocket_transcripts: bool = False  # use their transcript instead of local Whisper
    pocket_api_key: str = ""
    pocket_base_url: str = "https://public.heypocketai.com/api/v1"

    # --- Local transcription ---
    whisper_model: str = "small.en"
    whisper_device: str = "cpu"        # "cuda" if you have an NVIDIA GPU
    me_label: str = "Me"
    them_label: str = "Them"

    # --- Capture ---
    mic_device: str = ""               # substring of a mic name; "" = system default
    speaker_device: str = ""           # output to capture "their" side from; "" = Windows default
    auto_transcribe: bool = True       # transcribe automatically when a recording stops

    # --- Meeting detection and the floating recorder ---
    meeting_detect: bool = True        # pop up "Meeting detected" when a meeting app takes the mic
    meeting_apps: str = "zoom.exe, teams.exe, msteams, webex, ciscocollabhost.exe"
    meeting_browsers: bool = False     # also count browsers (Google Meet, Teams on the web)
    floating_recorder: bool = True     # small on-top recorder while Scribe's window isn't in front
    widget_pos: str = ""               # where you last dragged it ("x,y"); "" = bottom right

    # --- USB ingest ---
    usb_volume_label: str = "POCKET"
    usb_record_dir: str = "RECORD"
    delete_from_device: bool = False   # never delete by default

    # --- Phone remote ---
    server_port: int = 8765
    server_token: str = ""             # generated on first run

    # --- Nested sections ---
    ai: dict = field(default_factory=lambda: copy.deepcopy(AI_DEFAULTS))
    granola: dict = field(default_factory=lambda: copy.deepcopy(GRANOLA_DEFAULTS))

    # --- Paths (all under APP_ROOT so the stick stays self-contained) ---
    # recordings_dir is user-configurable (Settings in the GUI, or
    # "recordings_dir" in config.json — relative paths resolve under APP_ROOT).
    recordings_dir: Path = field(default_factory=lambda: APP_ROOT / "recordings")
    transcripts_dir: Path = field(default_factory=lambda: APP_ROOT / "transcripts")
    notes_dir: Path = field(default_factory=lambda: APP_ROOT / "notes")
    model_dir: Path = field(default_factory=lambda: APP_ROOT / "models")
    manifest_path: Path = field(default_factory=lambda: APP_ROOT / "ingested.json")
    archive_dir: Path = field(default_factory=lambda: APP_ROOT / "granola_archive")
    cache_dir: Path = field(default_factory=lambda: APP_ROOT / "cache")

    def ensure_dirs(self) -> None:
        for d in (self.recordings_dir, self.transcripts_dir, self.notes_dir,
                  self.model_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def pocket_enabled(self) -> bool:
        return self.use_pocket_api and bool(self.pocket_api_key)

    @property
    def ai_enabled(self) -> bool:
        return self.ai.get("provider", "off") in self.ai.get("providers", {})


FLAT_KEYS = (
    "use_pocket_api", "push_to_pocket", "prefer_pocket_transcripts",
    "pocket_api_key", "pocket_base_url", "whisper_model", "whisper_device",
    "me_label", "them_label", "mic_device", "speaker_device", "auto_transcribe",
    "meeting_detect", "meeting_apps", "meeting_browsers", "floating_recorder", "widget_pos",
    "usb_volume_label", "usb_record_dir",
    "delete_from_device", "server_port", "server_token",
)


def _coerce(value, default):
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        if value in ("", None):
            return default
        return int(value)
    if isinstance(default, str):
        return "" if value is None else str(value)
    return value


def merge(defaults: dict, data) -> dict:
    """Defaults, overlaid with whatever the file has (types follow the
    defaults). Keys the defaults don't know are kept, for forward-compat."""
    out = copy.deepcopy(defaults)
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        if key in defaults and isinstance(defaults[key], dict):
            out[key] = merge(defaults[key], value)
        elif key in defaults:
            try:
                out[key] = _coerce(value, defaults[key])
            except (TypeError, ValueError):
                pass
        else:
            out[key] = value
    return out


def apply_update(current: dict, defaults: dict, update) -> None:
    """Merge a partial update from the GUI into a nested section in place.
    A blank api_key keeps the saved one; null clears it."""
    if not isinstance(update, dict):
        return
    for key, value in update.items():
        sub_defaults = defaults.get(key) if isinstance(defaults, dict) else None
        if isinstance(current.get(key), dict) and isinstance(value, dict):
            apply_update(current[key], sub_defaults if isinstance(sub_defaults, dict) else {}, value)
            continue
        if key in SECRET_KEYS:
            if value is None:
                current[key] = ""
            elif str(value).strip():
                current[key] = str(value).strip()
            continue
        if sub_defaults is not None:
            try:
                current[key] = _coerce(value, sub_defaults)
            except (TypeError, ValueError):
                continue
        else:
            current[key] = value


def redacted(section: dict) -> dict:
    """A copy safe to send to a browser: secrets become "<key>_set" flags."""
    out = {}
    for key, value in section.items():
        if isinstance(value, dict):
            out[key] = redacted(value)
        elif key in SECRET_KEYS:
            out[f"{key}_set"] = bool(value)
        else:
            out[key] = value
    return out


def resolve_dir(value) -> Path:
    """A user-supplied folder: absolute stays put, relative goes under the app."""
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else APP_ROOT / p


def load() -> Config:
    cfg = Config()

    file_data = {}
    if CONFIG_PATH.exists():
        try:
            file_data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"config.json is not valid JSON ({e}). Using defaults.", file=sys.stderr)

    for key in FLAT_KEYS:
        default = getattr(cfg, key)
        if key in file_data:
            try:
                setattr(cfg, key, _coerce(file_data[key], default))
            except (TypeError, ValueError):
                pass
        env = os.environ.get(f"SCRIBE_{key.upper()}")
        if env is not None:
            setattr(cfg, key, _coerce(env, default))

    cfg.ai = merge(AI_DEFAULTS, file_data.get("ai"))
    cfg.granola = merge(GRANOLA_DEFAULTS, file_data.get("granola"))

    if "recordings_dir" in file_data:
        cfg.recordings_dir = resolve_dir(file_data["recordings_dir"])
    env_dir = os.environ.get("SCRIBE_RECORDINGS_DIR")
    if env_dir:
        cfg.recordings_dir = resolve_dir(env_dir)

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
    data = {key: getattr(cfg, key) for key in FLAT_KEYS}
    data["recordings_dir"] = str(cfg.recordings_dir)
    data["ai"] = cfg.ai
    data["granola"] = cfg.granola
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)  # a crash mid-save never leaves half a config
