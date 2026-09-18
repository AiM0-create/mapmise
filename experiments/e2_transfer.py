"""E2 — Windowed transfer: GDAL /vsicurl/ AOI clip vs whole-COG download.

District: Chitradurga (8,448 km²). Scene: the June 2026 planner selection for tile 43PFR.
Measures bytes, seconds, and pixel equality against a clip of the fully downloaded file.
"""
import json, time
from pathlib import Path

import httpx
import numpy as np
import rasterio

from geofetch.transfer.gdal_window import clip_cog, sha256_of

OUT = Path("experiments/out"); OUT.mkdir(parents=True, exist_ok=True)
CUTLINE = Path("data/chitradurga.geojson")
plan = json.loads((OUT / "e1_plan.json").read_text())
TILE, WINDOW = "MGRS-43PFR", "2026-06"
sel = next(s for s in plan["selections"] if s["tile"] == TILE and s["window"] == WINDOW)["selected"]
item_id = sel["item_id"]
print(f"scene {item_id} (cloud {sel['cloud_cover']:.0f}%)")
base = f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/43/P/FR/2026/6/{item_id}"
hrefs = {"red": f"{base}/B04.tif", "nir": f"{base}/B08.tif"}

results = {}
# 1. windowed clip straight from the remote COG, both bands
for band, href in hrefs.items():
    r = clip_cog(href, CUTLINE, OUT / f"e2_{band}_windowed.tif")
    results[f"{band}_windowed"] = {"bytes_downloaded": r.bytes_downloaded, "ranges": r.http_ranges, "seconds": round(r.seconds, 1), "output_bytes": r.output_bytes}
    print(f"[{band}] windowed: {r.bytes_downloaded/1e6:.1f} MB over {r.http_ranges} HTTP ranges in {r.seconds:.1f}s -> {r.output_bytes/1e6:.1f} MB GeoTIFF")

# 2. whole-COG download of red (streamed), then the same clip locally
full = OUT / "e2_red_full.tif"
t0 = time.time()
with httpx.stream("GET", hrefs["red"], timeout=None) as resp:
    total = int(resp.headers["content-length"])
    with open(full, "wb") as f:
        for chunk in resp.iter_bytes(1 << 20):
            f.write(chunk)
t_full = time.time() - t0
print(f"[red] full COG: {total/1e6:.1f} MB in {t_full:.1f}s")
r_local = clip_cog(str(full), CUTLINE, OUT / "e2_red_from_full.tif")
results["red_full"] = {"bytes_downloaded": total, "seconds": round(t_full, 1)}

# 3. pixel equality
with rasterio.open(OUT / "e2_red_windowed.tif") as a, rasterio.open(OUT / "e2_red_from_full.tif") as b:
    same_grid = a.transform == b.transform and a.shape == b.shape and a.crs == b.crs
    equal = same_grid and np.array_equal(a.read(1), b.read(1))
    print(f"[red] grid identical: {same_grid}; pixels identical: {equal}; shape {a.shape}; crs {a.crs}")
    print(f"[red] sha256 windowed={sha256_of(OUT/'e2_red_windowed.tif')[:16]} from_full={sha256_of(OUT/'e2_red_from_full.tif')[:16]}")
results["pixels_identical"] = bool(equal)

w = results["red_windowed"]["bytes_downloaded"]
results["reduction_factor_red"] = round(total / w, 1)
frac = next(t for t in plan["tiles"] if t["tile"] == TILE)["aoi_fraction_of_tile"]
results["planner_area_fraction_estimate_red"] = int(total * frac)
print(f"\nreduction: {total/1e6:.0f} MB -> {w/1e6:.1f} MB = {total/w:.1f}x ; speed-up {t_full/results['red_windowed']['seconds']:.1f}x")
print(f"E1 area-fraction estimate for this asset: {total*frac/1e6:.1f} MB vs measured {w/1e6:.1f} MB")
(OUT / "e2_results.json").write_text(json.dumps(results, indent=1))
