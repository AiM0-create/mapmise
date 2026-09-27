"""The registry and resolver are data-driven; these tests guard the contract, not any workflow."""
import re

import pytest

from mapmise.registry import DRIVERS, SHAPES, THEMES, load_ask_rules, load_sources
from mapmise.resolver import parse_ask, resolve_needs

BBOX_IN = [76.0, 13.5, 77.0, 15.0]


def test_registry_entries_are_valid():
    for s in load_sources().values():
        assert set(s.themes) <= THEMES and s.shape in SHAPES and s.driver in DRIVERS
        assert s.license and s.description
        if s.driver == "stac":
            assert s.access["provider"] and s.access["collection"] and s.access["assets"]
        if s.shape == "series":
            assert s.temporal["type"] == "series"


def test_ask_rules_are_valid_and_every_need_has_a_source_somewhere():
    sources = load_sources()
    themes_available = {t for s in sources.values() for t in s.themes}
    for r in load_ask_rules():
        assert r.keywords and r.needs
        for n in r.needs:
            assert n.why
            assert n.theme in themes_available or n.theme == "soil_moisture", f"rule {r.id}: theme {n.theme} has no source yet"


@pytest.mark.parametrize("text,rules", [
    ("I heard a flood happened in Chitradurga in August", {"flood"}),
    ("Assess agricultural drought during the 2026 monsoon", {"drought", "rainfall"}),
    ("Urban expansion of Bengaluru over five years", {"urban"}),
    ("Reservoir water spread through the dry season", {"water_bodies"}),
    ("Road accessibility of villages after landslides", {"access", "terrain"}),
    ("Slope and watershed delineation", {"terrain"}),
])
def test_asks_compose_from_rules_not_workflows(text, rules):
    a = parse_ask(text)
    assert set(a.matched_rules) == rules
    assert a.needs and all(n.priority in {"required", "recommended", "optional"} for n in a.needs)


def test_unknown_ask_yields_no_needs():
    assert parse_ask("what is the meaning of life").needs == []


def test_resolution_prefers_cloud_independent_when_asked():
    a = parse_ask("flood in the district")
    sar = next(n for n in a.needs if n.theme == "sar")
    res = resolve_needs([sar], BBOX_IN, "2026-06-01", "2026-09-15", "IND")[0]
    assert res.chosen is not None and not res.chosen.cloud_dependent


def test_resolution_respects_override_and_reports_unmet():
    a = parse_ask("drought over the district")
    veg = next(n for n in a.needs if n.theme == "vegetation")
    r = resolve_needs([veg], BBOX_IN, "2026-06-01", "2026-09-15", "IND", overrides={"vegetation:series": "sentinel-1-rtc"})[0]
    assert r.chosen.id == "sentinel-1-rtc"
    sm = next(n for n in a.needs if n.theme == "soil_moisture")
    assert resolve_needs([sm], BBOX_IN, "2026-06-01", "2026-09-15", "IND")[0].chosen is None


def test_period_and_coverage_filters():
    sources = load_sources()
    assert not sources["chirps-monthly"].covers_bbox([10, 60, 20, 70])  # outside ±50° latitude band
    assert sources["sentinel-2-l2a"].covers_period("2020-01-01", "2020-12-31")
    assert not sources["sentinel-2-l2a"].covers_period("2010-01-01", "2012-12-31")
