from datetime import date

import pytest

from mapmise.understand import parse_period, place_candidates

TODAY = date(2026, 9, 26)


@pytest.mark.parametrize("text,start,end,event", [
    ("I heard a flood happened in Chitradurga in August 2026", date(2026, 8, 1), date(2026, 8, 31), None),
    ("NDVI from June to September 2026 over Tumakuru", date(2026, 6, 1), date(2026, 9, 30), None),
    ("rainfall Jan 2024 to Mar 2025", date(2024, 1, 1), date(2025, 3, 31), None),
    ("urban expansion of Bengaluru between 2017 and 2025", date(2017, 1, 1), date(2025, 12, 31), None),
    ("drought during the 2026 monsoon", date(2026, 6, 1), date(2026, 9, 26), None),
    ("forest loss since 2019", date(2019, 1, 1), TODAY, None),
    ("reservoir levels over the last two years", date(2024, 9, 26), TODAY, None),
    ("flood on 2026-08-15 in Wayanad", date(2026, 7, 16), date(2026, 9, 14), date(2026, 8, 15)),
    ("flood in October", date(2025, 10, 1), date(2025, 10, 31), None),   # most recent October
    ("flood in August", date(2026, 8, 1), date(2026, 8, 31), None),
    ("slope map of Coorg in 2025", date(2025, 1, 1), date(2025, 12, 31), None),
])
def test_parse_period(text, start, end, event):
    p = parse_period(text, TODAY)
    assert p and (p.start, p.end, p.event) == (start, end, event), p


def test_no_period():
    assert parse_period("road network of Mysuru", TODAY) is None


@pytest.mark.parametrize("text,expected", [
    ("I heard a flood happened in Chitradurga in August 2026", ["Chitradurga"]),
    ("Urban expansion of Bengaluru over five years", ["Bengaluru"]),
    ("Assess agricultural drought in Chitradurga district during the 2026 monsoon", ["Chitradurga district"]),
    ("NDVI time series for Tumakuru, Karnataka from June to September 2026", ["Tumakuru", "Karnataka"]),
    ("Flood in Rio de Janeiro in April 2025", ["Rio de Janeiro"]),
    ("what is the slope of the land here", []),
])
def test_place_candidates(text, expected):
    assert place_candidates(text, parse_period(text, TODAY)) == expected


@pytest.mark.parametrize("text,first", [
    ("Farmers near Hiriyur lost their groundnut because it barely rained in July 2026", "Hiriyur"),
    ("Chitradurga flood in August", "Chitradurga"),
    ("Villagers in Wayanad say the landslides blocked roads", "Wayanad"),
])
def test_place_after_a_preposition_is_tried_before_a_sentence_initial_word(text, first):
    assert place_candidates(text, parse_period(text, TODAY))[0] == first
