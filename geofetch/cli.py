"""geofetch command line.

    geofetch init <dir> --name --aoi --start --end [--objective]
    geofetch plan <dir> [--collection] [--bands] [--cloud-max] [--clear-target] [--refresh]
    geofetch show <dir> [plan-id]
    geofetch run <dir> <plan-id> [--mode single|composite] [--yes] [--threads N]
    geofetch status <dir> [plan-id]
    geofetch open <dir>            build per-month VRT mosaics and open them (+AOI) in QGIS
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
from geofetch.run import execute, gaps, targets
from geofetch.catalog import Catalog
from geofetch.prepare import build_vrts, open_in_qgis


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


def _latest_plan(p: Project, plan_id: str | None) -> dict:
    plans = p.list_plans()
    if not plans:
        raise SystemExit("no plans yet; run `geofetch plan`")
    return p.load_plan(plan_id or plans[-1])


def cmd_run(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    plan = _latest_plan(p, a.plan_id)
    est = plan["estimate"][a.mode]
    missing = gaps(p, plan, a.mode)
    n_targets = len(targets(plan, a.mode))
    print(f"plan {plan['id']} · mode {a.mode} · {n_targets} scenes × {len(plan['bands'])} bands · "
          f"estimated {_gb(est['windowed_bytes'])} windowed · {len(missing)} assets not yet acquired")
    infeasible = [w["window"] for w in plan["windows"] if w["verdict"] == "infeasible"]
    if infeasible:
        print(f"note: windows {', '.join(infeasible)} are marked infeasible for the clear-coverage target; their best-available scenes are still included")
    if not missing:
        print("nothing to do")
        return 0
    if not a.yes:
        ans = input("proceed? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted; plan unchanged")
            return 1
    plan["status"] = "approved"
    p.save_plan(plan["id"], plan)
    r = execute(p, plan, a.mode, a.threads)
    print(f"done: fetched {r['fetched']}, skipped {r['skipped']} (already present), failed {r['failed']} → status {plan['status']}")
    return 0 if r["failed"] == 0 else 2


def cmd_status(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    cat = Catalog(p.root, p.meta.name)
    rows = cat.summary()
    print(f"project {p.meta.name} · {len(rows)} scenes in catalogue ({p.catalog_dir / 'catalog.json'})")
    by_w: dict[str, list] = {}
    for r in rows:
        by_w.setdefault(r["window"], []).append(r)
    for w in sorted(by_w):
        print(f"  {w}: " + ", ".join(f"{r['tile'].replace('MGRS-', '')}[{'+'.join(r['bands'])}]" for r in by_w[w]))
    if p.list_plans():
        plan = _latest_plan(p, a.plan_id)
        for mode in ("single", "composite"):
            g = gaps(p, plan, mode)
            print(f"  plan {plan['id']} {mode}: {len(targets(plan, mode)) * len(plan['bands']) - len(g)} assets present, {len(g)} missing"
                  + (" — e.g. " + ", ".join(f"{x['window']} {x['tile'].replace('MGRS-', '')} {x['band']}" for x in g[:4]) if g else ""))
    return 0


def cmd_open(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    vrts = build_vrts(Catalog(p.root, p.meta.name))
    if not vrts:
        print("catalogue is empty; run `geofetch run` first")
        return 1
    print("mosaics:"); [print(f"  {v}") for v in vrts]
    if a.no_launch:
        return 0
    if open_in_qgis([p.aoi_file, *vrts]):
        print(f"launched QGIS with {len(vrts)} mosaics + AOI; static STAC catalogue: {p.catalog_dir / 'catalog.json'}")
        return 0
    print("qgis not found on PATH; open the files above manually")
    return 1


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

    s = sub.add_parser("run", help="approve and execute a plan")
    s.add_argument("dir"); s.add_argument("plan_id", nargs="?"); s.add_argument("--mode", choices=["single", "composite"], default="single")
    s.add_argument("--yes", "-y", action="store_true"); s.add_argument("--threads", type=int, default=12)
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="what the project has, and what the latest plan still lacks")
    s.add_argument("dir"); s.add_argument("plan_id", nargs="?")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("open", help="build VRT mosaics per month/band and open them in QGIS")
    s.add_argument("dir"); s.add_argument("--no-launch", action="store_true", help="only build the VRTs")
    s.set_defaults(fn=cmd_open)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
