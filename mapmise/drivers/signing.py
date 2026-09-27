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


# ---------------------------------------------------------------- NASA Earthdata (protected archives)
#
# Protected NASA files answer an authenticated request with a redirect to a short-lived signed storage URL.
# The user's token is sent only to NASA Earthdata hosts; httpx drops the Authorization header when a redirect
# leaves the origin, so the storage host never sees it. GDAL then reads the signed URL, which carries no token.
# The signed URL is used in memory only: plans, catalogues and reports record the original NASA address.

_EARTHDATA_SUFFIXES = (".earthdatacloud.nasa.gov", ".earthdata.nasa.gov")
_ed_cache: dict[str, tuple[str, int | None, float]] = {}
_ed_lock = threading.Lock()
_ED_TTL = 45 * 60  # signed URLs last about an hour


def needs_earthdata(href: str) -> bool:
    u = urlparse(href)
    return u.scheme == "https" and u.hostname is not None and u.hostname.endswith(_EARTHDATA_SUFFIXES) and "protected" in u.path


def earthdata_resolve(href: str, client: httpx.Client | None = None) -> tuple[str, int | None]:
    """(signed URL, file size) for a protected NASA file. Raises EarthdataLoginRequired without a token."""
    from mapmise.auth import EarthdataLoginRequired, token
    with _ed_lock:
        hit = _ed_cache.get(href)
        if hit and time.time() - hit[2] < _ED_TTL:
            return hit[0], hit[1]
    tok = token()
    if not tok:
        raise EarthdataLoginRequired()
    own = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        r = client.get(href, headers={"Authorization": f"Bearer {tok}", "Range": "bytes=0-0"})
    finally:
        if own:
            client.close()
    if r.status_code in (401, 403) or "urs.earthdata.nasa.gov" in str(r.url.host):
        raise PermissionError("NASA Earthdata did not accept your token (expired, revoked, or the dataset's terms not yet "
                              "accepted in your Earthdata profile). Generate a new token at urs.earthdata.nasa.gov.")
    r.raise_for_status()
    size = None
    cr = r.headers.get("content-range", "")
    if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
        size = int(cr.rsplit("/", 1)[1])
    final = str(r.url)
    with _ed_lock:
        _ed_cache[href] = (final, size, time.time())
    return final, size


_URL_QUERY = __import__("re").compile(r"(https?://[^\s'\"?]+)\?[^\s'\"]*")


def redact(text: str) -> str:
    """Remove query strings from any URL in a message: signed links carry temporary credentials (and, for NASA,
    the user's Earthdata username), so errors are recorded and shown with the address only."""
    return _URL_QUERY.sub(r"\1?…", text)


_pc_sign = sign


def sign(href: str) -> str:  # noqa: F811 — extends the Planetary Computer signer above
    """A URL that can be read right now: Planetary Computer blobs get a SAS token; protected NASA files resolve
    to their signed storage URL; everything else is returned unchanged."""
    if needs_earthdata(href):
        return earthdata_resolve(href)[0]
    return _pc_sign(href)
