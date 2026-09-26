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


def _build_plans(p: Project, resolutions: list[Resolution], start: date, end: date, event: date | None, pre_days: int, post_days: int,
                 a: argparse.Namespace) -> tuple[list[dict], list[tuple[Need, str]]]:
    """Group resolved needs by (source, temporal class) and build one plan per group. Returns (plans, unmet)."""
    aoi = p.aoi()
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
                plan = scene_plan(aoi, s, needs, _assets_for(s, needs), start, end, p.cache_dir, sizes, event, pre_days, post_days,
                                  cloud_max=a.cloud_max, clear_target=a.clear_target, mode=a.mode)
            elif s.shape == "series" and s.driver == "http":
                plan = file_series_plan(aoi, s, needs, list(s.access["assets"]), start, end, sizes)
            elif s.shape == "layer" and s.kind == "raster":
                plan = layer_plan(aoi, s, needs, _assets_for(s, needs), p.cache_dir, sizes, p.meta.country_iso3)
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

    # automatic fallback: an optical plan with infeasible windows gets the best cloud-independent alternative for the same need
    planned = {pl["source"] for pl in plans}
    by_key = {r.need.key: r for r in resolutions}
    for pl in list(plans):
        bad = [w["label"] for w in pl["windows"] if w["verdict"] == "infeasible"]
        if not bad or sources[pl["source"]].planner != "optical":
            continue
        for n in pl["needs"]:
            r = by_key.get(f"{n['theme']}:{n['temporal']}")
            alt = next((c.source for c in (r.candidates if r else []) if not c.source.cloud_dependent and c.source.shape == "series"
                        and c.source.driver == "stac" and c.source.id not in planned), None)
            if not alt:
                continue
            need = r.need
            try:
                fb = scene_plan(aoi, alt, [need], _assets_for(alt, [need]), start, end, p.cache_dir, sizes, event, pre_days, post_days, mode="single")
            except Exception as e:  # noqa: BLE001
                unmet.append((need, f"fallback {alt.id} failed: {type(e).__name__}"))
                continue
            fb["id"] = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{alt.id}"
            fb["created"], fb["status"], fb["event"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), "proposed", event.isoformat() if event else None
            fb["fallback_for"] = {"source": pl["source"], "windows": bad}
            fb["explanations"] = [f"Added automatically: {pl['source']} is infeasible for {', '.join(bad)} (clouds); "
                                  f"{alt.name} is cloud-independent and serves the same need."] + fb["explanations"]
            p.save_plan(fb["id"], fb)
            plans.append(fb)
            planned.add(alt.id)
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
        src = pl["source"] + (" ↳fallback" if pl.get("fallback_for") else "")
        print(f"{needs[:28]:28} {src[:26]:26} {pl['estimate']['n_assets']:5d} {est:>8}  {verdicts}")
    for n, why in unmet:
        print(f"{(n.theme + '/' + n.temporal)[:28]:28} {'— unmet':26} {'':5} {'':8}  {why}")
    print(f"\ntotal: {len(plans)} sources, {n_assets} files, ≈ {_gb(total)} to transfer (AOI-windowed; vector queries not counted)")
    if verbose:
        print("\nWhy each dataset:")
        for pl in plans:
            for n in pl["needs"]:
                print(f"  {n['theme']}/{n['temporal']} ({n['priority']}) ← {pl['source']}: {n['why']}")
        for pl in plans:
            if pl.get("fallback_for"):
                f = pl["fallback_for"]
                print(f"  ↳ {pl['source']} added automatically because {f['source']} is infeasible for {', '.join(f['windows'])}")
        flagged = [(pl["source"], w) for pl in plans for w in pl["windows"] if w["verdict"] in ("infeasible", "incomplete", "feasible-composite")]
        if flagged:
            print("\nVerdicts needing your attention:")
            for sid, w in flagged:
                print(f"  {sid} {w['label']}: {w['verdict_text']}")


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "project"


def _open_or_create_project(a: argparse.Namespace, text: str, period: Period | None) -> tuple[Project, str]:
    """Existing project if --project points at one; otherwise create one from --aoi, --place, or a place named in the ask."""
    if a.project and (Path(a.project) / "project.json").exists():
        if a.place or a.aoi:
            raise SystemExit(f"{a.project} already has an AOI; drop --place/--aoi or use a new --project")
        return Project.load(Path(a.project)), "existing project"
    start, end = _request_period(a, period, None)
    if a.aoi:
        name = Path(a.aoi).stem
        root = Path(a.project or f"./{_slug(name)}-{start:%Y-%m}")
        return Project.init(root, name, text, Path(a.aoi), start, end), f"file {a.aoi}"
    queries = [a.place] if a.place else place_candidates(text, period)
    if not queries:
        raise SystemExit("no place found in the ask; say where (\"… in Chitradurga …\"), or pass --place NAME or --aoi FILE")
    last_err = None
    for q in queries:
        try:
            place = geocode(q, a.pick)
            break
        except LookupError as e:
            last_err = e
    else:
        raise SystemExit(str(last_err))
    root = Path(a.project or f"./{_slug(place.name)}-{start:%Y-%m}")
    p = Project.create(root, place.name, text, place.geometry, f"OpenStreetMap Nominatim {place.osm} ({place.display_name})", start, end,
                       aoi_attribution="© OpenStreetMap contributors, ODbL 1.0")
    how = f"“{q}” → {place.display_name} ({place.kind}, {place.osm})"
    if len(place.alternatives) > 1:
        how += "\n          other matches: " + "; ".join(place.alternatives[:4]) + "  (choose with --pick N)"
    return p, how


