from datetime import date

import pytest

from geofetch.objectives import TEMPLATES, match_objective, resolve
from geofetch.planner.s2_timeseries import pre_post_windows


def test_every_template_has_a_supported_or_explained_requirement():
    for t in TEMPLATES.values():
        assert t.requirements
        for r in t.requirements:
            assert r.priority in {"required", "recommended", "optional"} and r.why
            assert r.supported or r.unsupported_reason
            if r.supported:
                assert r.provider and r.collection and r.bands


@pytest.mark.parametrize("text,expected", [
    ("Assess agricultural drought during the 2026 monsoon", "vegetation_drought"),
    ("Map flood inundation after the cyclone", "flood_change_detection"),
    ("Monthly NDVI crop monitoring for kharif", "vegetation_timeseries"),
    ("Reservoir water spread over the dry season", "surface_water"),
])
def test_keyword_matching(text, expected):
    tpl, matches = resolve(text)
    assert tpl is not None and tpl.id == expected and matches[0].hits


def test_no_match_and_explicit_template():
    tpl, matches = resolve("something unrelated about roads")
    assert tpl is None and matches == []
    tpl, _ = resolve("anything", "surface_water")
    assert tpl.id == "surface_water"
    with pytest.raises(KeyError):
        resolve("anything", "nope")


def test_ambiguous_objective_returns_none_with_candidates():
    # one hit each for drought and flood → tie → caller must choose
    tpl, matches = resolve("drought and flood risk")
    assert tpl is None and {m.template.id for m in matches} == {"vegetation_drought", "flood_change_detection"}


def test_pre_post_windows():
    w = pre_post_windows(date(2026, 8, 15), pre_days=30, post_days=20, gap_days=2)
    assert [x.label for x in w] == ["pre", "post"]
    assert w[0].end == date(2026, 8, 13) and w[0].start == date(2026, 7, 14)
    assert w[1].start == date(2026, 8, 17) and w[1].end == date(2026, 9, 6)
