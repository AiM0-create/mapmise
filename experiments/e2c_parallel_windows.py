"""E2c — does parallel block reading fix windowed throughput?

Read every 1024×1024 block of the red COG intersecting the district bbox with N threads
(each thread its own dataset handle), compare wall time with gdalwarp's sequential fetch.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import rasterio
from rasterio.env import Env
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds, Window

href = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/43/P/FR/2026/6/S2B_43PFR_20260604_0_L2A/B04.tif"
env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", CPL_VSIL_CURL_CHUNK_SIZE="1048576")
geom = gpd.read_file("data/chitradurga.geojson").geometry.iloc[0]
with Env(**env), rasterio.open(href) as src:
    b = transform_bounds("EPSG:4326", src.crs, *geom.bounds)
    win = from_bounds(*b, src.transform).round_offsets().round_lengths()
    win = win.intersection(Window(0, 0, src.width, src.height))
    bs = src.block_shapes[0]
    print(f"src {src.width}x{src.height}, blocks {bs}, window {int(win.width)}x{int(win.height)} px")
    blocks = [Window(c, r, min(bs[1], src.width - c), min(bs[0], src.height - r))
              for r in range(int(win.row_off) // bs[0] * bs[0], int(win.row_off + win.height), bs[0])
              for c in range(int(win.col_off) // bs[1] * bs[1], int(win.col_off + win.width), bs[1])]
print(f"{len(blocks)} blocks to read")

def read(w):
    with Env(**env), rasterio.open(href) as ds:
        return ds.read(1, window=w).nbytes

for n in (1, 4, 8, 16):
    t0 = time.time()
    with ThreadPoolExecutor(n) as ex:
        nbytes = sum(ex.map(read, blocks))
    dt = time.time() - t0
    print(f"threads={n:2d}: {dt:5.1f}s  ({nbytes/1e6:.0f} MB uncompressed pixels; gdalwarp sequential was 33.6s, full stream 32s)")
