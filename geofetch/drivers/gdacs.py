"""GDACS driver: resolve "the flood in X" to real dated events near the AOI."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from geofetch.geo import UA

_API = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


@dataclass
class Event:
    id: str
    type: str
    name: str
    start: str  # ISO date
    end: str
    alert: str
    lon: float
    lat: float
    distance_deg: float  # from AOI centre (rough, for ranking only)


def events(event_code: str, bbox: list[float], start: str, end: str, radius_deg: float = 3.0) -> list[Event]:
    r = httpx.get(_API, params={"eventlist": event_code, "fromDate": start, "toDate": end, "alertlevel": "Green;Orange;Red"},
                  headers=UA, timeout=60)
    r.raise_for_status()
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    out = []
    for f in r.json().get("features", []):
        p, g = f["properties"], f["geometry"]
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        d = ((lon - cx) ** 2 + (lat - cy) ** 2) ** 0.5
        if d <= radius_deg:
            out.append(Event(str(p.get("eventid")), event_code, p.get("name", ""), p.get("fromdate", "")[:10], p.get("todate", "")[:10],
                             p.get("alertlevel", ""), lon, lat, round(d, 2)))
    return sorted(out, key=lambda e: e.distance_deg)
