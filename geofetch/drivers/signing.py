"""Planetary Computer SAS signing: blob hrefs need a per-container token that expires (~1 day).

One token per (storage account, container), fetched once — a lock ensures parallel workers wait for
the first request instead of all asking at once — and cached until shortly before expiry. The public
token service rate-limits (HTTP 429); requests back off and retry, honouring Retry-After.
Optionally set PC_SDK_SUBSCRIPTION_KEY for higher limits (free key from Planetary Computer).
"""

from __future__ import annotations

import calendar
import os
import threading
import time
from urllib.parse import urlparse

import httpx

_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
_cache: dict[tuple[str, str], tuple[str, float]] = {}
_locks: dict[tuple[str, str], threading.Lock] = {}
_locks_guard = threading.Lock()


def is_pc_blob(href: str) -> bool:
    return ".blob.core.windows.net" in href


def _token(account: str, container: str, attempts: int = 6) -> tuple[str, float]:
    headers = {}
    if os.environ.get("PC_SDK_SUBSCRIPTION_KEY"):
        headers["Ocp-Apim-Subscription-Key"] = os.environ["PC_SDK_SUBSCRIPTION_KEY"]
    delay = 2.0
    for i in range(attempts):
        r = httpx.get(_SAS.format(account=account, container=container), headers=headers, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            if i == attempts - 1:
                r.raise_for_status()
            time.sleep(float(r.headers.get("retry-after") or delay))
            delay = min(delay * 2, 60)
            continue
        r.raise_for_status()
        d = r.json()
        return d["token"], calendar.timegm(time.strptime(d["msft:expiry"], "%Y-%m-%dT%H:%M:%SZ"))
    raise RuntimeError("unreachable")


def sign(href: str) -> str:
    """Return href with a valid SAS query string appended (unchanged if not a Planetary Computer blob)."""
    if not is_pc_blob(href) or "?" in href:
        return href
    u = urlparse(href)
    key = (u.netloc.split(".")[0], u.path.lstrip("/").split("/")[0])
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        tok, exp = _cache.get(key, (None, 0.0))
        if tok is None or time.time() > exp - 300:
            tok, exp = _token(*key)
            _cache[key] = (tok, exp)
    return f"{href}?{tok}"
