"""Personal library: one index of every file geofetch has acquired, across all projects.

Uses a single SQLite file (standard library; no server): $GEOFETCH_LIBRARY, else
$XDG_DATA_HOME/geofetch/library.sqlite, else ~/.local/share/geofetch/library.sqlite.

The project folders remain the source of truth; the library is an index over them and can be
rebuilt at any time with `geofetch library scan`.

Reuse rule (exactness first): a file may stand in for a download only if it is still on the source's
own pixel grid (not reprojected), is in the projection the new project wants, still exists with its
recorded checksum, and covers everything the new project needs from that item. Re-clipping such a
file gives the same pixels as downloading again.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shapely import wkt
from shapely.geometry.base import BaseGeometry

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path        TEXT PRIMARY KEY,   -- absolute path of the file on disk
    project     TEXT NOT NULL,      -- absolute path of the project folder
    source      TEXT NOT NULL,      -- registry id
    item        TEXT NOT NULL,      -- provider item id
    asset       TEXT NOT NULL,      -- canonical asset/band
    window      TEXT,
    date        TEXT,
    epsg        INTEGER,            -- projection of the file
    native_grid INTEGER NOT NULL,   -- 1 if the file is on the source's own pixel grid (not reprojected)
    cover_wkt   TEXT NOT NULL,      -- area the file covers, WGS84
    bytes       INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    source_href TEXT,
    acquired    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS files_item ON files (source, item, asset);
CREATE INDEX IF NOT EXISTS files_project ON files (project);
"""


def library_path() -> Path:
    if os.environ.get("GEOFETCH_LIBRARY"):
        return Path(os.environ["GEOFETCH_LIBRARY"]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "geofetch" / "library.sqlite"


@dataclass
class Holding:
    path: Path
    project: Path
    source: str
    item: str
    asset: str
    window: str | None
    date: str | None
    epsg: int | None
    native_grid: bool
    cover: BaseGeometry
    bytes: int
    sha256: str


class Library:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or library_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # -- write
    def record(self, *, path: Path, project: Path, source: str, item: str, asset: str, window: str | None, date: str | None,
               epsg: int | None, native_grid: bool, cover: BaseGeometry, bytes_: int, sha256: str, source_href: str | None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(Path(path).resolve()), str(Path(project).resolve()), source, item, asset, window, date, epsg, int(native_grid),
             cover.wkt, int(bytes_), sha256, source_href, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.db.commit()

    def forget_missing(self) -> int:
        """Drop entries whose file no longer exists. Returns how many were removed."""
        gone = [p for (p,) in self.db.execute("SELECT path FROM files") if not Path(p).exists()]
        self.db.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in gone])
        self.db.commit()
        return len(gone)

    # -- read
    def _rows(self, where: str = "", args: tuple = ()) -> list[Holding]:
        out = []
        for r in self.db.execute(f"SELECT path, project, source, item, asset, window, date, epsg, native_grid, cover_wkt, bytes, sha256 FROM files {where}", args):
            out.append(Holding(Path(r[0]), Path(r[1]), r[2], r[3], r[4], r[5], r[6], r[7], bool(r[8]), wkt.loads(r[9]), r[10], r[11]))
        return out

    def reusable(self, source: str, item: str, asset: str, needed: BaseGeometry, epsg: int, exclude_project: Path | None = None) -> Holding | None:
        """A file already on disk that gives exactly what a download would: same item and asset, native grid,
        same projection, still present with its size, and covering `needed` (WGS84)."""
        for h in self._rows("WHERE source = ? AND item = ? AND asset = ?", (source, item, asset)):
            if exclude_project and h.project == Path(exclude_project).resolve():
                continue
            if not h.native_grid or h.epsg != epsg or not h.path.exists() or h.path.stat().st_size != h.bytes:
                continue
            if h.cover.buffer(1e-9).covers(needed):
                return h
        return None

    def items(self, source: str, epsg: int) -> set[str]:
        """Item ids of a source held on their native grid in this projection (candidates for reuse)."""
        return {r[0] for r in self.db.execute("SELECT DISTINCT item FROM files WHERE source = ? AND native_grid = 1 AND epsg = ?", (source, epsg))}

    def over(self, geom: BaseGeometry) -> list[Holding]:
        """Everything whose covered area intersects `geom` (WGS84)."""
        minx, miny, maxx, maxy = geom.bounds
        return [h for h in self._rows() if h.cover.intersects(geom)]

    def summary(self) -> dict:
        rows = self.db.execute("SELECT source, COUNT(*), SUM(bytes), COUNT(DISTINCT project) FROM files GROUP BY source ORDER BY SUM(bytes) DESC").fetchall()
        projects = self.db.execute("SELECT project, COUNT(*), SUM(bytes) FROM files GROUP BY project ORDER BY project").fetchall()
        total = self.db.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM files").fetchone()
        return {"library": str(self.path), "files": total[0], "bytes": total[1],
                "by_source": [{"source": s, "files": n, "bytes": b, "projects": k} for s, n, b, k in rows],
                "by_project": [{"project": p, "files": n, "bytes": b, "exists": Path(p).exists()} for p, n, b in projects]}


