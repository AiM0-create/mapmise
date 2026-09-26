"""Optical scene planner: cloud-aware, per tile, per time window.

Works for any registry source with `group_by` = a tile id and `eo:cloud_cover` metadata.
Given normalised catalogue items, an AOI and temporal windows:
  * select the best-available scene per (window, tile);
  * estimate how many scenes a composite needs to reach a clear-coverage target;
  * issue a feasibility verdict per window;
  * report what a naive cloud-threshold filter would have given, as an explanation.

Pure geometry + rules, no network. Every selection carries the reasons it was made.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone

from pyproj import Transformer
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union

from geofetch.aoi import AOI, EQUAL_AREA_CRS, to_equal_area
from geofetch.drivers.stac import Item as NormalisedItem


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
        windows.append(Window(cur.strftime("%Y-%m"), max(cur, start), min(end, date.fromordinal(nxt.toordinal() - 1))))
        cur = nxt
    return windows


def yearly_windows(start: date, end: date) -> list[Window]:
    return [Window(str(y), max(date(y, 1, 1), start), min(date(y, 12, 31), end)) for y in range(start.year, end.year + 1)]


def pre_post_windows(event: date, pre_days: int = 30, post_days: int = 30, gap_days: int = 0) -> list[Window]:
    """Two windows around an event date: [event−pre, event−gap] and [event+gap, event+post]."""
    pre_end = date.fromordinal(event.toordinal() - gap_days)
    post_start = date.fromordinal(event.toordinal() + gap_days)
    return [Window("pre", date.fromordinal(pre_end.toordinal() - pre_days), pre_end),
            Window("post", post_start, date.fromordinal(post_start.toordinal() + post_days))]


@dataclass
class TileInfo:
    tile: str
    extent_km2: float
    aoi_km2: float  # tile ∩ AOI
    exclusive_km2: float  # tile ∩ AOI minus earlier tiles (MGRS tiles overlap ~10 km)
    aoi_fraction_of_tile: float  # drives the windowed byte estimate
    extent_source: str  # "proj:bbox" or "footprint-union"


@dataclass
class Candidate:
    item_id: str
    date: str
    cloud_cover: float
    coverage: float  # fraction of (tile ∩ AOI) inside the footprint
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
    composite: list[Candidate] = field(default_factory=list)  # best-first scenes until clear target is met
    composite_expected_clear: float = 0.0


@dataclass
class WindowReport:
    window: str
    n_scenes_available: int
    tiles_without_scene: int
    single_scenes: int
    single_observed_coverage: float  # union(selected footprints) ∩ AOI / AOI
    single_clear_coverage: float  # Σ exclusive × coverage × (1 − cc) / AOI  (expectation, not a pixel mask)
    single_over_threshold: int
    composite_scenes: int
    composite_clear_coverage: float
    naive_scenes: int  # scenes with cc ≤ cloud_max — what a browser filter returns
    naive_observed_coverage: float
    verdict: str  # feasible-single | feasible-composite | infeasible | incomplete
    verdict_text: str


@dataclass
class Plan:
    aoi_name: str
    aoi_km2: float
    source: str
    bands: list[str]
    cloud_max: float
    min_coverage: float
    clear_target: float
    tiles: list[TileInfo]
    selections: list[Selection]
    windows: list[WindowReport]
    explanations: list[str]

    def to_dict(self) -> dict:
        return asdict(self)

    def acquire(self, mode: str = "single") -> list[tuple[str, str]]:
        """(window, item_id) pairs for the chosen mode."""
        pairs = set()
        for s in self.selections:
            if mode == "composite":
                pairs |= {(s.window, c.item_id) for c in s.composite}
            elif s.selected:
                pairs.add((s.window, s.selected.item_id))
        return sorted(pairs)

    def selected_ids(self, mode: str = "single") -> list[str]:
        if mode == "composite":
            return sorted({c.item_id for s in self.selections for c in s.composite})
        return sorted({s.selected.item_id for s in self.selections if s.selected})


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _tile_extent(items: list[NormalisedItem]) -> tuple[BaseGeometry, str]:
    """True tile extent from proj:bbox when present, else the union of observed footprints."""
    for it in items:
        if it.tile_bbox and it.epsg:
            tr = Transformer.from_crs(f"EPSG:{it.epsg}", EQUAL_AREA_CRS, always_xy=True).transform
            return transform(tr, box(*it.tile_bbox)), "proj:bbox"
    return unary_union([to_equal_area(it.geometry) for it in items]), "footprint-union"


def plan_optical(
    aoi: AOI,
    items: list[NormalisedItem],
    windows: list[Window],
    bands: list[str],
    cloud_max: float = 20.0,
    min_coverage: float = 0.9,
    clear_target: float = 0.8,
) -> Plan:
    aoi_ea = to_equal_area(aoi.geometry)
    aoi_area = aoi_ea.area
    source = items[0].source_id if items else "?"

    # 1. keep items whose footprint actually intersects the AOI (bbox search over-returns)
    items = [it for it in items if it.group and it.geometry.intersects(aoi.geometry)]
    fp_ea = {it.id: to_equal_area(it.geometry) for it in items}

    # 2. tiles. Two geometries per tile:
    #    data extent = union of real footprints (what the tile actually images) — drives coverage;
    #    grid extent = proj:bbox of the raster (what the file spans) — drives the windowed byte estimate.
    #    Tiles overlap (MGRS ~10 km, Landsat paths much more), so each tile claims the AOI area it covers
    #    that better tiles have not already claimed; tiles left with < 1 % of the AOI are redundant and dropped.
    by_tile: dict[str, list[NormalisedItem]] = defaultdict(list)
    for it in items:
        by_tile[it.group].append(it)
    data_extent = {t: unary_union([fp_ea[i.id] for i in lst]) for t, lst in by_tile.items()}
    tile_inter = {t: data_extent[t].intersection(aoi_ea) for t in by_tile}

    def quality(t: str) -> float:  # AOI area imaged × mean footprint coverage of that area across all scenes
        inter = tile_inter[t]
        if inter.area == 0:
            return 0.0
        return inter.area * sum(fp_ea[i.id].intersection(inter).area for i in by_tile[t]) / (inter.area * len(by_tile[t]))

    tiles: list[TileInfo] = []
    tile_aoi: dict[str, BaseGeometry] = {}
    dropped: list[str] = []
    covered = None
    for tile in sorted(by_tile, key=lambda t: (-quality(t), t)):
        inter = tile_inter[tile]
        exclusive = inter if covered is None else inter.difference(covered)
        if exclusive.area < 0.01 * aoi_area:
            dropped.append(tile)
            continue
        covered = inter if covered is None else covered.union(inter)
        grid, source_of_extent = _tile_extent(by_tile[tile])
        tile_aoi[tile] = inter
        tiles.append(TileInfo(tile, grid.area / 1e6, inter.area / 1e6, exclusive.area / 1e6,
                              min(1.0, grid.intersection(aoi_ea).area / grid.area) if grid.area else 1.0, source_of_extent))
    excl = {t.tile: t.exclusive_km2 * 1e6 for t in tiles}

    def observed(fps: list[BaseGeometry]) -> float:
        return unary_union(fps).intersection(aoi_ea).area / aoi_area if fps else 0.0

    # 3. per window × tile
    selections: list[Selection] = []
    reports: list[WindowReport] = []
    for w in windows:
        w_items = [it for it in items if it.group in tile_aoi and w.contains(it.datetime)]
        sel_fps: list[BaseGeometry] = []
        single_clear = comp_clear = 0.0
        over = missing = comp_n = 0
        for t in tiles:
            cands = []
            for it in (x for x in w_items if x.group == t.tile):
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
            reasons = [f"highest clear fraction {_pct(best.clear_fraction)} of {len(cands)} candidates"]
            if best.cloud_cover > cloud_max:
                reasons.append(f"best available exceeds cloud threshold ({best.cloud_cover:.0f}% > {cloud_max:.0f}%)")
                over += 1
            if best.coverage < min_coverage:
                reasons.append(f"footprint covers only {_pct(best.coverage)} of tile∩AOI (swath edge)")
            # composite: add best-first until expected clear ≥ target. Independence assumption, stated in explanations.
            comp, remaining = [], 1.0
            for c in cands:
                if remaining <= 1 - clear_target:
                    break
                comp.append(c)
                remaining *= 1 - c.clear_fraction
            selections.append(Selection(w.label, t.tile, best, len(cands), ok, reasons, comp, 1 - remaining))
            sel_fps.append(fp_ea[best.item_id])
            single_clear += excl[t.tile] * best.clear_fraction
            comp_clear += excl[t.tile] * (1 - remaining)
            comp_n += len(comp)

        naive = [it for it in w_items if it.cloud_cover is not None and it.cloud_cover <= cloud_max]
        single_clear_f, comp_clear_f = single_clear / aoi_area, comp_clear / aoi_area
        n_single = len(tiles) - missing
        obs = observed(sel_fps)
        if obs < min_coverage:
            verdict, text = "incomplete", (f"scenes in this window image only {_pct(obs)} of the AOI"
                                           + (f" ({missing} of {len(tiles)} tiles had no acquisition)" if missing else ""))
        elif single_clear_f >= clear_target:
            verdict, text = "feasible-single", f"one scene per tile reaches {_pct(single_clear_f)} expected clear coverage"
        elif comp_clear_f >= clear_target:
            verdict, text = "feasible-composite", f"single scenes give {_pct(single_clear_f)} clear; a {comp_n}-scene composite reaches {_pct(comp_clear_f)}"
        else:
            verdict, text = "infeasible", (f"all {len(w_items)} scenes combined reach only {_pct(comp_clear_f)} expected clear "
                                           f"(target {_pct(clear_target)}); consider SAR, a longer window, or coarser daily optical")
        reports.append(WindowReport(
            w.label, len(w_items), missing, n_single, observed(sel_fps), single_clear_f, over, comp_n, comp_clear_f,
            len(naive), observed([fp_ea[it.id] for it in naive]), verdict, text,
        ))

    explanations = [
        f"{source}: optical scenes, bands {', '.join(bands)}.",
        f"AOI needs {len(tiles)} tile(s) for a complete mosaic" + (f" (overlapping tile(s) {', '.join(dropped)} add < 1% and are not used)" if dropped else "") + ".",
        f"Selection rule per tile and window: maximise footprint coverage × (1 − cloud cover). "
        f"The {cloud_max:.0f}% cloud threshold is advisory: the best available scene is proposed even when it exceeds it, and flagged.",
        f"Composite mode adds scenes best-first until expected clear coverage ≥ {_pct(clear_target)} "
        "(assumes scene-level cloud percentages are independent — an estimate, not a pixel mask).",
        "Naive comparison shows what a plain 'cloud ≤ threshold' catalogue filter would have returned.",
    ]
    return Plan(aoi.name, aoi.area_km2, source, list(bands), cloud_max, min_coverage, clear_target,
                tiles, selections, reports, explanations)
