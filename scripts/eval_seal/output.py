"""Closed serializer for everything the broker prints (spec s5, s7; contract s3.2).

The broker's stdout is the only channel from the sealed side to the dev side, so it is built
from a closed schema: a document is accepted only if it has exactly the schema's keys and every
value has exactly the schema's type; every string must be a constant, an enum member, or match
the field's regex. Free text cannot be expressed. Any violation raises SealRefusal("INTERNAL")
with no detail, so a malformed document cannot echo what it contained.

Sealed-side code: stdlib only, Python 3.9 compatible.
"""
from __future__ import annotations

import json
import math
import re

from .codes import ALL_CODES, SealRefusal
from .spec import STRATA

RESULT_SCHEMA_ID = "zurvan.z1b.result.v1"
STATUS_SCHEMA_ID = "zurvan.z1b.status.v1"
SEALCHECK_SCHEMA_ID = "zurvan.z1b.sealcheck.v1"

METRICS = ("recall_at_1", "recall_at_3", "recall_at_10", "mrr")
SUITES = ("holdout", "reserve")
VERDICTS = ("PASS", "FAIL")
PROMOTION_RULES = ("non_inferiority_critical", "non_inferiority_overall", "improvement", "resource_budget")

# Every check id the seal-check may report. A check outside this list cannot be printed.
# (Ruling R7: the sets and runs hold ciphertext / opaque rows, so only keys/ needs a Time Machine
# exclusion; dev_cannot_read_sealed is the CLIENT half and is merged in by the dev side.)
CHECK_IDS = (
    "broker_root_owned", "broker_not_writable", "broker_sha_matches", "sudoers_valid", "pf_anchor_present",
    "keys_private", "sets_private", "runs_private", "corpus_read_only", "stage_private",
    "candidate_cannot_read_sealed", "dev_cannot_read_sealed", "tm_excluded_keys", "drop_dir_mode",
    "scan_agent_present", "pinned_binaries_match", "interpreter_root_owned", "ledger_valid",
    "manifest_valid", "tm_excluded_private",
)

_HEX16 = r"[0-9a-f]{16}"
_HEX40 = r"[0-9a-f]{40}"
_HEX64 = r"[0-9a-f]{64}"
_SEMVER = r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}"
_BIG = 10 ** 6
_LAT_MAX = 3600000


def _metric_fields():
    return {m: ("float", 0.0, 1.0, 3) for m in METRICS}


# Type specs are tuples: ("const", v) ("bool",) ("int", lo, hi) ("mult", lo, hi, step)
# ("float", lo, hi, ndigits) ("re", pattern) ("enum", members) ("nullable", spec)
# ("obj", {key: spec}) ("map", key_members, spec) ("pair", lo, hi, ndigits)
RESULT_SCHEMA = {
    "ok": ("const", True),
    "schema": ("const", RESULT_SCHEMA_ID),
    "suite": ("enum", SUITES),
    "suite_id": ("re", _HEX16),
    "n_items": ("int", 0, _BIG),
    "corpus_sha256": ("re", _HEX64),
    "suite_manifest_sha256": ("re", _HEX64),
    "evaluator_version": ("re", _SEMVER),
    "broker_sha": ("re", _HEX40),
    "candidate_commit": ("re", _HEX40),
    "metrics": ("obj", dict(_metric_fields(), ci=("obj", {m: ("pair", 0.0, 1.0, 2) for m in METRICS}))),
    "per_stratum": ("nullable", ("map", STRATA, ("obj", dict(_metric_fields(), n=("int", 0, _BIG))))),
    "target_missing": ("int", 0, _BIG),
    "abstain": ("nullable", ("obj", {"passed": ("int", 0, _BIG), "n": ("int", 0, _BIG)})),
    "latency_ms": ("obj", {"p50": ("mult", 0, _LAT_MAX, 10), "p95": ("mult", 0, _LAT_MAX, 10)}),
    "peak_rss_mb": ("int", 0, _BIG),
    "seal_valid": ("bool",),
    "cached": ("bool",),
    "budget_remaining": ("int", 0, 10000),
    "promotion": ("nullable", ("obj", {
        "declared": ("const", True),
        "rules": ("obj", {r: ("bool",) for r in PROMOTION_RULES}),
        "verdict": ("enum", VERDICTS),
    })),
}

