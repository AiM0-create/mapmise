"""geofetch command line.

    geofetch init <dir> --name --aoi --start --end [--objective]
    geofetch ask  <dir> "what you want to analyse" [--event YYYY-MM-DD] [--dry-run] [--yes] [--skip theme:temporal] [--use theme:temporal=source]
    geofetch run  <dir> [request-id] [--yes]        execute the plans of a request (default: latest)
    geofetch status <dir>                            what the project has, per need; what is missing and why
    geofetch open <dir>                              per-window VRT mosaics + vectors + AOI in QGIS
    geofetch report <dir>                            write REPORT.md — the human-readable acquisition record
    geofetch sources [--theme t]                     list the registry
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from geofetch.catalog import Catalog
from geofetch.drivers import gdacs
from geofetch.plans import file_series_plan, layer_plan, scene_plan, vector_plan
from geofetch.prepare import build_vrts, open_in_qgis, vector_files
from geofetch.project import Project
from geofetch.registry import Need, Source, load_sources
from geofetch.report import write_report
from geofetch.resolver import Resolution, parse_ask, resolve_needs
from geofetch.run import execute, gaps


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 5e7 else f"{n / 1e6:.0f} MB"


def _ensure_country(p: Project) -> str | None:
    if p.meta.country_iso3 is None:
        from geofetch.geo import country_iso3
        c = p.aoi().geometry.centroid
        try:
            p.meta.country_iso3 = country_iso3(c.x, c.y)
        except Exception as e:  # noqa: BLE001 — offline is fine; per-country sources will be unmet
            print(f"warning: country lookup failed ({e}); per-country sources unavailable")
        p.save()
    return p.meta.country_iso3


def cmd_init(a: argparse.Namespace) -> int:
    p = Project.init(Path(a.dir), a.name, a.objective or "", Path(a.aoi), date.fromisoformat(a.start), date.fromisoformat(a.end))
    iso3 = _ensure_country(p)
    m = p.meta
    print(f"created {p.root / 'project.json'}\n  AOI {m.aoi_area_km2:,.0f} km² ({iso3 or 'country unknown'}) from {m.aoi_source}\n  period {m.start} → {m.end}\n  project CRS EPSG:{m.project_epsg}")
    if a.objective:
        print(f"\nnext: geofetch ask {p.root} \"{a.objective}\"")
    return 0


def _assets_for(source: Source, needs: list[Need]) -> list[str]:
    ta = source.access.get("theme_assets", {})
    out: list[str] = []
    for n in needs:
        for k in ta.get(n.theme, source.access.get("default_assets", list(source.access.get("assets", {})))):
            if k not in out:
                out.append(k)
    return out


def _build_plans(p: Project, resolutions: list[Resolution], event: date | None, a: argparse.Namespace) -> tuple[list[dict], list[tuple[Need, str]]]:
    """Group resolved needs by (source, temporal class) and build one plan per group. Returns (plans, unmet)."""
    aoi, (start, end) = p.aoi(), p.period
    sizes = p.size_cache()
    groups: dict[tuple[str, str], list[Need]] = {}
    unmet: list[tuple[Need, str]] = []
    for r in resolutions:
        if r.chosen is None:
            unmet.append((r.need, r.unmet_reason or "no source"))
            continue
        if r.need.temporal == "pair" and event is None:
            unmet.append((r.need, "needs an event date: re-run with --event YYYY-MM-DD"))
            continue
        groups.setdefault((r.chosen.id, "pair" if r.need.temporal == "pair" else r.need.temporal), []).append(r.need)
    sources = load_sources()
    plans = []
    for (sid, tclass), needs in groups.items():
        s = sources[sid]
        try:
            if s.shape == "series" and s.driver == "stac":
                plan = scene_plan(aoi, s, needs, _assets_for(s, needs), start, end, p.cache_dir, sizes, event, a.pre_days, a.post_days,
                                  cloud_max=a.cloud_max, clear_target=a.clear_target, mode=a.mode)
            elif s.shape == "series" and s.driver == "http":
                plan = file_series_plan(aoi, s, needs, list(s.access["assets"]), start, end, sizes)
            elif s.shape == "layer" and s.kind == "raster":
                plan = layer_plan(aoi, s, needs, list(s.access["assets"]), p.cache_dir, sizes, p.meta.country_iso3)
            else:
                plan = vector_plan(aoi, s, needs, p.meta.country_iso3)
        except Exception as e:  # noqa: BLE001 — a failing source must not sink the request
            for n in needs:
                unmet.append((n, f"{sid}: {type(e).__name__}: {str(e)[:120]}"))
            continue
        plan["id"] = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{sid}"
        plan["created"], plan["status"], plan["event"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), "proposed", event.isoformat() if event else None
        p.save_plan(plan["id"], plan)
        plans.append(plan)
    p.save_size_cache(sizes)
    return plans, unmet


def print_request(plans: list[dict], unmet: list[tuple[Need, str]], verbose: bool = True) -> None:
    total = sum(pl["estimate"]["windowed_bytes"] for pl in plans)
    n_assets = sum(pl["estimate"]["n_assets"] for pl in plans)
    print(f"\n{'need':28} {'source':26} {'files':>5} {'est.':>8}  verdicts")
    for pl in plans:
        needs = ", ".join(f"{n['theme']}/{n['temporal']}" for n in pl["needs"])
        est = _gb(pl["estimate"]["windowed_bytes"]) if pl["estimate"]["known"] else "live"
        verdicts = ", ".join(f"{w['label']}={w['verdict'].replace('feasible-', '')}" for w in pl["windows"])
        print(f"{needs[:28]:28} {pl['source'][:26]:26} {pl['estimate']['n_assets']:5d} {est:>8}  {verdicts}")
    for n, why in unmet:
        print(f"{(n.theme + '/' + n.temporal)[:28]:28} {'— unmet':26} {'':5} {'':8}  {why}")
    print(f"\ntotal: {len(plans)} sources, {n_assets} files, ≈ {_gb(total)} to transfer (AOI-windowed; vector queries not counted)")
    if verbose:
        print("\nWhy each dataset:")
        for pl in plans:
            for n in pl["needs"]:
                print(f"  {n['theme']}/{n['temporal']} ({n['priority']}) ← {pl['source']}: {n['why']}")
        flagged = [(pl["source"], w) for pl in plans for w in pl["windows"] if w["verdict"] in ("infeasible", "incomplete", "feasible-composite")]
        if flagged:
            print("\nVerdicts needing your attention:")
            for sid, w in flagged:
                print(f"  {sid} {w['label']}: {w['verdict_text']}")


def cmd_ask(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    _ensure_country(p)
    text = a.text or p.meta.objective
    if not text:
        raise SystemExit("nothing asked and the project has no objective")
    ask = parse_ask(text)
    if not ask.needs:
        raise SystemExit(f"no ask rule matched {text!r}. Rules recognise e.g. flood, drought, ndvi, urban, reservoir, slope, road, rainfall — "
                         "or add a rule to geofetch/registry/asks.yaml")
    skip = set(a.skip or [])
    needs = [n for n in ask.needs if n.key not in skip]
    print(f"ask: {text}\nmatched rules: {', '.join(ask.matched_rules)} → {len(needs)} needs" + (f" (skipped {', '.join(skip)})" if skip else ""))
    start, end = p.meta.start, p.meta.end
    overrides = dict(kv.split("=", 1) for kv in (a.use or []))
    resolutions = resolve_needs(needs, p.aoi().bbox, start, end, p.meta.country_iso3, overrides)

    event = date.fromisoformat(a.event) if a.event else None
    if event is None and any(n.temporal == "pair" for n in needs):
        code = load_sources()["gdacs-events"].access["types"].get(ask.event_type or "", None)
        if code:
            try:
                evs = gdacs.events(code, p.aoi().bbox, start, end)
            except Exception as e:  # noqa: BLE001
                evs = []
                print(f"warning: GDACS lookup failed ({e})")
            if evs:
                print(f"\nGDACS {ask.event_type} events near the AOI in the period (pick one and re-run with --event):")
                for e in evs[:6]:
                    print(f"  {e.start} → {e.end}  {e.alert:6} {e.name}  (~{e.distance_deg}° from AOI centre)")
            else:
                print(f"\nno GDACS {ask.event_type} event within ~3° of the AOI in the period; give the date with --event YYYY-MM-DD")

    plans, unmet = _build_plans(p, resolutions, event, a)
    req_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p.save_request(req_id, {"id": req_id, "ask": text, "rules": ask.matched_rules, "event": event.isoformat() if event else None,
                            "needs": [{"theme": n.theme, "temporal": n.temporal, "priority": n.priority, "why": n.why} for n in needs],
                            "resolutions": [{"need": r.need.key, "chosen": r.chosen.id if r.chosen else None,
                                             "candidates": [c.source.id for c in r.candidates], "reasons": r.candidates[0].reasons if r.candidates else []} for r in resolutions],
                            "plans": [pl["id"] for pl in plans], "unmet": [{"need": n.key, "why": w} for n, w in unmet], "status": "proposed"})
    print_request(plans, unmet)
    print(f"\nrequest {req_id} written ({len(plans)} plans)")
    if a.dry_run or not plans:
        return 0
    return _run_plans(p, req_id, plans, a.yes, a.threads)


def _run_plans(p: Project, req_id: str, plans: list[dict], yes: bool, threads: int) -> int:
    todo = [pl for pl in plans if gaps(p, pl)]
    if not todo:
        print("everything already acquired")
        return 0
    if not yes:
        if input(f"\nfetch {len(todo)} source(s) into {p.root}? [y/N] ").strip().lower() != "y":
            print("not started; re-run `geofetch run` to execute later")
            return 1
    totals = {"fetched": 0, "skipped": 0, "failed": 0}
    for pl in todo:
        pl["status"] = "approved"
        p.save_plan(pl["id"], pl)
        r = execute(p, pl, threads)
        for k in totals:
            totals[k] += r[k]
        print(f"  {pl['source']}: fetched {r['fetched']}, skipped {r['skipped']}, failed {r['failed']} → {pl['status']}")
    reqs = {r["id"]: r for r in p.list_requests()}
    if req_id in reqs:
        reqs[req_id]["status"] = "complete" if totals["failed"] == 0 else "partial"
        p.save_request(req_id, reqs[req_id])
    rp = write_report(p)
    print(f"done: {totals} · report: {rp}")
    return 0 if totals["failed"] == 0 else 2


def cmd_run(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    reqs = p.list_requests()
    if not reqs:
        raise SystemExit("no requests yet; run `geofetch ask`")
    req = next((r for r in reqs if r["id"] == a.request_id), reqs[-1]) if a.request_id else reqs[-1]
    plans = [p.load_plan(pid) for pid in req["plans"]]
    print(f"request {req['id']}: {req['ask']}")
    print_request(plans, [], verbose=False)
    return _run_plans(p, req["id"], plans, a.yes, a.threads)


def cmd_status(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    cat = Catalog(p.root, p.meta.name)
    rows = cat.summary()
    by_src: dict[str, list] = {}
    for r in rows:
        by_src.setdefault(r["source"], []).append(r)
    print(f"project {p.meta.name} · {p.meta.aoi_area_km2:,.0f} km² · {p.meta.start} → {p.meta.end} · {len(rows)} items in catalogue")
    for s, items in sorted(by_src.items()):
        wins = sorted({i["window"] for i in items})
        print(f"  {s:26} {len(items):3d} items  windows: {', '.join(wins)}")
    for req in p.list_requests()[-3:]:
        print(f"\nrequest {req['id']} [{req['status']}]: {req['ask']}")
        for pid in req["plans"]:
            pl = p.load_plan(pid)
            g = gaps(p, pl)
            n = pl["estimate"]["n_assets"] or len(pl["acquire"])
            mark = "✓" if not g else ("○" if len(g) == n else "◐")
            needs = ", ".join(f"{x['theme']}/{x['temporal']}" for x in pl["needs"])
            verdicts = ", ".join(f"{w['label']}={w['verdict'].replace('feasible-', '')}" for w in pl["windows"])
            print(f"  {mark} {needs[:30]:30} {pl['source'][:24]:24} {n - len(g)}/{n} files · {verdicts}")
        for u in req["unmet"]:
            print(f"  ✗ {u['need']:30} {'— unmet':24} {u['why']}")
    return 0


def cmd_open(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    cat = Catalog(p.root, p.meta.name)
    layers = build_vrts(cat) + vector_files(cat)
    if not layers:
        print("catalogue is empty; run `geofetch ask` first")
        return 1
    print("layers:")
    for f in layers:
        print(f"  {f}")
    if a.no_launch:
        return 0
    if open_in_qgis([p.aoi_file, *layers]):
        print(f"launched QGIS with {len(layers)} layers + AOI; STAC catalogue: {p.catalog_dir / 'catalog.json'}")
        return 0
    print("qgis not found on PATH; open the files above manually")
    return 1


def cmd_report(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    print(write_report(p))
    return 0


def cmd_sources(a: argparse.Namespace) -> int:
    for s in load_sources().values():
        if a.theme and a.theme not in s.themes:
            continue
        res = f"{s.resolution_m:g} m" if s.resolution_m else "vector"
        print(f"{s.id:28} {s.kind:6} {s.shape:7} {res:>8}  {', '.join(s.themes):34} {s.license}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="geofetch", description="Local, reproducible acquisition of open geospatial data for an analysis")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project directory")
    s.add_argument("dir"); s.add_argument("--name", required=True); s.add_argument("--aoi", required=True, help="GeoJSON/GPKG/Shapefile")
    s.add_argument("--start", required=True); s.add_argument("--end", required=True); s.add_argument("--objective", default="")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("ask", help="say what you want to analyse; get a plan; approve; fetch")
    s.add_argument("dir"); s.add_argument("text", nargs="?", help="defaults to the project objective")
    s.add_argument("--event", help="event date for before/after data (YYYY-MM-DD)")
    s.add_argument("--pre-days", type=int, default=30); s.add_argument("--post-days", type=int, default=30)
    s.add_argument("--cloud-max", type=float, default=20.0); s.add_argument("--clear-target", type=float, default=0.8)
    s.add_argument("--mode", choices=["single", "composite"], default="single", help="scenes per window for optical series")
    s.add_argument("--skip", action="append", metavar="THEME:TEMPORAL", help="drop a need, e.g. buildings:static")
    s.add_argument("--use", action="append", metavar="THEME:TEMPORAL=SOURCE", help="force a source for a need")
    s.add_argument("--dry-run", action="store_true", help="plan only"); s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--threads", type=int, default=12)
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("run", help="execute a request's plans (default: latest)")
    s.add_argument("dir"); s.add_argument("request_id", nargs="?"); s.add_argument("--yes", "-y", action="store_true"); s.add_argument("--threads", type=int, default=12)
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("status", help="what the project has; what each request still lacks and why")
    s.add_argument("dir"); s.set_defaults(fn=cmd_status)

    s = sub.add_parser("open", help="build mosaics and open everything in QGIS")
    s.add_argument("dir"); s.add_argument("--no-launch", action="store_true"); s.set_defaults(fn=cmd_open)

    s = sub.add_parser("report", help="write REPORT.md"); s.add_argument("dir"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("sources", help="list the data registry"); s.add_argument("--theme"); s.set_defaults(fn=cmd_sources)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
