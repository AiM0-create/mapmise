"""Generic executor: acquire every entry of a plan, by driver, into the project; record in the catalogue.

Resumable: assets already recorded (file present with the recorded size) are skipped.
Each transfer is appended to the plan file under `execution`, so the plan is the record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from shapely.geometry import shape

from geofetch.catalog import COG_TYPE, GPKG_TYPE, Catalog
from geofetch.drivers import http as http_driver
from geofetch.drivers import overpass as overpass_driver
from geofetch.drivers.signing import sign
from geofetch.project import Project
from geofetch.registry import Source, load_sources
from geofetch.transfer.window import fetch_window, sha256_of


def gaps(project: Project, plan: dict) -> list[dict]:
    cat = Catalog(project.root, project.meta.name)
    return [{"window": e["window"], "item": e["item_id"], "asset": k}
            for e in plan["acquire"] for k in e["assets"] if not cat.has_asset(e["item_id"], k)]


def _out(project: Project, source: Source, e: dict, key: str, ext: str) -> Path:
    return project.root / "data" / source.id / e["window"] / f"{e['item_id']}_{key}{ext}"


def _record(project: Project, plan: dict, entry: dict) -> None:
    plan.setdefault("execution", {"started": datetime.now(timezone.utc).isoformat(timespec="seconds"), "transfers": []})
    plan["execution"]["transfers"].append({**entry, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    project.save_plan(plan["id"], plan)


def execute(project: Project, plan: dict, threads: int = 12, progress: Callable[[str], None] = print) -> dict:
    source = load_sources()[plan["source"]]
    cat = Catalog(project.root, project.meta.name)
    aoi = project.aoi()
    epsg = project.meta.project_epsg
    props_base = {"geofetch:source": source.id, "geofetch:plan": plan["id"], "geofetch:themes": [n["theme"] for n in plan["needs"]],
                  "geofetch:licence": source.license}
    done = skipped = failed = 0
    plan["status"] = "running"
    project.save_plan(plan["id"], plan)
    item_geoms = {}
    if plan["kind"] in ("scenes", "layer") and source.driver == "stac":  # footprints from the cached search, for the catalogue
        from geofetch.drivers import stac as stac_driver
        q = plan["query"]["query"]
        query = stac_driver.Query(q["source_id"], q["provider"], q["collection"], tuple(q["bbox"]), q["start"], q["end"], tuple(q["assets"]), q.get("extra", {}))
        for it, _ in [stac_driver.search(source, query, project.cache_dir)]:
            item_geoms = {i.id: i.geometry for i in it}

    for i, e in enumerate(plan["acquire"], 1):
        assets_done = {}
        for key, a in e["assets"].items():
            if cat.has_asset(e["item_id"], key):
                skipped += 1
                continue
            progress(f"[{i}/{len(plan['acquire'])}] {source.id} {e['window']} {e['item_id']} {key} …")
            try:
                if plan["kind"] == "vector":
                    out = _out(project, source, e, key, ".gpkg")
                    t0 = datetime.now()
                    if source.driver == "overpass":
                        _, n = overpass_driver.fetch(source, aoi.geometry, out)
                    else:
                        http_driver.fetch_vector(source, aoi.geometry, out, project.meta.country_iso3)
                    assets_done[key] = {"path": out, "media_type": GPKG_TYPE, "size": out.stat().st_size, "sha256": sha256_of(out),
                                        "source_href": a["href"], "seconds": (datetime.now() - t0).total_seconds()}
                elif plan["kind"] == "file_series" or e.get("whole_file"):
                    local = http_driver.download_file(a["href"], project.cache_dir / "files" / Path(a["href"]).name)
                    out = _out(project, source, e, key, ".tif")
                    r = fetch_window(str(local), aoi.geometry, out, epsg, threads=threads, nodata=source.access.get("nodata"))
                    assets_done[key] = {"path": out, "media_type": COG_TYPE, "size": r.output_bytes, "sha256": r.sha256, "source_href": a["href"],
                                        "seconds": r.seconds, "shape": [r.height, r.width]}
                else:  # scenes / layer over HTTP range reads
                    out = _out(project, source, e, key, ".tif")
                    r = fetch_window(sign(a["href"]), aoi.geometry, out, epsg, threads=threads, nodata=source.access.get("nodata"))
                    assets_done[key] = {"path": out, "media_type": COG_TYPE, "size": r.output_bytes, "sha256": r.sha256, "source_href": a["href"],
                                        "seconds": r.seconds, "shape": [r.height, r.width]}
                done += 1
                _record(project, plan, {"item": e["item_id"], "asset": key, "status": "ok", "path": str(out.relative_to(project.root)),
                                        "bytes": assets_done[key]["size"], "sha256": assets_done[key]["sha256"], "seconds": round(assets_done[key]["seconds"], 1)})
            except Exception as ex:  # noqa: BLE001 — record and continue; the plan shows what failed
                failed += 1
                _record(project, plan, {"item": e["item_id"], "asset": key, "status": "failed", "error": str(ex)[:300]})
        if assets_done:
            geom = item_geoms.get(e["item_id"], aoi.geometry).intersection(aoi.geometry)
            when = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc) if e.get("date") and e["date"][:4].isdigit() else None
            cat.add(e["item_id"], geom, when, {**props_base, "geofetch:window": e["window"], "geofetch:group": e.get("group")}, assets_done)
    plan["execution"]["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan["status"] = "complete" if failed == 0 else "partial"
    project.save_plan(plan["id"], plan)
    return {"fetched": done, "skipped": skipped, "failed": failed}
