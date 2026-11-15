"""Scoring (spec §6.3, §6.4).

Exact match after normalisation. Headline = macro accuracy over the five
record-time types Q1-Q5; Q6 ("now") is a control reported apart, so a
now-only memory cannot look half right. Q1 also reports set precision,
recall and F1 (micro, over multisets) - partial credit is shown, never counted.
"""

import re
from collections import Counter

HEADLINE_TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5")
CONTROL_TYPE = "Q6"


def normalise(answer) -> object:
    if isinstance(answer, (list, tuple)):
        return sorted(normalise(a) for a in answer)
    if not isinstance(answer, str):
        return None
    s = re.sub(r"\s+", " ", answer.strip().lower())
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    return s[:-1] if s.endswith(".") else s


def wilson(k, n) -> tuple:
    if n == 0:
        return (0.0, 1.0)
    z, p = 1.96, k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (c - h, c + h)


def score_answers(rows) -> dict:
    by_type, tp, n_gold, n_ans = {}, 0, 0, 0
    for r in rows:
        gold, ans = normalise(r["gold"]), normalise(r["answer"])
        ok = ans is not None and ans == gold
        b = by_type.setdefault(r["type"], {"n": 0, "correct": 0})
        b["n"] += 1
        b["correct"] += ok
        if r["type"] == "Q1" and isinstance(gold, list):
            got = Counter(ans) if isinstance(ans, list) else Counter()
            tp += sum((Counter(gold) & got).values())
            n_gold += len(gold)
            n_ans += sum(got.values())
    for b in by_type.values():
        b["accuracy"] = b["correct"] / b["n"]
        b["ci95"] = list(wilson(b["correct"], b["n"]))
    present = [t for t in HEADLINE_TYPES if t in by_type]
    precision = tp / n_ans if n_ans else (1.0 if n_gold == 0 else 0.0)
    recall = tp / n_gold if n_gold else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "by_type": by_type,
        "headline": sum(by_type[t]["accuracy"] for t in present) / len(present) if present else None,
        "control": by_type[CONTROL_TYPE]["accuracy"] if CONTROL_TYPE in by_type else None,
        "missing_types": [t for t in HEADLINE_TYPES + (CONTROL_TYPE,) if t not in by_type],
        "q1_prf": {"precision": precision, "recall": recall, "f1": f1},
    }
