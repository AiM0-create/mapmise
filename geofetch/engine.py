"""Engine: everything between an ask and a finished project, with no printing.

The command line and the GUI both call these functions; they differ only in how they show results.
Functions take an options object (argparse Namespace or `Options`) with the attributes listed in `Options`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from geofetch.catalog import Catalog
from geofetch.drivers import gdacs
from geofetch.plans import file_series_plan, layer_plan, scene_plan, vector_plan
from geofetch.project import Project
from geofetch.registry import Need, Source, load_sources
from geofetch.report import write_report
from geofetch.resolver import Resolution, parse_ask, resolve_needs
from geofetch.run import execute, gaps
from geofetch.understand import Period, geocode_first, parse_period, place_candidates


class AskError(Exception):
    """A problem the user can fix (no place found, unknown rule, …). The message is shown as-is."""


@dataclass
class Options:
    project: str | None = None
    place: str | None = None
    pick: int = 0
    aoi: str | None = None
    start: str | None = None
    end: str | None = None
    event: str | None = None
    pre_days: int = 30
    post_days: int = 30
    cloud_max: float = 20.0
    clear_target: float = 0.8
    mode: str = "single"
    skip: list[str] = field(default_factory=list)
    use: list[str] = field(default_factory=list)
    threads: int = 12
    workspace: str | None = None
    no_ai: bool = False


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 5e7 else f"{n / 1e6:.0f} MB"


def _ensure_country(p: Project, log: Callable[[str], None] = print) -> str | None:
    if p.meta.country_iso3 is None:
        from geofetch.geo import country_iso3
        c = p.aoi().geometry.centroid
        try:
            p.meta.country_iso3 = country_iso3(c.x, c.y)
        except Exception as e:  # noqa: BLE001 — offline is fine; per-country sources will be unmet
            log(f"warning: country lookup failed ({e}); per-country sources unavailable")
        p.save()
    return p.meta.country_iso3


def _assets_for(source: Source, needs: list[Need]) -> list[str]:
    ta = source.access.get("theme_assets", {})
    out: list[str] = []
    for n in needs:
        for k in ta.get(n.theme, source.access.get("default_assets", list(source.access.get("assets", {})))):
            if k not in out:
                out.append(k)
    return out


def _build_plans(p: Project, resolutions: list[Resolution], start: date, end: date, event: date | None, pre_days: int, post_days: int,
                 a) -> tuple[list[dict], list[tuple[Need, str]]]:
    """Group resolved needs by (source, temporal class) and build one plan per group. Returns (plans, unmet)."""
    aoi = p.aoi()
    sizes = p.size_cache()
    from geofetch.library import Library
    _lib = Library()
    held = lambda sid: _lib.items(sid, p.meta.project_epsg)  # noqa: E731 — prefer what the user already has, on ties
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
                                  cloud_max=a.cloud_max, clear_target=a.clear_target, mode=a.mode, prefer=held(sid))
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
    # automatic complement: years a series plan cannot cover (outside its product's record) go to the next candidate that covers them
    for pl in list(plans):
        gap = [w for w in pl["windows"] if w["verdict"] == "out-of-range"]
        if not gap or pl.get("complement_for"):
            continue
        g_start, g_end = min(w["start"] for w in gap), max(w["end"] for w in gap)
        for n in pl["needs"]:
            r = by_key.get(f"{n['theme']}:{n['temporal']}")
            alt = next((c.source for c in (r.candidates if r else []) if c.source.id not in planned and c.source.shape == "series"
                        and c.source.driver == "stac" and c.source.covers_period(g_start, g_end)), None)
            if not alt:
                continue
            try:
                cp = scene_plan(aoi, alt, [r.need], _assets_for(alt, [r.need]), date.fromisoformat(g_start), date.fromisoformat(g_end),
                                p.cache_dir, sizes, event, pre_days, post_days, cloud_max=a.cloud_max, clear_target=a.clear_target, mode=a.mode)
            except Exception as e:  # noqa: BLE001
                unmet.append((r.need, f"complement {alt.id} failed: {type(e).__name__}"))
                continue
            # keep only the gap windows, so years the primary source covers are not fetched twice
            labels = {w["label"] for w in gap}
            full_before = cp["estimate"]["full_bytes"] or 1
            cp["acquire"] = [e for e in cp["acquire"] if e["window"] in labels]
            cp["windows"] = [w for w in cp["windows"] if w["label"] in labels]
            kept = sum(v["size"] or 0 for e in cp["acquire"] for v in e["assets"].values())
            cp["estimate"] = {"n_assets": sum(len(e["assets"]) for e in cp["acquire"]), "full_bytes": kept,
                              "windowed_bytes": int(cp["estimate"]["windowed_bytes"] * kept / full_before), "known": True,
                              "unknown": sum(1 for e in cp["acquire"] for v in e["assets"].values() if v["size"] is None)}
            filled = [w["label"] for w in cp["windows"] if w["verdict"] != "out-of-range"]
            cp["id"] = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{alt.id}"
            cp["created"], cp["status"], cp["event"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), "proposed", event.isoformat() if event else None
            cp["complement_for"] = {"source": pl["source"], "gap": _year_ranges(list(labels)), "filled": _year_ranges(filled) if filled else ""}
            cp["explanations"] = [f"Added automatically: {pl['source']} has no products for {cp['complement_for']['gap']}; "
                                  f"{alt.name} fills {cp['complement_for']['filled'] or 'none of them'}."] + cp["explanations"]
            p.save_plan(cp["id"], cp)
            plans.append(cp)
            planned.add(alt.id)
            break
    p.save_size_cache(sizes)
    _lib.close()
    return plans, unmet


def _year_ranges(labels: list[str]) -> str:
    """['2005','2006','2007','2024','2025'] -> '2005–2007, 2024–2025'"""
    ys = sorted({int(l[:4]) for l in labels})
    runs, start = [], None
    for i, y in enumerate(ys):
        if start is None:
            start = y
        if i == len(ys) - 1 or ys[i + 1] != y + 1:
            runs.append(f"{start}" if start == y else f"{start}–{y}")
            start = None
    return ", ".join(runs)


def _verdict_summary(windows: list[dict]) -> str:
    """Counts per verdict, e.g. 'single×3 composite×1 infeasible×1' — the detail is in the plan file."""
    order = ["feasible-single", "feasible-composite", "infeasible", "incomplete", "out-of-range"]
    counts = {v: sum(1 for w in windows if w["verdict"] == v) for v in order}
    if len(windows) <= 4:
        return ", ".join(f"{w['label']}={w['verdict'].replace('feasible-', '')}" for w in windows)
    return " ".join(f"{v.replace('feasible-', '')}×{n}" for v, n in counts.items() if n) + f"  ({windows[0]['label']}…{windows[-1]['label']})"


def _vocabulary() -> set[str]:
    """Every plain word used in the ask rules' keywords (e.g. hospital, flood, reservoir) — never a place name on its own."""
    from geofetch.registry import load_ask_rules
    words = set()
    for r in load_ask_rules():
        for k in r.keywords:
            words |= set(re.findall(r"[a-z]{3,}", k.replace("\\b", " ").lower()))
    return words