def scan(project_root: Path, lib: Library | None = None) -> int:
    """Index an existing project's catalogue into the library (for projects made before the library existed)."""
    from geofetch.catalog import Catalog
    from geofetch.project import Project
    from shapely.geometry import shape

    lib = lib or Library()
    p = Project.load(project_root)
    cat = Catalog(p.root, p.meta.name)
    native = _native_items(p)
    n = 0
    for iid in cat.item_ids():
        it = cat.load_item(iid)
        props = it["properties"]
        cover = shape(it["geometry"])
        for key, a in it["assets"].items():
            f = (cat.items_dir / a["href"]).resolve()
            if not f.exists():
                continue
            lib.record(path=f, project=p.root, source=props.get("geofetch:source", "?"), item=iid, asset=key,
                       window=props.get("geofetch:window"), date=(props.get("datetime") or "")[:10] or None,
                       epsg=props.get("proj:epsg") or p.meta.project_epsg,
                       native_grid=bool(a["geofetch:native_grid"]) if "geofetch:native_grid" in a else iid in native,
                       cover=cover, bytes_=a["file:size"], sha256=a["file:checksum"][4:], source_href=a.get("geofetch:source_href"))
            n += 1
    return n


def _native_items(p) -> set[str]:
    """Items whose files are on the source's own grid, recovered for projects made before the library existed:
    geofetch reprojects only when the source projection differs from the project's, and the project's cached
    catalogue searches record each item's projection."""
    from geofetch.registry import load_sources
    from geofetch.run import item_footprints  # noqa: F401  (same search cache)
    from geofetch.drivers import stac as stac_driver
    sources = load_sources()
    out = set()
    for pid in p.list_plans():
        pl = p.load_plan(pid)
        src = sources.get(pl["source"])
        if not src or src.driver != "stac" or pl["kind"] not in ("scenes", "layer"):
            continue
        q = pl["query"]["query"]
        query = stac_driver.Query(q["source_id"], q["provider"], q["collection"], tuple(q["bbox"]), q["start"], q["end"], tuple(q["assets"]), q.get("extra", {}))
        try:
            items, _ = stac_driver.search(src, query, p.cache_dir)
        except Exception:  # noqa: BLE001 — no cache and no network: leave those items non-reusable
            continue
        out |= {i.id for i in items if i.epsg == p.meta.project_epsg}
    return out


def to_json(h: Holding) -> dict:
    return {"path": str(h.path), "project": str(h.project), "source": h.source, "item": h.item, "asset": h.asset,
            "window": h.window, "date": h.date, "bytes": h.bytes, "native_grid": h.native_grid, "cover": json.loads(json.dumps(h.cover.__geo_interface__))}
