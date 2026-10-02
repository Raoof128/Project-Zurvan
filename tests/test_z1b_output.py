"""Z1b Task 3 tests: closed result serializer, adversarial and fuzz (contract s3.2, gate 2)."""
import copy
import json
import math
import random

import pytest

from scripts.eval_seal import output as out
from scripts.eval_seal.codes import ALL_CODES, SealRefusal
from scripts.eval_seal.spec import STRATA

_T3_SHA40 = "a" * 40
_T3_HEX16 = "0123456789abcdef"
_T3_HEX64 = "b" * 64
_T3_METRICS = ("recall_at_1", "recall_at_3", "recall_at_10", "mrr")


def _t3_metric_block(n=None):
    block = {"recall_at_1": 0.25, "recall_at_3": 0.5, "recall_at_10": 0.75, "mrr": 0.4}
    if n is not None:
        block["n"] = n
    return block


def _t3_result(**over):
    res = {
        "ok": True,
        "schema": "zurvan.z1b.result.v1",
        "suite": "holdout",
        "suite_id": _T3_HEX16,
        "n_items": 150,
        "corpus_sha256": _T3_HEX64,
        "suite_manifest_sha256": "c" * 64,
        "evaluator_version": "1.0.0",
        "broker_sha": _T3_SHA40,
        "candidate_commit": "d" * 40,
        "metrics": dict(_t3_metric_block(), ci={m: [0.1, 0.9] for m in _T3_METRICS}),
        "per_stratum": None,
        "target_missing": 0,
        "abstain": None,
        "latency_ms": {"p50": 40, "p95": 120},
        "peak_rss_mb": 900,
        "seal_valid": True,
        "cached": False,
        "budget_remaining": 7,
        "promotion": None,
    }
    res.update(over)
    return res


def _t3_status(**over):
    st = {
        "ok": True, "schema": "zurvan.z1b.status.v1", "configured": True, "broker_sha": _T3_SHA40,
        "installed_sha": _T3_SHA40, "installed_tree": "e" * 40, "stale": False, "manifest_valid": True,
        "corpus_match": True, "budget_remaining": 8, "open_incidents": 0, "holdout_n": 160,
        "reserve_n": 80, "suite_id": _T3_HEX16,
    }
    st.update(over)
    return st


def _t3_sealcheck(**over):
    sc = {"ok": True, "schema": "zurvan.z1b.sealcheck.v1", "broker_sha": _T3_SHA40,
          "checks": {cid: True for cid in out.CHECK_IDS}}
    sc.update(over)
    return sc


def _t3_refused(fn, *args):
    with pytest.raises(SealRefusal) as exc:
        fn(*args)
    assert exc.value.code == "INTERNAL"
    assert str(exc.value) == "INTERNAL"
    return exc.value


# ------------------------------------------------------------ happy paths

def test_t3_good_result_serializes_to_canonical_json_and_round_trips():
    text = out.serialize_result(_t3_result())
    assert text == json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    parsed = out.validate_result_json(text)
    assert parsed["metrics"]["mrr"] == 0.4
    assert parsed["n_items"] == 150
    assert "\n" not in text


def test_t3_schema_constants_are_closed_dicts():
    assert set(out.RESULT_SCHEMA) == set(_t3_result())
    assert set(out.STATUS_SCHEMA) == set(_t3_status())
    assert set(out.SEALCHECK_SCHEMA) == set(_t3_sealcheck())
    assert len(out.CHECK_IDS) == len(set(out.CHECK_IDS)) >= 10


def test_t3_per_stratum_and_abstain_and_promotion_shapes_accepted():
    ps = {s: _t3_metric_block(n=10) for s in STRATA if s != "not_found"}
    promo = {"declared": True, "rules": {"non_inferiority_critical": True, "non_inferiority_overall": True,
                                          "improvement": True, "resource_budget": True, "resource_improved": False}, "verdict": "PASS"}
    res = _t3_result(per_stratum=ps, abstain={"passed": 7, "n": 10}, promotion=promo, cached=True,
                     suite="reserve", target_missing=2)
    parsed = out.validate_result_json(out.serialize_result(res))
    assert set(parsed["per_stratum"]) == set(ps)
    assert parsed["abstain"] == {"passed": 7, "n": 10}
    assert parsed["promotion"]["verdict"] == "PASS"


def test_t3_failed_promotion_with_one_false_rule_is_a_fail():
    rules = {"non_inferiority_critical": True, "non_inferiority_overall": True,
             "improvement": False, "resource_budget": True, "resource_improved": False}
    promo = {"declared": True, "rules": rules, "verdict": "FAIL"}
    out.serialize_result(_t3_result(promotion=promo))
    _t3_refused(out.serialize_result, _t3_result(promotion=dict(promo, verdict="PASS")))
    _t3_refused(out.serialize_result, _t3_result(
        promotion=dict(promo, rules=dict(rules, improvement=True))))


