"""Planner tests on recorded/synthetic items — no network."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from shapely.geometry import box

from geofetch.aoi import load_aoi
from geofetch.drivers.stac import Item, normalise
from geofetch.planner.optical import monthly_windows, plan_optical, pre_post_windows
from geofetch.planner.sar import plan_sar
from geofetch.registry import load_sources

ROOT = Path(__file__).resolve().parent.parent
FIX = json.loads((ROOT / "tests/fixtures/chitradurga_2026-06_2tiles.json").read_text())


@pytest.fixture(scope="module")
def aoi():
    return load_aoi(ROOT / "tests/fixtures/chitradurga.geojson", "Chitradurga")


@pytest.fixture(scope="module")
def s2_items():
    return [normalise(load_sources()["sentinel-2-l2a"], f) for f in FIX["features"]]


def test_windows():
    w = monthly_windows(date(2026, 6, 10), date(2026, 9, 15))
    assert [x.label for x in w] == ["2026-06", "2026-07", "2026-08", "2026-09"] and w[-1].end == date(2026, 9, 15)
    p = pre_post_windows(date(2026, 8, 15), 30, 20, 2)
    assert (p[0].end, p[1].start) == (date(2026, 8, 13), date(2026, 8, 17))


def test_optical_planner_selects_best_and_reports_verdict(aoi, s2_items):
    plan = plan_optical(aoi, s2_items, monthly_windows(date(2026, 6, 1), date(2026, 6, 30)), ["red", "nir"])
    assert {t.tile for t in plan.tiles} == {"MGRS-43PFR", "MGRS-43PFS"} and all(t.extent_source == "proj:bbox" for t in plan.tiles)
    w = plan.windows[0]
    assert w.verdict in {"feasible-single", "feasible-composite", "infeasible", "incomplete"} and 0 < w.single_clear_coverage <= w.single_observed_coverage
    for s in plan.selections:
        assert s.selected and s.reasons and s.composite[0].item_id == s.selected.item_id
    assert len(plan.acquire("single")) == 2 and len(plan.acquire("composite")) >= 2


def _s1(iid, orbit, state, day, minx, maxx):
    return Item("sentinel-1-rtc", iid, datetime(2026, 8, day, tzinfo=timezone.utc), box(minx, 13.4, maxx, 15.2), None, str(orbit), orbit, state,
                32643, None, {"vv": {"href": f"https://x/{iid}_vv.tif"}, "vh": {"href": f"https://x/{iid}_vh.tif"}})


def test_sar_planner_keeps_same_orbit_across_pair(aoi):
    # orbit 63 covers the whole AOI on Aug 3 and Aug 15; orbit 165 covers only the west, on Aug 9 and Aug 21
    items = [_s1("A_pre", 63, "descending", 3, 75.9, 77.1), _s1("A_post", 63, "descending", 15, 75.9, 77.1),
             _s1("B_pre", 165, "descending", 9, 75.9, 76.5), _s1("B_post", 165, "descending", 21, 75.9, 76.5)]
    w = pre_post_windows(date(2026, 8, 12), 30, 30)
    plan = plan_sar(aoi, items, w, ["vv", "vh"], same_orbit=True, reference={"pre": date(2026, 8, 12), "post": date(2026, 8, 12)})
    assert [r.verdict for r in plan.windows] == ["feasible-single", "feasible-single"]
    assert plan.acquire() == [("post", "A_post"), ("pre", "A_pre")]
    assert all("orbit 63" in r.verdict_text for r in plan.windows)


def test_sar_planner_reports_missing_window(aoi):
    items = [_s1("A_pre", 63, "descending", 3, 75.9, 77.1)]
    plan = plan_sar(aoi, items, pre_post_windows(date(2026, 8, 12), 30, 30), ["vv"], reference=None)
    assert plan.windows[1].verdict == "incomplete"


def test_overlapping_tiles_weak_tile_does_not_claim_the_aoi(aoi):
    """Landsat-style overlap: tile 'A' sorts first alphabetically but its real footprint barely touches the AOI;
    tile 'B' images the whole AOI. B must carry the AOI, A must be dropped, and the verdict must not be 'incomplete'."""
    minx, miny, maxx, maxy = aoi.bbox

    def it(iid, group, geom, day, cc):
        return Item("landsat-c2-l2", iid, datetime(2020, 3, day, tzinfo=timezone.utc), geom, cc, group, None, None, 32643,
                    None, {"red": {"href": f"https://x/{iid}.tif"}})
    sliver = box(maxx - 0.02, miny, maxx + 1.0, maxy)            # A: only the eastern edge
    whole = box(minx - 0.5, miny - 0.5, maxx + 0.5, maxy + 0.5)   # B: everything
    items = [it("A1", "143/051", sliver, 5, 0.0), it("B1", "144/051", whole, 6, 5.0), it("B2", "144/051", whole, 22, 40.0)]
    plan = plan_optical(aoi, items, monthly_windows(date(2020, 3, 1), date(2020, 3, 31)), ["red"])
    assert [t.tile for t in plan.tiles] == ["144/051"]
    w = plan.windows[0]
    assert w.verdict == "feasible-single" and w.single_observed_coverage > 0.99 and w.single_clear_coverage > 0.9
    assert plan.acquire() == [("2020-03", "B1")]
