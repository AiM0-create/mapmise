"""Small deterministic geo helpers with network lookups (cached by the caller)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

UA = {"User-Agent": "geofetch-prototype (open geospatial data acquisition; contact via repository)"}


def country_iso3(lon: float, lat: float) -> str | None:
    """ISO3 of the country at a point, from the OSM country relation's ISO3166-1:alpha3 tag via Nominatim."""
    r = httpx.get("https://nominatim.openstreetmap.org/reverse",
                  params={"lon": lon, "lat": lat, "format": "json", "zoom": 3, "extratags": 1}, headers=UA, timeout=30)
    r.raise_for_status()
    d = r.json()
    return (d.get("extratags") or {}).get("ISO3166-1:alpha3") or None


def shared_cache() -> Path:
    """User-level cache for whole files that are identical across projects (e.g. a country population raster).
    GEOFETCH_CACHE overrides; otherwise $XDG_CACHE_HOME/geofetch or ~/.cache/geofetch."""
    base = os.environ.get("GEOFETCH_CACHE") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "geofetch")
    return Path(base) / "files"
