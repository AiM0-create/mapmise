"""HTTP driver: datasets published as plain files.

modes (source.access.mode):
  window           GeoTIFF over HTTP — only the AOI window is read (rasterio /vsicurl/)
  file_per_window  one (possibly gzipped) global file per time window — downloaded once to the
                   project cache with resume, decompressed, then clipped
  vector           GeoJSON (directly, or via a JSON API whose `url_key` points at it) — clipped to the AOI
URL templates may use {iso3} {iso3_lower} {yyyy} {mm}.
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import geopandas as gpd
import httpx
from shapely.geometry.base import BaseGeometry

from geofetch.geo import UA
from geofetch.registry import Source


def render_url(template: str, *, iso3: str | None = None, yyyy: str | None = None, mm: str | None = None) -> str:
    return template.format(iso3=iso3 or "", iso3_lower=(iso3 or "").lower(), yyyy=yyyy or "", mm=mm or "")


def head_size(url: str) -> int | None:
    r = httpx.head(url, headers=UA, timeout=60, follow_redirects=True)
    return int(r.headers["content-length"]) if r.status_code == 200 and "content-length" in r.headers else None


def download_file(url: str, dest: Path) -> Path:
    """Whole-file download with byte-range resume; decompresses .gz next to it. Returns the usable file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    if not dest.exists():
        headers = {**UA, **({"Range": f"bytes={have}-"} if have else {})}
        with httpx.stream("GET", url, headers=headers, timeout=None, follow_redirects=True) as r:
            if r.status_code == 416:  # already complete
                pass
            else:
                r.raise_for_status()
                mode = "ab" if r.status_code == 206 else "wb"
                with open(part, mode) as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
        part.rename(dest)
    if dest.suffix == ".gz":
        out = dest.with_suffix("")
        if not out.exists():
            with gzip.open(dest, "rb") as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return out
    return dest


def fetch_vector(source: Source, aoi: BaseGeometry, out: Path, iso3: str | None) -> Path:
    a = source.access
    url = render_url(a["url"], iso3=iso3)
    if a.get("url_key"):
        r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        url = r.json()[a["url_key"]]
    r = httpx.get(url, headers=UA, timeout=300, follow_redirects=True)
    r.raise_for_status()
    gdf = gpd.GeoDataFrame.from_features(r.json()["features"], crs="EPSG:4326")
    clipped = gpd.clip(gdf, aoi)
    out.parent.mkdir(parents=True, exist_ok=True)
    clipped.to_file(out, driver="GPKG")
    return out