def _workspace(a) -> Path:
    """Where new projects are created: Options.workspace (the GUI's projects folder) or the current directory."""
    return Path(getattr(a, "workspace", None) or ".")


def _slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-") or "project"


def _open_or_create_project(a, text: str, period: Period | None) -> tuple[Project, str]:
    """Existing project if --project points at one; otherwise create one from --aoi, --place, or a place named in the ask."""
    if a.project and (Path(a.project) / "project.json").exists():
        if a.place or a.aoi:
            raise AskError(f"{a.project} already has an AOI; drop --place/--aoi or use a new --project")
        p = Project.load(Path(a.project))
        return p, f"{p.meta.name} (existing project)"
    start, end = _request_period(a, period, None)
    if a.aoi:
        name = Path(a.aoi).stem
        root = Path(a.project or _workspace(a) / f"{_slug(name)}-{start:%Y-%m}")
        if (root / "project.json").exists():
            return Project.load(root), f"file {a.aoi} — existing project {root} reused"
        return Project.init(root, name, text, Path(a.aoi), start, end), f"file {a.aoi}"
    queries = [a.place] if a.place else place_candidates(text, period, _vocabulary())
    if not queries:
        raise AskError("no place found in the ask; say where (\"… in Chitradurga …\"), or pass --place NAME or --aoi FILE")
    try:
        place, q = geocode_first(queries, a.pick)
    except LookupError as e:
        raise AskError(str(e))
    root = Path(a.project or _workspace(a) / f"{_slug(place.name)}-{start:%Y-%m}")
    if (root / "project.json").exists():  # same place and month as an earlier ask: keep adding to that project
        return Project.load(root), f"“{q}” → {place.display_name} — existing project reused"
    p = Project.create(root, place.name, text, place.geometry, f"OpenStreetMap Nominatim {place.osm} ({place.display_name})", start, end,
                       aoi_attribution="© OpenStreetMap contributors, ODbL 1.0")
    how = f"“{q}” → {place.display_name} ({place.kind}, {place.osm})"
    if len(place.alternatives) > 1:
        how += "\n          other matches: " + "; ".join(place.alternatives[:4]) + "  (choose with --pick N)"
    return p, how


