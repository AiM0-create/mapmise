"""Understand the WHERE and WHEN of an ask — deterministically, and always shown back to the user.

    "I heard a flood happened in Chitradurga in August 2026"
        → period 2026-08-01 … 2026-08-31, place candidates ["Chitradurga"]

Dates: ISO dates, "Month YYYY", "Month to Month YYYY", "YYYY to YYYY", "since YYYY",
"last N years/months", "YYYY monsoon" (June–September, South Asian convention), a bare month
(its most recent occurrence) and a bare year. Place: runs of capitalised words that are not
dates, keywords of the ask rules, or common lead words; geocoded with OpenStreetMap Nominatim
to a polygon. Anything wrong here is visible before approval and can be overridden with flags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from geofetch.aoi import area_km2
from geofetch.geo import UA

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9
_M = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_Y = r"((?:19|20)\d{2})"
_TO = r"\s*(?:to|until|till|through|thru|-|–|—)\s*"


@dataclass
class Period:
    start: date
    end: date
    text: str  # the words it came from
    event: date | None = None  # a single explicit date in the ask

    def describe(self) -> str:
        return f"{self.start} → {self.end}" + (f" (event {self.event})" if self.event else "") + f"  ← “{self.text}”"


def _month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _mnum(s: str) -> int:
    s = s.lower().rstrip(".")
    return MONTHS.get(s) or MONTHS[s[:3]]


def parse_period(text: str, today: date | None = None) -> Period | None:
    """First matching pattern wins; patterns are ordered from most to least specific."""
    today = today or date.today()
    t = text.lower()

    iso = re.findall(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b", t)
    if len(iso) >= 2:
        a, b = (date(int(y), int(m), int(d)) for y, m, d in iso[:2])
        return Period(min(a, b), max(a, b), f"{'-'.join(iso[0])} … {'-'.join(iso[1])}")
    if len(iso) == 1:
        d = date(*map(int, iso[0]))
        return Period(d - timedelta(days=30), d + timedelta(days=30), "-".join(iso[0]), event=d)

    if m := re.search(rf"\b{_M}\s+{_Y}{_TO}{_M}\s+{_Y}\b", t):
        return Period(date(int(m[2]), _mnum(m[1]), 1), _month_end(int(m[4]), _mnum(m[3])), m[0])
    if m := re.search(rf"\b{_M}{_TO}{_M},?\s+{_Y}\b", t):
        y = int(m[3])
        return Period(date(y, _mnum(m[1]), 1), _month_end(y, _mnum(m[2])), m[0])
    if m := re.search(rf"\b{_M},?\s+{_Y}\b", t):
        y, mo = int(m[2]), _mnum(m[1])
        return Period(date(y, mo, 1), _month_end(y, mo), m[0])
    if m := re.search(rf"\b(?:between\s+)?{_Y}(?:{_TO}|\s+and\s+){_Y}\b", t):
        a, b = sorted((int(m[1]), int(m[2])))
        return Period(date(a, 1, 1), min(date(b, 12, 31), today), m[0])
    if m := re.search(rf"\b(?:{_Y}\s+monsoon|monsoon\s+(?:of\s+)?{_Y})\b", t):
        y = int(m[1] or m[2])
        return Period(date(y, 6, 1), min(date(y, 9, 30), today), m[0])
    if m := re.search(rf"\bsince\s+{_Y}\b", t):
        return Period(date(int(m[1]), 1, 1), today, m[0])
    if m := re.search(r"\b(?:last|past|previous)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(years?|months?)\b", t):
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
        n = int(m[1]) if m[1].isdigit() else words[m[1]]
        days = n * (365 if m[2].startswith("year") else 30)
        return Period(today - timedelta(days=days), today, m[0])
    if m := re.search(r"\bmonsoon\b", t):
        y = today.year if today >= date(today.year, 6, 1) else today.year - 1
        return Period(date(y, 6, 1), min(date(y, 9, 30), today), m[0])
    if m := re.search(rf"\b{_M}\b", t):
        mo = _mnum(m[1])
        y = today.year if mo <= today.month else today.year - 1  # most recent occurrence
        return Period(date(y, mo, 1), min(_month_end(y, mo), today), m[0])
    if m := re.search(rf"\b{_Y}\b", t):
        y = int(m[1])
        return Period(date(y, 1, 1), min(date(y, 12, 31), today), m[0])
    return None


# words that start many asks but are never places
_LEAD = {
    "i", "we", "my", "our", "please", "help", "can", "could", "would", "show", "get", "give", "find", "fetch", "download", "map", "mapping",
    "assess", "analyse", "analyze", "analysis", "monitor", "monitoring", "detect", "study", "track", "estimate", "compute", "calculate",
    "compare", "need", "want", "what", "how", "where", "when", "which", "the", "a", "an", "in", "of", "for", "over", "is", "are", "was", "did",
    "flood", "floods", "drought", "urban", "slope", "road", "roads", "ndvi", "dem", "sar", "reservoir", "forest", "fire", "heat",
}
_ADMIN = r"(?:district|taluk|tehsil|mandal|block|state|province|county|city|region|division|municipality|village|town|basin|watershed|catchment)"


def place_candidates(text: str, period: Period | None = None, extra_stop: set[str] | None = None) -> list[str]:
    stop = _LEAD | set(MONTHS) | (extra_stop or set())
    t = text
    if period:
        t = re.sub(re.escape(period.text), " ", t, flags=re.IGNORECASE)
    pattern = rf"\b([A-Z][\w'’.-]*(?:\s+(?:[A-Z][\w'’.-]*|of|de|la|el|al|du|do|da))*(?:\s+{_ADMIN})?)"
    out = []
    for m in re.finditer(pattern, t):
        words = m[1].split()
        while words and words[0].lower().strip(".,") in stop:  # trim lead words / months
            words = words[1:]
        while words and words[-1].lower() in {"of", "de", "la", "el", "al", "du", "do", "da"}:
            words = words[:-1]
        cand = " ".join(words).strip(" .,")
        if cand and cand.lower() not in stop and cand not in out:
            out.append(cand)
    return out


@dataclass
class Place:
    query: str
    name: str
    display_name: str
    kind: str  # e.g. "boundary/administrative"
    osm: str  # "relation/2019638"
    geometry: BaseGeometry  # WGS84 (multi)polygon
    alternatives: list[str] = field(default_factory=list)

    @property
    def area_km2(self) -> float:
        return area_km2(self.geometry)


def geocode(query: str, pick: int = 0) -> Place:
    """Nominatim search → the `pick`-th result that has a polygon. Raises LookupError if none."""
    r = httpx.get("https://nominatim.openstreetmap.org/search",
                  params={"q": query, "format": "jsonv2", "polygon_geojson": 1, "polygon_threshold": 0.0005, "limit": 8},
                  headers=UA, timeout=60)
    r.raise_for_status()
    polys = [x for x in r.json() if x.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")]
    if not polys:
        raise LookupError(f"OpenStreetMap has no area (polygon) named {query!r}; pass --place with a fuller name, or --aoi FILE")
    if pick >= len(polys):
        raise LookupError(f"only {len(polys)} area(s) match {query!r}; --pick must be < {len(polys)}")
    x = polys[pick]
    alts = [f"[{i}] {p['display_name']} ({p.get('category')}/{p.get('type')})" for i, p in enumerate(polys)]
    return Place(query, x.get("name") or query, x["display_name"], f"{x.get('category')}/{x.get('type')}",
                 f"{x.get('osm_type')}/{x.get('osm_id')}", shape(x["geojson"]).buffer(0), alts)
