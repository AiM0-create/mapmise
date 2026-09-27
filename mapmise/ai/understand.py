"""Match an ask to ask rules by meaning, with the shipped embedding model."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Everyday requests that are not about geospatial data: the "none of the above" class. An ask is only
# matched by meaning if it is closer to some rule's examples than to all of these.
OFF_TOPIC = (
    "order food online", "cheap flights to Delhi", "tell me a joke", "translate this sentence into Hindi",
    "cricket score today", "recipe for biryani", "who won the election", "movie showtimes near me",
    "fix my laptop", "write an email to my manager", "what is the stock price", "play some music",
)

# Chosen on the tuning half of tests/fixtures/asks_eval.yaml by experiments/e5_ai_understanding.py
MIN_SCORE = 0.325        # a rule must be at least this close in meaning to one of its examples
MARGIN = 0.06            # other rules within this distance of the best one are also taken
ADD_TO_KEYWORDS = 0.50   # when keywords already matched, the AI's top rule is added only if this confident


@dataclass
class MeaningMatch:
    rule: str
    score: float
    example: str  # the rule example the ask was closest to — shown to the user as the reason


def _index(rules) -> tuple[list[str], list[str], np.ndarray, np.ndarray]:
    from mapmise.ai import embed
    rule_of, text = [], []
    for r in rules:
        for e in r.examples:
            rule_of.append(r.id)
            text.append(e)
    return rule_of, text, embed(text) if text else np.zeros((0, 384)), embed(list(OFF_TOPIC))


_cache: dict[tuple, tuple] = {}


def match(text: str, rules, keyword_hits: set[str]) -> list[MeaningMatch]:
    """Rules this ask means, beyond (or instead of) its keyword matches. Empty if nothing is close enough,
    or if the ask is closer to everyday non-geospatial requests than to any rule."""
    from mapmise.ai import embed
    key = tuple((r.id, r.examples) for r in rules)
    if key not in _cache:
        _cache.clear()
        _cache[key] = _index(rules)
    rule_of, ex_text, E, N = _cache[key]
    if not len(rule_of):
        return []
    q = embed([text])[0]
    sims = E @ q
    best_per_rule: dict[str, tuple[float, str]] = {}
    for rid, t, s in zip(rule_of, ex_text, sims):
        if s > best_per_rule.get(rid, (-1.0, ""))[0]:
            best_per_rule[rid] = (float(s), t)
    ranked = sorted(best_per_rule.items(), key=lambda kv: -kv[1][0])
    top_id, (top, top_ex) = ranked[0]
    if keyword_hits:
        return [MeaningMatch(top_id, top, top_ex)] if top >= ADD_TO_KEYWORDS and top_id not in keyword_hits else []
    if top < MIN_SCORE or top <= float((N @ q).max()):
        return []
    return [MeaningMatch(rid, s, ex) for rid, (s, ex) in ranked if s >= max(MIN_SCORE, top - MARGIN)]
