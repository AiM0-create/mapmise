"""Overpass driver: live OpenStreetMap queries for an AOI, clipped and saved as GeoPackage."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import httpx
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from mapmise.geo import UA
from mapmise.registry import Source

MIRRORS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]


def build_query(source: Source, bbox: list[float]) -> str:
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    body = source.access["query"].format(bbox=f"{s},{w},{n},{e}")
    out = source.access.get("out", "geom")
    return f"[out:json][timeout:180];({body});out {out};"


def run_query(query: str) -> list[dict]:
    last = None
    for url in MIRRORS:
        try:
            r = httpx.post(url, data={"data": query}, headers={**UA, "Accept": "application/json"}, timeout=200)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                return r.json().get("elements", [])
            last = f"{url}: HTTP {r.status_code}"
        except httpx.HTTPError as e:
            last = f"{url}: {type(e).__name__}"
    raise RuntimeError(f"Overpass query failed on all mirrors ({last})")


def to_gdf(elements: list[dict], geometry: str) -> gpd.GeoDataFrame:
    rows = []
    keep = ("highway", "name", "building", "waterway", "surface", "lanes", "amenity", "healthcare", "operator:type")
    if geometry == "point":
        for el in elements:
            c = el if "lat" in el else el.get("center")
            if c:
                rows.append({"osm_id": f"{el['type']}/{el['id']}", **{k: v for k, v in el.get("tags", {}).items() if k in keep}, "geometry": Point(c["lon"], c["lat"])})
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326") if rows else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    for el in elements:
        if el.get("type") != "way" or "geometry" not in el:
            continue
        coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
        if len(coords) < 2:
            continue
        if geometry == "polygon":
            if len(coords) < 4 or coords[0] != coords[-1]:
                continue
            geom = Polygon(coords)
        else:
            geom = LineString(coords)
        rows.append({"osm_id": el["id"], **{k: v for k, v in el.get("tags", {}).items() if k in keep}, "geometry": geom})
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326") if rows else gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")


def fetch(source: Source, aoi: BaseGeometry, out: Path, epsg: int | None = None) -> tuple[Path, int]:
    els = run_query(build_query(source, list(aoi.bounds)))
    gdf = to_gdf(els, source.access.get("geometry", "line"))
    if len(gdf):
        gdf = gpd.clip(gdf, aoi)
    if epsg:  # the same projection as every raster in the project
        gdf = gdf.to_crs(epsg)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GPKG")
    return out, len(gdf)
