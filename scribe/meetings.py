"""
meetings.py — notice when a meeting starts, the way Granola does.

Windows keeps a record of which apps are using the microphone right now:
it's what lights up the mic icon in the taskbar. Each app has an entry under

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\
        ConsentStore\\microphone\\<packaged app>           (e.g. new Teams)
        ConsentStore\\microphone\\NonPackaged\\<C:#path#to#app.exe>   (e.g. Zoom)

with LastUsedTimeStop = 0 while the app holds the mic. When Zoom, Teams or
Webex picks up the microphone, a meeting has started; when it lets go, the
meeting is over. Nothing is listened to or sent anywhere: this only reads
which apps Windows says are using the mic.

Which apps count is a setting (meeting_apps), so a new or renamed meeting
app is a settings change, not a code change. Browsers (Google Meet, Teams
on the web) are opt-in, since any web page using the mic would count.
"""

from __future__ import annotations

import sys
import threading
import time

from .log import get

log = get("scribe.meetings")

MIC_KEY = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
           r"\ConsentStore\microphone")

DEFAULT_APPS = "zoom.exe, teams.exe, msteams, webex, ciscocollabhost.exe"
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
            "vivaldi.exe", "arc.exe")

# Friendly names for what's matched (first hit wins).
NAMES = (
    ("zoom", "Zoom"), ("msteams", "Teams"), ("teams", "Teams"),
    ("webex", "Webex"), ("ciscocollab", "Webex"), ("slack", "Slack"),
    ("chrome", "Chrome"), ("msedge", "Edge"), ("firefox", "Firefox"),
    ("brave", "Brave"), ("opera", "Opera"), ("vivaldi", "Vivaldi"), ("arc.exe", "Arc"),
)


def mic_users() -> list[str]:
    """Apps Windows says are using the microphone right now (registry names)."""
    if sys.platform != "win32":
        return []
    import winreg

    users: list[str] = []

    def scan(key, prefix=""):
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                try:
                    with winreg.OpenKey(key, name) as sub:
                        scan(sub)
                except OSError:
                    pass
                continue
            try:
                with winreg.OpenKey(key, name) as app:
                    stop, _ = winreg.QueryValueEx(app, "LastUsedTimeStop")
                    start, _ = winreg.QueryValueEx(app, "LastUsedTimeStart")
                if stop == 0 and start > 0:
                    users.append(name.replace("#", "\\"))
            except OSError:
                continue

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY) as root:
            scan(root)
    except OSError:
        pass
    return users


def patterns(apps: str, browsers: bool) -> list[str]:
    pats = [p.strip().lower() for p in (apps or "").split(",") if p.strip()]
    if browsers:
        pats += list(BROWSERS)
    return pats


def friendly(entry: str) -> str:
    e = entry.lower()
    for key, name in NAMES:
        if key in e:
            return name
    tail = entry.replace("/", "\\").split("\\")[-1]
    name = tail.rsplit(".", 1)[0]
    return (name[:1].upper() + name[1:]) or "Meeting"


def find_meeting(users: list[str], pats: list[str]) -> str:
    """The friendly name of the first meeting app holding the mic, or ""."""
    for u in users:
        low = u.lower()
        if any(p in low for p in pats):
            return friendly(u)
    return ""


class MeetingWatcher:
    """
    Polls every couple of seconds. `app` is the meeting app holding the mic
    now; `ended` is set when it lets go (until something acknowledges it).
    """

    def __init__(self, cfg, reader=mic_users, interval: float = 2.0):
        self.cfg = cfg
        self.reader = reader
        self.interval = interval
        self.lock = threading.Lock()
        self.app = ""
        self.since = 0.0
        self.dismissed = False
        self.ended = ""
        self.preview_until = 0.0
        self.error = ""
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name="meetings").start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def tick(self) -> None:
        app = ""
        if time.monotonic() < self.preview_until:
            app = "Zoom"
        elif getattr(self.cfg, "meeting_detect", True):
            try:
                app = find_meeting(self.reader(),
                                   patterns(self.cfg.meeting_apps, self.cfg.meeting_browsers))
                self.error = ""
            except Exception as e:   # never let detection take anything else down
                if str(e) != self.error:
                    log.warning("meeting detection failed: %s", e)
                self.error = str(e)
        with self.lock:
            if app and app != self.app:
                log.info("meeting detected: %s is using the microphone", app)
                self.app, self.since, self.dismissed, self.ended = app, time.time(), False, ""
            elif not app and self.app:
                log.info("meeting ended: %s let go of the microphone", self.app)
                self.ended, self.app = self.app, ""

    def preview(self, seconds: float = 30) -> None:
        """Pretend a Zoom meeting started, to show what the pop-up looks like."""
        self.preview_until = time.monotonic() + seconds
        self.tick()

    def dismiss(self) -> None:
        with self.lock:
            self.dismissed = True
            self.preview_until = 0.0

    def clear_ended(self) -> None:
        with self.lock:
            self.ended = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {"app": self.app, "since": self.since, "dismissed": self.dismissed,
                    "ended": self.ended, "enabled": bool(getattr(self.cfg, "meeting_detect", True)),
                    "preview": time.monotonic() < self.preview_until}