# ------------------------------------------------------------ rounding and numbers

def test_t3_metrics_rounded_to_a_thousandth_and_ci_to_a_hundredth():
    res = _t3_result()
    res["metrics"] = dict(res["metrics"], recall_at_1=0.12349, mrr=0.3335,
                          ci={m: [0.12345, 0.98765] for m in _T3_METRICS})
    parsed = json.loads(out.serialize_result(res))
    assert parsed["metrics"]["recall_at_1"] == 0.123
    assert parsed["metrics"]["mrr"] == round(0.3335, 3)
    assert parsed["metrics"]["ci"]["mrr"] == [0.12, 0.99]


def test_t3_integer_metric_values_are_emitted_as_floats_and_negative_zero_is_normalised():
    res = _t3_result()
    res["metrics"] = dict(res["metrics"], recall_at_1=1, recall_at_3=0, mrr=-0.0)
    text = out.serialize_result(res)
    assert '"recall_at_1":1.0' in text
    assert '"recall_at_3":0.0' in text
    assert '"mrr":0.0' in text and "-0.0" not in text


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.001, 1.001, 2, "0.5", "", None,
                                 True, False, [0.5], {"v": 0.5}, b"0.5", 0.5j])
def test_t3_bad_metric_values_are_refused(bad):
    res = _t3_result()
    res["metrics"] = dict(res["metrics"], recall_at_1=bad)
    _t3_refused(out.serialize_result, res)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5, 1.5, "0.1", None, True])
def test_t3_bad_ci_values_are_refused(bad):
    for pos in (0, 1):
        res = _t3_result()
        pair = [0.1, 0.9]
        pair[pos] = bad
        res["metrics"] = dict(res["metrics"], ci=dict(res["metrics"]["ci"], mrr=pair))
        _t3_refused(out.serialize_result, res)


def test_t3_ci_must_be_an_ordered_pair_of_exactly_two():
    for pair in ([0.9, 0.1], [0.1], [0.1, 0.2, 0.3], [], "ab", 5, None, {"lo": 0.1, "hi": 0.2}):
        res = _t3_result()
        res["metrics"] = dict(res["metrics"], ci=dict(res["metrics"]["ci"], mrr=pair))
        _t3_refused(out.serialize_result, res)
    res = _t3_result()
    res["metrics"]["ci"]["mrr"] = (0.1, 0.9)  # a tuple is acceptable and is emitted as a JSON list
    assert json.loads(out.serialize_result(res))["metrics"]["ci"]["mrr"] == [0.1, 0.9]


@pytest.mark.parametrize("p50,p95,ok", [
    (40, 120, True), (0, 0, True), (10, 10, True), (45, 120, False), (40, 125, False), (1, 10, False),
    (40.0, 120, False), (-10, 120, False), (True, 120, False), (40, True, False), ("40", 120, False),
    (None, 120, False), (120, 40, False),
])
def test_t3_latency_must_be_a_multiple_of_ten_ints_and_ordered(p50, p95, ok):
    res = _t3_result(latency_ms={"p50": p50, "p95": p95})
    if ok:
        out.serialize_result(res)
    else:
        _t3_refused(out.serialize_result, res)


@pytest.mark.parametrize("field", ["n_items", "target_missing", "peak_rss_mb", "budget_remaining"])
@pytest.mark.parametrize("bad", [True, False, 1.0, "1", None, -1, 10 ** 30, [1], float("nan")])
def test_t3_bool_is_not_int_and_other_wrong_ints_are_refused(field, bad):
    _t3_refused(out.serialize_result, _t3_result(**{field: bad}))


@pytest.mark.parametrize("field", ["ok", "seal_valid", "cached"])
@pytest.mark.parametrize("bad", [1, 0, "true", "false", "True", None, [], {}, 1.0])
def test_t3_int_or_string_is_not_bool(field, bad):
    _t3_refused(out.serialize_result, _t3_result(**{field: bad}))


def test_t3_a_result_with_ok_false_is_not_a_result():
    _t3_refused(out.serialize_result, _t3_result(ok=False))


# ------------------------------------------------------------ closedness

