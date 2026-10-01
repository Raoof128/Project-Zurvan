"""Deterministic grader for the sealed evaluation (spec s4.4; contract s3.3).

No model grader. Pure functions over (item, returned results). Sealed-side code: stdlib only,
Python 3.9 compatible. Malformed results never match; malformed items raise ValueError.
"""
from __future__ import annotations

import math
import random

from .spec import STRATA

EVALUATOR_VERSION = "1.0.0"

_METRICS = ("recall_at_1", "recall_at_3", "recall_at_10", "mrr")
_KS = (1, 3, 10)
_GRADINGS = ("rank", "set", "abstain")
_PRECISION = 3


def _norm_page(page):
    """Store-relative normal form, or None when the path can never match (absolute, '..', NUL, empty)."""
    if type(page) is not str or "\x00" in page or page.startswith("/"):
        return None
    parts = [p for p in page.split("/") if p not in ("", ".")]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def hit(result, target) -> bool:
    """page locator: normalised page equality. section locator: page equal AND heading == value."""
    try:
        if type(result) is not dict or type(target) is not dict:
            return False
        rpage, tpage = _norm_page(result.get("page")), _norm_page(target.get("page"))
        if rpage is None or rpage != tpage:
            return False
        loc = target.get("locator")
        if type(loc) is not dict:
            return False
        kind = loc.get("kind")
        if kind == "page":
            return True
        if kind == "section":
            heading, value = result.get("heading"), loc.get("value")
            return type(heading) is str and type(value) is str and heading == value
        return False
    except Exception:  # noqa: BLE001 - grading is total
        return False


def resolve_target_presence(item, corpus_pages) -> bool:
    """False -> TARGET_MISSING: any target page absent from the corpus. Abstain items have no targets."""
    pages = {_norm_page(p) for p in corpus_pages}
    for target in item.get("targets", []):
        norm = _norm_page(target.get("page")) if type(target) is dict else None
        if norm is None or norm not in pages:
            return False
    return True


def _window(results, max_results):
    """Truncate to max_results FIRST, then drop repeats of the same (page, heading) chunk."""
    seen = set()
    kept = []
    for res in list(results)[:max_results]:
        key = None
        if type(res) is dict:
            page = _norm_page(res.get("page"))
            heading = res.get("heading")
            if page is not None and (heading is None or type(heading) is str):
                key = (page, heading)
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        kept.append(res)
    return kept


def _score_ok(score):
    return type(score) in (int, float) and math.isfinite(score)


def grade_item(item, results, spec, *, corpus_pages=None) -> dict:
    if type(item) is not dict:
        raise ValueError("item")
    stratum, grading = item.get("stratum"), item.get("grading")
    if stratum not in STRATA or grading not in _GRADINGS:
        raise ValueError("item")
    targets = item.get("targets")
    if type(targets) is not list or (grading == "abstain") != (not targets):
        raise ValueError("targets")
    row = {"stratum": stratum, "grading": grading, "rank": None, "set_fraction": None,
           "set_fraction_at": None, "abstained": None, "target_missing": False}
    window = _window(results, spec["run_policy"]["max_results"])
    if grading == "abstain":
        scores = [r.get("score") if type(r) is dict else None for r in window]
        if any(not _score_ok(s) for s in scores):
            row["abstained"] = False
        else:
            row["abstained"] = (max(scores) if scores else -math.inf) < spec["abstain_threshold"]
        return row
    if corpus_pages is not None and not resolve_target_presence(item, corpus_pages):
        row["target_missing"] = True
        return row
    for pos, res in enumerate(window, start=1):
        if any(hit(res, t) for t in targets):
            row["rank"] = pos
            break
    if grading == "set":
        at = {}
        for k in _KS:
            found = sum(1 for t in targets if any(hit(r, t) for r in window[:k]))
            at[str(k)] = found / len(targets)
        row["set_fraction_at"] = at
        row["set_fraction"] = at["10"]
    return row


def _row_values(row):
    """(recall@1, recall@3, recall@10, rr) for one scored row."""
    rank = row["rank"]
    if row["grading"] == "set":
        at = row["set_fraction_at"]
        recalls = [float(at[str(k)]) for k in _KS]
    else:
        recalls = [1.0 if rank is not None and rank <= k else 0.0 for k in _KS]
    return recalls + [1.0 / rank if rank is not None else 0.0]


def _check_rows(rows):
    for row in rows:
        if type(row) is not dict or row.get("stratum") not in STRATA or row.get("grading") not in _GRADINGS:
            raise ValueError("row")
        rank = row.get("rank")
        if rank is not None and (type(rank) is not int or not 1 <= rank <= 10):
            raise ValueError("row")
        if type(row.get("target_missing")) is not bool:
            raise ValueError("row")
        if row["grading"] == "set" and not row["target_missing"] and type(row.get("set_fraction_at")) is not dict:
            raise ValueError("row")


def _scored(rows):
    return [r for r in rows if r["grading"] != "abstain" and not r["target_missing"]]


def metric_vectors(rows, spec) -> dict:
    """Per-row metric values for the scored rows, in row order (inputs to paired_bootstrap_ci)."""
    _check_rows(rows)
    vals = [_row_values(r) for r in _scored(rows)]
    return {m: [v[i] for v in vals] for i, m in enumerate(_METRICS)}


def _mean_block(rows):
    vals = [_row_values(r) for r in rows]
    n = len(vals)
    return {m: round(math.fsum(v[i] for v in vals) / n, _PRECISION) + 0.0 if n else 0.0
            for i, m in enumerate(_METRICS)}


def aggregate(rows, spec, *, per_stratum) -> dict:
    """Recall@1/3/10 and MRR over rank+set rows. Abstain rows are reported separately; TARGET_MISSING
    rows are counted and excluded from every denominator. Fixed precision is applied here."""
    _check_rows(rows)
    scored = _scored(rows)
    abstain_rows = [r for r in rows if r["grading"] == "abstain"]
    abstain = None
    if abstain_rows:
        abstain = {"passed": sum(1 for r in abstain_rows if r["abstained"] is True), "n": len(abstain_rows)}
    ps = None
    if per_stratum:
        ps = {}
        for sid in STRATA:
            group = [r for r in scored if r["stratum"] == sid]
            if group:
                ps[sid] = dict(_mean_block(group), n=len(group))
    return {
        "n_items": len(rows),
        "n_scored": len(scored),
        "metrics": _mean_block(scored),
        "per_stratum": ps,
        "target_missing": sum(1 for r in rows if r["target_missing"]),
        "abstain": abstain,
    }


def paired_bootstrap_ci(cand, base, *, resamples, seed):
    """95% percentile bootstrap CI. base None: CI of mean(cand). Otherwise CI of mean(cand - base),
    resampling the same query indices for both. Deterministic: random.Random(seed), local state only."""
    if type(resamples) is not int or resamples < 1 or type(seed) is not int:
        raise ValueError("arguments")
    n = len(cand)
    if n == 0 or (base is not None and len(base) != n):
        raise ValueError("lengths")
    for series in (cand, base or []):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in series):
            raise ValueError("values")
    vals = [float(c) for c in cand] if base is None else [float(c) - float(b) for c, b in zip(cand, base)]
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        stats.append(math.fsum(vals[int(rng.random() * n)] for _ in range(n)) / n)
    stats.sort()
    return stats[int(0.025 * resamples)], stats[min(resamples - 1, int(0.975 * resamples))]
