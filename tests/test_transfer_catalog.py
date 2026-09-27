"""Offline tests for the windowed fetch and the catalogue using a synthetic local raster."""
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box

from mapmise.catalog import COG_TYPE, Catalog, multihash_sha256
from mapmise.project import Project
from mapmise.transfer.window import fetch_window, sha256_of

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
    p = Project.init(tmp_path / "proj", "P", "", FIXTURES / "chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
    cat = Catalog(p.root, "P")
    r1 = fetch_window(str(synthetic), _aoi(), p.root / "data/src/static/ITEM1_a.tif", 32643, threads=2)
    a1 = {"a": {"path": r1.output, "media_type": COG_TYPE, "size": r1.output_bytes, "sha256": r1.sha256, "source_href": "https://x/a", "seconds": 1.0}}
    cat.add("ITEM1", _aoi(), datetime(2026, 6, 4, tzinfo=timezone.utc), {"mapmise:source": "src", "mapmise:window": "static"}, a1)
    assert cat.has_asset("ITEM1", "a", verify=True) and not cat.has_asset("ITEM1", "b")
    r2 = fetch_window(str(synthetic), _aoi(), p.root / "data/src/static/ITEM1_b.tif", 32643, threads=2)
    cat.add("ITEM1", _aoi(), None, {"mapmise:source": "src"}, {"b": {"path": r2.output, "media_type": COG_TYPE, "size": r2.output_bytes, "sha256": r2.sha256, "source_href": "https://x/b"}})
    it = cat.load_item("ITEM1")
    assert set(it["assets"]) == {"a", "b"} and it["assets"]["a"]["file:checksum"] == multihash_sha256(r1.sha256)
    assert cat.summary()[0]["source"] == "src"
    r1.output.write_bytes(b"x" * r1.output_bytes)
    assert cat.has_asset("ITEM1", "a") and not cat.has_asset("ITEM1", "a", verify=True)


def test_fill_value_never_defaults_to_zero():
    from mapmise.transfer.window import fill_value
    assert fill_value("uint8", None) == 255 and fill_value("int16", None) == -32768 and fill_value("float32", None) == -9999.0
    assert fill_value("uint8", 0) == 0          # a declared nodata is respected
    assert fill_value("uint8", None, 200) == 200  # registry override


def test_zero_valued_pixels_survive_clipping(tmp_path):
    """A product whose valid range includes 0 and declares no nodata (e.g. water occurrence) must keep its zeros."""
    path = tmp_path / "zeros.tif"
    with rasterio.open(path, "w", driver="GTiff", width=200, height=200, count=1, dtype="uint8", crs="EPSG:32643",
                       transform=from_origin(700000, 1600000, 10, 10), tiled=True, blockxsize=128, blockysize=128) as ds:
        ds.write(np.zeros((200, 200), dtype="uint8"), 1)
    r = fetch_window(str(path), _aoi_small(), tmp_path / "o.tif", 32643, threads=2)
    with rasterio.open(r.output) as ds:
        arr = ds.read(1)
        assert ds.nodata == 255 and (arr == 0).sum() > 0 and set(np.unique(arr)) <= {0, 255}


def _aoi_small():
    t = Transformer.from_crs("EPSG:32643", "EPSG:4326", always_xy=True)
    return box(*t.transform(700300, 1598500), *t.transform(701200, 1599500))


def test_item_dates_accept_year_month_and_full_dates():
    from mapmise.run import _when
    assert _when("2020").year == 2020 and _when("2026-08").month == 8 and _when("2021-04-22").day == 22
    assert _when("2026-08-15T00:39:45+00:00").day == 15 and _when(None) is None and _when("live") is None


def test_vrt_mosaic_is_relative_and_matches_the_tiles(tmp_path):
    from mapmise.prepare import write_vrt
    tiles = []
    for i, x0 in enumerate((700000, 702000)):  # two side-by-side 2 km tiles
        f = tmp_path / "data" / f"t{i}.tif"
        f.parent.mkdir(exist_ok=True)
        with rasterio.open(f, "w", driver="GTiff", width=200, height=100, count=1, dtype="uint8", crs="EPSG:32643",
                           transform=from_origin(x0, 1600000, 10, 10), nodata=255) as ds:
            ds.write(np.full((100, 200), i + 1, dtype="uint8"), 1)
        tiles.append(f)
    vrt = tmp_path / "data" / "vrt" / "m.vrt"
    vrt.parent.mkdir()
    write_vrt(vrt, tiles)
    assert 'relativeToVRT="1">../t0.tif' in vrt.read_text(encoding="utf-8")
    with rasterio.open(vrt) as ds:
        a = ds.read(1)
        assert a.shape == (100, 400) and (a[:, :200] == 1).all() and (a[:, 200:] == 2).all() and ds.nodata == 255


def test_file_names_are_valid_on_every_os():
    from mapmise.geo import safe_name
    assert safe_name('a:b/c\\d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_name("S2B_MSIL2A_20260612.tif") == "S2B_MSIL2A_20260612.tif"
