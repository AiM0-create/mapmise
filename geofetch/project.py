"""Project store: a directory with project.json, the AOI, plans, a static STAC catalogue and caches.

Layout:
    <dir>/project.json      objective, period, AOI reference, project CRS
    <dir>/aoi/aoi.geojson   AOI as saved at init (WGS84, dissolved)
    <dir>/requests/<id>.json what was asked, the needs, the chosen sources and their plan ids
    <dir>/plans/<id>.json   one acquisition plan per source (proposed → approved → running → complete/partial)
    <dir>/catalog/          static STAC catalogue of acquired assets (phase 2)
    <dir>/.cache/           catalogue search pages and HEAD sizes; safe to delete
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path

import geopandas as gpd

from geofetch.aoi import AOI, load_aoi

PROJECT_FILE = "project.json"
FORMAT_VERSION = 1


@dataclass
class ProjectMeta:
    name: str
    objective: str
    start: str  # ISO date
    end: str  # ISO date
    aoi_path: str  # relative to project dir
    aoi_source: str  # original file the AOI came from
    aoi_area_km2: float
    project_epsg: int
    created: str
    format_version: int = FORMAT_VERSION
    country_iso3: str | None = None  # looked up once from the AOI centroid; used by per-country URL templates
    aoi_attribution: str | None = None  # licence/attribution of the AOI geometry when it came from a geocoder


class Project:
    def __init__(self, root: Path, meta: ProjectMeta):
        self.root = Path(root)
        self.meta = meta

    # -- paths
    @property
    def aoi_file(self) -> Path:
        return self.root / self.meta.aoi_path

    @property
    def plans_dir(self) -> Path:
        return self.root / "plans"

    @property
    def catalog_dir(self) -> Path:
        return self.root / "catalog"

    @property
    def cache_dir(self) -> Path:
        return self.root / ".cache"

    @property
    def period(self) -> tuple[date, date]:
        return date.fromisoformat(self.meta.start), date.fromisoformat(self.meta.end)

    def aoi(self) -> AOI:
        return load_aoi(self.aoi_file, self.meta.name)

    # -- lifecycle
    @classmethod
    def init(cls, root: Path, name: str, objective: str, aoi_source: Path, start: date, end: date, epsg: int | None = None) -> "Project":
        """Create a project from an AOI file (any OGR-readable vector)."""
        aoi = load_aoi(aoi_source, name)
        return cls.create(root, name, objective, aoi.geometry, str(Path(aoi_source).resolve()), start, end, epsg)

    @classmethod
    def create(cls, root: Path, name: str, objective: str, geometry, aoi_source: str, start: date, end: date,
               epsg: int | None = None, aoi_attribution: str | None = None) -> "Project":
        """Create a project from a WGS84 geometry; `aoi_source` records where it came from (file path or geocoder reference)."""
        root = Path(root)
        if (root / PROJECT_FILE).exists():
            raise FileExistsError(f"{root / PROJECT_FILE} already exists")
        if end < start:
            raise ValueError("end date is before start date")
        for d in ("aoi", "plans", "requests", "catalog", ".cache"):
            (root / d).mkdir(parents=True, exist_ok=True)
        gpd.GeoDataFrame({"name": [name]}, geometry=[geometry], crs="EPSG:4326").to_file(root / "aoi" / "aoi.geojson", driver="GeoJSON")
        aoi = load_aoi(root / "aoi" / "aoi.geojson", name)
        meta = ProjectMeta(
            name=name, objective=objective, start=start.isoformat(), end=end.isoformat(),
            aoi_path="aoi/aoi.geojson", aoi_source=aoi_source, aoi_area_km2=round(aoi.area_km2, 1),
            project_epsg=epsg or aoi.utm_epsg, created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            aoi_attribution=aoi_attribution,
        )
        p = cls(root, meta)
        p.save()
        return p

    @classmethod
    def load(cls, root: Path) -> "Project":
        root = Path(root)
        f = root / PROJECT_FILE
        if not f.exists():
            raise FileNotFoundError(f"no {PROJECT_FILE} in {root}")
        return cls(root, ProjectMeta(**json.loads(f.read_text())))

    def save(self) -> None:
        (self.root / PROJECT_FILE).write_text(json.dumps(asdict(self.meta), indent=2))

    # -- plans
    def save_plan(self, plan_id: str, data: dict) -> Path:
        self.plans_dir.mkdir(exist_ok=True)
        f = self.plans_dir / f"{plan_id}.json"
        f.write_text(json.dumps(data, indent=1, default=str))
        return f

    def load_plan(self, plan_id: str) -> dict:
        f = self.plans_dir / f"{plan_id}.json"
        if not f.exists():
            raise FileNotFoundError(f"no plan {plan_id} in {self.plans_dir}")
        return json.loads(f.read_text())

    def list_plans(self) -> list[str]:
        return sorted(p.stem for p in self.plans_dir.glob("*.json"))

    # -- requests
    @property
    def requests_dir(self) -> Path:
        return self.root / "requests"

    def save_request(self, req_id: str, data: dict) -> Path:
        self.requests_dir.mkdir(exist_ok=True)
        f = self.requests_dir / f"{req_id}.json"
        f.write_text(json.dumps(data, indent=1, default=str))
        return f

    def list_requests(self) -> list[dict]:
        return [json.loads(f.read_text()) for f in sorted(self.requests_dir.glob("*.json"))] if self.requests_dir.exists() else []

    # -- caches
    def size_cache(self) -> dict[str, int]:
        f = self.cache_dir / "asset_sizes.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def save_size_cache(self, sizes: dict[str, int]) -> None:
        self.cache_dir.mkdir(exist_ok=True)
        (self.cache_dir / "asset_sizes.json").write_text(json.dumps(sizes))