def _request_period(a: argparse.Namespace, period: Period | None, p: Project | None) -> tuple[date, date]:
    if a.start or a.end:
        s = date.fromisoformat(a.start) if a.start else (period.start if period else date.today() - timedelta(days=365))
        e = date.fromisoformat(a.end) if a.end else (period.end if period else date.today())
        return s, e
    if period:
        return period.start, period.end
    if p:
        return p.period
    return date.today() - timedelta(days=365), date.today()


def _event_for(a: argparse.Namespace, ask, period: Period | None, p: Project, start: date, end: date) -> tuple[date | None, int, int, str]:
    """(event, pre_days, post_days, explanation) for before/after needs. Never invents a date silently."""
    if a.event:
        return date.fromisoformat(a.event), a.pre_days, a.post_days, f"event {a.event} (given)"
    if period and period.event:
        return period.event, a.pre_days, a.post_days, f"event {period.event} (from the ask)"
    code = load_sources()["gdacs-events"].access["types"].get(ask.event_type or "")
    if code:
        try:
            evs = gdacs.events(code, p.aoi().bbox, start.isoformat(), end.isoformat())
        except Exception as e:  # noqa: BLE001 — GDACS is a convenience, not a dependency
            evs = []
            print(f"warning: GDACS lookup failed ({type(e).__name__})")
        if evs:
            ev = evs[0]
            others = f"; {len(evs) - 1} other(s) nearby" if len(evs) > 1 else ""
            return date.fromisoformat(ev.start), a.pre_days, a.post_days, f"event {ev.start} from GDACS: {ev.name} ({ev.alert}, ~{ev.distance_deg}° away{others})"
    # no exact date known: the stated period is 'after', the same number of days before it is 'before'
    span = max((end - start).days, 1)
    return start, max(a.pre_days, span), span, (f"no exact event date known (GDACS has none near here) — using the stated period {start} → {end} as 'after' "
                                                f"and the {max(a.pre_days, span)} days before it as 'before'. Pass --event YYYY-MM-DD to be precise")


def cmd_ask(a: argparse.Namespace) -> int:
    text = a.text
    period = parse_period(text)
    p, where = _open_or_create_project(a, text, period)
    _ensure_country(p)
    start, end = _request_period(a, period, p)
    print(f"understood:\n  place   {where}\n          {p.meta.aoi_area_km2:,.0f} km², {p.meta.country_iso3 or 'country unknown'}, project CRS EPSG:{p.meta.project_epsg}"
          f"\n  period  {start} → {end}" + (f"  ← “{period.text}”" if period and not (a.start or a.end) else "  (flags)" if (a.start or a.end) else "  (default: last 12 months)" if not period else "")
          + f"\n  project {p.root.resolve()}")
    ask = parse_ask(text)
    if not ask.needs:
        raise SystemExit(f"no ask rule matched {text!r}. Rules recognise e.g. flood, drought, ndvi, urban, reservoir, slope, road, rainfall, "
                         "forest, fire, heat, soil — see `geofetch rules`, or add one to geofetch/registry/asks.yaml")
    skip = set(a.skip or [])
    needs = [n for n in ask.needs if n.key not in skip]
    print(f"  asks    {', '.join(ask.matched_rules)} → {len(needs)} data needs" + (f" (skipped {', '.join(skip)})" if skip else ""))
    overrides = dict(kv.split("=", 1) for kv in (a.use or []))
    resolutions = resolve_needs(needs, p.aoi().bbox, start.isoformat(), end.isoformat(), p.meta.country_iso3, overrides)

    event, pre_days, post_days = None, a.pre_days, a.post_days
    if any(n.temporal == "pair" for n in needs):
        event, pre_days, post_days, why = _event_for(a, ask, period, p, start, end)
        print(f"  before/after  {why}")

    plans, unmet = _build_plans(p, resolutions, start, end, event, pre_days, post_days, a)
    req_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p.save_request(req_id, {"id": req_id, "ask": text, "rules": ask.matched_rules, "period": [start.isoformat(), end.isoformat()],
                            "event": event.isoformat() if event else None, "pre_days": pre_days, "post_days": post_days,
                            "needs": [{"theme": n.theme, "temporal": n.temporal, "priority": n.priority, "why": n.why} for n in needs],
                            "resolutions": [{"need": r.need.key, "chosen": r.chosen.id if r.chosen else None,
                                             "candidates": [c.source.id for c in r.candidates], "reasons": r.candidates[0].reasons if r.candidates else []} for r in resolutions],
                            "plans": [pl["id"] for pl in plans], "unmet": [{"need": n.key, "why": w} for n, w in unmet], "status": "proposed"})
    print_request(plans, unmet)
    print(f"\nrequest {req_id} written ({len(plans)} plans) — nothing downloaded yet")
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


def cmd_rules(a: argparse.Namespace) -> int:
    from geofetch.registry import load_ask_rules
    for r in load_ask_rules():
        words = ", ".join(k.replace("\\b", "").replace("\\", "") for k in r.keywords[:4])
        needs = ", ".join(f"{n.theme}/{n.temporal}" for n in r.needs)
        print(f"{r.id:13} [{words}]\n{'':14}→ {needs}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="geofetch", description="Local, reproducible acquisition of open geospatial data for an analysis")
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

    s = sub.add_parser("rules", help="list the ask vocabulary (which words map to which data needs)")
    s.set_defaults(fn=cmd_rules)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
