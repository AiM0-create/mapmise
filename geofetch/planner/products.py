"""Product planner: dated series of products that are already composited or classified
(MODIS 16-day NDVI, 8-day LST, annual land cover, yearly radar mosaics, monthly burned area).

No scene choice is needed — the provider already did the compositing. Per time window and tile,
take every product whose date falls in the window, and report how much of the AOI they cover.
Reuses the scene Plan structure so executor, status and report stay generic.
"""

from __future__ import annotations

from collections import defaultdict

from shapely.ops import unary_union

from geofetch.aoi import AOI, to_equal_area
from geofetch.drivers.stac import Item
from geofetch.planner.optical import Candidate, Plan, Selection, TileInfo, Window, WindowReport, _pct


def plan_products(aoi: AOI, items: list[Item], windows: list[Window], bands: list[str], min_coverage: float = 0.9) -> Plan:
    aoi_ea = to_equal_area(aoi.geometry)
    aoi_area = aoi_ea.area
    items = [it for it in items if it.geometry.intersects(aoi.geometry)]
    fp = {it.id: to_equal_area(it.geometry) for it in items}
    groups = sorted({it.group or "all" for it in items})
    tiles = [TileInfo(g, 0.0, 0.0, 0.0, 1.0, "footprint-union") for g in groups]
    selections, reports = [], []
    for w in windows:
        w_items = [it for it in items if w.contains(it.datetime)]
        by_group: dict[str, list[Item]] = defaultdict(list)
        for it in w_items:
            by_group[it.group or "all"].append(it)
        cov = unary_union([fp[it.id] for it in w_items]).intersection(aoi_ea).area / aoi_area if w_items else 0.0
        for g in groups:
            for it in sorted(by_group.get(g, []), key=lambda x: x.datetime):
                c = Candidate(it.id, it.date, 0.0, 1.0, 1.0, None)
                selections.append(Selection(w.label, g, c, len(by_group[g]), True, [f"product dated {it.date} (already composited by the provider)"], [c], 1.0))
        missing = sum(1 for g in groups if not by_group.get(g))
        if not w_items:
            verdict, text = "incomplete", "no product published for this window"
        elif cov < min_coverage:
            verdict, text = "incomplete", f"{len(w_items)} product(s) cover {_pct(cov)} of the AOI"
        else:
            verdict, text = "feasible-single", f"{len(w_items)} product(s) cover {_pct(cov)} of the AOI"
        reports.append(WindowReport(w.label, len(w_items), missing, len(w_items), cov, cov, 0, len(w_items), cov, len(w_items), cov, verdict, text))
    explanations = [
        f"{items[0].source_id if items else '?'}: ready-made products ({', '.join(bands)}); the provider already composited or classified them.",
        "Every product dated inside each window is fetched; coverage is the union of their footprints over the AOI.",
    ]
    return Plan(aoi.name, aoi.area_km2, items[0].source_id if items else "?", list(bands), 0.0, min_coverage, min_coverage,
                tiles, selections, reports, explanations)
