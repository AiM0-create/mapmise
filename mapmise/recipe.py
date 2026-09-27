"""Recipes: a whole project's acquisition as one small JSON file, and re-running it elsewhere.

A recipe holds the area, the questions, every plan (the exact catalogue items and asset URLs chosen) and the
SHA-256 of every file that was produced. Re-running it does not re-plan: it fetches the same items, then
compares checksums. Identical checksums mean an identical dataset. Differences are reported, not hidden —
typical causes are a different GDAL version, a provider reprocessing its archive, or live sources such as
OpenStreetMap that change by design.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from shapely.geometry import shape

from mapmise import __version__
from mapmise.project import Project

FORMAT = "mapmise-recipe/1"
LIVE_DRIVERS = {"overpass"}  # sources whose content changes by design


def export(p: Project) -> dict:
    import rasterio
    plans = []
    for pid in p.list_plans():
        pl = p.load_plan(pid)
        log = pl.get("execution", {}).get("transfers", [])
        expected = {f"{t['item']}/{t['asset']}": {"sha256": t["sha256"], "bytes": t["bytes"]} for t in log if t["status"] == "ok"}
        keep = {k: v for k, v in pl.items() if k not in ("execution", "detail", "status")}
        plans.append({**keep, "expected": expected})
    return {
        "format": FORMAT, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "made_with": {"mapmise": __version__, "gdal": rasterio.__gdal_version__},
        "project": {"name": p.meta.name, "objective": p.meta.objective, "start": p.meta.start, "end": p.meta.end,
                    "project_epsg": p.meta.project_epsg, "country_iso3": p.meta.country_iso3,
                    "aoi_source": p.meta.aoi_source, "aoi_attribution": p.meta.aoi_attribution,
                    "aoi": json.loads(p.aoi_file.read_text(encoding="utf-8"))},
        "requests": p.list_requests(),
        "plans": plans,
    }


def write(p: Project, out: Path | None = None) -> Path:
    out = Path(out or p.root / f"{p.root.name}.recipe.json")
    out.write_text(json.dumps(export(p), indent=1), encoding="utf-8")
    return out


def create_project(recipe: dict, root: Path) -> Project:
    """Recreate the project folder from a recipe: same area, projection, questions and plans; nothing fetched yet."""
    if recipe.get("format") != FORMAT:
        raise ValueError(f"not a mapmise recipe (format {recipe.get('format')!r})")
    m = recipe["project"]
    geom = shape(recipe["project"]["aoi"]["features"][0]["geometry"]) if recipe["project"]["aoi"].get("features") else shape(recipe["project"]["aoi"])
    p = Project.create(root, m["name"], m["objective"], geom, f"recipe: {m['aoi_source']}", date.fromisoformat(m["start"]),
                       date.fromisoformat(m["end"]), epsg=m["project_epsg"], aoi_attribution=m.get("aoi_attribution"))
    p.meta.country_iso3 = m.get("country_iso3")
    p.save()
    for req in recipe["requests"]:
        p.save_request(req["id"], {**req, "status": "proposed"})
    for pl in recipe["plans"]:
        p.save_plan(pl["id"], {**{k: v for k, v in pl.items() if k != "expected"}, "status": "proposed", "expected": pl["expected"]})
    return p


def verify(p: Project) -> dict:
    """Compare every produced file with the checksum the recipe expected."""
    from mapmise.registry import load_sources
    sources = load_sources()
    out = {"identical": [], "different": [], "missing": [], "live": []}
    for pid in p.list_plans():
        pl = p.load_plan(pid)
        got = {f"{t['item']}/{t['asset']}": t["sha256"] for t in pl.get("execution", {}).get("transfers", []) if t["status"] == "ok"}
        live = sources.get(pl["source"]) and sources[pl["source"]].driver in LIVE_DRIVERS
        for key, exp in pl.get("expected", {}).items():
            row = {"source": pl["source"], "file": key}
            if key not in got:
                out["missing"].append(row)
            elif got[key] == exp["sha256"]:
                out["identical"].append(row)
            else:
                out["live" if live else "different"].append(row)
    return out


def run(recipe_path: Path, root: Path, threads: int = 12, progress: Callable[[dict], None] | None = None) -> dict:
    from mapmise import engine
    recipe = json.loads(Path(recipe_path).read_text(encoding="utf-8"))
    p = create_project(recipe, root)
    for req in p.list_requests():
        engine.run(p, req["id"], threads=threads, progress=progress)
    return {"project": str(p.root), "made_with": recipe.get("made_with"), **verify(p)}
