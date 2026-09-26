"""Plan builders — one per DATA SHAPE, never per analysis.

Every builder returns the same plan dict so the executor, status and report are generic:
  kind        scenes | layer | file_series | vector
  source      registry id            need    the need(s) this plan serves
  windows     [{label, start, end, verdict, verdict_text}]
  acquire     [{window, item_id, date, assets: {canonical: {href, size}}, group}]
  estimate    {n_assets, full_bytes, windowed_bytes, known}
  explanations, query, detail (planner-specific extras)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx
import rasterio
from rasterio.env import Env
from rasterio.warp import transform_bounds
from shapely.geometry import box

from geofetch.aoi import AOI, to_equal_area
from geofetch.drivers import http as http_driver
from geofetch.drivers import stac as stac_driver
from geofetch.drivers.signing import sign
from geofetch.geo import UA
import math

from geofetch.planner.optical import Plan as ScenePlan, Window, monthly_windows, plan_optical, pre_post_windows, yearly_windows
from geofetch.planner.products import plan_products
from geofetch.planner.sar import plan_sar
from geofetch.registry import Need, Source
from geofetch.transfer.window import GDAL_ENV

WINDOW_OVERHEAD = 2_000_000
LONG_PERIOD_DAYS = 548  # beyond ~18 months, series are planned per year (one clear scene/product per tile per year)


def _need_dicts(needs: list[Need]) -> list[dict]:
    return [{"theme": n.theme, "temporal": n.temporal, "priority": n.priority, "why": n.why, "rule": n.rule} for n in needs]


def _sizes(hrefs: dict[str, str], cache: dict[str, int], workers: int = 16) -> dict[str, int | None]:
    """HEAD every href not in cache (signing Planetary Computer blobs). Only real sizes are cached;
    a failed or size-less answer is None ("unknown"), never 0, and is retried next time."""
    todo = {k: h for k, h in hrefs.items() if not cache.get(k)}

    def head(kv):
        k, h = kv
        for _ in range(2):
            try:
                r = httpx.head(sign(h), headers=UA, timeout=45, follow_redirects=True)
                if r.status_code == 200 and r.headers.get("content-length"):
                    return k, int(r.headers["content-length"])
            except httpx.HTTPError:
                pass
        return k, None

    with ThreadPoolExecutor(workers) as ex:
        for k, n in ex.map(head, todo.items()):
            if n:
                cache[k] = n
    return {k: cache.get(k) for k in hrefs}


def _windows_for(need_temporal: str, start: date, end: date, event: date | None, pre_days: int, post_days: int, yearly: bool = False) -> list[Window]:
    if need_temporal == "pair":
        if event is None:
            raise ValueError("pair windows need an event date")
        return pre_post_windows(event, pre_days, post_days)
    return yearly_windows(start, end) if yearly else monthly_windows(start, end)


def scene_plan(aoi: AOI, source: Source, needs: list[Need], assets: list[str], start: date, end: date, cache_dir: Path,
               size_cache: dict[str, int], event: date | None = None, pre_days: int = 30, post_days: int = 30,
               cloud_max: float = 20.0, clear_target: float = 0.8, mode: str = "single") -> dict:
    temporal = "pair" if any(n.temporal == "pair" for n in needs) else "series"
    yearly = temporal != "pair" and (source.yearly or (end - start).days > LONG_PERIOD_DAYS)
    windows = _windows_for(temporal, start, end, event, pre_days, post_days, yearly=yearly)
    q = stac_driver.build_query(source, aoi.bbox, windows[0].start.isoformat(), windows[-1].end.isoformat(), assets)
    items, record = stac_driver.search(source, q, cache_dir)
    if source.planner == "optical":
        sp: ScenePlan = plan_optical(aoi, items, windows, assets, cloud_max=cloud_max, clear_target=clear_target)
    elif source.planner == "sar":
        ref = {"pre": event, "post": event} if event else None
        sp = plan_sar(aoi, items, windows, assets, same_orbit=(temporal == "pair"), reference=ref)
    else:
        sp = plan_products(aoi, items, windows, assets, one_per_window=yearly and not source.yearly)
    by_id = {it.id: it for it in items}
    frac = {t.tile: t.aoi_fraction_of_tile for t in sp.tiles}
    acquire = []
    for w, iid in sp.acquire(mode):
        it = by_id[iid]
        acquire.append({"window": w, "item_id": iid, "date": it.date, "group": it.group,
                        "assets": {a: {"href": it.assets[a]["href"], "size": None} for a in assets if a in it.assets}})
    hrefs = {f"{e['item_id']}/{a}": v["href"] for e in acquire for a, v in e["assets"].items()}
    sizes = _sizes(hrefs, size_cache)
    full = windowed = unknown = 0
    for e in acquire:
        f = frac.get(e["group"], 1.0) if source.planner == "optical" else _footprint_fraction(aoi, by_id[e["item_id"]].geometry)
        for a, v in e["assets"].items():
            v["size"] = sizes.get(f"{e['item_id']}/{a}")
            if v["size"] is None:
                unknown += 1
                continue
            full += v["size"]
            windowed += int(v["size"] * f) + WINDOW_OVERHEAD
    return {
        "kind": "scenes", "source": source.id, "needs": _need_dicts(needs), "temporal": temporal, "assets": assets, "mode": mode,
        "step": "pair" if temporal == "pair" else ("yearly" if yearly else "monthly"),
        "windows": [_window_dict(source, win, w) for win, w in zip(windows, sp.windows)],
        "acquire": acquire, "estimate": {"n_assets": len(hrefs), "full_bytes": full, "windowed_bytes": windowed, "known": True, "unknown": unknown},
        "explanations": sp.explanations, "query": record, "detail": sp.to_dict(),
    }


def _window_dict(source: Source, win: Window, w) -> dict:
    d = {"label": w.window, "start": win.start.isoformat(), "end": win.end.isoformat(), "verdict": w.verdict, "verdict_text": w.verdict_text,
         "n_available": w.n_scenes_available, "observed": w.single_observed_coverage, "clear": w.single_clear_coverage,
         "composite_scenes": w.composite_scenes, "composite_clear": w.composite_clear_coverage, "naive_scenes": w.naive_scenes}
    frm, to = str(source.temporal.get("from") or "0000"), source.temporal.get("to")
    if w.n_scenes_available == 0 and (win.end.isoformat()[:len(frm)] < frm or (to and win.start.isoformat()[:len(str(to))] > str(to))):
        d["verdict"], d["verdict_text"] = "out-of-range", f"outside this product's record ({frm} → {to or 'now'})"
    return d


def _footprint_fraction(aoi: AOI, footprint) -> float:
    fp = to_equal_area(footprint)
    return fp.intersection(to_equal_area(aoi.geometry)).area / fp.area if fp.area else 1.0


def layer_plan(aoi: AOI, source: Source, needs: list[Need], assets: list[str], cache_dir: Path, size_cache: dict[str, int],
               iso3: str | None) -> dict:
    """Static raster: every catalogue tile intersecting the AOI (STAC), or one file per country/globe (HTTP window)."""
    a = source.access
    acquire, record = [], {}
    if source.driver == "stac":
        q = stac_driver.build_query(source, aoi.bbox, None, None, assets)
        items, record = stac_driver.search(source, q, cache_dir)
        items = [it for it in items if it.geometry.intersects(aoi.geometry)]
        for it in items:
            acquire.append({"window": "static", "item_id": it.id, "date": it.date, "group": it.group, "fraction": _footprint_fraction(aoi, it.geometry),
                            "assets": {k: {"href": it.assets[k]["href"], "size": None} for k in assets if k in it.assets}})
    elif source.driver == "http" and a.get("mode") == "tile_grid":
        # fixed lat/lon grid of files (e.g. 10° tiles named by their top-left corner: 20N_070E)
        step = a["tile_deg"]
        minx, miny, maxx, maxy = aoi.bbox
        for top in range(math.ceil(maxy / step) * step, math.floor(miny / step) * step, -step):
            for left in range(math.floor(minx / step) * step, math.ceil(maxx / step) * step, step):
                tile_geom = box(left, top - step, left + step, top)
                if not tile_geom.intersects(aoi.geometry):
                    continue
                tile = f"{abs(top):02d}{'N' if top >= 0 else 'S'}_{abs(left):03d}{'E' if left >= 0 else 'W'}"
                acquire.append({"window": "static", "item_id": f"{source.id}-{tile}", "date": str(source.temporal.get("to") or source.temporal.get("from")),
                                "group": tile, "fraction": _footprint_fraction(aoi, tile_geom),
                                "assets": {k: {"href": a["url"].format(tile=tile, layer=layer), "size": None} for k, layer in a["assets"].items() if k in assets}})
        record = {"query": {"url": a["url"], "tiles": [e["group"] for e in acquire]}, "searched_at": None}
    elif source.driver == "http" and a.get("mode") == "window":
        url = http_driver.render_url(a["url"], iso3=iso3) if a.get("url") else None
        urls = {k: http_driver.render_url(u, iso3=iso3) for k, u in a["urls"].items()} if a.get("urls") else {k: url for k in assets}
        url = url or next(iter(urls.values()))
        whole = False
        try:
            with Env(**GDAL_ENV), rasterio.open(f"/vsicurl/{url}") as ds:  # header only: bounds for the AOI fraction
                b = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            frac = _footprint_fraction(aoi, box(*b))
        except rasterio.errors.RasterioIOError:  # server ignores HTTP ranges: download the whole file once, then clip
            whole, frac = True, 1.0
        acquire.append({"window": "static", "item_id": f"{source.id}-{(iso3 or 'global').lower()}", "date": str(source.temporal.get("from")), "group": None,
                        "fraction": frac, "whole_file": whole, "assets": {k: {"href": urls[k], "size": None} for k in assets if k in urls}})
        record = {"query": {"url": url}, "searched_at": None}
    else:
        raise ValueError(f"layer_plan cannot handle {source.id} ({source.driver}/{a.get('mode')})")
    hrefs = {f"{e['item_id']}/{k}": v["href"] for e in acquire for k, v in e["assets"].items()}
    sizes = _sizes(hrefs, size_cache)
    full = windowed = unknown = 0
    for e in acquire:
        for k, v in e["assets"].items():
            v["size"] = sizes.get(f"{e['item_id']}/{k}")
            if v["size"] is None:
                unknown += 1
                continue
            full += v["size"]
            windowed += int(v["size"] * e["fraction"]) + WINDOW_OVERHEAD
    return {
        "kind": "layer", "source": source.id, "needs": _need_dicts(needs), "temporal": "static", "assets": assets, "mode": "single",
        "windows": [{"label": "static", "verdict": "feasible-single" if acquire else "incomplete",
                     "verdict_text": f"{len(acquire)} file(s) intersect the AOI" if acquire else "no tile intersects the AOI"}],
        "acquire": acquire, "estimate": {"n_assets": len(hrefs), "full_bytes": full, "windowed_bytes": windowed, "known": True, "unknown": unknown},
        "explanations": [f"{source.name}: {source.description}",
                         "Static layer — whole file downloaded once to the project cache (server does not support range reads), then clipped." if any(e.get("whole_file") for e in acquire)
                         else "Static layer — only the AOI window of each file is transferred."],
        "query": record,
    }


def file_series_plan(aoi: AOI, source: Source, needs: list[Need], assets: list[str], start: date, end: date, size_cache: dict[str, int]) -> dict:
    """One global/regional file per time window (e.g. CHIRPS monthly), downloaded whole then clipped."""
    a = source.access
    windows = monthly_windows(start, end)
    acquire = []
    for w in windows:
        url = http_driver.render_url(a["url"], yyyy=w.start.strftime("%Y"), mm=w.start.strftime("%m"))
        acquire.append({"window": w.label, "item_id": f"{source.id}-{w.label}", "date": w.start.isoformat(), "group": None, "fraction": 1.0,
                        "assets": {k: {"href": url, "size": None} for k in assets}})
    hrefs = {f"{e['item_id']}/{k}": v["href"] for e in acquire for k, v in e["assets"].items()}
    sizes = _sizes(hrefs, size_cache)
    missing = [e for e in acquire if all(not sizes.get(f"{e['item_id']}/{k}") for k in e["assets"])]
    for e in acquire:
        for k, v in e["assets"].items():
            v["size"] = sizes.get(f"{e['item_id']}/{k}")
    acquire = [e for e in acquire if e not in missing]
    full = sum(v["size"] for e in acquire for v in e["assets"].values())
    return {
        "kind": "file_series", "source": source.id, "needs": _need_dicts(needs), "temporal": "series", "assets": assets, "mode": "single",
        "windows": [{"label": w.label, "verdict": "incomplete" if any(m["window"] == w.label for m in missing) else "feasible-single",
                     "verdict_text": "file not published yet" if any(m["window"] == w.label for m in missing) else "one file, clipped to the AOI"} for w in windows],
        "acquire": acquire, "estimate": {"n_assets": len(acquire), "full_bytes": full, "windowed_bytes": full, "known": True},
        "explanations": [f"{source.name}: {source.description}", "Whole file per window is downloaded once (small, global), then clipped."],
        "query": {"url_template": a["url"]},
    }


def vector_plan(aoi: AOI, source: Source, needs: list[Need], iso3: str | None) -> dict:
    a = source.access
    key = next(iter(a.get("assets", {"file": "file"})))
    too_big = a.get("max_area_km2") and aoi.area_km2 > a["max_area_km2"]
    verdict = "incomplete" if too_big else "feasible-single"
    text = (f"AOI ({aoi.area_km2:,.0f} km²) exceeds this query's limit ({a['max_area_km2']:,} km²); use a smaller AOI"
            if too_big else ("live OpenStreetMap query, clipped to the AOI" if source.driver == "overpass" else "downloaded and clipped to the AOI"))
    acquire = [] if too_big else [{"window": "static", "item_id": source.id, "date": None, "group": None, "fraction": 1.0,
                                   "assets": {key: {"href": a.get("url", "overpass"), "size": None}}}]
    return {
        "kind": "vector", "source": source.id, "needs": _need_dicts(needs), "temporal": "static", "assets": [key], "mode": "single",
        "windows": [{"label": "static", "verdict": verdict, "verdict_text": text}], "acquire": acquire,
        "estimate": {"n_assets": len(acquire), "full_bytes": 0, "windowed_bytes": 0, "known": False},
        "explanations": [f"{source.name}: {source.description}", f"licence: {source.license}"],
        "query": {"driver": source.driver, "query": a.get("query") or a.get("url")},
    }
