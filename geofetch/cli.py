"""geofetch command line.

    geofetch init <dir> --name --aoi --start --end [--objective]
    geofetch plan <dir> [--collection] [--bands] [--cloud-max] [--clear-target] [--refresh]
    geofetch show <dir> [plan-id]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from geofetch.discovery.stac import Query, search
from geofetch.estimate import asset_sizes, estimate
from geofetch.planner.s2_timeseries import Plan, monthly_windows, plan_s2_timeseries
from geofetch.project import Project


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB"


def cmd_init(a: argparse.Namespace) -> int:
    p = Project.init(Path(a.dir), a.name, a.objective or "", Path(a.aoi), date.fromisoformat(a.start), date.fromisoformat(a.end))
    m = p.meta
    print(f"created {p.root / 'project.json'}\n  AOI {m.aoi_area_km2:,.0f} km² from {m.aoi_source}\n  period {m.start} → {m.end}\n  project CRS EPSG:{m.project_epsg}")
    return 0


def cmd_plan(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    aoi = p.aoi()
    start, end = p.period
    bands = [b.strip() for b in a.bands.split(",")]
    q = Query(a.provider, a.collection, tuple(aoi.bbox), start.isoformat(), end.isoformat(), tuple(bands))
    t0 = datetime.now(timezone.utc)
    items, record = search(q, p.cache_dir, refresh=a.refresh)
    print(f"catalogue: {len(items)} items ({'searched' if record['searched_at'] >= t0.isoformat(timespec='seconds') else 'cached from ' + record['searched_at']})")
    plan = plan_s2_timeseries(aoi, items, monthly_windows(start, end), bands, a.cloud_max, a.min_coverage, a.clear_target)

    by_id = {it.id: it for it in items}
    frac = {t.tile: t.aoi_fraction_of_tile for t in plan.tiles}
    single_ids, comp_ids = plan.selected_ids("single"), plan.selected_ids("composite")
    sizes = asset_sizes([by_id[i] for i in comp_ids], bands, p.cache_dir)
    est = {"single": estimate(by_id, single_ids, bands, frac, sizes), "composite": estimate(by_id, comp_ids, bands, frac, sizes)}

    plan_id = t0.strftime("%Y%m%dT%H%M%SZ") + "-" + q.key[:6]
    data = {"id": plan_id, "created": t0.isoformat(timespec="seconds"), "status": "proposed", "query": record,
            "params": {"cloud_max": a.cloud_max, "min_coverage": a.min_coverage, "clear_target": a.clear_target},
            "estimate": est, **plan.to_dict()}
    f = p.save_plan(plan_id, data)
    print_plan(plan, est, plan_id)
    print(f"\nplan written to {f}")
    return 0


def print_plan(plan: Plan, est: dict, plan_id: str) -> None:
    print(f"\nPLAN {plan_id}\n{plan.collection} on {plan.provider} · AOI {plan.aoi_name} ({plan.aoi_km2:,.0f} km², {len(plan.tiles)} tiles) · bands {', '.join(plan.bands)}")
    print(f"cloud ≤ {plan.cloud_max:.0f}% advisory · clear-coverage target {100 * plan.clear_target:.0f}%\n")
    print(f"{'window':8} {'avail':>5} | {'1/tile':>6} {'observed':>8} {'clear':>6} | {'composite':>9} {'clear':>6} | {'naive':>5} {'obs':>6} | verdict")
    for w in plan.windows:
        print(f"{w.window:8} {w.n_scenes_available:5d} | {w.single_scenes:6d} {100*w.single_observed_coverage:7.1f}% {100*w.single_clear_coverage:5.0f}% | "
              f"{w.composite_scenes:9d} {100*w.composite_clear_coverage:5.0f}% | {w.naive_scenes:5d} {100*w.naive_observed_coverage:5.0f}% | {w.verdict}")
    print("\nVerdicts:")
    for w in plan.windows:
        print(f"  {w.window}: {w.verdict_text}")
    s, c = est["single"], est["composite"]
    print(f"\nEstimated transfer (AOI-windowed / full COGs):\n  single scene per tile: {s['assets']} assets, {_gb(s['windowed_bytes'])} / {_gb(s['full_bytes'])}"
          f"\n  composite:             {c['assets']} assets, {_gb(c['windowed_bytes'])} / {_gb(c['full_bytes'])}")
    print("\nWhy:")
    for e in plan.explanations:
        print(f"  - {e}")
    flagged = [x for x in plan.selections if x.selected and not x.meets_threshold]
    if flagged:
        print(f"\n{len(flagged)} of {sum(1 for x in plan.selections if x.selected)} selections flagged, e.g.:")
        for x in flagged[:5]:
            print(f"  {x.window} {x.tile}: {x.selected.item_id}  cc {x.selected.cloud_cover:.0f}%  cov {100*x.selected.coverage:.0f}%  — {'; '.join(x.reasons)}")


def cmd_show(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    plans = p.list_plans()
    if not plans:
        print("no plans yet; run `geofetch plan`")
        return 1
    if a.plan_id is None:
        print(f"project {p.meta.name}: {p.meta.objective or '(no objective)'}\nplans:")
        for pid in plans:
            d = p.load_plan(pid)
            print(f"  {pid}  {d['status']:9} {d['collection']}  verdicts: " + ", ".join(f"{w['window']}={w['verdict']}" for w in d["windows"]))
        return 0
    d = p.load_plan(a.plan_id)
    from geofetch.planner.s2_timeseries import Plan as _P, TileInfo, Selection, Candidate, WindowReport
    plan = _P(d["aoi_name"], d["aoi_km2"], d["provider"], d["collection"], d["bands"], d["cloud_max"], d["min_coverage"], d["clear_target"],
              [TileInfo(**t) for t in d["tiles"]],
              [Selection(s["window"], s["tile"], Candidate(**s["selected"]) if s["selected"] else None, s["n_candidates"], s["meets_threshold"],
                         s["reasons"], [Candidate(**c) for c in s["composite"]], s["composite_expected_clear"]) for s in d["selections"]],
              [WindowReport(**w) for w in d["windows"]], d["explanations"])
    print_plan(plan, d["estimate"], d["id"])
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="geofetch", description="Project-first, reproducible EO data acquisition (prototype)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project directory")
    s.add_argument("dir"); s.add_argument("--name", required=True); s.add_argument("--aoi", required=True, help="GeoJSON/GPKG/Shapefile")
    s.add_argument("--start", required=True); s.add_argument("--end", required=True); s.add_argument("--objective", default="")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("plan", help="search the catalogue and write an acquisition plan")
    s.add_argument("dir"); s.add_argument("--provider", default="earth_search"); s.add_argument("--collection", default="sentinel-2-l2a")
    s.add_argument("--bands", default="red,nir"); s.add_argument("--cloud-max", type=float, default=20.0)
    s.add_argument("--min-coverage", type=float, default=0.9); s.add_argument("--clear-target", type=float, default=0.8)
    s.add_argument("--refresh", action="store_true", help="ignore the cached search")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("show", help="list plans or print one")
    s.add_argument("dir"); s.add_argument("plan_id", nargs="?")
    s.set_defaults(fn=cmd_show)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
