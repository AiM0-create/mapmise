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

from mapmise.aoi import area_km2
from mapmise.geo import UA

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
_PREPOSITIONS = {"in", "near", "at", "around", "of", "over", "across", "for", "from", "within", "outside", "inside", "along", "to"}
_ADMIN = r"(?:district|taluk|tehsil|mandal|block|state|province|county|city|region|division|municipality|village|town|basin|watershed|catchment)"


def place_candidates(text: str, period: Period | None = None, extra_stop: set[str] | None = None) -> list[str]:
    stop = _LEAD | set(MONTHS) | (extra_stop or set())
    t = text
    if period:
        t = re.sub(re.escape(period.text), " ", t, flags=re.IGNORECASE)
    pattern = rf"\b([A-Z][\w'’.-]*(?:\s+(?:[A-Z][\w'’.-]*|of|de|la|el|al|du|do|da))*(?:\s+{_ADMIN})?)"
    found: list[tuple[int, int, str]] = []
    for i, m in enumerate(re.finditer(pattern, t)):
        words = m[1].split()
        while words and words[0].lower().strip(".,") in stop:  # trim lead words / months
            words = words[1:]
        while words and words[-1].lower() in {"of", "de", "la", "el", "al", "du", "do", "da"}:
            words = words[:-1]
        cand = " ".join(words).strip(" .,")
        if not cand or cand.lower() in stop or any(cand == f[2] for f in found):
            continue
        before = t[:m.start()].rstrip()
        prev = before.split()[-1].lower() if before.split() else ""
        # rank: a name after a place preposition first; a capitalised first word of a sentence last
        rank = 0 if prev in _PREPOSITIONS else (2 if not before or before.endswith((".", "!", "?")) else 1)
        found.append((rank, i, cand))
    if not found:
        return _lowercase_places(t, stop)
    return [c for _, _, c in sorted(found)]


# words that end a place name typed in lower case ("flood in chitradurga during the monsoon")
_END = {"during", "since", "between", "from", "to", "and", "the", "last", "past", "this", "that", "in", "on", "at",
        "for", "with", "after", "before", "over", "by", "of", "when", "because", "due", "due", "is", "was", "were",
        "are", "has", "have", "had", "please", "using", "per", "each", "every", "year", "years", "month", "months",
        "area", "region", "areas", "happened", "occurred", "help", "me", "analyse", "analyze", "locally"}


def _lowercase_places(t: str, stop: set[str]) -> list[str]:
    """People often type place names in lower case. Take the words after a place preposition, up to the first word
    that cannot be part of a name. Each candidate is still confirmed by the geocoder."""
    out: list[str] = []
    for m in re.finditer(r"\b(?:in|near|at|around|over|across|of|within|for)\s+([a-z][\w'’.-]*(?:\s+[a-z][\w'’.-]*){0,4})",
                         t, flags=re.IGNORECASE):
        words = []
        for w in m[1].split():
            lw = w.lower().strip(".,;:!?")
            if words and re.fullmatch(_ADMIN, lw):  # "chitradurga district": the admin word belongs to the name, and ends it
                words.append(w.strip(".,;:!?"))
                break
            if lw in _END or lw in stop or re.fullmatch(r"(19|20)\d{2}", lw):
                break
            words.append(w.strip(".,;:!?"))
        cand = " ".join(words)
        if cand and cand.lower() not in stop and cand.lower() not in (c.lower() for c in out):
            out.append(cand.title() if cand.islower() else cand)
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


def geocode_first(queries: list[str], pick: int = 0) -> tuple[Place, str]:
    """Try each candidate for a real area first; only if none has one, use the area enclosing the first point place."""
    import time
    points: list[tuple[str, dict, list[str]]] = []
    for i, q in enumerate(queries):
        if i:
            time.sleep(1.0)  # Nominatim usage policy
        try:
            return geocode(q, pick, allow_point=False), q
        except _PointOnly as e:
            points.append((q, e.point, e.names))
        except LookupError:
            continue
    if points:
        q, point, names = points[0]
        return _enclosing_area(q, point, names), q
    raise LookupError(f"OpenStreetMap doesn't know {' or '.join(repr(q) for q in queries)} as a place. Try a fuller name "
                      "(for example “Hiriyur, Karnataka”), or give a boundary file.")


class _PointOnly(LookupError):
    def __init__(self, point: dict, names: list[str]):
        super().__init__("point only")
        self.point, self.names = point, names


def geocode(query: str, pick: int = 0, allow_point: bool = True) -> Place:
    """Nominatim search → the `pick`-th result that has a polygon. A point-only place (a town) resolves to the
    administrative area containing it when allow_point, else raises _PointOnly. LookupError if nothing matches."""
    r = httpx.get("https://nominatim.openstreetmap.org/search",
                  params={"q": query, "format": "jsonv2", "polygon_geojson": 1, "polygon_threshold": 0.0, "limit": 8},
                  headers=UA, timeout=60)
    r.raise_for_status()
    results = r.json()
    polys = [x for x in results if x.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon")]
    if not polys and results:
        if not allow_point:
            raise _PointOnly(results[0], [x["display_name"] for x in results])
        return _enclosing_area(query, results[0], [x["display_name"] for x in results])
    if not polys:
        raise LookupError(f"OpenStreetMap has nothing named {query!r}. Try a fuller name (for example “Hiriyur, Karnataka”), "
                          "or give a boundary file.")
    if pick >= len(polys):
        raise LookupError(f"only {len(polys)} area(s) match {query!r}; choose one of the first {len(polys)} matches")
    x = polys[pick]
    alts = [f"[{i}] {p['display_name']} ({p.get('category')}/{p.get('type')})" for i, p in enumerate(polys)]
    return Place(query, x.get("name") or query, x["display_name"], f"{x.get('category')}/{x.get('type')}",
                 f"{x.get('osm_type')}/{x.get('osm_id')}", shape(x["geojson"]).buffer(0), alts)


def _enclosing_area(query: str, point: dict, names: list[str]) -> Place:
    """The place is only a point (a town or village): use the smallest administrative area that contains it
    (taluk/county level first, then district), and say so."""
    import time
    for zoom in (10, 8):
        time.sleep(1.0)  # Nominatim usage policy: at most one request per second
        r = httpx.get("https://nominatim.openstreetmap.org/reverse",
                      params={"lat": point["lat"], "lon": point["lon"], "zoom": zoom, "format": "jsonv2",
                              "polygon_geojson": 1, "polygon_threshold": 0.0}, headers=UA, timeout=60)
        r.raise_for_status()
        d = r.json()
        if d.get("geojson", {}).get("type") in ("Polygon", "MultiPolygon"):
            area_name = d.get("name") or d.get("display_name", "").split(",")[0]
            return Place(query, area_name, f"{d.get('display_name')} — the area containing {point.get('name') or query} "
                         f"({point.get('type', 'place')}, a point in OpenStreetMap)",
                         f"{d.get('category')}/{d.get('type')}", f"{d.get('osm_type')}/{d.get('osm_id')}",
                         shape(d["geojson"]).buffer(0), [f"[{i}] {n}" for i, n in enumerate(names)])
    raise LookupError(f"{query!r} is only a point in OpenStreetMap and no area around it was found; give a boundary file instead")
