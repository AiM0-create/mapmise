"""Deterministic planner for a Sentinel-2 time series over an AOI.

Given normalised catalogue items, an AOI, and temporal windows, select the
best-available scene per (window, tile) and report AOI coverage per window.
Compared against the "naive" plan (every scene under a cloud threshold) to
quantify the coverage problem described in docs/PRODUCT_DISCOVERY.md §1.

No network access here; pure geometry + rules. Every selection carries the
reasons it was made so the UI can show "why".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from geofetch.aoi import AOI, to_equal_area
from geofetch.discovery.stac import NormalisedItem


@dataclass(frozen=True)
class Window:
    label: str
    start: date
    end: date  # inclusive

    def contains(self, dt: datetime) -> bool:
        d = dt.astimezone(timezone.utc).date()
        return self.start <= d <= self.end


def monthly_windows(start: date, end: date) -> list[Window]:
    windows = []
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
        w_end = min(end, date.fromordinal(nxt.toordinal() - 1))
        windows.append(Window(cur.strftime("%Y-%m"), max(cur, start), w_end))
        cur = nxt
    return windows


@dataclass
class TileInfo:
    tile: str
    extent_km2: float  # approx tile footprint (union of observed footprints)
    aoi_km2: float  # tile ∩ AOI
    exclusive_km2: float  # tile ∩ AOI minus earlier tiles (removes MGRS overlap double counting)
    aoi_fraction_of_tile: float  # (tile ∩ AOI) / tile — used for windowed byte estimates


@dataclass
class Candidate:
    item_id: str
    date: str
    cloud_cover: float
    coverage: float  # fraction of (tile ∩ AOI) covered by the footprint
    clear_fraction: float  # coverage × (1 − cloud/100)
    relative_orbit: int | None


@dataclass
class Selection:
    window: str
    tile: str
    selected: Candidate | None
    n_candidates: int
    meets_threshold: bool
    reasons: list[str] = field(default_factory=list)
    composite: list[Candidate] = field(default_factory=list)  # scenes needed to reach composite_target
    composite_expected_clear: float = 0.0


@dataclass
class WindowReport:
    window: str
    n_scenes_available: int
    planner_scenes: int
    planner_observed_coverage: float  # union(selected footprints) ∩ AOI / AOI
    planner_clear_coverage: float  # Σ exclusive_km2 × coverage × (1 − cc) / AOI
    planner_over_threshold: int  # selections whose best scene exceeds cloud_max
    tiles_without_scene: int
    naive_scenes: int  # scenes with cc ≤ cloud_max (what a browser filter gives)
    naive_observed_coverage: float
    naive_clear_coverage: float
    composite_scenes: int = 0
    composite_clear_coverage: float = 0.0


@dataclass
class Plan:
    aoi_name: str
    aoi_km2: float
    provider: str
    collection: str
    cloud_max: float
    min_coverage: float
    composite_target: float
    tiles: list[TileInfo]
    selections: list[Selection]
    windows: list[WindowReport]
    explanations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _fmt(x: float) -> str:
    return f"{100 * x:5.1f}%"


def plan_s2_timeseries(
    aoi: AOI,
    items: list[NormalisedItem],
    windows: list[Window],
    cloud_max: float = 20.0,
    min_coverage: float = 0.9,
    composite_target: float = 0.8,
) -> Plan:
    aoi_ea = to_equal_area(aoi.geometry)
    aoi_area = aoi_ea.area

    # 1. keep items that actually intersect the AOI (bbox search over-returns)
    items = [it for it in items if it.tile and it.geometry.intersects(aoi.geometry)]
    fp_ea = {it.id: to_equal_area(it.geometry) for it in items}

    # 2. tile geometry ≈ union of all observed footprints for that tile
    by_tile: dict[str, list[NormalisedItem]] = defaultdict(list)
    for it in items:
        by_tile[it.tile].append(it)
    tiles: list[TileInfo] = []
    tile_aoi: dict[str, BaseGeometry] = {}
    covered_so_far = None
    for tile in sorted(by_tile):
        extent = unary_union([fp_ea[it.id] for it in by_tile[tile]])
        inter = extent.intersection(aoi_ea)
        if inter.area < 1e6:  # < 1 km² of AOI in this tile: ignore sliver
            continue
        exclusive = inter if covered_so_far is None else inter.difference(covered_so_far)
        covered_so_far = inter if covered_so_far is None else covered_so_far.union(inter)
        tile_aoi[tile] = inter
        tiles.append(TileInfo(tile, extent.area / 1e6, inter.area / 1e6, exclusive.area / 1e6, inter.area / extent.area))
    excl_km2 = {t.tile: t.exclusive_km2 for t in tiles}

    # 3. per window × tile: rank candidates by clear fraction, select the best
    selections: list[Selection] = []
    reports: list[WindowReport] = []
    for w in windows:
        w_items = [it for it in items if it.tile in tile_aoi and w.contains(it.datetime)]
        sel_fps, sel_clear_km2, over, missing, n_sel = [], 0.0, 0, 0, 0
        comp_n, comp_clear_km2 = 0, 0.0
        for t in tiles:
            cands: list[Candidate] = []
            for it in (x for x in w_items if x.tile == t.tile):
                cov = fp_ea[it.id].intersection(tile_aoi[t.tile]).area / tile_aoi[t.tile].area
                cc = it.cloud_cover if it.cloud_cover is not None else 100.0
                cands.append(Candidate(it.id, it.date, cc, cov, cov * (1 - cc / 100), it.relative_orbit))
            cands.sort(key=lambda c: (-c.clear_fraction, c.cloud_cover, c.date))
            if not cands:
                selections.append(Selection(w.label, t.tile, None, 0, False, ["no acquisition intersects this tile in the window"]))
                missing += 1
                continue
            best = cands[0]
            ok = best.cloud_cover <= cloud_max and best.coverage >= min_coverage
            reasons = [f"highest clear fraction {_fmt(best.clear_fraction)} of {len(cands)} candidates"]
            if best.cloud_cover > cloud_max:
                reasons.append(f"best available exceeds cloud threshold ({best.cloud_cover:.0f}% > {cloud_max:.0f}%)")
                over += 1
            if best.coverage < min_coverage:
                reasons.append(f"footprint covers only {_fmt(best.coverage)} of tile∩AOI (swath edge)")
            # composite mode: add scenes (best first) until expected clear fraction reaches the target.
            # Independence assumption: expected clear = 1 − Π(1 − clear_i). Crude but deterministic and stated.
            comp, remaining = [], 1.0
            for c in cands:
                if remaining <= 1 - composite_target:
                    break
                comp.append(c)
                remaining *= 1 - c.clear_fraction
            comp_clear = 1 - remaining
            comp_n += len(comp)
            comp_clear_km2 += excl_km2[t.tile] * comp_clear
            selections.append(Selection(w.label, t.tile, best, len(cands), ok, reasons, comp, comp_clear))
            sel_fps.append(fp_ea[best.item_id])
            sel_clear_km2 += excl_km2[t.tile] * best.clear_fraction
            n_sel += 1

        # naive comparator: everything under the threshold, as a browser filter would return
        naive = [it for it in w_items if it.cloud_cover is not None and it.cloud_cover <= cloud_max]
        naive_best: dict[str, float] = {}
        for it in naive:
            cov = fp_ea[it.id].intersection(tile_aoi[it.tile]).area / tile_aoi[it.tile].area
            naive_best[it.tile] = max(naive_best.get(it.tile, 0.0), cov * (1 - it.cloud_cover / 100))
        naive_clear = sum(excl_km2[t] * f for t, f in naive_best.items())

        def observed(fps):
            return unary_union(fps).intersection(aoi_ea).area / aoi_area if fps else 0.0

        reports.append(WindowReport(
            window=w.label,
            n_scenes_available=len(w_items),
            planner_scenes=n_sel,
            planner_observed_coverage=observed(sel_fps),
            planner_clear_coverage=sel_clear_km2 * 1e6 / aoi_area,
            planner_over_threshold=over,
            tiles_without_scene=missing,
            naive_scenes=len(naive),
            naive_observed_coverage=observed([fp_ea[it.id] for it in naive]),
            naive_clear_coverage=naive_clear * 1e6 / aoi_area,
            composite_scenes=comp_n,
            composite_clear_coverage=comp_clear_km2 * 1e6 / aoi_area,
        ))

    explanations = [
        f"Collection {items[0].collection if items else '?'} on {items[0].provider if items else '?'}: 10 m optical, red/NIR bands support vegetation indices.",
        f"AOI intersects {len(tiles)} MGRS tiles; one scene per tile per window is the minimum for a complete mosaic.",
        f"Selection rule: per tile and window, the scene maximising footprint coverage × (1 − cloud cover). Threshold {cloud_max:.0f}% cloud is advisory — the best available scene is proposed even when it exceeds it, and flagged.",
        f"Composite mode: per tile and window, scenes are added best-first until expected clear coverage reaches {100*composite_target:.0f}% (independence assumption on scene-level cloud %).",
        "Coverage is computed from catalogue footprints and scene-level cloud percentages; pixel-level cloud masks (SCL) are not consulted at planning time.",
    ]
    return Plan(aoi.name, aoi.area_km2, items[0].provider if items else "?", items[0].collection if items else "?",
                cloud_max, min_coverage, composite_target, tiles, selections, reports, explanations)