def test_t3_unknown_missing_and_nested_extra_keys_are_refused():
    base = _t3_result()
    _t3_refused(out.serialize_result, dict(base, extra="x"))
    _t3_refused(out.serialize_result, dict(base, **{"query": "hello"}))
    for key in base:
        res = dict(base)
        del res[key]
        _t3_refused(out.serialize_result, res)
    res = _t3_result()
    res["metrics"]["debug"] = 1.0
    _t3_refused(out.serialize_result, res)
    res = _t3_result()
    res["latency_ms"]["p99"] = 10
    _t3_refused(out.serialize_result, res)
    res = _t3_result()
    res["metrics"]["ci"]["extra"] = [0.1, 0.2]
    _t3_refused(out.serialize_result, res)
    res = _t3_result()
    del res["metrics"]["ci"]["mrr"]
    _t3_refused(out.serialize_result, res)


def test_t3_non_dict_inputs_and_non_str_keys_are_refused():
    for bad in (None, [], "x", 5, _t3_result().items(), [("ok", True)]):
        _t3_refused(out.serialize_result, bad)
    res = _t3_result()
    res[1] = "x"
    _t3_refused(out.serialize_result, res)


@pytest.mark.parametrize("field,bad", [
    ("suite", "other"), ("suite", ""), ("suite", "HOLDOUT"), ("suite", "holdout\n"), ("suite", None),
    ("schema", "zurvan.z1b.result.v2"), ("suite_id", "0123456789abcde"), ("suite_id", "0123456789ABCDEF"),
    ("suite_id", "0123456789abcdef\n"), ("suite_id", "g" * 16), ("corpus_sha256", "b" * 63),
    ("corpus_sha256", "B" * 64), ("suite_manifest_sha256", "c" * 65), ("broker_sha", "a" * 39),
    ("broker_sha", "a" * 40 + "\n"), ("candidate_commit", "z" * 40), ("candidate_commit", None),
    ("evaluator_version", "1.0"), ("evaluator_version", "1.0.0-rc1"), ("evaluator_version", "v1.0.0"),
    ("evaluator_version", "1.0.0\n"), ("evaluator_version", "١.٠.٠"),
    ("per_stratum", {}), ("per_stratum", "none"), ("per_stratum", []), ("abstain", {}), ("abstain", "x"),
    ("promotion", {}), ("promotion", "PASS"), ("promotion", True), ("latency_ms", None), ("metrics", None),
])
def test_t3_each_string_must_match_its_field_regex_or_enum(field, bad):
    if field == "per_stratum" and bad == {}:
        out.serialize_result(_t3_result(**{field: bad}))  # an empty map is a legal (empty) report
        return
    _t3_refused(out.serialize_result, _t3_result(**{field: bad}))


@pytest.mark.parametrize("key", ["query text with spaces", "semantic ", "SEMANTIC", "", "x" * 400, "\u0000", 5])
def test_t3_per_stratum_keys_must_be_one_of_the_sixteen_ids(key):
    ps = {"semantic": _t3_metric_block(n=1)}
    ps[key] = _t3_metric_block(n=1)
    _t3_refused(out.serialize_result, _t3_result(per_stratum=ps))


def test_t3_per_stratum_values_are_closed_blocks():
    for block in (_t3_metric_block(), dict(_t3_metric_block(n=1), extra=1), dict(_t3_metric_block(n=True)),
                  dict(_t3_metric_block(n=-1)), dict(_t3_metric_block(n="3")), "x", None):
        _t3_refused(out.serialize_result, _t3_result(per_stratum={"semantic": block}))
    blk = _t3_metric_block(n=3)
    blk["mrr"] = 1.5
    _t3_refused(out.serialize_result, _t3_result(per_stratum={"semantic": blk}))


def test_t3_abstain_must_be_ordered_ints():
    out.serialize_result(_t3_result(abstain={"passed": 0, "n": 0}))
    for bad in ({"passed": 11, "n": 10}, {"passed": True, "n": 10}, {"passed": 1, "n": "10"},
                {"passed": 1}, {"passed": 1, "n": 2, "x": 3}, {"passed": -1, "n": 2}):
        _t3_refused(out.serialize_result, _t3_result(abstain=bad))


def test_t3_promotion_must_be_declared_closed_and_coherent():
    rules = {"non_inferiority_critical": True, "non_inferiority_overall": True,
             "improvement": True, "resource_budget": True, "resource_improved": False}
    good = {"declared": True, "rules": rules, "verdict": "PASS"}
    out.serialize_result(_t3_result(promotion=good))
    for bad in (dict(good, declared=False), dict(good, declared=1), dict(good, verdict="pass"),
                dict(good, verdict="MAYBE"), dict(good, extra=1), dict(good, rules=dict(rules, extra=True)),
                dict(good, rules={k: v for k, v in rules.items() if k != "improvement"}),
                dict(good, rules=dict(rules, improvement="yes")), {"declared": True, "verdict": "PASS"}):
        _t3_refused(out.serialize_result, _t3_result(promotion=bad))


