"""
window.py — Scribe's own app window.

The interface is a web page served by this process. On Windows it opens in
a native window (pywebview on Microsoft's WebView2, the engine built into
Windows 10/11), so the window, its taskbar button and a pinned shortcut all
belong to Scribe.exe and show Scribe's icon. If WebView2 isn't available,
server.py falls back to an Edge app window.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

from .config import APP_ROOT
from .log import get

log = get("scribe.window")

_window = None          # the open window, if any
_shown = threading.Event()


_unblocked = False
last_error = ""


def unblock() -> int:
    """
    Clear Windows' "downloaded from the internet" mark from Scribe's own .NET
    files, as the Unblock checkbox in a file's Properties does.

    Files extracted from a downloaded zip carry that mark unless the zip was
    unblocked first, and .NET Framework refuses to load marked libraries: the
    window library then fails with "Failed to resolve
    Python.Runtime.Loader.Initialize" and Scribe falls back to an Edge window.
    Only the window library's own folders are touched.
    """
    global _unblocked
    if _unblocked or sys.platform != "win32" or os.environ.get("SCRIBE_NO_UNBLOCK"):
        return 0
    _unblocked = True
    import importlib.util
    roots = []
    base = getattr(sys, "_MEIPASS", "")
    for pkg in ("pythonnet", "webview", "clr_loader"):
        if base and os.path.isdir(os.path.join(base, pkg)):
            roots.append(os.path.join(base, pkg))
            continue
        try:
            spec = importlib.util.find_spec(pkg)
            roots += list(spec.submodule_search_locations or []) if spec else []
        except Exception:
            pass
    cleared = 0
    for root in roots:
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if name.lower().endswith((".dll", ".exe")):
                    try:
                        os.remove(os.path.join(folder, name) + ":Zone.Identifier")
                        cleared += 1
                    except OSError:
                        pass   # not marked (the usual case)
    if cleared:
        log.info("unblocked %d downloaded library file(s) for the app window", cleared)
    return cleared


def available() -> tuple[bool, str]:
    """Can a native window open here? (ok, detail) — for `doctor` too."""
    global last_error
    if sys.platform != "win32":
        return False, "native window is Windows-only"
    unblock()
    try:
        import webview  # noqa: F401
        from webview.platforms import winforms
    except Exception as e:
        last_error = f"window library didn't load: {e.__class__.__name__}: {e}"
        return False, last_error
    if getattr(winforms, "renderer", "") != "edgechromium":
        last_error = "Microsoft Edge WebView2 Runtime not found"
        return False, last_error
    return True, "WebView2"


def _background() -> str:
    """Match Windows' light/dark setting so the window doesn't flash white."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            light, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
        return "#FFFFFF" if light else "#1b1b1d"
    except Exception:
        return "#FFFFFF"


def run(url: str, on_closing=None, widget=None) -> bool:
    """
    Show Scribe's window and block until it closes. Must run on the main
    thread. Returns False, without blocking, if no native window can open.
    `widget` (a WidgetUI) also sets up the floating window, hidden until needed.
    """
    global _window
    ok, detail = available()
    if not ok:
        log.info("native window unavailable (%s); using a browser window", detail)
        return False
    import webview

    try:
        try:
            # The library blocks downloads unless told otherwise; Scribe's
            # Download buttons need them (each opens a normal Save dialog).
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        _window = webview.create_window(
            "Scribe", url, width=1240, height=840, min_size=(400, 560),
            text_select=True, background_color=_background())
        if on_closing:
            _window.events.closing += on_closing
        _window.events.shown += _shown.set
        if widget is not None:
            widget.create(_window)
        # A hidden page gets slowed to one timer tick a minute after a while;
        # the floating window's page is hidden most of the time and must stay
        # current, so turn that off for Scribe's windows.
        flags = ("--disable-background-timer-throttling --disable-renderer-backgrounding "
                 "--disable-features=ElasticOverscroll")   # (pywebview's own flag, kept)
        extra = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
        if flags not in extra:
            os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (extra + " " + flags).strip()
        log.info("opening the app window")
        # private_mode=False keeps the page's own settings (theme, last view)
        # between runs, stored next to the app.
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=str(APP_ROOT / "webview_data"))
    except Exception:
        log.exception("app window failed")
        if not _shown.is_set():
            _window = None
            return False
    _window = None
    log.info("app window closed")
    return True


