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
from geofetch.geo import shared_cache
from geofetch.library import Library
from geofetch.project import Project
from geofetch.registry import Source, load_sources
from geofetch.transfer.window import fetch_window, sha256_of

TRANSIENT = ("429", "Too Many Requests", "500", "502", "503", "504", "Timeout", "timed out", "Connection", "RemoteProtocolError", "CURL error")


def _with_retry(fn, attempts: int = 4, first_delay: float = 5.0):
    """Run fn(); on a transient network/server error wait and try again (5, 10, 20 s). Other errors raise at once."""
    import time
    delay = first_delay
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1 or not any(t in f"{type(e).__name__} {e}" for t in TRANSIENT):
                raise
            time.sleep(delay)
            delay *= 2


def _when(value: str | None) -> datetime | None:
    """Item date from a plan entry: full ISO date/datetime, 'YYYY-MM' or 'YYYY' (static products); None if absent."""
    if not value or not str(value)[:4].isdigit():
        return None
    v = str(value)
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(v[:len(datetime(2000, 1, 1).strftime(fmt))], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _cache_name(url: str) -> str:
    """Stable, collision-free file name for a URL in the shared cache: host + path, flattened."""
    from urllib.parse import urlparse
    u = urlparse(url)
    return (u.netloc + u.path).replace("/", "__")


def item_footprints(project: Project, plan: dict, source: Source) -> dict:
    """WGS84 footprints of a plan's catalogue items, from the project's cached search (empty for non-STAC sources)."""
    if plan["kind"] not in ("scenes", "layer") or source.driver != "stac":
        return {}
    from geofetch.drivers import stac as stac_driver
    q = plan["query"]["query"]
    query = stac_driver.Query(q["source_id"], q["provider"], q["collection"], tuple(q["bbox"]), q["start"], q["end"], tuple(q["assets"]), q.get("extra", {}))
    items, _ = stac_driver.search(source, query, project.cache_dir)
    return {i.id: i.geometry for i in items}


def library_hits(project: Project, plan: dict, lib: Library) -> set[tuple[str, str]]:
    """(item, asset) pairs of a plan that the library can supply without downloading."""
    if plan["kind"] not in ("scenes", "layer") or any(e.get("whole_file") for e in plan["acquire"]):
        return set()
    source = load_sources()[plan["source"]]
    geoms = item_footprints(project, plan, source)
    aoi = project.aoi().geometry
    hits = set()
    for e in plan["acquire"]:
        needed = geoms.get(e["item_id"], aoi).intersection(aoi)
        for k in e["assets"]:
            if lib.reusable(source.id, e["item_id"], k, needed, project.meta.project_epsg, exclude_project=project.root):
                hits.add((e["item_id"], k))
    return hits


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
    done = skipped = failed = reused = 0
    try:
        lib = Library()
    except Exception:  # noqa: BLE001 — the library is an optimisation; a locked or unwritable index must not stop a fetch
        lib = None
    plan["status"] = "running"
    project.save_plan(plan["id"], plan)
    item_geoms = item_footprints(project, plan, source)

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
                        _with_retry(lambda: overpass_driver.fetch(source, aoi.geometry, out), attempts=2)
                    else:
                        _with_retry(lambda: http_driver.fetch_vector(source, aoi.geometry, out, project.meta.country_iso3))
                    assets_done[key] = {"path": out, "media_type": GPKG_TYPE, "size": out.stat().st_size, "sha256": sha256_of(out),
                                        "source_href": a["href"], "seconds": (datetime.now() - t0).total_seconds()}
                elif plan["kind"] == "file_series" or e.get("whole_file"):
                    local = _with_retry(lambda: http_driver.download_file(a["href"], shared_cache() / _cache_name(a["href"])))
                    out = _out(project, source, e, key, ".tif")
                    r = fetch_window(str(local), aoi.geometry, out, epsg, threads=threads, nodata=source.access.get("nodata"))
                    assets_done[key] = {"path": out, "media_type": COG_TYPE, "size": r.output_bytes, "sha256": r.sha256, "source_href": a["href"],
                                        "seconds": r.seconds, "shape": [r.height, r.width], "native_grid": r.native_grid}
                else:  # scenes / layer over HTTP range reads — or a local re-clip of a file already in the library
                    out = _out(project, source, e, key, ".tif")
                    needed = item_geoms.get(e["item_id"], aoi.geometry).intersection(aoi.geometry)
                    held = lib.reusable(source.id, e["item_id"], key, needed, epsg, exclude_project=project.root) if lib else None
                    if held:
                        progress(f"  ↳ reusing {held.path.name} from {held.project.name} (no download)")
                        r = fetch_window(str(held.path), aoi.geometry, out, epsg, threads=threads, nodata=source.access.get("nodata"))
                        reused += 1
                    else:
                        r = _with_retry(lambda: fetch_window(sign(a["href"]), aoi.geometry, out, epsg, threads=threads, nodata=source.access.get("nodata")))
                    assets_done[key] = {"path": out, "media_type": COG_TYPE, "size": r.output_bytes, "sha256": r.sha256, "source_href": a["href"],
                                        "seconds": r.seconds, "shape": [r.height, r.width], "native_grid": r.native_grid and not held,
                                        "reused_from": str(held.path) if held else None}
                done += 1
                _record(project, plan, {"item": e["item_id"], "asset": key, "status": "ok", "path": str(out.relative_to(project.root)),
                                        **({"reused_from": assets_done[key]["reused_from"]} if assets_done[key].get("reused_from") else {}),
                                        "bytes": assets_done[key]["size"], "sha256": assets_done[key]["sha256"], "seconds": round(assets_done[key]["seconds"], 1)})
            except Exception as ex:  # noqa: BLE001 — record and continue; the plan shows what failed
                failed += 1
                _record(project, plan, {"item": e["item_id"], "asset": key, "status": "failed", "error": str(ex)[:300]})
        if assets_done:
            geom = item_geoms.get(e["item_id"], aoi.geometry).intersection(aoi.geometry)
            try:
                cat.add(e["item_id"], geom, _when(e.get("date")), {**props_base, "geofetch:window": e["window"], "geofetch:group": e.get("group")}, assets_done)
                if lib:
                    for k, v in assets_done.items():
                        lib.record(path=Path(v["path"]), project=project.root, source=source.id, item=e["item_id"], asset=k, window=e["window"],
                                   date=e.get("date"), epsg=epsg if v["media_type"] == COG_TYPE else None, native_grid=bool(v.get("native_grid")),
                                   cover=geom, bytes_=v["size"], sha256=v["sha256"], source_href=v.get("source_href"))
            except Exception as ex:  # noqa: BLE001 — files are on disk; record the problem and keep going
                failed += 1
                _record(project, plan, {"item": e["item_id"], "asset": "*", "status": "failed", "error": f"catalogue entry: {ex}"[:300]})
    plan["execution"]["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan["status"] = "complete" if failed == 0 else "partial"
    project.save_plan(plan["id"], plan)
    if lib:
        lib.close()
    return {"fetched": done, "skipped": skipped, "failed": failed, "reused": reused}
