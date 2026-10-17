"""
window.py — Scribe's own app window.

The interface is a web page served by this process. On Windows it opens in
a native window (pywebview on Microsoft's WebView2, the engine built into
Windows 10/11), so the window, its taskbar button and a pinned shortcut all
belong to Scribe.exe and show Scribe's icon. If WebView2 isn't available,
server.py falls back to an Edge app window.
"""

from __future__ import annotations

import queue
import sys
import threading

from .config import APP_ROOT
from .log import get

log = get("scribe.window")

_window = None          # the open window, if any
_shown = threading.Event()


def available() -> tuple[bool, str]:
    """Can a native window open here? (ok, detail) — for `doctor` too."""
    if sys.platform != "win32":
        return False, "native window is Windows-only"
    try:
        import webview  # noqa: F401
        from webview.platforms import winforms
    except Exception as e:
        return False, f"window library didn't load: {e.__class__.__name__}: {e}"
    if getattr(winforms, "renderer", "") != "edgechromium":
        return False, "Microsoft Edge WebView2 Runtime not found"
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
        _window = webview.create_window(
            "Scribe", url, width=1240, height=840, min_size=(400, 560),
            text_select=True, background_color=_background())
        if on_closing:
            _window.events.closing += on_closing
        _window.events.shown += _shown.set
        if widget is not None:
            widget.create(_window)
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
            try:
                if op == "show":
                    self._do_show(*arg)
                else:
                    self.visible = False
                    self.win.hide()
            except Exception as e:
                log.warning("floating window %s failed: %s", op, e)

    def _do_show(self, mode: str, size: tuple[int, int]) -> None:
        import webview
        w, h = size
        try:
            self.win.evaluate_js(f"window.scribeMode && window.scribeMode({mode!r})")
        except Exception:
            pass
        if (w, h) != self.size:
            self.win.resize(w, h)
            self.size = (w, h)
        x, y = self._place(w, h, webview)
        self.win.move(x, y)
        self.visible = True
        self.win.show()

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