STATUS_SCHEMA = {
    "ok": ("const", True),
    "schema": ("const", STATUS_SCHEMA_ID),
    "configured": ("bool",),
    "broker_sha": ("re", _HEX40),
    "installed_sha": ("nullable", ("re", _HEX40)),
    "installed_tree": ("nullable", ("re", _HEX40)),
    "stale": ("bool",),
    "manifest_valid": ("bool",),
    "corpus_match": ("bool",),
    "budget_remaining": ("int", 0, 10000),
    "open_incidents": ("int", 0, _BIG),
    "holdout_n": ("int", 0, _BIG),
    "reserve_n": ("int", 0, _BIG),
    "suite_id": ("nullable", ("re", _HEX16)),
}

SEALCHECK_SCHEMA = {
    "ok": ("bool",),
    "schema": ("const", SEALCHECK_SCHEMA_ID),
    "broker_sha": ("re", _HEX40),
    "checks": ("map", CHECK_IDS, ("bool",)),
}

REFUSAL_SCHEMA = {
    "ok": ("const", False),
    "code": ("enum", ALL_CODES),
    "broker_sha": ("re", _HEX40),
}

_RE_CACHE = {}


class _Bad(Exception):
    pass


def _regex(pattern):
    compiled = _RE_CACHE.get(pattern)
    if compiled is None:
        compiled = _RE_CACHE[pattern] = re.compile(pattern)
    return compiled


def _float(value, lo, hi, ndigits):
    if type(value) not in (int, float):
        raise _Bad()
    num = float(value)
    if not math.isfinite(num) or num < lo or num > hi:
        raise _Bad()
    rounded = round(num, ndigits) + 0.0  # + 0.0 turns -0.0 into 0.0
    if rounded < lo or rounded > hi:
        raise _Bad()
    return rounded


def _emit(value, spec):
    kind = spec[0]
    if kind == "const":
        if type(value) is not type(spec[1]) or value != spec[1]:
            raise _Bad()
        return value
    if kind == "bool":
        if type(value) is not bool:
            raise _Bad()
        return value
    if kind == "int":
        if type(value) is not int or value < spec[1] or value > spec[2]:
            raise _Bad()
        return value
    if kind == "mult":
        if type(value) is not int or value < spec[1] or value > spec[2] or value % spec[3]:
            raise _Bad()
        return value
    if kind == "float":
        return _float(value, spec[1], spec[2], spec[3])
    if kind == "re":
        if type(value) is not str or not _regex(spec[1]).fullmatch(value):
            raise _Bad()
        return value
    if kind == "enum":
        if type(value) is not str or value not in spec[1]:
            raise _Bad()
        return value
    if kind == "nullable":
        return None if value is None else _emit(value, spec[1])
    if kind == "obj":
        fields = spec[1]
        if type(value) is not dict:
            raise _Bad()
        for key in value:
            if type(key) is not str:
                raise _Bad()
        if set(value) != set(fields):
            raise _Bad()
        return {key: _emit(value[key], fields[key]) for key in fields}
    if kind == "map":
        if type(value) is not dict:
            raise _Bad()
        out = {}
        for key, val in value.items():
            if type(key) is not str or key not in spec[1]:
                raise _Bad()
            out[key] = _emit(val, spec[2])
        return out
    if kind == "pair":
        if type(value) not in (list, tuple) or len(value) != 2:
            raise _Bad()
        return [_float(value[0], spec[1], spec[2], spec[3]), _float(value[1], spec[1], spec[2], spec[3])]
    raise _Bad()


def _post_result(doc):
    for lo, hi in doc["metrics"]["ci"].values():
        if lo > hi:
            raise _Bad()
    lat = doc["latency_ms"]
    if lat["p50"] > lat["p95"]:
        raise _Bad()
    if doc["abstain"] is not None and doc["abstain"]["passed"] > doc["abstain"]["n"]:
        raise _Bad()
    promo = doc["promotion"]
    if promo is not None and (promo["verdict"] == "PASS") != all(promo["rules"].values()):
        raise _Bad()


