"""geofetch command line.

    geofetch ask "flood in Chitradurga in August 2026"   one prompt: place and dates are understood, a project is created
          [--project DIR] [--place NAME] [--pick N] [--aoi FILE] [--start --end] [--event YYYY-MM-DD]
          [--dry-run] [--yes] [--skip theme:temporal] [--use theme:temporal=source] [--mode composite]
    geofetch init <dir> --name --aoi FILE --start --end [--objective]   explicit project from an AOI file
    geofetch run  <dir> [request-id] [--yes]        execute the plans of a request (default: latest)
    geofetch status <dir>                            what the project has, per need; what is missing and why
    geofetch open <dir>                              per-window VRT mosaics + vectors + AOI in QGIS
    geofetch report <dir>                            write REPORT.md — the human-readable acquisition record
    geofetch sources [--theme t]                     list the registry
    geofetch gui [--workspace DIR]                   the app: ask → plan → fetch → project, in your browser
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
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
from geofetch.understand import Period, geocode, parse_period, place_candidates
from geofetch.engine import (AskError, _assets_for, _build_plans, _ensure_country, _event_for, _open_or_create_project,
                             _request_period, _slug, _verdict_summary, _vocabulary, _year_ranges)


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 5e7 else f"{n / 1e6:.0f} MB"


def cmd_init(a: argparse.Namespace) -> int:
    p = Project.init(Path(a.dir), a.name, a.objective or "", Path(a.aoi), date.fromisoformat(a.start), date.fromisoformat(a.end))
    iso3 = _ensure_country(p)
    m = p.meta
    print(f"created {p.root / 'project.json'}\n  AOI {m.aoi_area_km2:,.0f} km² ({iso3 or 'country unknown'}) from {m.aoi_source}\n  period {m.start} → {m.end}\n  project CRS EPSG:{m.project_epsg}")
    if a.objective:
        print(f"\nnext: geofetch ask {p.root} \"{a.objective}\"")
    return 0


def print_request(plans: list[dict], unmet: list[dict], verbose: bool = True) -> None:
    from geofetch.engine import attention, why_lines
    total = sum(pl["estimate"].get("to_fetch_bytes", pl["estimate"]["windowed_bytes"]) for pl in plans)
    n_assets = sum(pl["estimate"]["n_assets"] for pl in plans)
    print(f"\n{'need':28} {'source':30} {'files':>5} {'est.':>8}  verdicts")
    for pl in plans:
        needs = ", ".join(f"{n['theme']}/{n['temporal']}" for n in pl["needs"])
        e = pl["estimate"]
        est = ("have" if e["n_assets"] and e.get("present") == e["n_assets"] else
               ("≥" if e.get("unknown") else "") + _gb(e.get("to_fetch_bytes", e["windowed_bytes"])) if e["known"] else "live")
        tag = " ↳fallback" if pl.get("fallback_for") else " ↳complement" if pl.get("complement_for") else ""
        print(f"{needs[:28]:28} {(pl['source'] + tag)[:30]:30} {pl['estimate']['n_assets']:5d} {est:>8}  {_verdict_summary(pl['windows'])}")
    met_themes = {n["theme"] for pl in plans for n in pl["needs"]}
    for u in unmet:
        why = f"no {u['temporal']} source for this period; '{u['theme']}' is supplied in another form above" if u["theme"] in met_themes else u["why"]
        print(f"{u['need'].replace(':', '/')[:28]:28} {'— unmet':30} {'':5} {'':8}  {why}")
    unknown = sum(pl["estimate"].get("unknown", 0) for pl in plans)
    print(f"\ntotal: {len(plans)} sources, {n_assets} files, ≈ {_gb(total)} to transfer (AOI-windowed; vector queries not counted"
          + (f"; {unknown} file sizes unknown — servers did not answer" if unknown else "") + ")")
    if not verbose:
        return
    print("\nWhy each dataset:")
    for line in why_lines(plans):
        print(f"  {line}")
    att = attention(plans)
    if att:
        print("\nVerdicts needing your attention:")
        for x in att:
            print(f"  {x['source']} {x['text']}")


def cmd_ask(a: argparse.Namespace) -> int:
    from geofetch import engine
    try:
        r = engine.prepare(a.text, a)
    except AskError as e:
        raise SystemExit(str(e))
    print(f"understood:\n  place   {r['place']}\n          {r['area_km2']:,.0f} km², {r['country'] or 'country unknown'}, project CRS EPSG:{r['epsg']}"
          f"\n  period  {r['start']} → {r['end']}  ({r['period_source']})\n  project {r['project']}"
          f"\n  asks    {', '.join(r['rules'])} → {r['n_needs']} data needs")
    if r["event_note"]:
        print(f"  before/after  {r['event_note']}")
    print_request(r["plans"], r["unmet"])
    print(f"\nrequest {r['request_id']} written ({len(r['plans'])} plans) — nothing downloaded yet")
    if a.dry_run or not r["plans"]:
        return 0
    return _run_plans(Project.load(Path(r["project"])), r["request_id"], r["plans"], a.yes, a.threads)


def _run_plans(p: Project, req_id: str, plans: list[dict], yes: bool, threads: int) -> int:
    from geofetch import engine
    todo = [pl for pl in plans if gaps(p, pl)]
    if not todo:
        print("everything already acquired")
        return 0
    if not yes and input(f"\nfetch {len(todo)} source(s) into {p.root}? [y/N] ").strip().lower() != "y":
        print("not started; re-run `geofetch run` to execute later")
        return 1

    def show(ev: dict) -> None:
        if ev["message"] == "done":
            r = ev["result"]
            print(f"  {ev['source']}: fetched {r['fetched']}, skipped {r['skipped']}, failed {r['failed']} → {ev['status']}")
        else:
            print(ev["message"])
    t = engine.run(p, req_id, [pl["id"] for pl in todo], threads, show)
    print(f"done: fetched {t['fetched']}, skipped {t['skipped']}, failed {t['failed']} · report: {t['report']}")
    return 0 if t["failed"] == 0 else 2


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
    from geofetch import engine
    st = engine.status(Project.load(Path(a.dir)))
    print(f"project {st['name']} · {st['area_km2']:,.0f} km² · {st['start']} → {st['end']} · {st['items']} items · {_gb(st['bytes_on_disk'])} on disk")
    marks = {"complete": "✓", "partial": "◐", "missing": "○", "skipped": "✗"}
    for req in st["requests"][-3:]:
        print(f"\nrequest {req['id']} [{req['status']}]: {req['ask']}")
        for r in req["plans"]:
            detail = f"not fetched — {r['reason']}" if r["state"] == "skipped" else f"{r['present']}/{r['total']} files · {r['verdicts']}"
            print(f"  {marks[r['state']]} {', '.join(r['needs'])[:30]:30} {r['source'][:26]:26} {detail}")
        for u in req["unmet"]:
            print(f"  ✗ {u['need']:30} {'— unmet':26} {u['why']}")
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
    if a.check is not None:
        from concurrent.futures import ThreadPoolExecutor

        from geofetch.registry.check import check_source
        srcs = [s for s in load_sources().values() if not a.check or s.id in a.check]
        with ThreadPoolExecutor(6) as ex:
            results = list(ex.map(check_source, srcs))
        for r in results:
            print(f"{'✓' if r.ok else '✗'} {r.source:28} {r.detail}")
        bad = [r for r in results if not r.ok]
        print(f"\n{len(results) - len(bad)}/{len(results)} sources reachable")
        return 1 if bad else 0
    for s in load_sources().values():
        if a.theme and a.theme not in s.themes:
            continue
        res = f"{s.resolution_m:g} m" if s.resolution_m else "vector"
        print(f"{s.id:28} {s.kind:6} {s.shape:7} {res:>8}  {', '.join(s.themes):34} {s.license}")
    return 0


def cmd_gui(a: argparse.Namespace) -> int:
    from geofetch.gui.server import serve
    serve(port=a.port, open_browser=not a.no_browser, workspace=a.workspace)
    return 0


def cmd_rules(a: argparse.Namespace) -> int:
    from geofetch.registry import load_ask_rules
    for r in load_ask_rules():
        words = ", ".join(k.replace("\\b", "").replace("\\", "") for k in r.keywords[:4])
        needs = ", ".join(f"{n.theme}/{n.temporal}" for n in r.needs)
        print(f"{r.id:13} [{words}]\n{'':14}→ {needs}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from geofetch import __version__
    ap = argparse.ArgumentParser(prog="geofetch", description="Say what you want to analyse and where; get the right open data, clipped, organised and documented.")
    ap.add_argument("--version", action="version", version=f"geofetch {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project directory")
    s.add_argument("dir"); s.add_argument("--name", required=True); s.add_argument("--aoi", required=True, help="GeoJSON/GPKG/Shapefile")
    s.add_argument("--start", required=True); s.add_argument("--end", required=True); s.add_argument("--objective", default="")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("ask", help="one prompt: what you want to analyse, where, when → plan → approve → fetch")
    s.add_argument("text", help='e.g. "flood in Chitradurga in August 2026"')
    s.add_argument("--project", help="project directory (default: ./<place>-<yyyy-mm>; reused if it exists)")
    s.add_argument("--place", help="place name to geocode instead of the one found in the ask"); s.add_argument("--pick", type=int, default=0, help="choose another geocoder match")
    s.add_argument("--aoi", help="AOI file (GeoJSON/GPKG/Shapefile) instead of a place name")
    s.add_argument("--start"); s.add_argument("--end")
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
    s = sub.add_parser("sources", help="list the data registry, or --check entries live")
    s.add_argument("--theme"); s.add_argument("--check", nargs="*", metavar="ID", help="probe sources live (all if no ids given)")
    s.set_defaults(fn=cmd_sources)

    s = sub.add_parser("gui", help="open the geofetch app in your browser (runs locally)")
    s.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    s.add_argument("--workspace", help="folder for new projects (default: ~/geofetch-projects)")
    s.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("rules", help="list the ask vocabulary (which words map to which data needs)")
    s.set_defaults(fn=cmd_rules)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