def _request_period(a, period: Period | None, p: Project | None) -> tuple[date, date]:
    if a.start or a.end:
        s = date.fromisoformat(a.start) if a.start else (period.start if period else date.today() - timedelta(days=365))
        e = date.fromisoformat(a.end) if a.end else (period.end if period else date.today())
        return s, e
    if period:
        return period.start, period.end
    if p:
        return p.period
    return date.today() - timedelta(days=365), date.today()


def _event_for(a, ask, period: Period | None, p: Project, start: date, end: date, log: Callable[[str], None] = print) -> tuple[date | None, int, int, str]:
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
            log(f"warning: GDACS lookup failed ({type(e).__name__})")
        if evs:
            ev = evs[0]
            others = f"; {len(evs) - 1} other(s) nearby" if len(evs) > 1 else ""
            return date.fromisoformat(ev.start), a.pre_days, a.post_days, f"event {ev.start} from GDACS: {ev.name} ({ev.alert}, ~{ev.distance_deg}° away{others})"
    # no exact date known: the stated period is 'after', the same number of days before it is 'before'
    span = max((end - start).days, 1)
    return start, max(a.pre_days, span), span, (f"no exact event date known (GDACS has none near here) — using the stated period {start} → {end} as 'after' "
                                                f"and the {max(a.pre_days, span)} days before it as 'before'. Pass --event YYYY-MM-DD to be precise")




# ---------------------------------------------------------------- public entry points (CLI and GUI)

def why_lines(plans: list[dict]) -> list[str]:
    out = []
    for pl in plans:
        for n in pl["needs"]:
            out.append(f"{n['theme']}/{n['temporal']} ({n['priority']}) ← {pl['source']}: {n['why']}")
        if pl.get("fallback_for"):
            ws = pl["fallback_for"]["windows"]
            out.append(f"  ↳ added automatically: {pl['fallback_for']['source']} is infeasible (cloud) for {', '.join(ws[:4])}"
                       + (f" and {len(ws) - 4} more" if len(ws) > 4 else ""))
        if pl.get("complement_for"):
            c = pl["complement_for"]
            out.append(f"  ↳ added automatically: {c['source']} has no products for {c['gap']}; this fills {c['filled'] or 'none of them'}")
    return out