def _main_in_front() -> bool:
    """Is Scribe's main window the one you're using right now?"""
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value == "Scribe"


class WidgetUI:
    """
    The floating window: small, frameless, always on top, never takes focus
    from your meeting, and stays where you drag it. All window calls go
    through one worker thread, so nothing that asks it to show or hide ever
    waits on the window.
    """

    TITLE = "Scribe recorder"

    def __init__(self, url: str, position: str = "", on_moved=None):
        self.url = url
        self.pos = _parse_pos(position)
        self.on_moved = on_moved
        self.win = None
        self.ready = threading.Event()
        self.closing_app = False
        self.q: "queue.Queue" = queue.Queue()
        self.size = (0, 0)
        self.visible = False
        self.last = ""          # what the last show/hide did, for status and the log

    # -- set-up (main thread, before webview.start) --------------------------
    def create(self, main) -> None:
        import webview
        try:
            self.win = webview.create_window(
                self.TITLE, self.url, width=184, height=164, hidden=True, frameless=True,
                easy_drag=False, on_top=True, focus=False, resizable=False,
                background_color=_background(), text_select=False)
        except Exception:
            log.exception("couldn't create the floating window")
            self.win = None
            return
        self.win.events.shown += self._shown
        self.win.events.moved += self._moved
        self.win.events.closing += self._closing
        main.events.closed += self._main_closed
        threading.Thread(target=self._work, daemon=True, name="widget-ui").start()

    def _shown(self) -> None:
        self.ready.set()
        try:   # rounded corners on Windows 11 (DWMWA_WINDOW_CORNER_PREFERENCE = round)
            import ctypes
            hwnd = self.win.native.Handle.ToInt32()
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception:
            pass

    def _moved(self, x, y) -> None:
        if self.visible and self.on_moved:
            self.pos = (int(x), int(y))
            self.on_moved(int(x), int(y))

    def _closing(self):
        # Alt+F4 on the floating window just hides it; only the app closing closes it.
        if self.closing_app:
            return True
        self.q.put(("hide", None))
        return False

    def _main_closed(self) -> None:
        self.closing_app = True
        try:
            if self.win is not None:
                self.win.destroy()
        except Exception:
            pass

    # -- used by WidgetController ---------------------------------------------
    def available(self) -> bool:
        return self.win is not None and self.ready.is_set() and not self.closing_app

    def main_in_front(self) -> bool:
        return _main_in_front()

    def show(self, mode: str, size: tuple[int, int]) -> None:
        self.q.put(("show", (mode, size)))

    def hide(self) -> None:
        self.q.put(("hide", None))

    # -- worker -------------------------------------------------------------
    def _work(self) -> None:
        while True:
            op, arg = self.q.get()
            if self.closing_app:
                return
            done = threading.Event()

            def run(op=op, arg=arg):
                try:
                    if op == "show":
                        self._do_show(*arg)
                    else:
                        self.visible = False
                        self.win.hide()
                        self.last = "hidden"
                except Exception as e:
                    self.last = f"{op} failed: {e.__class__.__name__}: {e}"
                    log.warning("floating window %s failed: %s", op, e, exc_info=True)
                finally:
                    done.set()

            started = time.monotonic()
            threading.Thread(target=run, daemon=True).start()
            if not done.wait(8):
                log.warning("floating window: %s is taking over 8 s; the window isn't responding", op)
            else:
                log.info("floating window: %s %s (%.2f s)", op,
                         arg[0] if op == "show" else "", time.monotonic() - started)

    def _do_show(self, mode: str, size: tuple[int, int]) -> None:
        import webview
        w, h = size
        problems = []
        for step in ("resize", "move", "show"):   # each on its own: one failing mustn't stop the rest
            try:
                if step == "resize" and (w, h) != self.size:
                    self.win.resize(w, h)
                    self.size = (w, h)
                elif step == "move":
                    x, y = self._place(w, h, webview)
                    self.win.move(x, y)
                elif step == "show":
                    self.win.show()
            except Exception as e:
                problems.append(f"{step}: {e}")
        if problems:
            log.warning("floating window: %s", "; ".join(problems))
        self.visible = True
        # Make sure Windows really shows it: if the window still isn't on
        # screen, show it directly, on top and without taking focus from the
        # meeting app.
        hwnd = self.hwnd()
        if hwnd and not _user32().IsWindowVisible(hwnd):
            _user32().ShowWindow(hwnd, 4)            # SW_SHOWNOACTIVATE
            _user32().SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            self.last = f"shown {mode} (directly); visible={bool(_user32().IsWindowVisible(hwnd))}"
        else:
            self.last = f"shown {mode}; visible={bool(hwnd and _user32().IsWindowVisible(hwnd))}"
        # Tell the page which face to draw, without waiting for it: a page
        # that's been hidden a while can be slow to answer, and the window
        # must appear regardless. (The page also checks for itself.)
        threading.Thread(target=self._tell_page, args=(mode,), daemon=True).start()

    def hwnd(self) -> int:
        """The floating window's Windows handle (0 if it isn't there)."""
        try:
            return int(self.win.native.Handle.ToInt64())
        except Exception:
            try:
                return int(_user32().FindWindowW(None, self.TITLE) or 0)
            except Exception:
                return 0

    def native_state(self) -> dict:
        """What Windows itself says about the floating window."""
        try:
            h = self.hwnd()
            return {"found": bool(h), "visible": bool(h and _user32().IsWindowVisible(h)),
                    "last": self.last}
        except Exception as e:
            return {"found": False, "visible": False, "last": self.last, "error": str(e)}

    def _tell_page(self, mode: str) -> None:
        try:
            self.win.evaluate_js(f"window.scribeMode && window.scribeMode({mode!r})")
        except Exception:
            pass

    def _place(self, w: int, h: int, webview) -> tuple[int, int]:
        """Where you last left it, kept on screen; bottom right the first time."""
        try:
            scr = webview.screens[0]
            sw, sh = int(scr.width), int(scr.height)
        except Exception:
            sw, sh = 1920, 1080
        if self.pos:
            x, y = self.pos
        else:
            x, y = sw - w - 24, sh - h - 88   # clear of the taskbar
        return max(0, min(x, sw - w)), max(0, min(y, sh - h))


_u32 = None


def _user32():
    """
    Scribe's own handle on user32.dll. A private one: the window library
    calls the shared ctypes.windll.user32 with loosely typed arguments, and
    declaring types on the shared one broke its calls.
    """
    global _u32
    if _u32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32")
        u.FindWindowW.restype = wintypes.HWND
        u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        _u32 = u
    return _u32


def _parse_pos(text: str):
    try:
        x, y = (int(v) for v in str(text).split(","))
        return (x, y)
    except Exception:
        return None


def bring_to_front() -> bool:
    """
    Show and focus the open window (a second launch of Scribe calls this).
    Returns at once: the window acts on it when its own thread is free (it
    may still be loading), so the caller never waits on the window.
    """
    w = _window
    if w is None:
        return False

    def act():
        _shown.wait(60)
        try:
            w.restore()
            w.show()
            w.on_top = True   # the dependable way to come to the front on Windows
            w.on_top = False
        except Exception as e:
            log.warning("couldn't bring the window to the front: %s", e)

    threading.Thread(target=act, daemon=True).start()
    return True
