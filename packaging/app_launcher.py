"""Entry point of the desktop app (Mapmise.exe / Mapmise.app / the AppImage).

Double-clicked, it starts the local app and opens it in the browser, or reopens the one already running.
There is no terminal window, so output goes to a log file next to the library.
"""
import sys

from mapmise.geo import user_dir


def main() -> int:
    if sys.stdout is None or sys.stderr is None:  # no console (windowed app)
        log_dir = user_dir("data")
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "mapmise.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — lives as long as the app
        sys.stdout = sys.stderr = log
    from mapmise.cli import main as cli_main
    # macOS passes -psn_… when launched from Finder; any other argument is a normal command
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    return cli_main(args or ["gui"])


if __name__ == "__main__":
    sys.exit(main())