def test_t3_str_and_dict_subclasses_with_rigged_behaviour_are_refused():
    class _T3Str(str):
        def __str__(self):
            return "LEAK"

        def __repr__(self):
            return "LEAK"

    class _T3Dict(dict):
        pass

    class _T3Float(float):
        pass

    class _T3Int(int):
        pass

    _t3_refused(out.serialize_result, _t3_result(suite_id=_T3Str(_T3_HEX16)))
    _t3_refused(out.serialize_result, _t3_result(suite=_T3Str("holdout")))
    _t3_refused(out.serialize_result, _t3_result(latency_ms=_T3Dict(p50=10, p95=10)))
    _t3_refused(out.serialize_result, _t3_result(n_items=_T3Int(5)))
    res = _t3_result()
    res["metrics"]["mrr"] = _T3Float(0.5)
    _t3_refused(out.serialize_result, res)
    _t3_refused(out.serialize_result, _T3Dict(_t3_result()))
    res = _t3_result()
    res[_T3Str("extra")] = 1
    _t3_refused(out.serialize_result, res)


def test_t3_objects_that_blow_up_or_recurse_become_internal_not_other_exceptions():
    class _T3Boom:
        def __eq__(self, other):
            raise RuntimeError("secret detail")

        def __hash__(self):
            raise RuntimeError("secret detail")

        def __str__(self):
            raise RuntimeError("secret detail")

    deep = cur = {}
    for _ in range(5000):
        cur["a"] = {}
        cur = cur["a"]
    for bad in (_T3Boom(), deep, [deep], object(), {"ok": _T3Boom()}):
        for field in ("suite", "metrics", "per_stratum", "promotion", "n_items"):
            _t3_refused(out.serialize_result, _t3_result(**{field: bad}))
    cyc = {}
    cyc["self"] = cyc
    _t3_refused(out.serialize_result, _t3_result(per_stratum=cyc))


def test_t3_output_is_pure_ascii_and_single_line():
    text = out.serialize_result(_t3_result())
    assert text.isascii()
    assert "\n" not in text and "\r" not in text


# ------------------------------------------------------------ refusal, status, seal-check

@pytest.mark.parametrize("code", ALL_CODES)
def test_t3_refusal_known_codes_emit_exactly_three_keys(code):
    text = out.serialize_refusal(code, _T3_SHA40)
    assert json.loads(text) == {"ok": False, "code": code, "broker_sha": _T3_SHA40}
    assert out.validate_result_json(text)["code"] == code


@pytest.mark.parametrize("bad", ["NOPE", "", "bad_args", "who was the user in item 7", None, 7, b"BAD_ARGS",
                                 "BAD_ARGS\n", ("BAD_ARGS",)])
def test_t3_refusal_unknown_code_emits_internal(bad):
    parsed = json.loads(out.serialize_refusal(bad, _T3_SHA40))
    assert parsed == {"ok": False, "code": "INTERNAL", "broker_sha": _T3_SHA40}


def test_t3_refusal_with_unusable_broker_sha_emits_zeros_not_the_input():
    for bad in ("not a sha", None, "A" * 40, "a" * 41, "SECRET QUERY TEXT", 5):
        parsed = json.loads(out.serialize_refusal("BAD_ARGS", bad))
        assert parsed["broker_sha"] == "0" * 40
        assert "SECRET" not in json.dumps(parsed)


def test_t3_refusal_rigged_str_code_is_internal():
    class _T3Code(str):
        def __eq__(self, other):
            return True

        __hash__ = str.__hash__

    assert json.loads(out.serialize_refusal(_T3Code("BAD_ARGS"), _T3_SHA40))["code"] == "INTERNAL"


def test_t3_status_round_trips_and_is_closed():
    text = out.serialize_status(_t3_status())
    assert out.validate_status_json(text)["holdout_n"] == 160
    assert json.loads(out.serialize_status(_t3_status(installed_sha=None, installed_tree=None, suite_id=None)))[
        "suite_id"] is None
    for bad in (_t3_status(extra=1), _t3_status(configured="yes"), _t3_status(stale=0),
                _t3_status(broker_sha="x"), _t3_status(installed_sha="x" * 40), _t3_status(holdout_n=True),
                _t3_status(suite_id="not hex"), _t3_status(open_incidents=-1), _t3_status(schema="other")):
        _t3_refused(out.serialize_status, bad)
    st = _t3_status()
    del st["configured"]
    _t3_refused(out.serialize_status, st)


