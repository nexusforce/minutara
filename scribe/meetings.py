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
# A browser using the mic counts as a meeting when one of its windows is
# titled like one (Teams, Meet and Zoom on the web, or installed as web apps).
DEFAULT_TITLES = ("microsoft teams, | teams, google meet, meet -, meet \u2013, meet.google, "
                  "zoom, webex, whereby, jitsi, huddle")
TITLE_NAMES = (("teams", "Teams"), ("meet", "Google Meet"), ("zoom", "Zoom"),
               ("webex", "Webex"), ("whereby", "Whereby"), ("jitsi", "Jitsi"),
               ("huddle", "Slack"))
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


def _exe_of(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").split("\\")[-1].lower()
        return ""
    finally:
        k32.CloseHandle(h)


def window_titles() -> list[tuple[str, str]]:
    """(program, title) of every visible top-level window. Windows only."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out: list[tuple[str, str]] = []
    exes: dict[int, str] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in exes:
                    exes[pid.value] = _exe_of(pid.value)
                out.append((exes[pid.value], buf.value))
        return True

    user32.EnumWindows(each, 0)
    return out


def meeting_in_title(title: str, title_pats: list[str]) -> str:
    t = title.lower()
    for p in title_pats:
        if p and p in t:
            for key, name in TITLE_NAMES:
                if key in p:
                    return name
            return title.split(" - ")[0].split(" | ")[0][:30] or "Meeting"
    return ""


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


def find_meeting(users: list[str], pats: list[str], titles=None,
                 title_pats: list[str] | None = None) -> str:
    """The friendly name of the first meeting app holding the mic, or ""."""
    for u in users:
        low = u.lower()
        if any(p in low for p in pats):
            return friendly(u)
    # A browser holding the mic: is one of its windows a meeting?
    browsers = {u.lower().replace("/", "\\").split("\\")[-1] for u in users
                if any(b in u.lower() for b in BROWSERS)}
    if browsers and titles and title_pats:
        for exe, title in titles():
            if exe in browsers:
                name = meeting_in_title(title, title_pats)
                if name:
                    return name
    return ""


def _short(users: list[str]) -> str:
    return ", ".join(sorted({u.replace("/", "\\").split("\\")[-1] for u in users})) or "nothing"


class MeetingWatcher:
    """
    Polls every couple of seconds. `app` is the meeting app holding the mic
    now; `ended` is set when it lets go (until something acknowledges it).
    """

    def __init__(self, cfg, reader=mic_users, interval: float = 2.0, titles=window_titles):
        self.cfg = cfg
        self.reader = reader
        self.titles = titles
        self.users_seen = None
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
                users = self.reader()
                # Say which apps hold the mic whenever that changes: it's what
                # to look at when a meeting isn't noticed.
                short = _short(users)
                if short != self.users_seen:
                    log.info("microphone in use by: %s", short)
                    self.users_seen = short
                title_pats = [p.strip().lower() for p in
                              str(getattr(self.cfg, "meeting_titles", DEFAULT_TITLES)).split(",")
                              if p.strip()]
                app = find_meeting(users,
                                   patterns(self.cfg.meeting_apps, self.cfg.meeting_browsers),
                                   self.titles, title_pats)
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
