"""Planetary Computer SAS signing: blob hrefs need a per-container token that expires (~1 day).

Tokens are fetched from the public SAS API and cached per (account, container) until shortly
before expiry, so long runs re-sign transparently. No account is needed for public collections.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
_cache: dict[tuple[str, str], tuple[str, float]] = {}


def is_pc_blob(href: str) -> bool:
    return ".blob.core.windows.net" in href


def sign(href: str) -> str:
    """Return href with a valid SAS query string appended (unchanged if not a Planetary Computer blob)."""
    if not is_pc_blob(href) or "?" in href:
        return href
    u = urlparse(href)
    account = u.netloc.split(".")[0]
    container = u.path.lstrip("/").split("/")[0]
    key = (account, container)
    tok, exp = _cache.get(key, (None, 0.0))
    if tok is None or time.time() > exp - 300:
        r = httpx.get(_SAS.format(account=account, container=container), timeout=30)
        r.raise_for_status()
        d = r.json()
        tok = d["token"]
        exp = time.mktime(time.strptime(d["msft:expiry"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        _cache[key] = (tok, exp)
    return f"{href}?{tok}"
