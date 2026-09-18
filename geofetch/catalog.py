"""Project catalogue: a static STAC catalogue describing every locally acquired asset.

    <project>/catalog/catalog.json          pystac Catalog (relative, self-contained links)
    <project>/catalog/items/<id>.json       one Item per acquired scene (all bands as assets)
    <project>/data/<collection>/<YYYY-MM>/<id>_<band>.tif   the rasters

QGIS ≥ 3.40 opens catalog.json directly; pystac reads it; this *is* the index of local data.
Provenance for each item: the source provider/collection/item id, source hrefs, plan id,
window, and a sha256 per file (STAC `file:checksum` multihash).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pystac
from shapely.geometry import mapping

from geofetch.discovery.stac import NormalisedItem
from geofetch.transfer.window import WindowResult, sha256_of

COG_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"


def multihash_sha256(hex_digest: str) -> str:
    return "1220" + hex_digest  # multihash: sha2-256 (0x12), 32 bytes (0x20)


class Catalog:
    def __init__(self, project_root: Path, project_name: str):
        self.root = Path(project_root)
        self.dir = self.root / "catalog"
        self.items_dir = self.dir / "items"
        self.file = self.dir / "catalog.json"
        self.name = project_name

    # -- read
    def item_ids(self) -> set[str]:
        return {p.stem for p in self.items_dir.glob("*.json")} if self.items_dir.exists() else set()

    def load_item(self, item_id: str) -> dict | None:
        f = self.items_dir / f"{item_id}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def has_asset(self, item_id: str, band: str, verify: bool = False) -> bool:
        """True if the item records this band and the file exists with the recorded size (and checksum if verify)."""
        it = self.load_item(item_id)
        a = (it or {}).get("assets", {}).get(band)
        if not a:
            return False
        path = self.items_dir / a["href"]
        if not path.exists() or path.stat().st_size != a.get("file:size"):
            return False
        return not verify or multihash_sha256(sha256_of(path)) == a.get("file:checksum")

    def summary(self) -> list[dict]:
        rows = []
        for iid in sorted(self.item_ids()):
            it = self.load_item(iid)
            p = it["properties"]
            rows.append({"id": iid, "window": p.get("geofetch:window"), "tile": p.get("grid:code"), "date": p["datetime"][:10],
                         "cloud": p.get("eo:cloud_cover"), "bands": sorted(it["assets"]), "plan": p.get("geofetch:plan"),
                         "requirement": p.get("geofetch:requirement")})
        return rows

    # -- write
    def add_item(self, src: NormalisedItem, aoi_geom, band_results: dict[str, WindowResult], plan_id: str, window: str,
                 requirement: str | None = None) -> Path:
        self.items_dir.mkdir(parents=True, exist_ok=True)
        geom = src.geometry.intersection(aoi_geom)
        first = next(iter(band_results.values()))
        item = pystac.Item(
            id=src.id, geometry=mapping(geom), bbox=list(geom.bounds), datetime=src.datetime,
            properties={
                "eo:cloud_cover": src.cloud_cover, "grid:code": src.tile, "sat:relative_orbit": src.relative_orbit,
                "sat:orbit_state": src.orbit_state, "proj:epsg": first.epsg,
                "geofetch:plan": plan_id, "geofetch:window": window, "geofetch:requirement": requirement,
                "geofetch:source": {"provider": src.provider, "collection": src.collection, "id": src.id},
                "geofetch:acquired": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        for band, r in band_results.items():
            item.add_asset(band, pystac.Asset(
                href=self._rel(r.output),
                media_type=COG_TYPE, roles=["data"],
                extra_fields={"eo:bands": [{"name": band}], "file:size": r.output_bytes, "file:checksum": multihash_sha256(r.sha256),
                              "proj:shape": [r.height, r.width], "geofetch:source_href": src.assets[band]["href"],
                              "geofetch:transfer_seconds": round(r.seconds, 1)},
            ))
        item.stac_extensions = [
            "https://stac-extensions.github.io/eo/v1.1.0/schema.json",
            "https://stac-extensions.github.io/file/v2.1.0/schema.json",
            "https://stac-extensions.github.io/projection/v1.1.0/schema.json",
        ]
        d = item.to_dict(include_self_link=False)
        existing = self.load_item(src.id)
        if existing:  # keep bands acquired earlier (possibly by another plan)
            d["assets"] = {**existing["assets"], **d["assets"]}
        f = self.items_dir / f"{src.id}.json"
        f.write_text(json.dumps(d, indent=1))
        self._write_root()
        return f

    def _rel(self, path: Path) -> str:
        return str(Path(*[".."] * 2) / path.relative_to(self.root))  # relative to catalog/items/

    def _write_root(self) -> None:
        cat = {
            "type": "Catalog", "stac_version": "1.0.0", "id": self.name.lower().replace(" ", "-"),
            "description": f"geofetch project catalogue: {self.name}",
            "links": [{"rel": "root", "href": "./catalog.json", "type": "application/json"}]
            + [{"rel": "item", "href": f"./items/{iid}.json", "type": "application/geo+json"} for iid in sorted(self.item_ids())],
        }
        self.file.write_text(json.dumps(cat, indent=1))
