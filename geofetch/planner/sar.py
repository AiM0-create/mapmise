"""Radar scene planner: cloud-independent, per orbit pass, per time window.

A Sentinel-1 pass is all frames of one relative orbit on one date. Rule set:
  * a pass's coverage = union of its footprints ∩ AOI;
  * for `pair` windows (before/after), the SAME relative orbit and orbit direction must be
    used in every window so that geometry stays constant and only the ground changes;
  * within a window pick the pass with the best coverage, ties broken by closeness to the
    window's reference date (the event for pairs, the middle of the window otherwise).
Reuses the optical Plan structure with cloud_cover = 0 so "clear" == coverage.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from shapely.ops import unary_union

from geofetch.aoi import AOI, to_equal_area
from geofetch.drivers.stac import Item
from geofetch.planner.optical import Candidate, Plan, Selection, TileInfo, Window, WindowReport, _pct


def plan_sar(aoi: AOI, items: list[Item], windows: list[Window], bands: list[str], same_orbit: bool = True,
             min_coverage: float = 0.9, reference: dict[str, date] | None = None, prefer: set[str] | None = None) -> Plan:
    """prefer: item ids already in the user's library — used only to break ties between equally good orbits."""
    aoi_ea = to_equal_area(aoi.geometry)
    aoi_area = aoi_ea.area
    items = [it for it in items if it.geometry.intersects(aoi.geometry) and it.relative_orbit is not None]
    # passes: (orbit, state, date) -> items
    passes: dict[tuple[int, str, str], list[Item]] = defaultdict(list)
    for it in items:
        passes[(it.relative_orbit, it.orbit_state or "?", it.date)].append(it)
    cov = {k: unary_union([to_equal_area(i.geometry) for i in v]).intersection(aoi_ea).area / aoi_area for k, v in passes.items()}
    orbits = sorted({(o, s) for o, s, _ in passes})

    def ref_date(w: Window) -> date:
        if reference and w.label in reference:
            return reference[w.label]
        return date.fromordinal((w.start.toordinal() + w.end.toordinal()) // 2)

    def best_pass(orbit, w: Window):
        cands = [k for k in passes if (k[0], k[1]) == orbit and w.start <= date.fromisoformat(k[2]) <= w.end]
        return max(cands, key=lambda k: (cov[k], -abs((date.fromisoformat(k[2]) - ref_date(w)).days)), default=None)

    # choose the orbit: for pairs, the one whose worst window coverage is best; otherwise per window
    chosen_orbit = None
    if same_orbit and orbits:
        scored = []
        for o in orbits:
            per_w = [best_pass(o, w) for w in windows]
            if all(per_w):
                held = sum(1 for k in per_w for it in passes[k] if prefer and it.id in prefer)
                scored.append((round(min(cov[k] for k in per_w), 3), held, o))
        if scored:
            chosen_orbit = max(scored)[-1]

    tiles = [TileInfo(f"orbit-{o}-{s}", 0.0, aoi_area / 1e6, aoi_area / 1e6, 1.0, "footprint-union") for o, s in orbits]
    selections, reports = [], []
    for w in windows:
        pick = best_pass(chosen_orbit, w) if chosen_orbit else max(
            (k for k in passes if w.start <= date.fromisoformat(k[2]) <= w.end), key=lambda k: cov[k], default=None)
        n_avail = sum(1 for k in passes if w.start <= date.fromisoformat(k[2]) <= w.end)
        if pick is None:
            reports.append(WindowReport(w.label, n_avail, 1, 0, 0.0, 0.0, 0, 0, 0.0, 0, 0.0, "incomplete", "no radar pass covers the AOI in this window"))
            selections.append(Selection(w.label, "orbit-none", None, n_avail, False, ["no pass in window"]))
            continue
        o, st, d = pick
        c = cov[pick]
        reasons = [f"orbit {o} {st}, {len(passes[pick])} frame(s) on {d}, covers {_pct(c)} of AOI"]
        if same_orbit:
            reasons.append("same relative orbit and direction kept across windows so acquisition geometry is constant")
        if c < min_coverage:
            reasons.append(f"coverage below {_pct(min_coverage)} — AOI straddles swaths; consider a second orbit")
        for it in passes[pick]:
            selections.append(Selection(w.label, f"orbit-{o}-{st}", Candidate(it.id, d, 0.0, c, c, o), n_avail, c >= min_coverage, reasons,
                                        [Candidate(it.id, d, 0.0, c, c, o)], c))
        verdict = "feasible-single" if c >= min_coverage else "incomplete"
        text = f"orbit {o} {st} on {d} covers {_pct(c)} of the AOI" + ("" if c >= min_coverage else " (partial)")
        reports.append(WindowReport(w.label, n_avail, 0 if c >= min_coverage else 1, len(passes[pick]), c, c, 0, len(passes[pick]), c, n_avail, c, verdict, text))

    explanations = [
        f"{items[0].source_id if items else '?'}: radar backscatter, bands {', '.join(bands)}; cloud-independent.",
        "A pass = all frames of one relative orbit on one date; coverage is the union of frame footprints ∩ AOI.",
        "Same relative orbit + direction across windows (pairs) so that geometry is constant and only the ground changes." if same_orbit else "Best coverage per window.",
    ]
    return Plan(aoi.name, aoi.area_km2, items[0].source_id if items else "?", list(bands), 0.0, min_coverage, min_coverage, tiles, selections, reports, explanations)
