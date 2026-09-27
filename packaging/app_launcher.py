"""Entry point of the desktop app (Mapmise.exe / Mapmise.app / the AppImage).

Double-clicked, it opens the app's window at once with a start screen and loads the data engine behind it.
Only one copy runs per user: a second double-click — even while the first is still starting — brings the
existing window to the front and exits. There is no terminal window, so output goes to mapmise.log.
Any arguments are ordinary `mapmise` commands.
"""
import sys
import time

from mapmise.geo import user_dir


def _log_to_file() -> None:
    if sys.stdout is None or sys.stderr is None:  # no console (windowed app)
        d = user_dir("data")
        d.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(d / "mapmise.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115


def _bring_existing_forward() -> None:
    """Another copy holds the claim: wait for it to finish starting (up to two minutes), then show its window."""
    import json
    import urllib.request
    rec_file = user_dir("data") / "running.json"
    for _ in range(120):
        try:
            rec = json.loads(rec_file.read_text(encoding="utf-8"))
            req = urllib.request.Request(rec["url"] + "api/show", data=b"{}", method="POST",
                                         headers={"X-Mapmise-Token": rec["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=3):
                return
        except Exception:  # noqa: BLE001 — still starting
            time.sleep(1)


def main() -> None:
    _log_to_file()
    from mapmise.proc import claim_single_instance, hard_exit
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # macOS passes -psn_… when opened from Finder
    smoke = args == ["--smoke-test"]
    if args and not smoke:
        from mapmise.cli import run
        run(args)
        return
    if not smoke and not claim_single_instance(user_dir("data")):
        _bring_existing_forward()
        hard_exit(0)
    from mapmise.gui import window as win
    if not win.available():  # no web view on this system: the browser instead
        from mapmise.cli import run
        run(["gui", "--browser"])
        return
    result = {"code": 0}

    def start_app() -> str:
        from mapmise.gui import server  # the heavy import happens here, behind the start screen
        url = server.start_in_background()
        if smoke:
            import threading
            threading.Thread(target=_smoke, args=(url, result), daemon=True).start()
        return url

    def jobs() -> int:
        mod = sys.modules.get("mapmise.gui.server")
        return mod.running_jobs() if mod else 0

    win.run_with_splash(start_app, jobs, on_closed=lambda: None)
    mod = sys.modules.get("mapmise.gui.server")
    if mod:
        mod.stop_background()
    hard_exit(result["code"])


def _smoke(url: str, result: dict) -> None:
    """Release check of the desktop path: start screen → app loaded in the window → API answers → close."""
    from mapmise.gui import window as win
    ok = False
    for _ in range(90):
        time.sleep(1)
        w = win._window
        try:
            if w and w.evaluate_js("!!(window.MAPMISE_READY && window.MAPMISE_PROJECTS_LOADED)"):
                ok = True
                break
        except Exception:  # noqa: BLE001 — page still loading
            pass
    print(f"smoke test (desktop app): start screen → app ready={ok}", flush=True)
    result["code"] = 0 if ok else 1
    win.close()


if __name__ == "__main__":
    main()
