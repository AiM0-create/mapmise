"""E2b — how windowed transfer scales with AOI size relative to a Sentinel-2 tile.

Same scene as E2. AOIs: Chitradurga district (multi-tile), a 30 km box, a 10 km box
(both inside the district and tile 43PFR). Two GDAL settings: default 16 KB curl chunks
vs 5 MB chunks + multithreaded warp. Full-COG baseline reused from E2.
"""
import json, time
from pathlib import Path

import geopandas as gpd
import httpx
from shapely.geometry import box, shape

from geofetch.aoi import area_km2, to_equal_area
from geofetch.transfer.gdal_window import clip_cog

OUT = Path("experiments/out")
res_e2 = json.loads((OUT / "e2_results.json").read_text())
full_bytes, full_s = res_e2["red_full"]["bytes_downloaded"], res_e2["red_full"]["seconds"]
item_id = "S2B_43PFR_20260604_0_L2A"
href = f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/43/P/FR/2026/6/{item_id}/B04.tif"
foot = shape(httpx.get(f"https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{item_id}", timeout=60).json()["geometry"])
foot_ea = to_equal_area(foot)

aois = {"district": gpd.read_file("data/chitradurga.geojson").geometry.iloc[0]}
cx, cy = 76.45, 14.25  # inside Chitradurga and tile 43PFR
for km in (30, 10):
    d = km / 2 / 111.0
    aois[f"box{km}km"] = box(cx - d, cy - d / 0.97, cx + d, cy + d / 0.97)

settings = {
    "default": {},
    "tuned": {"CPL_VSIL_CURL_CHUNK_SIZE": "5000000", "CPL_VSIL_CURL_CACHE_SIZE": "200000000", "GDAL_NUM_THREADS": "ALL_CPUS"},
}
rows = []
print(f"full COG baseline: {full_bytes/1e6:.0f} MB in {full_s:.0f}s ({full_bytes/1e6/full_s:.1f} MB/s)\n")
print(f"{'aoi':10} {'km²':>7} {'frac of tile':>12} {'setting':8} {'MB':>7} {'ranges':>6} {'s':>6} {'MB/s':>5} {'reduction':>9} {'est MB':>7}")
for name, geom in aois.items():
    cut = OUT / f"e2b_{name}.geojson"
    gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326").to_file(cut, driver="GeoJSON")
    frac = to_equal_area(geom).intersection(foot_ea).area / foot_ea.area
    for sname, env in settings.items():
        r = clip_cog(href, cut, OUT / f"e2b_{name}_{sname}.tif", extra_env=env)
        mb = r.bytes_downloaded / 1e6
        rows.append({"aoi": name, "km2": area_km2(geom), "frac_of_tile": frac, "setting": sname, "bytes": r.bytes_downloaded, "ranges": r.http_ranges, "seconds": r.seconds, "estimate_bytes": int(full_bytes * frac)})
        print(f"{name:10} {area_km2(geom):7,.0f} {100*frac:11.1f}% {sname:8} {mb:7.1f} {r.http_ranges:6d} {r.seconds:6.1f} {mb/r.seconds:5.1f} {full_bytes/r.bytes_downloaded:8.1f}x {full_bytes*frac/1e6:7.1f}")
(OUT / "e2b_results.json").write_text(json.dumps(rows, indent=1))
