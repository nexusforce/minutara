"""
window.py — Scribe's own app window.

The interface is a web page served by this process. On Windows it opens in
a native window (pywebview on Microsoft's WebView2, the engine built into
Windows 10/11), so the window, its taskbar button and a pinned shortcut all
belong to Scribe.exe and show Scribe's icon. If WebView2 isn't available,
server.py falls back to an Edge app window.
"""

from __future__ import annotations

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


def run(url: str, on_closing=None) -> bool:
    """
    Show Scribe's window and block until it closes. Must run on the main
    thread. Returns False, without blocking, if no native window can open.
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
