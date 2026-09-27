"""mapmise command line.

    mapmise ask "flood in Chitradurga in August 2026"   one prompt: place and dates are understood, a project is created
          [--project DIR] [--place NAME] [--pick N] [--aoi FILE] [--start --end] [--event YYYY-MM-DD]
          [--dry-run] [--yes] [--skip theme:temporal] [--use theme:temporal=source] [--mode composite]
    mapmise init <dir> --name --aoi FILE --start --end [--objective]   explicit project from an AOI file
    mapmise run  <dir> [request-id] [--yes]        execute the plans of a request (default: latest)
    mapmise status <dir>                            what the project has, per need; what is missing and why
    mapmise open <dir>                              per-window VRT mosaics + vectors + AOI in QGIS
    mapmise report <dir>                            write REPORT.md — the human-readable acquisition record
    mapmise sources [--theme t]                     list the registry
    mapmise gui [--workspace DIR]                   the app: ask → plan → fetch → project, in your browser
    mapmise library [--here DIR|--place NAME|--aoi FILE] [--scan DIR...] [--forget-missing]
                                                     everything you have downloaded, across projects
    mapmise recipe export <dir> [-o FILE]           the project as one small shareable file
    mapmise recipe run <recipe.json> <new-dir>      rebuild the same dataset and compare checksums
    mapmise refresh <dir>                           extend the latest ask to today; fetch only what is new
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mapmise.catalog import Catalog
from mapmise.drivers import gdacs
from mapmise.plans import file_series_plan, layer_plan, scene_plan, vector_plan
from mapmise.prepare import build_vrts, open_in_qgis, vector_files
from mapmise.project import Project
from mapmise.registry import Need, Source, load_sources
from mapmise.report import write_report
from mapmise.resolver import Resolution, parse_ask, resolve_needs
from mapmise.run import execute, gaps
from mapmise.understand import Period, geocode, parse_period, place_candidates
from mapmise.engine import (AskError, _assets_for, _build_plans, _ensure_country, _event_for, _open_or_create_project,
                             _request_period, _slug, _verdict_summary, _vocabulary, _year_ranges)


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 5e7 else f"{n / 1e6:.0f} MB"


def cmd_init(a: argparse.Namespace) -> int:
    p = Project.init(Path(a.dir), a.name, a.objective or "", Path(a.aoi), date.fromisoformat(a.start), date.fromisoformat(a.end))
    iso3 = _ensure_country(p)
    m = p.meta
    print(f"created {p.root / 'project.json'}\n  AOI {m.aoi_area_km2:,.0f} km² ({iso3 or 'country unknown'}) from {m.aoi_source}\n  period {m.start} → {m.end}\n  project CRS EPSG:{m.project_epsg}")
    if a.objective:
        print(f"\nnext: mapmise ask {p.root} \"{a.objective}\"")
    return 0


def print_request(plans: list[dict], unmet: list[dict], verbose: bool = True) -> None:
    from mapmise.engine import attention, why_lines
    total = sum(pl["estimate"].get("to_fetch_bytes", pl["estimate"]["windowed_bytes"]) for pl in plans)
    n_assets = sum(pl["estimate"]["n_assets"] for pl in plans)
    print(f"\n{'need':28} {'source':30} {'files':>5} {'est.':>8}  verdicts")
    for pl in plans:
        needs = ", ".join(f"{n['theme']}/{n['temporal']}" for n in pl["needs"])
        e = pl["estimate"]
        est = ("have" if e["n_assets"] and e.get("present") == e["n_assets"] else
               "library" if e["n_assets"] and e.get("present", 0) + e.get("from_library", 0) == e["n_assets"] else
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
    from mapmise import engine
    try:
        r = engine.prepare(a.text, a)
    except AskError as e:
        raise SystemExit(str(e))
    print(f"understood:\n  place   {r['place']}\n          {r['area_km2']:,.0f} km², {r['country'] or 'country unknown'}, project CRS EPSG:{r['epsg']}"
          f"\n  period  {r['start']} → {r['end']}  ({r['period_source']})\n  project {r['project']}"
          f"\n  asks    {r['n_needs']} data needs from:" + "".join(f"\n          {rid}  ({r['how'][rid]})" for rid in r["rules"])
          + ("" if r["ai"] == "on" else f"\n          (built-in AI {r['ai']})"))
    if r["event_note"]:
        print(f"  before/after  {r['event_note']}")
    print_request(r["plans"], r["unmet"])
    print(f"\nrequest {r['request_id']} written ({len(r['plans'])} plans) — nothing downloaded yet")
    if a.dry_run or not r["plans"]:
        return 0
    return _run_plans(Project.load(Path(r["project"])), r["request_id"], r["plans"], a.yes, a.threads)


def _run_plans(p: Project, req_id: str, plans: list[dict], yes: bool, threads: int) -> int:
    from mapmise import engine
    todo = [pl for pl in plans if gaps(p, pl)]
    if not todo:
        print("everything already acquired")
        return 0
    if not yes and input(f"\nfetch {len(todo)} source(s) into {p.root}? [y/N] ").strip().lower() != "y":
        print("not started; re-run `mapmise run` to execute later")
        return 1

    def show(ev: dict) -> None:
        if ev["message"] == "done":
            r = ev["result"]
            print(f"  {ev['source']}: fetched {r['fetched']}" + (f" ({r['reused']} from your library)" if r.get("reused") else "")
                  + f", skipped {r['skipped']}, failed {r['failed']} → {ev['status']}")
        else:
            print(ev["message"])
    t = engine.run(p, req_id, [pl["id"] for pl in todo], threads, show)
    print(f"done: fetched {t['fetched']}, skipped {t['skipped']}, failed {t['failed']} · report: {t['report']}")
    return 0 if t["failed"] == 0 else 2


def cmd_run(a: argparse.Namespace) -> int:
    p = Project.load(Path(a.dir))
    reqs = p.list_requests()
    if not reqs:
        raise SystemExit("no requests yet; run `mapmise ask`")
    req = next((r for r in reqs if r["id"] == a.request_id), reqs[-1]) if a.request_id else reqs[-1]
    plans = [p.load_plan(pid) for pid in req["plans"]]
    print(f"request {req['id']}: {req['ask']}")
    print_request(plans, [], verbose=False)
    return _run_plans(p, req["id"], plans, a.yes, a.threads)


def cmd_status(a: argparse.Namespace) -> int:
    from mapmise import engine
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
        print("catalogue is empty; run `mapmise ask` first")
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

        from mapmise.registry.check import check_source
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


def cmd_selftest(a: argparse.Namespace) -> int:
    from mapmise import selftest
    print(f"mapmise {__import__('mapmise').__version__} self-test")
    return 0 if selftest.run(offline=a.offline) else 1


def cmd_gui(a: argparse.Namespace) -> int:
    from mapmise.gui.server import already_running, serve
    if not a.port and (url := already_running()):
        print(f"mapmise is already running at {url}; opening it")
        if not a.no_browser:
            import webbrowser
            webbrowser.open(url)
        return 0
    serve(port=a.port, open_browser=not a.no_browser, workspace=a.workspace)
    return 0


def cmd_library(a: argparse.Namespace) -> int:
    from mapmise.library import Library, scan
    lib = Library()
    if a.forget_missing:
        print(f"removed {lib.forget_missing()} entries whose files no longer exist")
    for d in a.scan or []:
        roots = [Path(d)] if (Path(d) / "project.json").exists() else [x for x in Path(d).iterdir() if (x / "project.json").exists()]
        for r in roots:
            print(f"indexed {scan(r, lib):4d} files from {r}")
    geom = None
    if a.here:
        geom = Project.load(Path(a.here)).aoi().geometry
    elif a.aoi:
        from mapmise.aoi import load_aoi
        geom = load_aoi(Path(a.aoi)).geometry
    elif a.place:
        from mapmise.understand import geocode
        geom = geocode(a.place).geometry
    if geom is not None:
        held = lib.over(geom)
        by: dict[tuple[str, str], list] = {}
        for h in held:
            by.setdefault((h.source, h.project.name), []).append(h)
        print(f"{len(held)} files in your library overlap this area")
        for (src, proj), hs in sorted(by.items()):
            dates = sorted({h.date for h in hs if h.date})
            span = f"{dates[0]} … {dates[-1]}" if len(dates) > 1 else (dates[0] if dates else "static")
            print(f"  {src:28} {len(hs):4d} files  {_gb(sum(h.bytes for h in hs)):>8}  {span:24} in {proj}")
        return 0
    st = lib.summary()
    print(f"library {st['library']}\n{st['files']} files, {_gb(st['bytes'])} across {len(st['by_project'])} projects\n")
    for r in st["by_source"]:
        print(f"  {r['source']:28} {r['files']:5d} files {_gb(r['bytes']):>9}  in {r['projects']} project(s)")
    gone = [r for r in st["by_project"] if not r["exists"]]
    if gone:
        print(f"\n{len(gone)} project folder(s) no longer exist; run `mapmise library --forget-missing`")
    if not st["files"]:
        print("empty — files are added as you fetch; index older projects with `mapmise library --scan ~/mapmise-projects`")
    return 0


def cmd_recipe(a: argparse.Namespace) -> int:
    from mapmise import recipe
    if a.action == "export":
        out = recipe.write(Project.load(Path(a.path)), Path(a.output) if a.output else None)
        size = out.stat().st_size
        print(f"recipe written: {out} ({size / 1e3:.0f} kB) — share it; `mapmise recipe run {out.name} <folder>` rebuilds the dataset")
        return 0
    if not a.dest:
        raise SystemExit("recipe run needs a destination folder")
    r = recipe.run(Path(a.path), Path(a.dest), threads=a.threads,
                   progress=lambda ev: print(ev["message"] if ev["message"] != "done" else f"  {ev['source']}: done"))
    print(f"\nrebuilt in {r['project']} (recipe made with mapmise {r['made_with']['mapmise']}, GDAL {r['made_with']['gdal']})")
    print(f"  identical: {len(r['identical'])} files")
    if r["live"]:
        print(f"  changed (live sources such as OpenStreetMap, expected): {len(r['live'])}")
    if r["different"]:
        print(f"  different: {len(r['different'])} — e.g. {r['different'][0]} (different GDAL version, or the provider reprocessed)")
    if r["missing"]:
        print(f"  missing: {len(r['missing'])} — e.g. {r['missing'][0]}; retry with `mapmise run {r['project']}`")
    return 0 if not (r["different"] or r["missing"]) else 2


def cmd_refresh(a: argparse.Namespace) -> int:
    from mapmise import engine
    p = Project.load(Path(a.dir))
    reqs = p.list_requests()
    if not reqs:
        raise SystemExit("no requests yet; run `mapmise ask`")
    last = reqs[-1]
    start, end = last.get("period", [p.meta.start, p.meta.end])
    today = date.today().isoformat()
    if end >= today:
        print(f"the latest ask already runs to {end}; nothing to refresh")
        return 0
    opts = engine.Options(project=str(p.root), start=start, end=today, event=last.get("event"))
    r = engine.prepare(last["ask"], opts)
    print(f"refreshed “{last['ask']}”: {start} → {today} (was → {end})")
    print_request(r["plans"], r["unmet"], verbose=False)
    if a.dry_run:
        return 0
    return _run_plans(p, r["request_id"], r["plans"], a.yes, a.threads)


def cmd_rules(a: argparse.Namespace) -> int:
    from mapmise.registry import load_ask_rules
    for r in load_ask_rules():
        words = ", ".join(k.replace("\\b", "").replace("\\", "") for k in r.keywords[:4])
        needs = ", ".join(f"{n.theme}/{n.temporal}" for n in r.needs)
        print(f"{r.id:13} [{words}]\n{'':14}→ {needs}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from mapmise import __version__
    for stream in (sys.stdout, sys.stderr):  # never crash on a console or log that cannot show "→" or "✓"
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="mapmise", description="Say what you want to analyse and where; get the right open data, clipped, organised and documented.")
    ap.add_argument("--version", action="version", version=f"mapmise {__version__}")
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
    s.add_argument("--no-ai", action="store_true", help="keyword rules only; do not use the built-in model")
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

    s = sub.add_parser("gui", help="open the mapmise app in your browser (runs locally)")
    s.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    s.add_argument("--workspace", help="folder for new projects (default: ~/mapmise-projects)")
    s.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("library", help="everything you have downloaded, across projects")
    s.add_argument("--here", metavar="DIR", help="what you already have over this project's area")
    s.add_argument("--place", help="what you already have over a named place"); s.add_argument("--aoi", help="… or over a boundary file")
    s.add_argument("--scan", nargs="+", metavar="DIR", help="index existing project folders (or a folder of projects)")
    s.add_argument("--forget-missing", action="store_true", help="drop entries whose files were deleted")
    s.set_defaults(fn=cmd_library)

    s = sub.add_parser("recipe", help="export a project as a shareable recipe, or rebuild one")
    s.add_argument("action", choices=["export", "run"]); s.add_argument("path", help="project folder (export) or recipe file (run)")
    s.add_argument("dest", nargs="?", help="new project folder (run)"); s.add_argument("-o", "--output")
    s.add_argument("--threads", type=int, default=12)
    s.set_defaults(fn=cmd_recipe)

    s = sub.add_parser("refresh", help="extend the latest ask to today and fetch only what is new")
    s.add_argument("dir"); s.add_argument("--dry-run", action="store_true"); s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--threads", type=int, default=12)
    s.set_defaults(fn=cmd_refresh)

    s = sub.add_parser("selftest", help="check that this installation works on this computer")
    s.add_argument("--offline", action="store_true", help="skip the check that downloads a small test file")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("rules", help="list the ask vocabulary (which words map to which data needs)")
    s.set_defaults(fn=cmd_rules)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
