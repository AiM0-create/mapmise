"""Process-level helpers for the desktop app: one running copy per user, and a clean exit on Windows.

Standard library only, so the desktop launcher can use them before anything heavy is imported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_held = None  # the lock/mutex handle, kept for the life of the process


def claim_single_instance(lock_dir: Path) -> bool:
    """True if this is the only running copy of the app for this user; False if another copy holds the claim
    (even one that is still starting). The claim is released automatically when the process ends."""
    global _held
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = k32.CreateMutexW(None, False, "Local\\Mapmise-desktop-app")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _held = handle
        return True
    import fcntl
    lock_dir.mkdir(parents=True, exist_ok=True)
    fh = open(lock_dir / "app.lock", "w")  # noqa: SIM115 — held open for the life of the process
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    _held = fh
    return True


def hard_exit(code: int = 0) -> None:
    """Flush output and end the process. On Windows the process is terminated directly: a library's unload
    routine can deadlock at interpreter shutdown after network reads, which keeps the process alive forever."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.flush()
        except Exception:  # noqa: BLE001
            pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess(k32.GetCurrentProcess(), code or 0)
    os._exit(code or 0)
