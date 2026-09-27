"""Large areas are refused before any work or folder, imagery is not planned for huge areas, one app copy runs."""
import json
import sys

import pytest

from mapmise import engine
from mapmise.registry import Need
from mapmise.resolver import resolve_needs


def _box_file(tmp_path, w, s, e, n):
    f = tmp_path / "area.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry":
        {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}}]}), encoding="utf-8")
    return f


def test_large_area_is_refused_before_any_project_is_created(tmp_path):
    aoi = _box_file(tmp_path, 70, 10, 76, 16)  # ~430,000 km²
    opts = engine.Options(aoi=str(aoi), workspace=str(tmp_path / "ws"))
    with pytest.raises(engine.LargeArea) as e:
        engine.prepare("forest loss here in 2024", opts, log=lambda m: None)
    assert e.value.area_km2 > engine.LARGE_AREA_KM2 and "smaller place" in str(e.value)
    assert not (tmp_path / "ws").exists() or not any((tmp_path / "ws").iterdir())


def test_imagery_is_not_chosen_for_huge_areas_but_area_wide_products_are():
    need = Need("vegetation", "series", "required", "greenness")
    bbox, start, end = [-60, -15, -50, -5], "2025-01-01", "2025-12-31"
    [small] = resolve_needs([need], bbox, start, end, None, area_km2=5_000)
    [huge] = resolve_needs([need], bbox, start, end, None, area_km2=8_000_000)
    assert small.chosen.planner == "optical"
    assert huge.chosen.id == "modis-ndvi-16day"


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows mutex is exercised by the release smoke test")
def test_only_one_app_copy_can_hold_the_claim(tmp_path):
    import subprocess
    from mapmise.proc import claim_single_instance
    assert claim_single_instance(tmp_path)
    other = subprocess.run([sys.executable, "-c", f"from mapmise.proc import claim_single_instance as c; from pathlib import Path; "
                            f"print(c(Path({str(tmp_path)!r})))"], capture_output=True, text=True)
    assert other.stdout.strip() == "False"
