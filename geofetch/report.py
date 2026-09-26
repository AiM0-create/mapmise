"""REPORT.md — the human-readable acquisition record, generated from the project's own files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from geofetch.catalog import Catalog
from geofetch.project import Project
from geofetch.registry import load_sources


def _mb(n: int) -> str:
    return f"{n / 1e6:,.1f} MB"


def write_report(p: Project) -> Path:
    sources = load_sources()
    cat = Catalog(p.root, p.meta.name)
    items = cat.summary()
    m = p.meta
    L = [f"# {m.name} — data acquisition record", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by geofetch. Everything below is derived from `project.json`, `requests/`, `plans/` and `catalog/`.", "",
         "## Project", "",
         f"- First ask: {m.objective or '(none)'}", f"- Area of interest: {m.aoi_area_km2:,.0f} km² ({m.country_iso3 or 'country unknown'}), from {m.aoi_source}"
         + (f" — {m.aoi_attribution}" if getattr(m, "aoi_attribution", None) else ""),
         f"- Period: {m.start} → {m.end}", f"- Project CRS: EPSG:{m.project_epsg}", ""]
    for req in p.list_requests():
        period = f" Period: {req['period'][0]} → {req['period'][1]}." if req.get("period") else ""
        L += [f"## Request {req['id']} — “{req['ask']}”", "", f"Matched rules: {', '.join(req['rules'])}.{period}" + (f" Event date: {req['event']}." if req.get("event") else ""), "",
              "| need | priority | source | why |", "|---|---|---|---|"]
        chosen = {r["need"]: r["chosen"] for r in req["resolutions"]}
        for n in req["needs"]:
            key = f"{n['theme']}:{n['temporal']}"
            L.append(f"| {key} | {n['priority']} | {chosen.get(key) or '—'} | {n['why']} |")
        L.append("")
        for pid in req["plans"]:
            pl = p.load_plan(pid)
            s = sources.get(pl["source"])
            L += [f"### {s.name if s else pl['source']} (`{pl['source']}`)", ""]
            for why in pl.get("explanations", [])[:1]:
                if pl.get("fallback_for") or pl.get("complement_for"):
                    L += [f"_{why}_", ""]
            L += [
                  f"- Licence: {s.license if s else '?'}" + (f" · {s.resolution_m:g} m" if s and s.resolution_m else ""),
                  (f"- Plan `{pid}` — status **{pl['status']}**; {pl['estimate']['n_assets']} files, estimated {_mb(pl['estimate']['windowed_bytes'])}"
                   + (f" (+{pl['estimate']['unknown']} of unknown size)" if pl['estimate'].get('unknown') else "")
                   + (f"; planned {pl['step']}" if pl.get('step') else "")) if pl["estimate"]["known"] else f"- Plan `{pid}` — status **{pl['status']}**; live query"]
            if pl.get("query", {}).get("query"):
                q = pl["query"]["query"]
                L.append(f"- Query: {q if isinstance(q, str) else ', '.join(f'{k}={v}' for k, v in q.items() if k in ('endpoint', 'collection', 'start', 'end', 'url'))}" + (f" (at {pl['query'].get('searched_at')})" if pl["query"].get("searched_at") else ""))
            L += ["", "| window | verdict | detail |", "|---|---|---|"]
            for w in pl["windows"]:
                L.append(f"| {w['label']} | {w['verdict']} | {w['verdict_text']} |")
            log = pl.get("execution", {}).get("transfers", [])
            done_keys = {(t["item"], t["asset"]) for t in log if t["status"] == "ok"}
            tr = [t for t in log if t["status"] == "ok"]
            if tr:
                L += ["", "| file | bytes | sha256 |", "|---|---|---|"] + [f"| `{t['path']}` | {t['bytes']:,} | `{t['sha256'][:16]}…` |" for t in tr]
            fails = list({(t["item"], t["asset"]): t for t in log if t["status"] != "ok" and (t["item"], t["asset"]) not in done_keys}.values())
            if fails:
                L += ["", "Still missing (retry with `geofetch run`):"] + [f"- {t['item']} {t['asset']}: {t['error']}" for t in fails]
            L.append("")
        if req.get("unmet"):
            L += ["**Not acquired:**", ""] + [f"- {u['need']}: {u['why']}" for u in req["unmet"]] + [""]
    L += ["## Catalogue", "", f"{len(items)} items in `catalog/catalog.json` (static STAC; open in QGIS ≥ 3.40 via Browser → STAC).", "",
          "| item | source | window | assets |", "|---|---|---|---|"] + [f"| {i['id']} | {i['source']} | {i['window']} | {', '.join(i['assets'])} |" for i in items]
    L += ["", "## Processing applied", "", "None beyond clipping to the area of interest and, where the source projection differs, reprojection to "
          f"EPSG:{m.project_epsg} with nearest-neighbour resampling (every output value exists in the source). Output rasters are Cloud-Optimised GeoTIFFs.", "",
          "## How to reproduce", "", "Each plan file records the exact catalogue query (endpoint, collection, bbox, dates), the selected items with their source URLs, "
          "the parameters, and per-file SHA-256. `geofetch run` on a copy of this project re-fetches whatever is missing from the same records.", ""]
    out = p.root / "REPORT.md"
    out.write_text("\n".join(L))
    return out
