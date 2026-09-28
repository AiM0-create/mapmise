"""E5 — does the shipped AI understand asks the keyword rules miss? Tune on 'tune', report 'test'."""
import re
import sys
from pathlib import Path

import numpy as np
import yaml

from mapmise.ai import embed
from mapmise.registry import load_ask_rules

rules = load_ask_rules()
cases = yaml.safe_load(Path("tests/fixtures/asks_eval.yaml").read_text(encoding="utf-8"))
ex_rule, ex_text = [], []
for r in rules:
    for e in r.examples:
        ex_rule.append(r.id); ex_text.append(e)
from mapmise.ai.understand import OFF_TOPIC
E = embed(ex_text)
N = embed(list(OFF_TOPIC))
Q = embed([c["text"] for c in cases])
S = Q @ E.T
ids = [r.id for r in rules]
null = (Q @ N.T).max(axis=1)  # similarity to everyday non-GIS sentences
score = np.stack([S[:, [i for i, x in enumerate(ex_rule) if x == rid]].max(axis=1) for rid in ids], axis=1)  # (cases, rules)


def keyword(text):
    return {r.id for r in rules if any(re.search(k, text, re.I) for k in r.keywords)}


def predict(i, t_abs, margin, t_add=0.55):
    kw = keyword(cases[i]["text"])
    order = np.argsort(-score[i])
    best = score[i, order[0]]
    if kw:
        top = ids[order[0]]
        return (kw | {top}, "kw+ai") if best >= t_add and top not in kw else (kw, "keyword")
    if best < t_abs or best <= null[i]:
        return set(), "none"
    return {ids[j] for j in order if score[i, j] >= best - margin and score[i, j] >= t_abs}, "ai"


def evaluate(split, t_abs, margin, verbose=False, t_add=0.55):
    ok = wrong = 0
    rows = []
    for i, c in enumerate(cases):
        if c["split"] != split:
            continue
        pred, how = predict(i, t_abs, margin, t_add)
        allowed = {c["primary"], *c.get("allowed", [])} - {None}
        good = (not pred) if c["primary"] is None else (c["primary"] in pred and pred <= allowed)
        ok += good
        rows.append((good, how, c["text"], c["primary"], sorted(pred), round(float(score[i].max()), 2)))
    if verbose:
        for r in rows:
            print(("✓" if r[0] else "✗"), f"{r[1]:7}", f"{r[5]:.2f}", r[2][:62].ljust(62), "→", r[4], "(want", r[3], ")")
    return ok, len(rows)


kw_only = lambda split: sum(1 for c in cases if c["split"] == split and (
    (c["primary"] is None and not keyword(c["text"])) or (c["primary"] in keyword(c["text"]) and keyword(c["text"]) <= {c["primary"], *c.get("allowed", [])})))
best = max(((evaluate("tune", t, m, t_add=a)[0], -abs(t - 0.45), -a, t, m, a) for t in np.arange(0.30, 0.66, 0.025)
             for m in (0.0, 0.02, 0.04, 0.06) for a in (0.5, 0.55, 0.6, 0.65, 0.7, 9.9)))
_, _, _, T, M, A = best
print(f"chosen on tune: threshold={T:.3f} margin={M:.2f} add-to-keywords={A}")
for split in ("tune", "test"):
    ok, n = evaluate(split, T, M, verbose=(split == "test" or "-v" in sys.argv), t_add=A)
    print(f"{split}: keywords only {kw_only(split)}/{n}  ·  keywords + AI {ok}/{n}\n")


# The shipped pipeline end to end — resolver.parse_ask with its real thresholds — with and without place masking
# (place names in the ask replaced by "the area" before the model reads it).
import mapmise.resolver as resolver
from mapmise.resolver import parse_ask


def shipped(split, masked):
    real = resolver.for_model
    if not masked:
        resolver.for_model = lambda text, rules: text
    try:
        ok = n = 0
        for c in cases:
            if c["split"] != split:
                continue
            pred = set(parse_ask(c["text"]).matched_rules)
            allowed = {c["primary"], *c.get("allowed", [])} - {None}
            ok += (not pred) if c["primary"] is None else (c["primary"] in pred and pred <= allowed)
            n += 1
        return ok, n
    finally:
        resolver.for_model = real


for split in ("tune", "test"):
    (a, n), (b, _) = shipped(split, False), shipped(split, True)
    print(f"shipped pipeline, {split}: without place masking {a}/{n}  ·  with place masking {b}/{n}")