def test_t3_sealcheck_round_trips_and_ok_must_agree_with_checks():
    text = out.serialize_sealcheck(_t3_sealcheck())
    assert out.validate_sealcheck_json(text)["ok"] is True
    failing = _t3_sealcheck()
    failing["checks"][out.CHECK_IDS[0]] = False
    _t3_refused(out.serialize_sealcheck, failing)  # ok True with a failing check
    failing["ok"] = False
    assert json.loads(out.serialize_sealcheck(failing))["ok"] is False
    _t3_refused(out.serialize_sealcheck, _t3_sealcheck(ok=False))  # ok False with all passing
    _t3_refused(out.serialize_sealcheck, _t3_sealcheck(checks={}))
    _t3_refused(out.serialize_sealcheck, _t3_sealcheck(checks={"made_up_check": True}))
    _t3_refused(out.serialize_sealcheck, _t3_sealcheck(checks={out.CHECK_IDS[0]: "true"}))
    _t3_refused(out.serialize_sealcheck, _t3_sealcheck(extra=1))


def test_t3_serialize_functions_do_not_accept_each_others_documents():
    _t3_refused(out.serialize_result, _t3_status())
    _t3_refused(out.serialize_status, _t3_result())
    _t3_refused(out.serialize_sealcheck, _t3_status())
    _t3_refused(out.serialize_result, _t3_sealcheck())


# ------------------------------------------------------------ dev-side validators

def _t3_good_text():
    return out.serialize_result(_t3_result())


def test_t3_validate_accepts_one_trailing_newline_only():
    text = _t3_good_text()
    out.validate_result_json(text + "\n")
    for tail in ("\n\n", " ", "\t", "x", "{}", "\n{}", "\r\n"):
        with pytest.raises(ValueError):
            out.validate_result_json(text + tail)


def test_t3_validate_rejects_duplicate_json_keys_at_any_depth():
    text = _t3_good_text()
    dup_top = text[:-1] + ',"ok":true}'
    with pytest.raises(ValueError):
        out.validate_result_json(dup_top)
    dup_nested = text.replace('"latency_ms":{"p50":40,', '"latency_ms":{"p50":40,"p50":50,', 1)
    assert dup_nested != text
    with pytest.raises(ValueError):
        out.validate_result_json(dup_nested)


