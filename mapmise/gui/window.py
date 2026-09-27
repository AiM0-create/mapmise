"""The app's own window: the local GUI shown in the operating system's web view (WebView2 on Windows,
WKWebView on macOS, WebKitGTK or Qt on Linux) via pywebview, instead of a browser tab.

Optional: if pywebview or a Linux GUI backend is missing, `open_window` raises WindowUnavailable and the
caller falls back to the browser. The window must run on the main thread (macOS requires it); the HTTP
server runs beside it in a background thread.
"""

from __future__ import annotations

import sys
from typing import Callable

TITLE = "Mapmise"
_window = None


class WindowUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — any import problem means no window
        return False


def open_window(url: str, running_jobs: Callable[[], int], on_closed: Callable[[], None],
                smoke: Callable[[object], None] | None = None) -> None:
    """Show the app in its own window and block until the window is closed."""
    global _window
    try:
        import webview
    except Exception as e:  # noqa: BLE001
        raise WindowUnavailable(f"pywebview is not installed ({e})") from e

    window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(960, 640),
                                   background_color="#F2F2F7", text_select=True)
    _window = window

    def closing():
        n = running_jobs()
        if n and not window.create_confirmation_dialog(
                "Quit Mapmise?", f"{n} acquisition job(s) are still running. Files finished so far are kept, "
                                 "and you can resume from the project later."):
            return False  # keep the window open
        return True

    window.events.closing += closing
    window.events.closed += lambda: on_closed()
    gui = "edgechromium" if sys.platform == "win32" else None  # never fall back to the legacy IE engine
    try:
        webview.start(smoke, window, gui=gui, private_mode=False) if smoke else webview.start(gui=gui, private_mode=False)
    except Exception as e:  # noqa: BLE001 — no GUI backend (typically a Linux system without GTK/Qt web view)
        raise WindowUnavailable(str(e)) from e
    finally:
        _window = None


def bring_to_front() -> bool:
    """Restore and focus the running window (used when the app is started a second time)."""
    if _window is None:
        return False
    try:
        _window.restore()
        _window.show()
        return True
    except Exception:  # noqa: BLE001
        return False


def close() -> None:
    """Close the window (which ends the app)."""
    if _window is not None:
        try:
            _window.destroy()
        except Exception:  # noqa: BLE001
            pass