def _post_status(doc):
    return None


def _post_sealcheck(doc):
    checks = doc["checks"]
    if not checks or doc["ok"] != all(checks.values()):
        raise _Bad()


def _post_refusal(doc):
    return None


def _dumps(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _serialize(doc, schema, post) -> str:
    try:
        norm = _emit(doc, ("obj", schema))
        post(norm)
        return _dumps(norm)
    except Exception:  # noqa: BLE001 - every failure, even a hostile object's own, is one opaque refusal
        raise SealRefusal("INTERNAL") from None


def serialize_result(result) -> str:
    return _serialize(result, RESULT_SCHEMA, _post_result)


def serialize_status(status) -> str:
    return _serialize(status, STATUS_SCHEMA, _post_status)


def serialize_sealcheck(check) -> str:
    return _serialize(check, SEALCHECK_SCHEMA, _post_sealcheck)


def serialize_refusal(code, broker_sha) -> str:
    """Never raises. An unknown code is INTERNAL; an unusable sha is 40 zeros, never the input."""
    known = type(code) is str and code in ALL_CODES
    sha_ok = type(broker_sha) is str and _regex(_HEX40).fullmatch(broker_sha) is not None
    return _dumps({
        "ok": False,
        "code": code if known else "INTERNAL",
        "broker_sha": broker_sha if sha_ok else "0" * 40,
    })


# ---------------------------------------------------------------- dev-side mirrors

def _no_dups(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constant(_name):
    raise ValueError("non-finite constant")


def _parse(text):
    if type(text) is not str:
        raise ValueError("invalid broker output")
    body = text[:-1] if text.endswith("\n") else text
    if body != body.strip():
        raise ValueError("invalid broker output")
    try:
        return json.loads(body, object_pairs_hook=_no_dups, parse_constant=_no_constant)
    except Exception:  # noqa: BLE001
        raise ValueError("invalid broker output") from None


def _check_parsed(parsed, schema, post):
    try:
        norm = _emit(parsed, ("obj", schema))
        post(norm)
        if _dumps(norm) != _dumps(parsed):  # not canonical: unrounded float, int for float, ...
            raise _Bad()
    except Exception:  # noqa: BLE001
        raise ValueError("invalid broker output") from None
    return parsed


def _is_refusal(parsed) -> bool:
    # a FAILING seal-check is a legitimate document with ok=false; only a document carrying a refusal
    # `code` (and no schema tag) is a refusal
    return type(parsed) is dict and parsed.get("ok") is False and "code" in parsed and "schema" not in parsed


def validate_result_json(text) -> dict:
    """Parse a result (or a refusal) and enforce the closed schema; reject duplicate keys and trailing data."""
    parsed = _parse(text)
    if _is_refusal(parsed):
        return _check_parsed(parsed, REFUSAL_SCHEMA, _post_refusal)
    return _check_parsed(parsed, RESULT_SCHEMA, _post_result)


def validate_status_json(text) -> dict:
    return _check_parsed(_parse(text), STATUS_SCHEMA, _post_status)


def validate_sealcheck_json(text) -> dict:
    return _check_parsed(_parse(text), SEALCHECK_SCHEMA, _post_sealcheck)


def validate_broker_json(text) -> dict:
    """Validate any broker document by its declared kind: refusal, result, status or seal-check."""
    parsed = _parse(text)
    if _is_refusal(parsed):
        return _check_parsed(parsed, REFUSAL_SCHEMA, _post_refusal)
    kinds = {
        RESULT_SCHEMA_ID: (RESULT_SCHEMA, _post_result),
        STATUS_SCHEMA_ID: (STATUS_SCHEMA, _post_status),
        SEALCHECK_SCHEMA_ID: (SEALCHECK_SCHEMA, _post_sealcheck),
    }
    tag = parsed.get("schema") if type(parsed) is dict else None
    if type(tag) is not str or tag not in kinds:
        raise ValueError("invalid broker output")
    schema, post = kinds[tag]
    return _check_parsed(parsed, schema, post)
