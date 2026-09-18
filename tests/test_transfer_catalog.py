"""Offline tests for the windowed fetch, catalogue and executor using a local synthetic raster."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from geofetch.catalog import Catalog, multihash_sha256
from geofetch.discovery.stac import NormalisedItem
from geofetch.project import Project
from geofetch.run import gaps, targets
from geofetch.transfer.window import fetch_window, sha256_of

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def synthetic(tmp_path):
    """A 400×400 uint16 raster in EPSG:32643 with value = row index + 1 (so 0 stays nodata)."""
    path = tmp_path / "src.tif"
    data = (np.arange(400, dtype=np.uint16)[:, None] + 1).repeat(400, axis=1)
    tr = from_origin(700000, 1600000, 10, 10)
    with rasterio.open(path, "w", driver="GTiff", width=400, height=400, count=1, dtype="uint16", crs="EPSG:32643",
                       transform=tr, nodata=0, tiled=True, blockxsize=128, blockysize=128) as ds:
        ds.write(data, 1)
    return path, data, tr


def _aoi_wgs84():
    # a box well inside the raster: x 701000–702000, y 1597000–1598500 (UTM 43N) → WGS84 via pyproj
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    x0, y0 = t.transform(701000, 1597000)
    x1, y1 = t.transform(702000, 1598500)
    return box(x0, y0, x1, y1)


def test_fetch_window_clips_masks_and_writes_cog(synthetic, tmp_path):
    path, data, tr = synthetic
    out = tmp_path / "out.tif"
    r = fetch_window(str(path), _aoi_wgs84(), out, 32643, threads=4)
    assert r.sha256 == sha256_of(out) and r.output_bytes == out.stat().st_size
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 32643 and ds.nodata == 0
        assert ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        arr = ds.read(1)
        # AOI is ~100×150 px; pixels inside carry the source row value, corners of the bbox are masked only if outside polygon (box → none)
        assert 95 <= arr.shape[1] <= 105 and 145 <= arr.shape[0] <= 155
        assert arr.max() > 0 and (arr[arr > 0] >= 150).all()  # rows ≥ 150 (y ≤ 1598500)


def test_fetch_window_reprojects_when_project_crs_differs(synthetic, tmp_path):
    path, _, _ = synthetic
    out = tmp_path / "out44.tif"
    r = fetch_window(str(path), _aoi_wgs84(), out, 32644, threads=2)
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 32644 and r.epsg == 32644 and ds.read(1).max() > 0


def test_catalog_add_has_merge(tmp_path, synthetic):
    path, _, _ = synthetic
    p = Project.init(tmp_path / "proj", "P", "", ROOT / "data/chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
    cat = Catalog(p.root, "P")
    src = NormalisedItem("earth_search", "sentinel-2-l2a", "ITEM1", datetime(2026, 6, 4, tzinfo=timezone.utc), _aoi_wgs84(), 12.0,
                         "MGRS-43PFR", 5, "descending", 32643, None, None, {"red": {"href": str(path)}, "nir": {"href": str(path)}})
    r_red = fetch_window(str(path), _aoi_wgs84(), p.root / "data/sentinel-2-l2a/2026-06/ITEM1_red.tif", 32643, threads=2)
    cat.add_item(src, p.aoi().geometry, {"red": r_red}, "plan-1", "2026-06")
    assert cat.has_asset("ITEM1", "red") and not cat.has_asset("ITEM1", "nir")
    assert cat.has_asset("ITEM1", "red", verify=True)
    r_nir = fetch_window(str(path), _aoi_wgs84(), p.root / "data/sentinel-2-l2a/2026-06/ITEM1_nir.tif", 32643, threads=2)
    cat.add_item(src, p.aoi().geometry, {"nir": r_nir}, "plan-2", "2026-06")
    it = cat.load_item("ITEM1")
    assert set(it["assets"]) == {"red", "nir"} and it["assets"]["red"]["file:checksum"] == multihash_sha256(r_red.sha256)
    root = json.loads(cat.file.read_text())
    assert [l["href"] for l in root["links"] if l["rel"] == "item"] == ["./items/ITEM1.json"]
    # tamper → no longer verified
    (p.root / "data/sentinel-2-l2a/2026-06/ITEM1_red.tif").write_bytes(b"x" * r_red.output_bytes)
    assert cat.has_asset("ITEM1", "red") and not cat.has_asset("ITEM1", "red", verify=True)


def test_targets_and_gaps_follow_mode(tmp_path):
    p = Project.init(tmp_path / "proj", "P", "", ROOT / "data/chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
    plan = {"bands": ["red"], "selections": [
        {"window": "2026-06", "tile": "T1", "selected": {"item_id": "A"}, "composite": [{"item_id": "A"}, {"item_id": "B"}]},
        {"window": "2026-06", "tile": "T2", "selected": None, "composite": []},
    ]}
    assert targets(plan, "single") == [("2026-06", "A")]
    assert targets(plan, "composite") == [("2026-06", "A"), ("2026-06", "B")]
    g = gaps(p, plan, "composite")
    assert [(x["item"], x["tile"]) for x in g] == [("A", "T1"), ("B", "T1")]
