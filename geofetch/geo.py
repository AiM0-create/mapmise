"""Small deterministic geo helpers with network lookups (cached by the caller)."""

from __future__ import annotations

import httpx

UA = {"User-Agent": "geofetch-prototype (open geospatial data acquisition; contact via repository)"}


def country_iso3(lon: float, lat: float) -> str | None:
    """ISO3 of the country at a point, from the OSM country relation's ISO3166-1:alpha3 tag via Nominatim."""
    r = httpx.get("https://nominatim.openstreetmap.org/reverse",
                  params={"lon": lon, "lat": lat, "format": "json", "zoom": 3, "extratags": 1}, headers=UA, timeout=30)
    r.raise_for_status()
    d = r.json()
    return (d.get("extratags") or {}).get("ISO3166-1:alpha3") or None
