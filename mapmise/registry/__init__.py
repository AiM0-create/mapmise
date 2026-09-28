"""Registry: open data sources (sources.yaml) and ask rules (asks.yaml), validated on load.

Everything here is data. Adding a dataset or an ask rule means editing YAML, not code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

THEMES = {
    "optical", "sar", "elevation", "water", "hydrography", "vegetation", "landcover", "builtup", "buildings",
    "population", "precipitation", "soil_moisture", "temperature", "boundaries", "transport", "events",
    "fire", "forest", "soil", "facilities", "surface_water",
}
PLANNERS = {"optical", "sar", "products"}
KINDS = {"raster", "vector", "event"}
SHAPES = {"layer", "series", "query", "events"}
DRIVERS = {"stac", "http", "overpass", "gdacs"}
TEMPORAL_MODES = {"static", "series", "pair"}
PRIORITIES = {"required", "recommended", "optional"}

_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    themes: tuple[str, ...]
    kind: str
    shape: str
    resolution_m: float | None
    temporal: dict
    coverage: object  # "global" | [iso3...] | [minx, miny, maxx, maxy]
    license: str
    analysis_ready: bool
    description: str
    access: dict
    cloud_dependent: bool = False

    @property
    def needs_login(self) -> bool:
        """True for datasets that need the user's own NASA Earthdata token (access.auth: earthdata)."""
        return self.access.get("auth") == "earthdata"

    @property
    def driver(self) -> str:
        return self.access["driver"]

    @property
    def planner(self) -> str | None:
        """Scene planner for series sources: explicit `access.planner`, else optical if cloud-dependent, sar if radar, else products."""
        if self.shape != "series" or self.driver != "stac":
            return None
        if self.access.get("planner"):
            return self.access["planner"]
        if self.cloud_dependent:
            return "optical"
        return "sar" if "sar" in self.themes and self.access.get("group_by") == "sat:relative_orbit" else "products"

    @property
    def yearly(self) -> bool:
        return (self.temporal.get("revisit_days") or 0) >= 300

    def covers_bbox(self, bbox: list[float], iso3: str | None = None) -> bool:
        c = self.coverage
        if c == "global":
            return True
        if isinstance(c, list) and len(c) == 4 and all(isinstance(x, (int, float)) for x in c):
            return not (bbox[2] < c[0] or bbox[0] > c[2] or bbox[3] < c[1] or bbox[1] > c[3])
        if isinstance(c, list):
            return iso3 in c if iso3 else True
        return False

    def covers_full_period(self, start: str, end: str) -> bool:
        t = self.temporal
        if t.get("type") == "static":
            return True
        frm, to = str(t.get("from", "0000")), t.get("to")
        return frm <= start[:7] and (to is None or str(to) >= end[:7])

    def covers_period(self, start: str, end: str) -> bool:
        """Year-month string comparison; open-ended `to` means ongoing."""
        t = self.temporal
        if t.get("type") == "static":
            return True
        frm, to = str(t.get("from", "0000")), t.get("to")
        return frm <= end[:7] and (to is None or str(to) >= start[:7])


@dataclass(frozen=True)
class Need:
    theme: str
    temporal: str  # static | series | pair
    priority: str
    why: str
    prefer: dict = field(default_factory=dict)
    rule: str = ""

    @property
    def key(self) -> str:
        return f"{self.theme}:{self.temporal}"


@dataclass(frozen=True)
class AskRule:
    id: str
    keywords: tuple[str, ...]
    needs: tuple[Need, ...]
    event_type: str | None = None
    examples: tuple[str, ...] = ()
    title: str = ""

    @property
    def label(self) -> str:
        return self.title or self.id.replace("_", " ").capitalize()


def _load_yaml(name: str) -> list[dict]:
    with open(_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_sources() -> dict[str, Source]:
    out: dict[str, Source] = {}
    for e in _load_yaml("sources.yaml"):
        for key in ("id", "name", "themes", "kind", "shape", "temporal", "coverage", "license", "analysis_ready", "description", "access"):
            if key not in e:
                raise ValueError(f"source {e.get('id', '?')}: missing {key}")
        bad = set(e["themes"]) - THEMES
        if bad:
            raise ValueError(f"source {e['id']}: unknown themes {bad}")
        if e["kind"] not in KINDS or e["shape"] not in SHAPES or e["access"].get("driver") not in DRIVERS:
            raise ValueError(f"source {e['id']}: bad kind/shape/driver")
        if e["access"].get("planner") and e["access"]["planner"] not in PLANNERS:
            raise ValueError(f"source {e['id']}: unknown planner {e['access']['planner']}")
        if e["id"] in out:
            raise ValueError(f"duplicate source id {e['id']}")
        out[e["id"]] = Source(
            id=e["id"], name=e["name"], themes=tuple(e["themes"]), kind=e["kind"], shape=e["shape"],
            resolution_m=e.get("resolution_m"), temporal=e["temporal"], coverage=e["coverage"], license=e["license"],
            analysis_ready=bool(e["analysis_ready"]), description=e["description"], access=e["access"],
            cloud_dependent=bool(e.get("cloud_dependent", False)),
        )
    return out


def load_ask_rules() -> list[AskRule]:
    rules = []
    for r in _load_yaml("asks.yaml"):
        needs = []
        for n in r["needs"]:
            if n["theme"] not in THEMES or n["temporal"] not in TEMPORAL_MODES or n["priority"] not in PRIORITIES:
                raise ValueError(f"ask rule {r['id']}: bad need {n}")
            needs.append(Need(n["theme"], n["temporal"], n["priority"], n["why"], n.get("prefer", {}), r["id"]))
        for k in r["keywords"]:
            re.compile(k)
        rules.append(AskRule(r["id"], tuple(r["keywords"]), tuple(needs), r.get("event_type"), tuple(r.get("examples", [])),
                             r.get("title", "")))
    return rules
