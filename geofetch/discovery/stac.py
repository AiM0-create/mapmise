"""STAC discovery adapter: search a collection and return normalised items.

Provider differences (asset names, property names) are absorbed here so the
planner only ever sees `NormalisedItem`. Nothing in this module makes
selection decisions; it only fetches and normalises metadata.

Search results are cached as raw GeoJSON features in a project cache directory,
keyed by the exact query, so re-planning is offline and the query is recorded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pystac_client import Client
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# Canonical band name -> per-collection asset key.
# Keep this table small and explicit; it is the only place provider naming leaks in.
BAND_ALIASES: dict[str, dict[str, str]] = {
    "earth_search/sentinel-2-l2a": {
        "red": "red", "green": "green", "blue": "blue", "nir": "nir",
        "nir08": "nir08", "swir16": "swir16", "swir22": "swir22", "scl": "scl",
    },
    "planetary_computer/sentinel-2-l2a": {
        "red": "B04", "green": "B03", "blue": "B02", "nir": "B08",
        "nir08": "B8A", "swir16": "B11", "swir22": "B12", "scl": "SCL",
    },
}

PROVIDERS: dict[str, str] = {
    "earth_search": "https://earth-search.aws.element84.com/v1",
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
}

_FIELDS = [
    "id", "geometry", "properties.datetime", "properties.eo:cloud_cover",
    "properties.grid:code", "properties.s2:mgrs_tile", "properties.sat:relative_orbit",
    "properties.sat:orbit_state", "properties.proj:epsg", "properties.proj:code",
    "properties.proj:bbox", "properties.s2:nodata_pixel_percentage",
]


@dataclass(frozen=True)
class Query:
    provider: str
    collection: str
    bbox: tuple[float, float, float, float]
    start: str  # ISO date
    end: str  # ISO date (inclusive)
    bands: tuple[str, ...]

    @property
    def datetime_range(self) -> str:
        return f"{self.start}T00:00:00Z/{self.end}T23:59:59Z"

    @property
    def key(self) -> str:
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True, default=list).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {**self.__dict__, "bbox": list(self.bbox), "bands": list(self.bands), "endpoint": PROVIDERS[self.provider]}


@dataclass
class NormalisedItem:
    provider: str
    collection: str
    id: str
    datetime: datetime
    geometry: BaseGeometry  # WGS84 footprint of valid data
    cloud_cover: float | None
    tile: str | None  # e.g. "MGRS-43PCQ"
    relative_orbit: int | None
    orbit_state: str | None
    epsg: int | None
    tile_bbox: tuple[float, float, float, float] | None  # proj:bbox in `epsg` — the full tile extent
    nodata_pct: float | None
    assets: dict[str, dict] = field(default_factory=dict)  # canonical band -> asset dict (href, type, ...)

    @property
    def date(self) -> str:
        return self.datetime.strftime("%Y-%m-%d")


def _epsg(props: dict) -> int | None:
    if props.get("proj:epsg"):
        return int(props["proj:epsg"])
    code = props.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        return int(code.split(":")[1])
    return None


def _asset_bbox(asset: dict) -> list[float] | None:
    """Tile extent from an asset: proj:bbox if present, else proj:transform × proj:shape (Earth Search)."""
    if asset.get("proj:bbox"):
        return asset["proj:bbox"]
    t, shp = asset.get("proj:transform"), asset.get("proj:shape")
    if t and shp and len(t) >= 6:
        a, b, c, d, e, f = t[:6]  # x = c + a·col + b·row ; y = f + d·col + e·row
        rows, cols = shp
        xs = [c, c + a * cols, c + b * rows, c + a * cols + b * rows]
        ys = [f, f + d * cols, f + e * rows, f + d * cols + e * rows]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def normalise(provider: str, collection: str, feature: dict) -> NormalisedItem:
    p = feature["properties"]
    aliases = BAND_ALIASES.get(f"{provider}/{collection}", {})
    assets = {c: feature["assets"][k] for c, k in aliases.items() if k in feature.get("assets", {})}
    bbox = p.get("proj:bbox") or next((_asset_bbox(a) for a in assets.values() if _asset_bbox(a)), None)
    return NormalisedItem(
        provider=provider,
        collection=collection,
        id=feature["id"],
        datetime=datetime.fromisoformat(p["datetime"].replace("Z", "+00:00")).astimezone(timezone.utc),
        geometry=shape(feature["geometry"]),
        cloud_cover=p.get("eo:cloud_cover"),
        tile=p.get("grid:code") or (f"MGRS-{p['s2:mgrs_tile']}" if p.get("s2:mgrs_tile") else None),
        relative_orbit=p.get("sat:relative_orbit"),
        orbit_state=p.get("sat:orbit_state"),
        epsg=_epsg(p),
        tile_bbox=tuple(bbox) if bbox and len(bbox) == 4 else None,
        nodata_pct=p.get("s2:nodata_pixel_percentage"),
        assets=assets,
    )


def _fetch(query: Query, page_size: int = 100) -> list[dict]:
    client = Client.open(PROVIDERS[query.provider])
    aliases = BAND_ALIASES.get(f"{query.provider}/{query.collection}", {})
    include = _FIELDS + [f"assets.{aliases[b]}" for b in query.bands if b in aliases]
    search = client.search(
        collections=[query.collection], bbox=list(query.bbox), datetime=query.datetime_range,
        limit=page_size, fields={"include": include, "exclude": ["links"]},
    )
    return list(search.items_as_dicts())


def search(query: Query, cache_dir: Path | None = None, refresh: bool = False) -> tuple[list[NormalisedItem], dict]:
    """Search by bbox + datetime only; all other filtering is client-side in the planner.

    Returns (items, record) where record documents the query and when/where it was answered.
    """
    cache_file = (cache_dir / f"search_{query.key}.json") if cache_dir else None
    if cache_file and cache_file.exists() and not refresh:
        blob = json.loads(cache_file.read_text())
        return [normalise(query.provider, query.collection, f) for f in blob["features"]], blob["record"]
    t0 = datetime.now(timezone.utc)
    features = _fetch(query)
    record = {"query": query.to_dict(), "searched_at": t0.isoformat(timespec="seconds"), "n_features": len(features)}
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"record": record, "features": features}))
    return [normalise(query.provider, query.collection, f) for f in features], record
