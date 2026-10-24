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

import os
import sys
import threading
import time

from .log import get

log = get("minutara.meetings")

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


def registry_mic_users() -> list[str]:
    """
    Apps Windows' privacy settings record as using the microphone right now
    (the same record behind the taskbar's mic icon). Scanned all the way
    down, as Windows nests some entries (NonPackaged apps, and more on
    some builds).
    """
    if sys.platform != "win32":
        return []
    import winreg

    users: list[str] = []

    def scan(key, depth=0):
        if depth > 4:
            return
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(key, name) as sub:
                    try:
                        stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                        start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                        if stop == 0 and start > 0:
                            users.append(name.replace("#", "\\"))
                    except OSError:
                        pass
                    scan(sub, depth + 1)
            except OSError:
                continue

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, MIC_KEY) as root:
            scan(root)
    except OSError:
        pass
    return users


_com = threading.local()


def audio_session_users() -> list[str]:
    """
    Programs recording from any microphone right now, straight from Windows'
    audio system: every active capture session on every active microphone,
    and the program behind it. Real time, and it doesn't depend on the
    privacy record being kept up to date. Minutara's own recording is left out.
    """
    if sys.platform != "win32":
        return []
    import comtypes
    from pycaw.api.audiopolicy import IAudioSessionControl2, IAudioSessionManager2
    from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    from pycaw.constants import CLSID_MMDeviceEnumerator

    if not getattr(_com, "ready", False):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass   # this thread already has COM set up
        _com.ready = True

    me = os.getpid()
    out: list[str] = []
    devices = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                        comtypes.CLSCTX_INPROC_SERVER)
    mics = devices.EnumAudioEndpoints(1, 1)   # capture endpoints, active only
    for i in range(mics.GetCount()):
        try:
            mic = mics.Item(i)
            mgr = mic.Activate(IAudioSessionManager2._iid_, comtypes.CLSCTX_ALL, None)
            sessions = mgr.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                if ctl.GetState() != 1:          # AudioSessionStateActive
                    continue
                pid = ctl.GetProcessId()
                if pid in (0, me):
                    continue
                exe = _exe_of(pid)
                if exe and exe not in out:
                    out.append(exe)
        except Exception as e:   # one odd device mustn't hide the others
            log.debug("audio sessions on microphone %d: %s", i, e)
    return out


_source_errors: dict[str, str] = {}


def mic_users() -> list[str]:
    """Everything known to be using the microphone: privacy record + audio sessions."""
    users: list[str] = []
    for name, source in (("registry", registry_mic_users), ("audio sessions", audio_session_users)):
        try:
            for u in source():
                if u not in users:
                    users.append(u)
            _source_errors.pop(name, None)
        except Exception as e:
            if _source_errors.get(name) != str(e):
                log.warning("meeting detection: couldn't read %s: %s", name, e)
            _source_errors[name] = str(e)
    return users


def _exe_of(pid: int) -> str:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32")   # a private handle: typing the shared one affects other code
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
    user32 = ctypes.WinDLL("user32")
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
    # Web-based apps (new Teams among them) record through an embedded
    # browser, msedgewebview2.exe, whose windows belong to the app: then any
    # window titled like a meeting counts.
    webview = any("msedgewebview2" in u.lower() for u in users)
    if (browsers or webview) and titles and title_pats:
        for exe, title in titles():
            if webview or exe in browsers:
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
                    "preview": time.monotonic() < self.preview_until,
                    "mic_users": self.users_seen or "", "error": self.error}
