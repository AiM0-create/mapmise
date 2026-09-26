"""STAC driver: search a registry source's collection and return normalised items.

Provider differences (asset names, property names, signing) are absorbed here via the
source's `access` block. Searches are cached per project as raw features, keyed by the exact
query, so re-planning is offline and the query itself is recorded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import warnings

from pystac_client import Client
from pystac_client.warnings import DoesNotConformTo
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from geofetch.registry import Source

PROVIDERS: dict[str, str] = {
    "earth_search": "https://earth-search.aws.element84.com/v1",
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
}
_FIELDS = ["id", "geometry", "bbox", "properties.datetime", "properties.start_datetime", "properties.end_datetime",
           "properties.eo:cloud_cover", "properties.grid:code", "properties.sat:relative_orbit", "properties.sat:orbit_state",
           "properties.proj:epsg", "properties.proj:code", "properties.proj:bbox", "properties.proj:shape", "properties.proj:transform"]


@dataclass(frozen=True)
class Query:
    source_id: str
    provider: str
    collection: str
    bbox: tuple[float, float, float, float]
    start: str | None  # ISO date; None for static layers
    end: str | None
    assets: tuple[str, ...]  # canonical names
    extra: dict = field(default_factory=dict, hash=False, compare=False)

    @property
    def key(self) -> str:
        d = {**self.__dict__, "extra": json.dumps(self.extra, sort_keys=True)}
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=list).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {**self.__dict__, "bbox": list(self.bbox), "assets": list(self.assets), "endpoint": PROVIDERS[self.provider]}


@dataclass
class Item:
    source_id: str
    id: str
    datetime: datetime
    geometry: BaseGeometry  # WGS84 footprint
    cloud_cover: float | None
    group: str | None  # value of access.group_by (MGRS tile, relative orbit, ...)
    relative_orbit: int | None
    orbit_state: str | None
    epsg: int | None
    tile_bbox: tuple[float, float, float, float] | None
    assets: dict[str, dict]  # canonical -> {href, type, ...}

    @property
    def date(self) -> str:
        return self.datetime.strftime("%Y-%m-%d")


def _epsg(p: dict) -> int | None:
    if p.get("proj:epsg"):
        return int(p["proj:epsg"])
    code = p.get("proj:code")
    return int(code.split(":")[1]) if isinstance(code, str) and code.upper().startswith("EPSG:") else None


def _bbox_from(obj: dict) -> list[float] | None:
    if obj.get("proj:bbox"):
        return obj["proj:bbox"]
    t, shp = obj.get("proj:transform"), obj.get("proj:shape")
    if t and shp and len(t) >= 6:
        a, b, c, d, e, f = t[:6]
        rows, cols = shp
        xs = [c, c + a * cols, c + b * rows, c + a * cols + b * rows]
        ys = [f, f + d * cols, f + e * rows, f + d * cols + e * rows]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _group(props: dict, key) -> str | None:
    """access.group_by: one property name or a list (joined with '/'), e.g. [landsat:wrs_path, landsat:wrs_row]."""
    if not key:
        return None
    keys = key if isinstance(key, list) else [key]
    vals = [props.get(k) for k in keys]
    return "/".join(str(v) for v in vals) if all(v is not None for v in vals) else None


def normalise(source: Source, feature: dict) -> Item:
    p = feature["properties"]
    alias = source.access.get("assets", {})
    assets = {c: feature["assets"][k] for c, k in alias.items() if k in feature.get("assets", {})}
    dt = p.get("datetime") or p.get("start_datetime")
    group_key = source.access.get("group_by")
    bbox = _bbox_from(p) or next((_bbox_from(a) for a in assets.values() if _bbox_from(a)), None)
    return Item(
        source_id=source.id, id=feature["id"],
        datetime=datetime.fromisoformat(dt.replace("Z", "+00:00")).astimezone(timezone.utc),
        geometry=shape(feature["geometry"]), cloud_cover=p.get("eo:cloud_cover"),
        group=_group(p, group_key),
        relative_orbit=p.get("sat:relative_orbit"), orbit_state=p.get("sat:orbit_state"), epsg=_epsg(p),
        tile_bbox=tuple(bbox) if bbox and len(bbox) == 4 else None, assets=assets,
    )


def build_query(source: Source, bbox: list[float], start: str | None, end: str | None, assets: list[str]) -> Query:
    a = source.access
    return Query(source.id, a["provider"], a["collection"], tuple(bbox), start, end, tuple(assets), a.get("query", {}))


def _fetch(q: Query, source: Source, page_size: int = 100) -> list[dict]:
    warnings.simplefilter("ignore", DoesNotConformTo)  # Planetary Computer ignores the fields extension; harmless
    client = Client.open(PROVIDERS[q.provider])
    alias = source.access.get("assets", {})
    gb = source.access.get("group_by") or []
    include = _FIELDS + [f"properties.{k}" for k in (gb if isinstance(gb, list) else [gb])] + [f"assets.{alias[c]}" for c in q.assets if c in alias]
    kw = dict(collections=[q.collection], bbox=list(q.bbox), limit=page_size, fields={"include": include, "exclude": ["links"]})
    if q.start and q.end:
        kw["datetime"] = f"{q.start}T00:00:00Z/{q.end}T23:59:59Z"
    if q.extra:
        kw["query"] = q.extra
    return list(client.search(**kw).items_as_dicts())


def search(source: Source, q: Query, cache_dir: Path | None = None, refresh: bool = False) -> tuple[list[Item], dict]:
    cache_file = (cache_dir / f"search_{q.key}.json") if cache_dir else None
    if cache_file and cache_file.exists() and not refresh:
        blob = json.loads(cache_file.read_text())
        return [normalise(source, f) for f in blob["features"]], blob["record"]
    t0 = datetime.now(timezone.utc)
    features = _fetch(q, source)
    record = {"query": q.to_dict(), "searched_at": t0.isoformat(timespec="seconds"), "n_features": len(features)}
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"record": record, "features": features}))
    return [normalise(source, f) for f in features], record
