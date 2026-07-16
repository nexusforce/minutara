"""
log.py — one rotating log file next to the app, plus the console when there
is one.

Everything the app does lands in scribe.log (capped at ~1 MB x 3 files, so it
can never eat the disk). The console, when present, only shows warnings — the
file has the detail. View it from the GUI (Settings -> View log) or open the
file directly.

This module also keeps Scribe alive under pythonw.exe: windowless Python has
no stdout/stderr, so any bare print() would crash. We point both at the log.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import APP_ROOT

LOG_PATH = APP_ROOT / "scribe.log"
_configured = False


class _StreamToLog:
    """stdout/stderr stand-in under pythonw, where the real ones are None."""

    def __init__(self, level: int):
        self.level = level

    def write(self, msg: str) -> None:
        msg = msg.rstrip()
        if msg:
            logging.getLogger("scribe.console").log(self.level, msg)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def setup() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2,
                             encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(fh)

    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)  # console stays quiet; the file has it all
        sh.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
        root.addHandler(sh)

    if sys.stdout is None:
        sys.stdout = _StreamToLog(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _StreamToLog(logging.ERROR)


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
