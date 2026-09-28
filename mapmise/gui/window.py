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
    """True if this system can show the app's own window: pywebview is installed and, on Linux, a web view
    backend (WebKitGTK through PyGObject, or Qt) is importable. Windows and macOS always have one."""
    try:
        import webview  # noqa: F401
    except Exception:  # noqa: BLE001 — any import problem means no window
        return False
    if sys.platform.startswith("linux"):
        try:
            import gi
            for v in ("4.1", "4.0"):
                try:
                    gi.require_version("WebKit2", v)
                    from gi.repository import WebKit2  # noqa: F401
                    return True
                except (ValueError, ImportError):
                    continue
        except ImportError:
            pass
        try:
            import qtpy  # noqa: F401
            return True
        except ImportError:
            return False
    return True


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


def _splash_html() -> str:
    """Shown the moment the app starts, while the data engine loads behind it."""
    import base64
    from pathlib import Path
    logo = Path(__file__).parent / "static" / "logo.png"
    img = f'<img src="data:image/png;base64,{base64.b64encode(logo.read_bytes()).decode()}" alt="">' if logo.exists() else ""
    return """<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; }
body { margin: 0; height: 100vh; display: grid; place-items: center; background: #f2f2f7; color: #1d1d1f; }
@media (prefers-color-scheme: dark) { body { background: #000; color: #f5f5f7; } p { color: rgba(235,235,245,.64) !important; } }
main { text-align: center; } img { width: 96px; }
h1 { font-size: 22px; letter-spacing: -.02em; margin: 18px 0 6px; font-weight: 700; }
p { margin: 0; color: rgba(60,60,67,.64); font-size: 13px; max-width: 360px; }
.spin { width: 22px; height: 22px; margin: 20px auto 14px; border-radius: 50%; border: 2.5px solid rgba(120,120,128,.25);
  border-top-color: #0a84ff; animation: s .8s linear infinite; } @keyframes s { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation-duration: 2.4s; } }
</style></head><body><main>""" + img + """<h1>Starting Mapmise…</h1><div class="spin"></div>
<p id="note">Loading the data engine.</p></main>
<script>setTimeout(() => { document.getElementById("note").textContent =
  "The first start can take up to a minute while your computer checks the new app. Later starts are faster."; }, 6000);</script>
</body></html>"""


def run_with_splash(start_app: Callable[[], str], running_jobs: Callable[[], int], on_closed: Callable[[], None]) -> None:
    """Open the window immediately with a start screen, then load the app into it once start_app() — which
    imports the engine and starts the local server — returns the app's URL. Blocks until the window closes."""
    global _window
    import webview
    window = webview.create_window(TITLE, html=_splash_html(), width=1280, height=860, min_size=(960, 640),
                                   background_color="#F2F2F7", text_select=True)
    _window = window

    def closing():
        n = running_jobs()
        if n and not window.create_confirmation_dialog(
                "Quit Mapmise?", f"{n} acquisition job(s) are still running. Files finished so far are kept, "
                                 "and you can resume from the project later."):
            return False
        return True

    def boot(w):
        try:
            w.load_url(start_app())
        except Exception as e:  # noqa: BLE001 — show the problem in the window instead of a silent failure
            import html
            import traceback
            traceback.print_exc()
            w.load_html(f"<body style='font-family:system-ui;padding:40px'><h2>Mapmise could not start</h2>"
                        f"<p>{html.escape(type(e).__name__ + ': ' + str(e))}</p><p>Details are in mapmise.log in your "
                        "Mapmise data folder. Please report this at github.com/AiM0-create/mapmise/issues.</p></body>")

    window.events.closing += closing
    window.events.closed += lambda: on_closed()
    gui = "edgechromium" if sys.platform == "win32" else None
    try:
        webview.start(boot, window, gui=gui, private_mode=False)
    finally:
        _window = None
