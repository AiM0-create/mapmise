"""geofetch command line.

    geofetch init <dir> --name --aoi --start --end [--objective] [--template]
    geofetch requirements <dir> [--template]      what the objective needs, and why
    geofetch plan <dir> [--requirement id] [--event YYYY-MM-DD] [--collection] [--bands] [--cloud-max] [--clear-target] [--refresh]
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
from geofetch.objectives import TEMPLATES, resolve
from geofetch.planner.s2_timeseries import Plan, monthly_windows, plan_s2_timeseries, pre_post_windows
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
    return set_requirements(p, a.template)


def set_requirements(p: Project, template_id: str | None) -> int:
    """Resolve the objective to a template (explicit id wins), store its requirements, print them with reasons."""
    tpl, matches = resolve(p.meta.objective, template_id)
    if tpl is None:
        if not p.meta.objective:
            print("\nno objective given; choose a template: " + ", ".join(TEMPLATES) + "  (geofetch requirements <dir> --template <id>)")
        elif matches:
            print("\nobjective is ambiguous between: " + ", ".join(f"{m.template.id} ({', '.join(m.hits)})" for m in matches)
                  + "\nchoose one: geofetch requirements <dir> --template <id>")
        else:
            print(f"\nno template matches the objective {p.meta.objective!r}; known templates: " + ", ".join(TEMPLATES)
                  + "\nchoose one: geofetch requirements <dir> --template <id>")
        return 1
    p.meta.template = tpl.id
    p.meta.requirements = [r.to_dict() for r in tpl.requirements]
    p.save()
    how = f"matched on {', '.join(matches[0].hits)}" if matches else "chosen explicitly"
    print(f"\nobjective → {tpl.name} [{tpl.id}] ({how})")
    for r in tpl.requirements:
        what = f"{r.collection} {','.join(r.bands)} {r.temporal}" if r.collection else "—"
        flag = "" if r.supported else f"   ✗ not acquirable in prototype: {r.unsupported_reason}"
        print(f"  {r.id:16} {r.priority:11} {r.name}\n{'':18}{what}{flag}")
        for w in r.why:
            print(f"{'':18}• {w}")
    for n in tpl.notes:
        print(f"  note: {n}")
    return 0


def cmd_requirements(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    if a.objective:
        p.meta.objective = a.objective
        p.save()
    return set_requirements(p, a.template)


def cmd_plan(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    aoi = p.aoi()
    start, end = p.period
    # requirement supplies the defaults; explicit flags override
    req = None
    reqs = {r["id"]: r for r in p.meta.requirements}
    if a.requirement:
        if a.requirement not in reqs:
            raise SystemExit(f"unknown requirement {a.requirement!r}; project has: {', '.join(reqs) or 'none (run geofetch requirements)'}")
        req = reqs[a.requirement]
    elif reqs and not (a.collection or a.bands):
        req = next((r for r in reqs.values() if r["supported"] and r["priority"] == "required"), None) or next(iter(reqs.values()))
    if req and not req["supported"]:
        raise SystemExit(f"requirement {req['id']!r} is not acquirable in this prototype: {req['unsupported_reason']}")
    provider = a.provider or (req["provider"] if req else "earth_search")
    collection = a.collection or (req["collection"] if req else "sentinel-2-l2a")
    bands = [b.strip() for b in a.bands.split(",")] if a.bands else (list(req["bands"]) if req else ["red", "nir"])
    cloud_max = a.cloud_max if a.cloud_max is not None else (req["cloud_max"] if req and req["cloud_max"] is not None else 20.0)
    temporal = req["temporal"] if req else "monthly"
    if temporal == "pre_post":
        if not a.event:
            raise SystemExit(f"requirement {req['id']!r} needs an event date: --event YYYY-MM-DD")
        windows = pre_post_windows(date.fromisoformat(a.event), a.pre_days, a.post_days)
        start, end = windows[0].start, windows[-1].end
    else:
        windows = monthly_windows(start, end)
    if req:
        print(f"requirement {req['id']}: {req['name']}")
    q = Query(provider, collection, tuple(aoi.bbox), start.isoformat(), end.isoformat(), tuple(bands))
    t0 = datetime.now(timezone.utc)
    items, record = search(q, p.cache_dir, refresh=a.refresh)
    print(f"catalogue: {len(items)} items ({'searched' if record['searched_at'] >= t0.isoformat(timespec='seconds') else 'cached from ' + record['searched_at']})")
    plan = plan_s2_timeseries(aoi, items, windows, bands, cloud_max, a.min_coverage, a.clear_target)
    if req:
        plan.explanations = [f"Requirement '{req['id']}' ({req['priority']}) for objective template '{p.meta.template}':"] + [f"  • {w}" for w in req["why"]] + plan.explanations

    by_id = {it.id: it for it in items}
    frac = {t.tile: t.aoi_fraction_of_tile for t in plan.tiles}
    single_ids, comp_ids = plan.selected_ids("single"), plan.selected_ids("composite")
    sizes = asset_sizes([by_id[i] for i in comp_ids], bands, p.cache_dir)
    est = {"single": estimate(by_id, single_ids, bands, frac, sizes), "composite": estimate(by_id, comp_ids, bands, frac, sizes)}

    plan_id = t0.strftime("%Y%m%dT%H%M%SZ") + "-" + q.key[:6]
    data = {"id": plan_id, "created": t0.isoformat(timespec="seconds"), "status": "proposed", "query": record,
            "requirement": req["id"] if req else None, "temporal": temporal, "event": a.event,
            "params": {"cloud_max": cloud_max, "min_coverage": a.min_coverage, "clear_target": a.clear_target},
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
    if p.meta.requirements:
        print(f"\nobjective: {p.meta.objective or '(none)'}  [{p.meta.template}]")
        plans = [p.load_plan(pid) for pid in p.list_plans()]
        for r in p.meta.requirements:
            line = f"  {r['id']:16} {r['priority']:11} "
            if not r["supported"]:
                print(line + f"✗ not acquirable in prototype — {r['unsupported_reason']}")
                continue
            mine = [pl for pl in plans if pl.get("requirement") == r["id"]]
            if not mine:
                print(line + f"○ no plan yet — geofetch plan {p.root} --requirement {r['id']}" + (" --event YYYY-MM-DD" if r["temporal"] == "pre_post" else ""))
                continue
            pl = mine[-1]
            g = gaps(p, pl, "single")
            n = len(targets(pl, "single")) * len(pl["bands"])
            verdicts = ", ".join(f"{w['window']}={w['verdict'].replace('feasible-', '')}" for w in pl["windows"])
            mark = "✓" if not g else "◐"
            print(line + f"{mark} {n - len(g)}/{n} assets (plan {pl['id']}, {pl['status']}) · {verdicts}")
    elif p.list_plans():
        plan = _latest_plan(p, a.plan_id)
        g = gaps(p, plan, "single")
        print(f"  plan {plan['id']}: {len(targets(plan, 'single')) * len(plan['bands']) - len(g)} assets present, {len(g)} missing")
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
    s.add_argument("--template", help="objective template id (overrides keyword matching)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("requirements", help="resolve the objective to data requirements (with reasons)")
    s.add_argument("dir"); s.add_argument("--template", help="one of: " + ", ".join(TEMPLATES))
    s.add_argument("--objective", help="set or replace the project objective text")
    s.set_defaults(fn=cmd_requirements)

    s = sub.add_parser("plan", help="search the catalogue and write an acquisition plan")
    s.add_argument("dir"); s.add_argument("--requirement", help="requirement id from `geofetch requirements`")
    s.add_argument("--event", help="event date for pre/post requirements (YYYY-MM-DD)")
    s.add_argument("--pre-days", type=int, default=30); s.add_argument("--post-days", type=int, default=30)
    s.add_argument("--provider"); s.add_argument("--collection"); s.add_argument("--bands"); s.add_argument("--cloud-max", type=float)
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
