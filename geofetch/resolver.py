"""Resolver: ask text → data needs → ranked registry sources, with reasons.

Deterministic. Rules (registry/asks.yaml) turn words into generic needs; the registry
(registry/sources.yaml) is queried per need. Nothing here knows what a "flood" is beyond
the rule that mentions the word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from geofetch.registry import Need, Source, load_ask_rules, load_sources

_PRIORITY_RANK = {"required": 0, "recommended": 1, "optional": 2}


@dataclass
class Ask:
    text: str
    needs: list[Need]
    matched_rules: list[str]
    event_type: str | None  # set when a matched rule describes a dated event


def parse_ask(text: str) -> Ask:
    """Union of the needs of every matching rule; duplicate (theme, temporal) keeps the higher priority."""
    rules = load_ask_rules()
    needs: dict[str, Need] = {}
    matched, event_type = [], None
    for r in rules:
        if any(re.search(k, text, re.IGNORECASE) for k in r.keywords):
            matched.append(r.id)
            event_type = event_type or r.event_type
            for n in r.needs:
                cur = needs.get(n.key)
                if cur is None or _PRIORITY_RANK[n.priority] < _PRIORITY_RANK[cur.priority]:
                    needs[n.key] = n
    ordered = sorted(needs.values(), key=lambda n: (_PRIORITY_RANK[n.priority], n.theme))
    return Ask(text, ordered, matched, event_type)


@dataclass
class Candidate:
    source: Source
    reasons: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    need: Need
    chosen: Source | None
    candidates: list[Candidate]
    unmet_reason: str | None = None


def _compatible_shape(need: Need, s: Source) -> bool:
    if need.temporal == "static":
        return s.shape in {"layer", "query"}
    return s.shape == "series"  # series and pair both need dated scenes


def resolve_needs(needs: list[Need], bbox: list[float], start: str, end: str, iso3: str | None = None,
                  overrides: dict[str, str] | None = None) -> list[Resolution]:
    """Rank registry sources per need. `overrides` maps need.key (e.g. 'water:static') to a source id."""
    sources = load_sources()
    out = []
    for need in needs:
        cands: list[Candidate] = []
        for s in sources.values():
            if need.theme not in s.themes or not _compatible_shape(need, s) or s.kind == "event":
                continue
            if not s.covers_bbox(bbox, iso3):
                continue
            if need.temporal != "static" and not s.covers_period(start, end):
                continue
            reasons = [f"theme '{need.theme}', {s.shape} data"]
            if s.analysis_ready:
                reasons.append("analysis-ready")
            if need.prefer.get("cloud_independent") and not s.cloud_dependent:
                reasons.append("cloud-independent as preferred")
            if s.resolution_m:
                reasons.append(f"{s.resolution_m:g} m")
            reasons.append(f"licence: {s.license}")
            cands.append(Candidate(s, reasons))

        def rank(c: Candidate):
            s = c.source
            return (
                0 if s.analysis_ready else 1,
                0 if not (need.prefer.get("cloud_independent") and s.cloud_dependent) else 1,
                0 if s.kind == "raster" else 1,
                s.resolution_m if s.resolution_m is not None else 1e9,
            )

        cands.sort(key=rank)
        chosen = cands[0].source if cands else None
        if overrides and need.key in overrides:
            chosen = sources.get(overrides[need.key], chosen)
        unmet = None if chosen else f"no registered source with theme '{need.theme}' for {need.temporal} data over this area/period"
        out.append(Resolution(need, chosen, cands, unmet))
    return out
