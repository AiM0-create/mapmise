"""Library reuse rules and recipe round trip — offline."""
import json
from datetime import date
from pathlib import Path

from shapely.geometry import box

from mapmise import recipe
from mapmise.library import Library
from mapmise.project import Project

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _file(tmp_path, name, content=b"x" * 100):
    f = tmp_path / name
    f.write_bytes(content)
    return f


def _rec(lib, f, project, *, cover=box(0, 0, 10, 10), native=True, epsg=32643, item="I1", asset="red"):
    lib.record(path=f, project=project, source="sentinel-2-l2a", item=item, asset=asset, window="2026-06", date="2026-06-04",
               epsg=epsg, native_grid=native, cover=cover, bytes_=f.stat().st_size, sha256="ab" * 32, source_href="https://x")


def test_reuse_requires_native_grid_same_projection_coverage_and_the_file(tmp_path):
    lib = Library(tmp_path / "lib.sqlite")
    a = tmp_path / "A"; a.mkdir()
    f = _file(tmp_path, "red.tif")
    _rec(lib, f, a)
    inside, outside = box(2, 2, 5, 5), box(8, 8, 12, 12)
    assert lib.reusable("sentinel-2-l2a", "I1", "red", inside, 32643).path == f.resolve()
    assert lib.reusable("sentinel-2-l2a", "I1", "red", outside, 32643) is None          # not fully covered
    assert lib.reusable("sentinel-2-l2a", "I1", "red", inside, 32644) is None           # other projection
    assert lib.reusable("sentinel-2-l2a", "I1", "nir", inside, 32643) is None           # other band
    assert lib.reusable("sentinel-2-l2a", "I1", "red", inside, 32643, exclude_project=a) is None
    g = _file(tmp_path, "red2.tif")
    _rec(lib, g, tmp_path / "B", native=False, item="I2")
    assert lib.reusable("sentinel-2-l2a", "I2", "red", inside, 32643) is None           # reprojected: never reused
    f.write_bytes(b"y" * 5)                                                              # changed on disk
    assert lib.reusable("sentinel-2-l2a", "I1", "red", inside, 32643) is None


def test_over_summary_and_forget_missing(tmp_path):
    lib = Library(tmp_path / "lib.sqlite")
    f, g = _file(tmp_path, "a.tif"), _file(tmp_path, "b.tif")
    _rec(lib, f, tmp_path / "A"); _rec(lib, g, tmp_path / "A", cover=box(50, 50, 60, 60), item="I2")
    assert [h.item for h in lib.over(box(1, 1, 2, 2))] == ["I1"]
    s = lib.summary()
    assert s["files"] == 2 and s["bytes"] == 200 and s["by_source"][0]["source"] == "sentinel-2-l2a"
    g.unlink()
    assert lib.forget_missing() == 1 and lib.summary()["files"] == 1


def test_recipe_round_trip_recreates_project_and_verifies(tmp_path):
    p = Project.init(tmp_path / "orig", "Orig", "flood in orig", FIXTURES / "chitradurga.geojson", date(2026, 8, 1), date(2026, 8, 31))
    plan = {"id": "P1", "kind": "vector", "source": "osm-roads", "needs": [], "windows": [], "acquire": [], "estimate": {"n_assets": 1},
            "status": "complete", "execution": {"transfers": [{"item": "osm-roads", "asset": "roads", "status": "ok", "sha256": "aa", "bytes": 5}]}}
    p.save_plan("P1", plan)
    p.save_request("R1", {"id": "R1", "ask": "roads", "plans": ["P1"], "status": "complete"})
    r = recipe.export(p)
    assert r["format"] == recipe.FORMAT and r["plans"][0]["expected"] == {"osm-roads/roads": {"sha256": "aa", "bytes": 5}}
    assert "execution" not in r["plans"][0] and len(json.dumps(r)) < 50_000
    q = recipe.create_project(r, tmp_path / "copy")
    assert q.meta.project_epsg == p.meta.project_epsg and abs(q.meta.aoi_area_km2 - p.meta.aoi_area_km2) < 1
    assert q.load_plan("P1")["status"] == "proposed" and q.list_requests()[0]["status"] == "proposed"
    v = recipe.verify(q)
    assert [x["file"] for x in v["missing"]] == ["osm-roads/roads"]
    pl = q.load_plan("P1"); pl["execution"] = {"transfers": [{"item": "osm-roads", "asset": "roads", "status": "ok", "sha256": "bb", "bytes": 5}]}
    q.save_plan("P1", pl)
    assert recipe.verify(q)["live"] == [{"source": "osm-roads", "file": "osm-roads/roads"}]  # OSM changes by design