def attention(plans: list[dict]) -> list[dict]:
    """Verdicts the user should look at, as {source, level, text}. level: infeasible | composite | incomplete | uncovered."""
    out = []
    for pl in plans:
        if pl.get("complement_for"):
            continue  # its gaps are reported against the primary plan
        comp = next((q for q in plans if q.get("complement_for", {}).get("source") == pl["source"]), None)
        filled = {w["label"] for w in comp["windows"] if w["verdict"] != "out-of-range"} if comp else set()
        bad = [w for w in pl["windows"] if w["verdict"] in ("infeasible", "incomplete", "feasible-composite")]
        for w in bad[:3]:
            out.append({"source": pl["source"], "level": w["verdict"].replace("feasible-", ""), "text": f"{w['label']}: {w['verdict_text']}"})
        if len(bad) > 3:
            out.append({"source": pl["source"], "level": "incomplete", "text": f"… {len(bad) - 3} more windows (details in the plan file)"})
        oor = [w["label"] for w in pl["windows"] if w["verdict"] == "out-of-range" and w["label"] not in filled]
        if oor:
            needs = ", ".join(f"{n['theme']}/{n['temporal']}" for n in pl["needs"])
            out.append({"source": pl["source"], "level": "uncovered", "text": f"{needs}: no registered source has data for {_year_ranges(oor)}"})
    return out


def prepare(text: str, a, log: Callable[[str], None] = print) -> dict:
    """Understand the ask, open or create the project, resolve needs, build and save plans and the request.
    Nothing is downloaded. Raises AskError for problems the user can fix."""
    period = parse_period(text)
    p, where = _open_or_create_project(a, text, period)
    _ensure_country(p, log)
    start, end = _request_period(a, period, p)
    existing = "existing project" in where
    period_source = ("flags" if (a.start or a.end) else f"“{period.text}”" if period else "project period" if existing else "default: last 12 months")
    ask = parse_ask(text, use_ai=not getattr(a, "no_ai", False))
    if not ask.needs:
        raise AskError(f"No ask rule matched “{text}”. Try words like flood, drought, NDVI, urban, reservoir, slope, road, rainfall, "
                       "forest, fire, heat or soil — or add a rule to geofetch/registry/asks.yaml.")
    skip = set(a.skip or [])
    needs = [n for n in ask.needs if n.key not in skip]
    overrides = dict(kv.split("=", 1) for kv in (a.use or []))
    resolutions = resolve_needs(needs, p.aoi().bbox, start.isoformat(), end.isoformat(), p.meta.country_iso3, overrides)
    event, pre_days, post_days, event_note = None, a.pre_days, a.post_days, None
    if any(n.temporal == "pair" for n in needs):
        event, pre_days, post_days, event_note = _event_for(a, ask, period, p, start, end, log)
    plans, unmet = _build_plans(p, resolutions, start, end, event, pre_days, post_days, a)
    from geofetch.library import Library
    from geofetch.run import library_hits
    lib = Library()
    for pl in plans:
        n = pl["estimate"]["n_assets"]
        missing = {(g["item"], g["asset"]) for g in gaps(p, pl)} if n else set()
        from_library = library_hits(p, pl, lib) & missing if missing else set()
        to_download = len(missing) - len(from_library)
        pl["estimate"]["present"] = n - len(missing)
        pl["estimate"]["from_library"] = len(from_library)
        pl["estimate"]["to_fetch_bytes"] = int(pl["estimate"]["windowed_bytes"] * to_download / n) if n else 0
    lib.close()
    req_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unmet_d = [{"need": n.key, "theme": n.theme, "temporal": n.temporal, "why": w} for n, w in unmet]
    p.save_request(req_id, {"id": req_id, "ask": text, "rules": ask.matched_rules, "period": [start.isoformat(), end.isoformat()],
                            "event": event.isoformat() if event else None, "pre_days": pre_days, "post_days": post_days,
                            "needs": [{"theme": n.theme, "temporal": n.temporal, "priority": n.priority, "why": n.why} for n in needs],
                            "resolutions": [{"need": r.need.key, "chosen": r.chosen.id if r.chosen else None,
                                             "candidates": [c.source.id for c in r.candidates], "reasons": r.candidates[0].reasons if r.candidates else []}
                                            for r in resolutions],
                            "plans": [pl["id"] for pl in plans], "unmet": unmet_d, "status": "proposed"})
    return {
        "request_id": req_id, "project": str(p.root.resolve()), "place": where, "area_km2": p.meta.aoi_area_km2,
        "country": p.meta.country_iso3, "epsg": p.meta.project_epsg, "start": start.isoformat(), "end": end.isoformat(),
        "period_source": period_source, "rules": ask.matched_rules, "how": ask.how, "ai": ask.ai,
        "n_needs": len(needs), "event_note": event_note,
        "plans": plans, "unmet": unmet_d, "attention": attention(plans), "why": why_lines(plans),
        "alternatives": {r.need.key: [c.source.id for c in r.candidates] for r in resolutions},
        "total_bytes": sum(pl["estimate"]["to_fetch_bytes"] for pl in plans),
        "total_files": sum(pl["estimate"]["n_assets"] for pl in plans),
    }


