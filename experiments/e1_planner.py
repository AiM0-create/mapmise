"""E1 — Planner value: coverage-aware selection vs naive cloud filter.

Karnataka, Sentinel-2 L2A on Earth Search, monsoon 2026 (Jun 1 – Sep 15).
Writes experiments/out/e1_plan.json and prints the comparison table.
"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx

from geofetch.aoi import load_aoi
from geofetch.discovery.stac import search
from geofetch.planner.s2_timeseries import monthly_windows, plan_s2_timeseries

OUT = Path("experiments/out"); OUT.mkdir(parents=True, exist_ok=True)
START, END = "2026-06-01", "2026-09-15"
BANDS = ["red", "nir"]
CLOUD_MAX = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0

aoi = load_aoi("data/karnataka.geojson", "Karnataka")
print(f"AOI {aoi.name}: {aoi.area_km2:,.0f} km², bbox {[round(b,2) for b in aoi.bbox]}, project CRS EPSG:{aoi.utm_epsg}")

t0 = time.time()
items = list(search("earth_search", "sentinel-2-l2a", aoi.bbox, START, END, bands=BANDS))
print(f"catalogue: {len(items)} items in bbox, {time.time()-t0:.1f}s")

plan = plan_s2_timeseries(aoi, items, monthly_windows(date.fromisoformat(START), date.fromisoformat(END)), cloud_max=CLOUD_MAX)
print(f"tiles intersecting AOI: {len(plan.tiles)}")

# ---- byte estimate: HEAD each selected asset (Earth Search items carry no file:size)
by_id = {it.id: it for it in items}
sel_items = [by_id[s.selected.item_id] for s in plan.selections if s.selected]
comp_items = list({c.item_id: by_id[c.item_id] for s in plan.selections for c in s.composite}.values())
hrefs = [(it.id, b, it.assets[b]["href"]) for it in {x.id: x for x in sel_items + comp_items}.values() for b in BANDS if b in it.assets]
CACHE = OUT / "head_cache.json"
sizes: dict[str, int] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
hrefs = [h for h in hrefs if f"{h[0]}/{h[1]}" not in sizes]
ranges = "cached"
def head(t):
    iid, b, href = t
    r = httpx.head(href, timeout=30, follow_redirects=True)
    return f"{iid}/{b}", int(r.headers.get("content-length", 0)), r.headers.get("accept-ranges")
t0 = time.time()
with ThreadPoolExecutor(16) as ex:
    for key, n, ranges in ex.map(head, hrefs):
        sizes[key] = n
CACHE.write_text(json.dumps(sizes))
print(f"HEAD {len(hrefs)} assets in {time.time()-t0:.1f}s; accept-ranges={ranges}")

tile_frac = {t.tile: t.aoi_fraction_of_tile for t in plan.tiles}
full_bytes = sum(sizes[f"{it.id}/{b}"] for it in sel_items for b in BANDS if f"{it.id}/{b}" in sizes)
window_bytes = sum(sizes[f"{it.id}/{b}"] * tile_frac[it.tile] for it in sel_items for b in BANDS if f"{it.id}/{b}" in sizes)

# ---- report
print(f"\nComposite mode (target {100*plan.composite_target:.0f}% expected clear):")
for w in plan.windows:
    print(f"  {w.window}: {w.composite_scenes:4d} scenes -> expected clear {100*w.composite_clear_coverage:5.1f}% of AOI")
print("\nPer-window coverage (fraction of AOI):")
print(f"{'window':8} {'avail':>6} | {'planner':>7} {'observed':>9} {'clear':>7} {'>thr':>5} {'noTile':>6} | {'naive<thr':>9} {'observed':>9} {'clear':>7}")
for w in plan.windows:
    print(f"{w.window:8} {w.n_scenes_available:6d} | {w.planner_scenes:7d} {100*w.planner_observed_coverage:8.1f}% {100*w.planner_clear_coverage:6.1f}% {w.planner_over_threshold:5d} {w.tiles_without_scene:6d} | {w.naive_scenes:9d} {100*w.naive_observed_coverage:8.1f}% {100*w.naive_clear_coverage:6.1f}%")
print(f"\nSelected scenes: {len(sel_items)}  (cloud threshold {CLOUD_MAX:.0f}%)")
print(f"Estimated transfer, bands {BANDS}: full COGs {full_bytes/1e9:.2f} GB; AOI-windowed estimate {window_bytes/1e9:.2f} GB")
comp_full = sum(sizes[f"{it.id}/{b}"] for it in comp_items for b in BANDS if f"{it.id}/{b}" in sizes)
comp_win = sum(sizes[f"{it.id}/{b}"] * tile_frac[it.tile] for it in comp_items for b in BANDS if f"{it.id}/{b}" in sizes)
print(f"Composite plan: {len(comp_items)} scenes; full COGs {comp_full/1e9:.2f} GB; AOI-windowed estimate {comp_win/1e9:.2f} GB")
print("\nWhy:"); [print(" -", e) for e in plan.explanations]

flagged = [s for s in plan.selections if s.selected and not s.meets_threshold][:6]
print(f"\nExample flagged selections ({sum(1 for s in plan.selections if s.selected and not s.meets_threshold)} total):")
for s in flagged:
    print(f"  {s.window} {s.tile}: {s.selected.item_id} cc={s.selected.cloud_cover:.0f}% cov={100*s.selected.coverage:.0f}% -> {'; '.join(s.reasons)}")

d = plan.to_dict(); d["byte_estimate"] = {"full_cog_bytes": full_bytes, "aoi_window_estimate_bytes": int(window_bytes), "composite_full_cog_bytes": comp_full, "composite_aoi_window_estimate_bytes": int(comp_win), "composite_scenes": len(comp_items), "asset_sizes": sizes, "bands": BANDS}
(OUT / "e1_plan.json").write_text(json.dumps(d, indent=1, default=str))
print(f"\nplan written to {OUT/'e1_plan.json'}")
