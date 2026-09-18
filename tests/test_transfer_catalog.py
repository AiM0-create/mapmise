"""Offline tests for the windowed fetch and the catalogue using a synthetic local raster."""
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box

from geofetch.catalog import COG_TYPE, Catalog, multihash_sha256
from geofetch.project import Project
from geofetch.transfer.window import fetch_window, sha256_of

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def synthetic(tmp_path):
    path = tmp_path / "src.tif"
    data = (np.arange(400, dtype=np.uint16)[:, None] + 1).repeat(400, axis=1)
    with rasterio.open(path, "w", driver="GTiff", width=400, height=400, count=1, dtype="uint16", crs="EPSG:32643",
                       transform=from_origin(700000, 1600000, 10, 10), nodata=0, tiled=True, blockxsize=128, blockysize=128) as ds:
        ds.write(data, 1)
    return path


def _aoi():
    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    return box(*t.transform(701000, 1597000), *t.transform(702000, 1598500))


def test_fetch_window_clips_and_writes_cog(synthetic, tmp_path):
    out = tmp_path / "out.tif"
    r = fetch_window(str(synthetic), _aoi(), out, 32643, threads=4)
    assert r.sha256 == sha256_of(out)
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 32643 and ds.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
        arr = ds.read(1)
        assert 95 <= arr.shape[1] <= 105 and 145 <= arr.shape[0] <= 155 and (arr[arr > 0] >= 150).all()


def test_fetch_window_reprojects(synthetic, tmp_path):
    r = fetch_window(str(synthetic), _aoi(), tmp_path / "o.tif", 32644, threads=2)
    with rasterio.open(r.output) as ds:
        assert ds.crs.to_epsg() == 32644 and ds.read(1).max() > 0


def test_catalog_add_merge_verify(tmp_path, synthetic):
    p = Project.init(tmp_path / "proj", "P", "", ROOT / "data/chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
    cat = Catalog(p.root, "P")
    r1 = fetch_window(str(synthetic), _aoi(), p.root / "data/src/static/ITEM1_a.tif", 32643, threads=2)
    a1 = {"a": {"path": r1.output, "media_type": COG_TYPE, "size": r1.output_bytes, "sha256": r1.sha256, "source_href": "https://x/a", "seconds": 1.0}}
    cat.add("ITEM1", _aoi(), datetime(2026, 6, 4, tzinfo=timezone.utc), {"geofetch:source": "src", "geofetch:window": "static"}, a1)
    assert cat.has_asset("ITEM1", "a", verify=True) and not cat.has_asset("ITEM1", "b")
    r2 = fetch_window(str(synthetic), _aoi(), p.root / "data/src/static/ITEM1_b.tif", 32643, threads=2)
    cat.add("ITEM1", _aoi(), None, {"geofetch:source": "src"}, {"b": {"path": r2.output, "media_type": COG_TYPE, "size": r2.output_bytes, "sha256": r2.sha256, "source_href": "https://x/b"}})
    it = cat.load_item("ITEM1")
    assert set(it["assets"]) == {"a", "b"} and it["assets"]["a"]["file:checksum"] == multihash_sha256(r1.sha256)
    assert cat.summary()[0]["source"] == "src"
    r1.output.write_bytes(b"x" * r1.output_bytes)
    assert cat.has_asset("ITEM1", "a") and not cat.has_asset("ITEM1", "a", verify=True)