def run(project: Project, req_id: str, plan_ids: list[str] | None = None, threads: int = 12,
        progress: Callable[[dict], None] | None = None) -> dict:
    """Execute a request's plans (optionally only `plan_ids`). progress receives {plan, source, message} events."""
    reqs = {r["id"]: r for r in project.list_requests()}
    req = reqs[req_id]
    plans = [project.load_plan(pid) for pid in req["plans"] if plan_ids is None or pid in plan_ids]
    todo = [pl for pl in plans if gaps(project, pl)]
    totals = {"fetched": 0, "skipped": 0, "failed": 0, "reused": 0}
    for pl in todo:
        pl["status"] = "approved"
        project.save_plan(pl["id"], pl)
        cb = (lambda msg, pid=pl["id"], src=pl["source"]: progress({"plan": pid, "source": src, "message": msg})) if progress else (lambda msg: None)
        r = execute(project, pl, threads, progress=cb)
        for k in totals:
            totals[k] += r.get(k, 0)
        if progress:
            progress({"plan": pl["id"], "source": pl["source"], "message": "done", "result": r, "status": pl["status"]})
    req["status"] = "complete" if totals["failed"] == 0 else "partial"
    project.save_request(req_id, req)
    totals["report"] = str(write_report(project))
    return totals


def status(project: Project) -> dict:
    """What the project holds, and per request and plan what is present, missing and why."""
    cat = Catalog(project.root, project.meta.name)
    items = cat.summary()
    reqs = []
    for req in project.list_requests():
        rows = []
        for pid in req["plans"]:
            pl = project.load_plan(pid)
            g = gaps(project, pl)
            n = pl["estimate"]["n_assets"] or len(pl["acquire"])
            skipped = pl["estimate"]["n_assets"] == 0 and not pl["acquire"]
            rows.append({"plan": pid, "source": pl["source"], "needs": [f"{x['theme']}/{x['temporal']}" for x in pl["needs"]],
                         "present": n - len(g), "total": n, "state": "skipped" if skipped else ("complete" if not g else ("missing" if len(g) == n else "partial")),
                         "verdicts": _verdict_summary(pl["windows"]), "reason": pl["windows"][0]["verdict_text"] if skipped else None})
        reqs.append({"id": req["id"], "ask": req["ask"], "status": req["status"], "plans": rows, "unmet": req.get("unmet", []),
                     "period": req.get("period"), "event": req.get("event")})
    size = sum(f.stat().st_size for f in (project.root / "data").rglob("*") if f.is_file()) if (project.root / "data").exists() else 0
    return {"project": str(project.root.resolve()), "name": project.meta.name, "area_km2": project.meta.aoi_area_km2,
            "start": project.meta.start, "end": project.meta.end, "items": len(items), "bytes_on_disk": size, "requests": reqs}
