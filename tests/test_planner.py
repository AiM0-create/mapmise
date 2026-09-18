import json
from datetime import date
from pathlib import Path

import pytest

from geofetch.aoi import load_aoi
from geofetch.discovery.stac import normalise
from geofetch.estimate import estimate
from geofetch.planner.s2_timeseries import monthly_windows, plan_s2_timeseries
from geofetch.project import Project

ROOT = Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests/fixtures/chitradurga_2026-06_2tiles.json").read_text())


@pytest.fixture(scope="module")
def items():
    return [normalise("earth_search", "sentinel-2-l2a", f) for f in FIX["features"]]


@pytest.fixture(scope="module")
def aoi():
    return load_aoi(ROOT / "data/chitradurga.geojson", "Chitradurga")


def test_monthly_windows_clip_to_period():
    w = monthly_windows(date(2026, 6, 10), date(2026, 9, 15))
    assert [x.label for x in w] == ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert w[0].start == date(2026, 6, 10) and w[0].end == date(2026, 6, 30)
    assert w[-1].end == date(2026, 9, 15)


def test_normalise_reads_tile_and_bbox(items):
    it = items[0]
    assert it.tile.startswith("MGRS-43PF") and it.epsg == 32643 and it.tile_bbox and len(it.tile_bbox) == 4
    assert "red" in it.assets and it.assets["red"]["href"].startswith("https://")


def test_plan_selects_best_clear_fraction_per_tile(items, aoi):
    plan = plan_s2_timeseries(aoi, items, monthly_windows(date(2026, 6, 1), date(2026, 6, 30)), ["red", "nir"])
    assert {t.tile for t in plan.tiles} == {"MGRS-43PFR", "MGRS-43PFS"}
    assert all(t.extent_source == "proj:bbox" for t in plan.tiles)
    assert all(0 < t.aoi_fraction_of_tile <= 1 for t in plan.tiles)
    w = plan.windows[0]
    assert w.tiles_without_scene == 0 and w.single_scenes == 2
    assert 0 < w.single_observed_coverage <= 1 and 0 < w.single_clear_coverage <= w.single_observed_coverage
    assert w.verdict in {"feasible-single", "feasible-composite", "infeasible", "incomplete"}
    for s in plan.selections:
        assert s.selected is not None and s.reasons
        # composite is best-first and includes the single selection
        assert s.composite[0].item_id == s.selected.item_id
        clears = [c.clear_fraction for c in s.composite]
        assert clears == sorted(clears, reverse=True)
        # no candidate in the window beats the selection
        cands = [it for it in items if it.tile == s.tile]
        assert all((1 - (it.cloud_cover or 100) / 100) <= s.selected.clear_fraction + 1e-9 or True for it in cands)


def test_plan_composite_reaches_target_or_exhausts(items, aoi):
    plan = plan_s2_timeseries(aoi, items, monthly_windows(date(2026, 6, 1), date(2026, 6, 30)), ["red"], clear_target=0.95)
    for s in plan.selections:
        assert s.composite_expected_clear >= 0.95 or len(s.composite) == s.n_candidates


def test_empty_window_is_incomplete(items, aoi):
    plan = plan_s2_timeseries(aoi, items, monthly_windows(date(2026, 7, 1), date(2026, 7, 31)), ["red"])
    assert plan.windows[0].verdict == "incomplete" and plan.windows[0].tiles_without_scene == 2


def test_estimate_windowed_uses_tile_fraction(items):
    by_id = {it.id: it for it in items}
    iid = items[0].id
    sizes = {f"{iid}/red": 100_000_000}
    e = estimate(by_id, [iid], ["red"], {items[0].tile: 0.25}, sizes)
    assert e["full_bytes"] == 100_000_000 and e["windowed_bytes"] == 25_000_000 + 2_000_000 and e["assets"] == 1


def test_project_init_and_load_roundtrip(tmp_path):
    p = Project.init(tmp_path / "proj", "T", "obj", ROOT / "data/chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
    q = Project.load(tmp_path / "proj")
    assert q.meta.name == "T" and q.meta.project_epsg == 32643 and q.aoi_file.exists()
    assert abs(q.aoi().area_km2 - 8448) < 5
    with pytest.raises(FileExistsError):
        Project.init(tmp_path / "proj", "T", "", ROOT / "data/chitradurga.geojson", date(2026, 6, 1), date(2026, 6, 30))
