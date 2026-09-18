"""STAC discovery adapter: search a collection and return normalised items.

Provider differences (asset names, property names) are absorbed here so the
planner only ever sees `NormalisedItem`. Nothing in this module makes
selection decisions; it only fetches and normalises metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from pystac_client import Client
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

# Canonical band name -> per-collection asset key.
# Keep this table small and explicit; it is the only place provider naming leaks in.
BAND_ALIASES: dict[str, dict[str, str]] = {
    # Earth Search v1 (Element 84)
    "earth_search/sentinel-2-l2a": {
        "red": "red", "green": "green", "blue": "blue", "nir": "nir",
        "nir08": "nir08", "swir16": "swir16", "swir22": "swir22", "scl": "scl",
    },
    # Microsoft Planetary Computer
    "planetary_computer/sentinel-2-l2a": {
        "red": "B04", "green": "B03", "blue": "B02", "nir": "B08",
        "nir08": "B8A", "swir16": "B11", "swir22": "B12", "scl": "SCL",
    },
    # Copernicus Data Space Ecosystem STAC
    "cdse/sentinel-2-l2a": {
        "red": "B04_10m", "green": "B03_10m", "blue": "B02_10m", "nir": "B08_10m",
        "nir08": "B8A_20m", "swir16": "B11_20m", "swir22": "B12_20m", "scl": "SCL_20m",
    },
}

PROVIDERS: dict[str, str] = {
    "earth_search": "https://earth-search.aws.element84.com/v1",
    "planetary_computer": "https://planetarycomputer.microsoft.com/api/stac/v1",
    "cdse": "https://stac.dataspace.copernicus.eu/v1",
}


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
    nodata_pct: float | None
    assets: dict[str, dict] = field(default_factory=dict)  # canonical band -> asset dict (href, type, ...)
    raw_properties: dict = field(default_factory=dict)

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


def normalise(provider: str, collection: str, feature: dict) -> NormalisedItem:
    p = feature["properties"]
    aliases = BAND_ALIASES.get(f"{provider}/{collection}", {})
    assets = {}
    for canonical, key in aliases.items():
        if key in feature.get("assets", {}):
            assets[canonical] = feature["assets"][key]
    return NormalisedItem(
        provider=provider,
        collection=collection,
        id=feature["id"],
        datetime=datetime.fromisoformat(p["datetime"].replace("Z", "+00:00")),
        geometry=shape(feature["geometry"]),
        cloud_cover=p.get("eo:cloud_cover"),
        tile=p.get("grid:code") or (f"MGRS-{p['s2:mgrs_tile']}" if p.get("s2:mgrs_tile") else None),
        relative_orbit=p.get("sat:relative_orbit"),
        orbit_state=p.get("sat:orbit_state"),
        epsg=_epsg(p),
        nodata_pct=p.get("s2:nodata_pixel_percentage"),
        assets=assets,
        raw_properties=p,
    )


def search(
    provider: str,
    collection: str,
    bbox: list[float],
    start: str,
    end: str,
    bands: list[str] | None = None,
    page_size: int = 100,
) -> Iterator[NormalisedItem]:
    """Search by bbox + datetime only. All other filtering is done client-side by the planner.

    Rationale (see docs/PRODUCT_DISCOVERY.md §12): server-side filter semantics differ
    between providers, so we fetch metadata and apply deterministic filters ourselves.
    """
    client = Client.open(PROVIDERS[provider])
    aliases = BAND_ALIASES.get(f"{provider}/{collection}", {})
    include = [
        "id", "geometry", "properties.datetime", "properties.eo:cloud_cover",
        "properties.grid:code", "properties.s2:mgrs_tile", "properties.sat:relative_orbit",
        "properties.sat:orbit_state", "properties.proj:epsg", "properties.proj:code",
        "properties.s2:nodata_pixel_percentage",
    ]
    for b in bands or []:
        if b in aliases:
            include.append(f"assets.{aliases[b]}")
    search = client.search(
        collections=[collection],
        bbox=bbox,
        datetime=f"{start}/{end}",
        limit=page_size,
        fields={"include": include, "exclude": ["links"]},
    )
    for feature in search.items_as_dicts():
        yield normalise(provider, collection, feature)
