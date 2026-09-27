"""Small deterministic geo helpers with network lookups (cached by the caller)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import httpx

UA = {"User-Agent": "mapmise-prototype (open geospatial data acquisition; contact via repository)"}


def country_iso3(lon: float, lat: float) -> str | None:
    """ISO3 of the country at a point, from the OSM country relation's ISO3166-1:alpha3 tag via Nominatim."""
    r = httpx.get("https://nominatim.openstreetmap.org/reverse",
                  params={"lon": lon, "lat": lat, "format": "json", "zoom": 3, "extratags": 1}, headers=UA, timeout=30)
    r.raise_for_status()
    d = r.json()
    return (d.get("extratags") or {}).get("ISO3166-1:alpha3") or None


def user_dir(kind: str) -> Path:
    """Where this operating system keeps an application's per-user data ("data") or disposable cache ("cache")."""
    home = Path.home()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "mapmise"
        return base / ("Cache" if kind == "cache" else "Data")
    if sys.platform == "darwin":
        return home / "Library" / ("Caches" if kind == "cache" else "Application Support") / "mapmise"
    xdg = os.environ.get("XDG_CACHE_HOME" if kind == "cache" else "XDG_DATA_HOME")
    return Path(xdg or home / (".cache" if kind == "cache" else ".local/share")) / "mapmise"


def safe_name(s: str) -> str:
    """A file name valid on every operating system (Windows forbids <>:"/\\|?* and control characters)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).rstrip(" .")


def shared_cache() -> Path:
    """User-level cache for whole files that are identical across projects (e.g. a country population raster).
    MAPMISE_CACHE overrides; otherwise the operating system's cache folder for mapmise."""
    return Path(os.environ.get("MAPMISE_CACHE") or user_dir("cache")) / "files"
