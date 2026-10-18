"""
widget.py — when the floating window shows, and what it shows.

The floating window (window.py draws it) has three faces:

    prompt     "Zoom meeting · Take notes? [Start] [Not now]"   a meeting app took the mic
    recording  "● 12:34  [Pause] [Stop] [Open] [–]"             while recording, whenever
                                                                 Scribe's own window isn't in front
    ended      "Zoom call ended · [Stop] [Keep recording]"      the meeting app let go of the mic

This module only decides; it never touches audio. If anything here fails,
the floating window simply doesn't appear and recording carries on.
"""

from __future__ import annotations

import threading
import time

from .log import get

log = get("scribe.widget")

# Window size (logical pixels) for each face.
SIZES = {"prompt": (184, 164), "recording": (300, 60), "ended": (236, 148)}
LIVE = ("recording", "paused", "saving")


class WidgetController:
    def __init__(self, cfg, rec, watcher, ui=None):
        self.cfg = cfg
        self.rec = rec
        self.watcher = watcher
        self.ui = ui              # window.WidgetUI on Windows; None where there's no window
        self.mode = ""            # what's showing now ("" = hidden)
        self.hidden_for = ""      # recording (note id) the user tucked the recorder away for
        self.page_seen = 0.0      # when the floating page last drew itself
        self.page_mode = ""
        self._err = ""
        self._stop = threading.Event()

    def available(self) -> bool:
        try:
            return bool(self.ui and self.ui.available())
        except Exception:
            return False

    def desired(self) -> str:
        m = self.watcher.snapshot()
        live = self.rec.state in LIVE
        if not live and m["ended"]:
            self.watcher.clear_ended()   # nothing to ask about once recording has stopped
        if live:
            app = getattr(self.rec, "meeting_app", "")
            if app and m["ended"] == app and self.rec.state != "saving":
                return "ended"
            if (self.cfg.floating_recorder and self.hidden_for != self.rec.note_id
                    and not self._main_in_front()):
                return "recording"
            return ""
        if m["app"] and not m["dismissed"]:
            return "prompt"
        return ""

    def _main_in_front(self) -> bool:
        try:
            return bool(self.ui and self.ui.main_in_front())
        except Exception:
            return False

    def tick(self) -> None:
        if not self.available():
            self.mode = ""
            return
        want = self.desired()
        if want == self.mode:
            return
        if want:
            self.ui.show(want, SIZES[want])
        else:
            self.ui.hide()
        log.info("floating window: %s", want or "hidden")
        self.mode = want

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="widget").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self._err = ""
            except Exception as e:
                if str(e) != self._err:
                    log.warning("floating window: %s", e)
                self._err = str(e)
            self._stop.wait(0.4)

    def hide_for_recording(self) -> None:
        """The – button: tuck the recorder away until this recording ends."""
        self.hidden_for = self.rec.note_id

    def seen(self, mode: str) -> None:
        self.page_seen, self.page_mode = time.time(), mode

    def state(self) -> dict:
        m = self.watcher.snapshot()
        reason = ""
        if not self.available():
            try:
                from .window import last_error
                reason = last_error
            except Exception:
                pass
        return {"available": self.available(), "mode": self.mode, "reason": reason,
                "app": m["app"] or m["ended"], "preview": m.get("preview", False),
                "page_mode": self.page_mode,
                "page_seen_ago": round(time.time() - self.page_seen, 1) if self.page_seen else None}
