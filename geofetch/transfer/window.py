"""Parallel windowed fetch of a remote COG, clipped to an AOI, written as a local COG.

Why not a single gdalwarp: E2/E2c (docs/EXPERIMENTS.md) showed sequential range fetching is
latency-bound; 8–16 parallel block reads beat even a full download. So: compute the pixel
window covering the AOI, read its blocks with N threads (one dataset handle each), then
mask to the AOI polygon, reproject if the project CRS differs, and write a COG.

Memory: one band window is held in RAM (≤ one Sentinel-2 tile ≈ 240 MB uint16). Fine for
a prototype; a tiled writer is the obvious next step if larger sources appear.
"""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.env import Env
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_geom
from rasterio.windows import Window, from_bounds
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="2",
    CPL_VSIL_CURL_CHUNK_SIZE="1048576",
)


@dataclass
class WindowResult:
    href: str
    output: Path
    seconds: float
    sha256: str
    output_bytes: int
    width: int
    height: int
    epsg: int


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _blocks(win: Window, block: tuple[int, int], width: int, height: int) -> list[Window]:
    bh, bw = block
    r0, c0 = int(win.row_off) // bh * bh, int(win.col_off) // bw * bw
    r1, c1 = int(win.row_off + win.height), int(win.col_off + win.width)
    return [Window(c, r, min(bw, width - c), min(bh, height - r)) for r in range(r0, r1, bh) for c in range(c0, c1, bw)]


def fetch_window(href: str, aoi_wgs84: BaseGeometry, output: Path, dst_epsg: int, threads: int = 12, nodata: float = 0) -> WindowResult:
    src_path = f"/vsicurl/{href}" if href.startswith("http") else href
    t0 = time.time()
    with Env(**GDAL_ENV), rasterio.open(src_path) as src:
        src_crs, src_nodata = src.crs, src.nodata if src.nodata is not None else nodata
        aoi_src = shape(transform_geom("EPSG:4326", src_crs, mapping(aoi_wgs84)))
        win = from_bounds(*aoi_src.bounds, src.transform).round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, src.width, src.height))
        if win.width <= 0 or win.height <= 0:
            raise ValueError("AOI does not intersect the raster")
        blocks = _blocks(win, src.block_shapes[0], src.width, src.height)
        union = blocks[0]
        for b in blocks[1:]:
            union = rasterio.windows.union(union, b)
        data = np.full((int(union.height), int(union.width)), src_nodata, dtype=src.dtypes[0])
        dtype = src.dtypes[0]

        def read(b: Window):
            with Env(**GDAL_ENV), rasterio.open(src_path) as ds:
                return b, ds.read(1, window=b)

        with ThreadPoolExecutor(threads) as ex:
            for b, arr in ex.map(read, blocks):
                r, c = int(b.row_off - union.row_off), int(b.col_off - union.col_off)
                data[r : r + arr.shape[0], c : c + arr.shape[1]] = arr
        # crop the block-aligned union back to the exact AOI window
        r, c = int(win.row_off - union.row_off), int(win.col_off - union.col_off)
        data = data[r : r + int(win.height), c : c + int(win.width)]
        src_transform = src.window_transform(win)
        data[geometry_mask([mapping(aoi_src)], data.shape, src_transform, invert=False)] = src_nodata

        dst_crs = f"EPSG:{dst_epsg}"
        if src_crs.to_epsg() == dst_epsg:
            out, out_transform = data, src_transform
        else:
            out_transform, w, h = calculate_default_transform(src_crs, dst_crs, data.shape[1], data.shape[0], *rasterio.transform.array_bounds(data.shape[0], data.shape[1], src_transform))
            out = np.full((h, w), src_nodata, dtype=dtype)
            reproject(data, out, src_transform=src_transform, src_crs=src_crs, dst_transform=out_transform, dst_crs=dst_crs,
                      src_nodata=src_nodata, dst_nodata=src_nodata, resampling=Resampling.nearest)
        output.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(output, "w", driver="COG", width=out.shape[1], height=out.shape[0], count=1, dtype=dtype,
                           crs=dst_crs, transform=out_transform, nodata=src_nodata, compress="DEFLATE", predictor=2) as dst:
            dst.write(out, 1)
    return WindowResult(href, output, time.time() - t0, sha256_of(output), output.stat().st_size,
                        out.shape[1], out.shape[0], dst_epsg)
