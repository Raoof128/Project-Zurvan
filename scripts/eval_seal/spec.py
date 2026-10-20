"""Stratum spec, item validation and pool coverage for the sealed evaluation (spec s4.2-s4.3, s9.3).

Sealed-side code: stdlib only, Python 3.9 compatible. `load_spec` refuses with a code only;
`validate_item` raises ValueError carrying a FIELD NAME and never any item content, so an
error message cannot become a side channel for a sealed query.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .codes import SealRefusal

STRATA = (
    "exact_identifier", "path", "hash", "error_string", "semantic", "alias", "decision_rationale",
    "temporal", "contradiction", "provenance", "code", "multi_hop", "synthesis", "cross_project",
    "hard_negative", "not_found",
)

_GRADING = {sid: "rank" for sid in STRATA}
_GRADING["multi_hop"] = "set"
_GRADING["synthesis"] = "set"
_GRADING["not_found"] = "abstain"

SPEC_SCHEMA = "zurvan.z1b.spec.v1"
_TOP_KEYS = frozenset((
    "schema", "strata", "totals", "abstain_threshold", "run_policy", "predeclaration_schema",
    "locator_kinds",
))
_STRATUM_KEYS = frozenset(("id", "name", "grading", "min_holdout", "min_reserve", "difficulty_targets"))
_DIFFICULTIES = ("easy", "medium", "hard")
# keys that would mean evaluation content has been put in the tracked spec
_FORBIDDEN_KEYS = frozenset(("query", "queries", "targets", "items", "chunk_ids", "answer", "answers"))
_MAX_STRING = 200
_MAX_DEPTH = 8
_RUN_POLICY_FIXED = {
    "holdout_budget": 8,
    "latency_resolution_ms": 10,
    "metric_precision": 0.001,
    "ci_precision": 0.01,
    "max_results": 10,
}
_RUN_POLICY_KEYS = frozenset(_RUN_POLICY_FIXED) | {"query_timeout_s", "run_timeout_s", "bootstrap_resamples"}
_PREDECLARATION_FIELDS = frozenset((
    "role", "suite_id", "primary_metric", "critical_strata", "non_inferiority_margins",
    "improvement_target", "resource_budgets", "resource_improvement",
))
_LOCATOR_KINDS = ["page", "section"]

_ID_RE = re.compile(r"[0-9a-f]{32}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_MAX_QUERY = 2000
_MAX_PAGE = 512
_MAX_LABEL = 64
_POOLS = ("holdout", "reserve")


class _Bad(Exception):
    pass


def _is_int(value) -> bool:
    return type(value) is int


def _is_num(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _no_dup_pairs(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _Bad("duplicate key")
    return dict(pairs)


def _no_constant(_name):
    raise _Bad("non-finite constant")


def _walk(obj, depth=0) -> None:
    if depth > _MAX_DEPTH:
        raise _Bad("too deep")
    if isinstance(obj, dict):
        for key, val in obj.items():
            if type(key) is not str or key in _FORBIDDEN_KEYS or len(key) > _MAX_STRING:
                raise _Bad("key")
            _walk(val, depth + 1)
    elif isinstance(obj, list):
        for val in obj:
            _walk(val, depth + 1)
    elif isinstance(obj, str):
        if len(obj) > _MAX_STRING:
            raise _Bad("string too long")


def _check_strata(strata) -> None:
    if type(strata) is not list or len(strata) != len(STRATA):
        raise _Bad("strata")
    for entry, sid in zip(strata, STRATA):
        if type(entry) is not dict or set(entry) != _STRATUM_KEYS:
            raise _Bad("stratum keys")
        if entry["id"] != sid or entry["grading"] != _GRADING[sid]:
            raise _Bad("stratum id or grading")
        name = entry["name"]
        if type(name) is not str or not name or len(name) > _MAX_LABEL:
            raise _Bad("name")
        if not _is_int(entry["min_holdout"]) or entry["min_holdout"] < 10:
            raise _Bad("min_holdout")
        if not _is_int(entry["min_reserve"]) or entry["min_reserve"] < 5:
            raise _Bad("min_reserve")
        diff = entry["difficulty_targets"]
        if type(diff) is not dict or set(diff) != set(_DIFFICULTIES):
            raise _Bad("difficulty keys")
        if not all(_is_num(diff[k]) and 0 <= diff[k] <= 1 for k in _DIFFICULTIES):
            raise _Bad("difficulty values")
        if abs(math.fsum(diff[k] for k in _DIFFICULTIES) - 1.0) > 1e-6:
            raise _Bad("difficulty sum")


def _check_predeclaration(schema) -> None:
    if type(schema) is not dict or set(schema) != {"required", "properties"}:
        raise _Bad("predeclaration keys")
    required = schema["required"]
    props = schema["properties"]
    if type(required) is not list or set(required) != _PREDECLARATION_FIELDS or len(required) != len(set(required)):
        raise _Bad("predeclaration required")
    if type(props) is not dict or set(props) != _PREDECLARATION_FIELDS:
        raise _Bad("predeclaration properties")
    if not all(type(p) is dict and "type" in p for p in props.values()):
        raise _Bad("predeclaration property")
    crit = props["critical_strata"].get("element", {}).get("enum")
    if crit != list(STRATA):
        raise _Bad("critical strata enum")


def _validate_spec(spec) -> None:
    if type(spec) is not dict or set(spec) != _TOP_KEYS:
        raise _Bad("top-level keys")
    _walk(spec)
    if spec["schema"] != SPEC_SCHEMA:
        raise _Bad("schema")
    _check_strata(spec["strata"])
    totals = spec["totals"]
    if type(totals) is not dict or set(totals) != {"min_holdout", "min_reserve"}:
        raise _Bad("totals keys")
    if not _is_int(totals["min_holdout"]) or totals["min_holdout"] < 150:
        raise _Bad("holdout total")
    if not _is_int(totals["min_reserve"]) or totals["min_reserve"] < 80:
        raise _Bad("reserve total")
    thr = spec["abstain_threshold"]
    if type(thr) is not float or not math.isfinite(thr) or not 0.0 <= thr <= 1.0:
        raise _Bad("abstain threshold")
    policy = spec["run_policy"]
    if type(policy) is not dict or set(policy) != _RUN_POLICY_KEYS:
        raise _Bad("run policy keys")
    for key, expected in _RUN_POLICY_FIXED.items():
        if type(policy[key]) is not type(expected) or policy[key] != expected:
            raise _Bad("run policy " + key)
    for key, lo, hi in (("query_timeout_s", 1, 86400), ("run_timeout_s", 1, 86400),
                        ("bootstrap_resamples", 1000, 100000)):
        if not _is_int(policy[key]) or not lo <= policy[key] <= hi:
            raise _Bad("run policy " + key)
    _check_predeclaration(spec["predeclaration_schema"])
    if spec["locator_kinds"] != _LOCATOR_KINDS:
        raise _Bad("locator kinds")


def load_spec(path) -> dict:
    """Parse and validate the tracked spec. ANY defect is MANIFEST_INVALID, with no detail."""
    try:
        text = Path(path).read_bytes().decode("utf-8")
        spec = json.loads(text, object_pairs_hook=_no_dup_pairs, parse_constant=_no_constant)
        _validate_spec(spec)
    except SealRefusal:
        raise
    except Exception:  # noqa: BLE001 - every defect, including I/O, is the same refusal
        raise SealRefusal("MANIFEST_INVALID") from None
    return spec


def _page_ok(page) -> bool:
    if type(page) is not str or not page.strip() or len(page) > _MAX_PAGE:
        return False
    if "\x00" in page or page.startswith("/"):
        return False
    return ".." not in page.split("/")


def _target_ok(target, spec) -> bool:
    if type(target) is not dict or set(target) != {"page", "source_id", "locator"}:
        return False
    if not _page_ok(target["page"]):
        return False
    src = target["source_id"]
    if src is not None and (type(src) is not str or not src or len(src) > _MAX_PAGE):
        return False
    loc = target["locator"]
    if type(loc) is not dict or set(loc) != {"kind", "value"}:
        return False
    if loc["kind"] not in spec["locator_kinds"] or type(loc["value"]) is not str:
        return False
    if len(loc["value"]) > _MAX_PAGE or "\x00" in loc["value"]:
        return False
    return loc["kind"] != "section" or bool(loc["value"])


def validate_item(item, spec) -> None:
    """s4.3 item schema. Raises ValueError(<field name>) only; never echoes item content."""
    if type(item) is not dict:
        raise ValueError("item")
    required = ("id", "stratum", "query", "targets", "grading", "difficulty", "provenance")
    allowed = set(required) | {"snapshot_aliases"}
    if any(type(k) is not str for k in item) or set(item) - allowed:
        raise ValueError("keys")
    for key in required:
        if key not in item:
            raise ValueError(key)
    if type(item["id"]) is not str or not _ID_RE.fullmatch(item["id"]):
        raise ValueError("id")
    by_id = {s["id"]: s for s in spec["strata"]}
    if type(item["stratum"]) is not str or item["stratum"] not in by_id:
        raise ValueError("stratum")
    grading = by_id[item["stratum"]]["grading"]
    if item["grading"] != grading or type(item["grading"]) is not str:
        raise ValueError("grading")
    q = item["query"]
    if type(q) is not str or not q.strip() or len(q) > _MAX_QUERY:
        raise ValueError("query")
    if item["difficulty"] not in _DIFFICULTIES or type(item["difficulty"]) is not str:
        raise ValueError("difficulty")
    targets = item["targets"]
    if type(targets) is not list:
        raise ValueError("targets")
    if grading == "abstain":
        if targets:
            raise ValueError("targets")
    elif not targets or not all(_target_ok(t, spec) for t in targets):
        raise ValueError("targets")
    prov = item["provenance"]
    if (type(prov) is not dict or set(prov) != {"author", "reviewer"}
            or not all(type(v) is str and 0 < len(v) <= _MAX_LABEL for v in prov.values())):
        raise ValueError("provenance")
    if "snapshot_aliases" in item:
        al = item["snapshot_aliases"]
        ok = (type(al) is dict and set(al) == {"snapshot", "chunk_ids"}
              and type(al["snapshot"]) is str and bool(_HEX64_RE.fullmatch(al["snapshot"]))
              and type(al["chunk_ids"]) is list
              and all(type(c) is str and 0 < len(c) <= _MAX_PAGE for c in al["chunk_ids"]))
        if not ok:
            raise ValueError("snapshot_aliases")


def coverage(items_by_pool, spec) -> dict:
    """Count items per stratum per pool against the spec minimums. Reports shortfalls; never pads.

    `totals_ok` means the pool sums reach the spec totals AND no stratum is short, so a surplus
    in one stratum can never stand in for an empty one.
    """
    need_key = {"holdout": "min_holdout", "reserve": "min_reserve"}
    counts = {}
    for pool in _POOLS:
        per = {sid: 0 for sid in STRATA}
        for item in items_by_pool.get(pool, []):
            sid = item.get("stratum") if isinstance(item, dict) else None
            if sid in per:
                per[sid] += 1
        counts[pool] = per
    short = []
    for pool in _POOLS:
        for entry in spec["strata"]:
            have, need = counts[pool][entry["id"]], entry[need_key[pool]]
            if have < need:
                short.append({"pool": pool, "stratum": entry["id"], "have": have, "need": need})
    sums_ok = (sum(counts["holdout"].values()) >= spec["totals"]["min_holdout"]
               and sum(counts["reserve"].values()) >= spec["totals"]["min_reserve"])
    totals_ok = sums_ok and not short
    return {"holdout": counts["holdout"], "reserve": counts["reserve"], "short": short,
            "totals_ok": totals_ok, "ok": totals_ok}