def test_t3_validate_rejects_non_finite_tokens_and_garbage():
    text = _t3_good_text()
    for bad in (text.replace('"mrr":0.4', '"mrr":NaN'), text.replace('"mrr":0.4', '"mrr":Infinity'),
                "", "[]", "null", "5", '"x"', "{", text[: len(text) // 2], "﻿" + text):
        with pytest.raises(ValueError):
            out.validate_result_json(bad)
    with pytest.raises(ValueError):
        out.validate_result_json(None)
    with pytest.raises(ValueError):
        out.validate_result_json(text.encode())


def test_t3_validate_rejects_unrounded_floats_and_float_for_int_and_extra_keys():
    text = _t3_good_text()
    with pytest.raises(ValueError):
        out.validate_result_json(text.replace('"mrr":0.4', '"mrr":0.4001'))
    with pytest.raises(ValueError):
        out.validate_result_json(text.replace('"mrr":0.4', '"mrr":"0.4"'))
    with pytest.raises(ValueError):
        out.validate_result_json(text.replace('"n_items":150', '"n_items":150.0'))
    with pytest.raises(ValueError):
        out.validate_result_json(text[:-1] + ',"note":"hi"}')
    with pytest.raises(ValueError):
        out.validate_result_json(text.replace('"p50":40', '"p50":45'))
    with pytest.raises(ValueError):
        out.validate_result_json(text.replace('"suite":"holdout"', '"suite":"dev"'))


def test_t3_validate_error_message_never_echoes_input():
    marker = "MARKER-QUERY-xyz"
    for bad in (_t3_good_text().replace('"holdout"', '"%s"' % marker), '{"ok":true,"%s":1}' % marker, marker):
        with pytest.raises(ValueError) as exc:
            out.validate_result_json(bad)
        assert marker not in str(exc.value)


def test_t3_validate_status_and_sealcheck_reject_a_result_document_and_vice_versa():
    with pytest.raises(ValueError):
        out.validate_status_json(_t3_good_text())
    with pytest.raises(ValueError):
        out.validate_sealcheck_json(out.serialize_status(_t3_status()))
    with pytest.raises(ValueError):
        out.validate_result_json(out.serialize_status(_t3_status()))
    with pytest.raises(ValueError):
        out.validate_status_json(out.serialize_refusal("BAD_ARGS", _T3_SHA40))


def test_t3_validate_broker_json_dispatches_on_the_document_kind():
    assert out.validate_broker_json(_t3_good_text())["suite"] == "holdout"
    assert out.validate_broker_json(out.serialize_status(_t3_status()))["configured"] is True
    assert out.validate_broker_json(out.serialize_sealcheck(_t3_sealcheck()))["ok"] is True
    assert out.validate_broker_json(out.serialize_refusal("BAD_ARGS", _T3_SHA40))["ok"] is False
    for bad in ('{"ok":true,"schema":"other"}', "{}", '{"schema":"zurvan.z1b.result.v1"}'):
        with pytest.raises(ValueError):
            out.validate_broker_json(bad)


# ------------------------------------------------------------ gate 2: sealed-row fuzz

_T3_SEED = 20261002
_T3_WORDS = [
    "quartzlinewords", "café-schüssel", "日本語のクエリ·長い",
    "emoji\U0001f525fire\U0001f680", "مرحبا-العالم",
    'quote"inside', "back\\slash\\\\path", "brace{open}close", "brack[et]list", "comma,colon:pair",
    "line1\nline2", "tab\there", "</script><b>x</b>", "nul\x00byte", "percent%20encoded", " sep ",
    "control\x07bell", "surrogate-adjacent퟿x", "Ünïcödé-wörd", "SHOUTING-UPPERCASE-QUERY",
]


def _t3_sealed_strings(rng, n):
    rows = []
    for _ in range(n):
        parts = [rng.choice(_T3_WORDS) for _ in range(rng.randint(1, 4))]
        parts.append("zc-%024x" % rng.getrandbits(96))  # a canary token
        rng.shuffle(parts)
        rows.append(" ".join(parts) + "?" * rng.randint(0, 2))
    return rows


def _t3_forms(sealed):
    forms = {sealed, sealed.lower(), sealed.upper(),
             json.dumps(sealed)[1:-1], json.dumps(sealed, ensure_ascii=False)[1:-1],
             json.dumps(sealed.lower())[1:-1],
             "".join("\\u%04x" % ord(c) for c in sealed), "".join("\\u%04X" % ord(c) for c in sealed)}
    for tok in sealed.split():
        if len(tok) >= 6:
            forms.add(tok)
            forms.add(tok.lower())
            forms.add(json.dumps(tok)[1:-1])
    return {f for f in forms if len(f) >= 6}


def _t3_assert_clean(text, sealed):
    for form in _t3_forms(sealed):
        assert form not in text, "sealed form leaked"


def _t3_set(res, path, value):
    cur = res
    for key in path[:-1]:
        cur = cur[key]
    cur[path[-1]] = value


# every attack takes (rng, sealed) and returns (kind, document) with a document that MUST be refused
def _t3_attacks():
    hex_paths = [("suite_id",), ("corpus_sha256",), ("suite_manifest_sha256",), ("broker_sha",),
                 ("candidate_commit",), ("evaluator_version",), ("suite",), ("schema",)]
    int_paths = [("n_items",), ("target_missing",), ("peak_rss_mb",), ("budget_remaining",),
                 ("latency_ms", "p50"), ("latency_ms", "p95")]
    bool_paths = [("ok",), ("seal_valid",), ("cached",)]
    metric_paths = [("metrics", m) for m in _T3_METRICS]

    def unknown_key_name(rng, s):
        res = _t3_result()
        res[s] = 1
        return res

    def unknown_key_value(rng, s):
        res = _t3_result()
        res["note"] = s
        return res

    def hex_field(rng, s):
        res = _t3_result()
        _t3_set(res, rng.choice(hex_paths), s)
        return res

    def hex_field_prefixed(rng, s):
        res = _t3_result()
        path = rng.choice(hex_paths[:5])
        _t3_set(res, path, "a" * 8 + s)
        return res

    def int_field(rng, s):
        res = _t3_result()
        _t3_set(res, rng.choice(int_paths), s)
        return res

    def bool_field(rng, s):
        res = _t3_result()
        _t3_set(res, rng.choice(bool_paths), s)
        return res

    def metric_string(rng, s):
        res = _t3_result()
        _t3_set(res, rng.choice(metric_paths), rng.choice([s, "0.5" + s, str(0.5), "0.5"]))
        return res

    def metric_nested(rng, s):
        res = _t3_result()
        _t3_set(res, rng.choice(metric_paths), {s: 0.5})
        return res

    def ci_injection(rng, s):
        res = _t3_result()
        res["metrics"]["ci"][rng.choice(_T3_METRICS)] = rng.choice([[s, 0.9], [0.1, s], [s], [0.1, 0.9, s],
                                                                    {s: 0.1}, s])
        return res

    def ci_key(rng, s):
        res = _t3_result()
        res["metrics"]["ci"][s] = [0.1, 0.9]
        return res

    def per_stratum_key(rng, s):
        res = _t3_result()
        res["per_stratum"] = {s: _t3_metric_block(n=3)}
        return res

    def per_stratum_value(rng, s):
        res = _t3_result()
        blk = _t3_metric_block(n=3)
        blk[rng.choice(list(blk))] = s
        res["per_stratum"] = {rng.choice(STRATA): blk}
        return res

    def per_stratum_extra(rng, s):
        res = _t3_result()
        blk = _t3_metric_block(n=3)
        blk[s] = 0.1
        res["per_stratum"] = {rng.choice(STRATA): blk}
        return res

    def promotion_verdict(rng, s):
        res = _t3_result()
        rules = {"non_inferiority_critical": True, "non_inferiority_overall": True,
                 "improvement": True, "resource_budget": True, "resource_improved": False}
        res["promotion"] = {"declared": True, "rules": rules, "verdict": s}
        return res

    def promotion_rule_key(rng, s):
        res = _t3_result()
        rules = {"non_inferiority_critical": True, "non_inferiority_overall": True,
                 "improvement": True, "resource_budget": True, "resource_improved": False, s: True}
        res["promotion"] = {"declared": True, "rules": rules, "verdict": "PASS"}
        return res

    def promotion_string(rng, s):
        res = _t3_result()
        res["promotion"] = s
        return res

    def abstain_field(rng, s):
        res = _t3_result()
        res["abstain"] = {"passed": rng.choice([s, 1]), "n": s}
        return res

    def whole_document(rng, s):
        return rng.choice([s, [s], {s: s}, (s,), {"ok": True, "schema": s}])

    def latency_extra(rng, s):
        res = _t3_result()
        res["latency_ms"][s] = 10
        return res

    def metrics_extra(rng, s):
        res = _t3_result()
        res["metrics"][s] = 0.5
        return res

    def rigged_object(rng, s):
        class _T3Leaky:
            def __str__(self):
                return s

            def __repr__(self):
                return s

            def __format__(self, spec):
                return s

        res = _t3_result()
        _t3_set(res, rng.choice(hex_paths + int_paths + bool_paths + metric_paths), _T3Leaky())
        return res

    def rigged_str_subclass(rng, s):
        class _T3SneakyStr(str):
            def __str__(self):
                return s

            def __repr__(self):
                return s

        res = _t3_result()
        _t3_set(res, rng.choice([("suite_id",), ("suite",), ("evaluator_version",)]),
                _T3SneakyStr({"suite_id": _T3_HEX16, "suite": "holdout", "evaluator_version": "1.0.0"}[
                    rng.choice(["suite_id", "suite", "evaluator_version"])]))
        return res

    return [unknown_key_name, unknown_key_value, hex_field, hex_field_prefixed, int_field, bool_field,
            metric_string, metric_nested, ci_injection, ci_key, per_stratum_key, per_stratum_value,
            per_stratum_extra, promotion_verdict, promotion_rule_key, promotion_string, abstain_field,
            whole_document, latency_extra, metrics_extra, rigged_object, rigged_str_subclass]


def _t3_status_attacks():
    def field(rng, s):
        st = _t3_status()
        _t3_set(st, [rng.choice(["broker_sha", "installed_sha", "installed_tree", "suite_id", "schema",
                                 "configured", "stale", "manifest_valid", "corpus_match", "budget_remaining",
                                 "open_incidents", "holdout_n", "reserve_n"])], s)
        return st

    def extra(rng, s):
        st = _t3_status()
        st[rng.choice([s, "note"])] = s
        return st

    return [field, extra]


def _t3_sealcheck_attacks():
    def check_key(rng, s):
        sc = _t3_sealcheck()
        sc["checks"][s] = True
        return sc

    def check_value(rng, s):
        sc = _t3_sealcheck()
        sc["checks"][rng.choice(out.CHECK_IDS)] = s
        return sc

    def field(rng, s):
        sc = _t3_sealcheck()
        sc[rng.choice(["broker_sha", "schema", "ok", "checks"])] = s
        return sc

    def extra(rng, s):
        sc = _t3_sealcheck()
        sc[s] = s
        return sc

    return [check_key, check_value, field, extra]


def _t3_benign_variant(rng):
    res = _t3_result()
    res["suite"] = rng.choice(["holdout", "reserve"])
    res["n_items"] = rng.randint(0, 400)
    res["target_missing"] = rng.randint(0, 5)
    res["metrics"] = {m: rng.random() for m in _T3_METRICS}
    res["metrics"]["ci"] = {m: sorted([rng.random(), rng.random()]) for m in _T3_METRICS}
    res["latency_ms"] = {"p50": 10 * rng.randint(0, 50), "p95": 10 * rng.randint(50, 500)}
    res["peak_rss_mb"] = rng.randint(1, 4000)
    res["suite_id"] = "%016x" % rng.getrandbits(64)
    res["corpus_sha256"] = "%064x" % rng.getrandbits(256)
    res["per_stratum"] = rng.choice([None, {rng.choice(STRATA): _t3_metric_block(n=rng.randint(0, 20))}])
    return res


def test_t3_fuzz_gate2_no_sealed_string_can_reach_any_output():
    rng = random.Random(_T3_SEED)
    sealed_pool = _t3_sealed_strings(rng, 400)
    result_attacks = _t3_attacks()
    stat_attacks = _t3_status_attacks()
    seal_attacks = _t3_sealcheck_attacks()
    cases = refused = benign_ok = refusals_emitted = 0
    for i in range(3000):
        sealed = rng.choice(sealed_pool)
        kind = i % 5
        if kind in (0, 1, 2):
            doc = rng.choice(result_attacks)(rng, sealed)
            fn = out.serialize_result
        elif kind == 3:
            doc = rng.choice(stat_attacks + seal_attacks[:0])(rng, sealed)
            fn = out.serialize_status
            if rng.random() < 0.5:
                doc = rng.choice(seal_attacks)(rng, sealed)
                fn = out.serialize_sealcheck
        else:
            fn = None
            doc = None
        cases += 1
        if fn is None:
            # refusal path: sealed text as code and as sha, plus a benign control result
            text = out.serialize_refusal(sealed, rng.choice([sealed, _T3_SHA40]))
            assert json.loads(text)["code"] == "INTERNAL"
            _t3_assert_clean(text, sealed)
            refusals_emitted += 1
            benign = _t3_benign_variant(rng)
            text = out.serialize_result(benign)
            _t3_assert_clean(text, sealed)
            out.validate_result_json(text)
            benign_ok += 1
            continue
        try:
            text = fn(doc)
        except SealRefusal as exc:
            assert exc.code == "INTERNAL" and str(exc) == "INTERNAL"
            assert sealed not in repr(exc) and sealed not in str(exc.args)
            refused += 1
        else:  # an attack that is accepted must still emit no sealed string, and must not exist at all
            _t3_assert_clean(text, sealed)
            pytest.fail("attack document was accepted: %s" % type(doc).__name__)
    assert cases == 3000
    assert refused >= 2400
    assert benign_ok >= 500 and refusals_emitted >= 500


def test_t3_fuzz_random_json_shaped_documents_are_either_refused_or_clean():
    rng = random.Random(_T3_SEED + 1)
    sealed_pool = _t3_sealed_strings(rng, 200)

    def rand_value(depth=0):
        choice = rng.random()
        if depth > 3 or choice < 0.45:
            return rng.choice([rng.choice(sealed_pool), rng.random(), rng.randint(-5, 500), None, True, False,
                               "holdout", _T3_HEX16, "a" * 40])
        if choice < 0.75:
            return {rng.choice(sealed_pool + list(_t3_result())): rand_value(depth + 1)
                    for _ in range(rng.randint(0, 4))}
        return [rand_value(depth + 1) for _ in range(rng.randint(0, 4))]

    accepted = 0
    for _ in range(1500):
        doc = _t3_result()
        for key in rng.sample(list(doc), rng.randint(1, 4)):
            doc[key] = rand_value()
        sealed_in = json.dumps(doc, default=str, ensure_ascii=False)
        try:
            text = out.serialize_result(doc)
        except SealRefusal as exc:
            assert exc.code == "INTERNAL"
            continue
        accepted += 1
        for sealed in sealed_pool:
            if sealed in sealed_in:
                _t3_assert_clean(text, sealed)
    assert accepted < 1500


def test_t3_fuzz_accepted_documents_contain_only_schema_vocabulary():
    # an independent check of what can ever be emitted: every string in an accepted document is a schema
    # key, an enum member, or matches a hex/semver pattern. Query text has no way in.
    rng = random.Random(_T3_SEED + 2)
    vocab = set(out.RESULT_SCHEMA) | set(_T3_METRICS) | {"ci", "n", "p50", "p95", "passed", "declared", "rules",
        "verdict", "non_inferiority_critical", "non_inferiority_overall", "improvement", "resource_budget", "resource_improved",
        "PASS", "FAIL", "holdout", "reserve", "zurvan.z1b.result.v1"} | set(STRATA)
    import re
    hexish = re.compile(r"^(?:[0-9a-f]{16}|[0-9a-f]{40}|[0-9a-f]{64}|[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})$")

    def strings(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from strings(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from strings(v)
        elif isinstance(obj, str):
            yield obj

    for _ in range(300):
        text = out.serialize_result(_t3_benign_variant(rng))
        for s in strings(json.loads(text)):
            assert s in vocab or hexish.match(s), s
