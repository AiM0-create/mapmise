"""The shipped model: loads offline, embeds, and matches asks by meaning within the evaluated bounds."""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from mapmise import ai
from mapmise.resolver import parse_ask

pytestmark = pytest.mark.skipif(not ai.available(), reason="built-in model or runtime not installed")
ROOT = Path(__file__).resolve().parent.parent


def test_embeddings_are_unit_vectors_and_capture_meaning():
    v = ai.embed(["drought", "the rains failed and the crops are drying", "best restaurants in town"])
    assert v.shape == (3, 384) and np.allclose(np.linalg.norm(v, axis=1), 1, atol=1e-4)
    assert v[0] @ v[1] > v[0] @ v[2]


@pytest.mark.parametrize("text,rule", [
    ("after the cloudburst the low lying areas were submerged", "flood"),
    ("how much water is left in the dam", "water_bodies"),
    ("tree cutting in the hills since 2010", "forest"),
])
def test_meaning_matches_without_keywords(text, rule):
    a = parse_ask(text)
    assert rule in a.matched_rules and a.how[rule].startswith("meaning:")
    assert parse_ask(text, use_ai=False).matched_rules == []


def test_ai_adds_to_keywords_only_when_confident():
    a = parse_ask("Farmers near Hiriyur lost their groundnut because it barely rained in July")
    assert a.how.get("rainfall") == "keyword" and "drought" in a.how and a.how["drought"].startswith("meaning:")


def test_off_topic_is_not_matched():
    assert parse_ask("what is the capital of France").matched_rules == []


def test_evaluation_does_not_regress():
    """Held-out half of tests/fixtures/asks_eval.yaml: keyword rules + AI must understand at least 15 of 17."""
    out = subprocess.run([sys.executable, "experiments/e5_ai_understanding.py"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    line = next(l for l in out.splitlines() if l.startswith("test:"))
    ok = int(line.split("keywords + AI ")[1].split("/")[0])
    assert ok >= 15, line
