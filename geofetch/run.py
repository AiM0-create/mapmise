"""Execute an approved plan: fetch selected assets into the project and record them in the catalogue.

Resumable: assets already present in the catalogue (file exists with the recorded size)
are skipped. Every transfer is appended to the plan file under `execution`, so the plan
file becomes the acquisition record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from geofetch.catalog import Catalog
from geofetch.discovery.stac import NormalisedItem, Query, search
from geofetch.project import Project
from geofetch.transfer.window import fetch_window


def items_for_plan(project: Project, plan: dict) -> dict[str, NormalisedItem]:
    """Re-read the catalogue metadata the plan was built from (cached search)."""
    q = plan["query"]["query"]
    query = Query(q["provider"], q["collection"], tuple(q["bbox"]), q["start"], q["end"], tuple(q["bands"]))
    items, record = search(query, project.cache_dir)
    if record["searched_at"] != plan["query"]["searched_at"]:
        print("warning: search cache was refreshed after this plan was made; item metadata may differ")
    return {it.id: it for it in items}


def targets(plan: dict, mode: str) -> list[tuple[str, str]]:
    """(window, item_id) pairs to acquire for the chosen mode."""
    out = []
    for s in plan["selections"]:
        if mode == "composite":
            out += [(s["window"], c["item_id"]) for c in s["composite"]]
        elif s["selected"]:
            out.append((s["window"], s["selected"]["item_id"]))
    return sorted(set(out))


def output_path(project: Project, collection: str, window: str, item_id: str, band: str) -> Path:
    return project.root / "data" / collection / window / f"{item_id}_{band}.tif"


def execute(project: Project, plan: dict, mode: str = "single", threads: int = 12,
            progress: Callable[[str], None] = print) -> dict:
    items = items_for_plan(project, plan)
    cat = Catalog(project.root, project.meta.name)
    aoi = project.aoi()
    bands = plan["bands"]
    todo = targets(plan, mode)
    ex = plan.setdefault("execution", {"mode": mode, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "transfers": []})
    plan["status"] = "running"
    project.save_plan(plan["id"], plan)

    done = skipped = failed = 0
    for i, (window, iid) in enumerate(todo, 1):
        it = items[iid]
        results = {}
        for band in bands:
            if cat.has_asset(iid, band):
                skipped += 1
                continue
            out = output_path(project, plan["collection"], window, iid, band)
            progress(f"[{i}/{len(todo)}] {window} {it.tile} {iid} {band} …")
            try:
                r = fetch_window(it.assets[band]["href"], aoi.geometry, out, project.meta.project_epsg, threads=threads)
            except Exception as e:  # noqa: BLE001 — record and continue; the plan file shows what failed
                failed += 1
                ex["transfers"].append({"item": iid, "band": band, "status": "failed", "error": str(e)[:300],
                                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                project.save_plan(plan["id"], plan)
                continue
            results[band] = r
            done += 1
            ex["transfers"].append({"item": iid, "band": band, "status": "ok", "path": str(out.relative_to(project.root)),
                                    "bytes": r.output_bytes, "sha256": r.sha256, "seconds": round(r.seconds, 1),
                                    "source_href": r.href, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            project.save_plan(plan["id"], plan)
        if results:
            cat.add_item(it, aoi.geometry, results, plan["id"], window, plan.get("requirement"))
    ex["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan["status"] = "complete" if failed == 0 else "partial"
    project.save_plan(plan["id"], plan)
    return {"fetched": done, "skipped": skipped, "failed": failed, "targets": len(todo)}


def gaps(project: Project, plan: dict, mode: str = "single") -> list[dict]:
    """Which (window, tile, item, band) the plan wants but the catalogue does not have."""
    cat = Catalog(project.root, project.meta.name)
    tile_of = {}
    for s in plan["selections"]:
        for c in ([s["selected"]] if s["selected"] else []) + s["composite"]:
            tile_of[c["item_id"]] = s["tile"]
    return [{"window": w, "tile": tile_of.get(iid), "item": iid, "band": b}
            for w, iid in targets(plan, mode) for b in plan["bands"] if not cat.has_asset(iid, b)]
