"""Z1b Task 2 tests: stratum spec, item validation, coverage (contract s3.1)."""
import copy
import json
import secrets
from pathlib import Path

import pytest

from scripts.eval_seal import spec as sp
from scripts.eval_seal.codes import SealRefusal

_T2_SPEC_PATH = Path(__file__).resolve().parent.parent / "eval" / "z1b" / "spec.json"
_T2_IDS = [
    "exact_identifier", "path", "hash", "error_string", "semantic", "alias", "decision_rationale",
    "temporal", "contradiction", "provenance", "code", "multi_hop", "synthesis", "cross_project",
    "hard_negative", "not_found",
]


def _t2_raw():
    return json.loads(_T2_SPEC_PATH.read_text(encoding="utf-8"))


def _t2_write(tmp_path, obj, name="spec.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return p


def _t2_load(tmp_path, mutate):
    raw = _t2_raw()
    mutate(raw)
    return sp.load_spec(_t2_write(tmp_path, raw))


def _t2_walk(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", k)
            yield from _t2_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _t2_walk(v)
    else:
        yield ("val", obj)


# ------------------------------------------------------------ the shipped spec

def test_t2_strata_constant_is_the_sixteen_ids_in_order():
    assert list(sp.STRATA) == _T2_IDS
    assert len(set(sp.STRATA)) == 16


def test_t2_shipped_spec_loads_and_matches_contract():
    spec = sp.load_spec(_T2_SPEC_PATH)
    assert spec["schema"] == "zurvan.z1b.spec.v1"
    assert [s["id"] for s in spec["strata"]] == _T2_IDS
    grading = {s["id"]: s["grading"] for s in spec["strata"]}
    assert {k for k, v in grading.items() if v == "set"} == {"multi_hop", "synthesis"}
    assert {k for k, v in grading.items() if v == "abstain"} == {"not_found"}
    assert sum(1 for v in grading.values() if v == "rank") == 13
    assert all(s["min_holdout"] == 10 and s["min_reserve"] == 5 for s in spec["strata"])
    assert spec["totals"] == {"min_holdout": 150, "min_reserve": 80}
    assert spec["run_policy"]["holdout_budget"] == 8
    assert spec["run_policy"]["max_results"] == 10
    assert spec["locator_kinds"] == ["page", "section"]
    assert isinstance(spec["abstain_threshold"], float)
    for s in spec["strata"]:
        assert abs(sum(s["difficulty_targets"].values()) - 1.0) < 1e-9
        assert set(s["difficulty_targets"]) == {"easy", "medium", "hard"}


def test_t2_predeclaration_schema_covers_the_five_canonical_fields_plus_bindings():
    schema = sp.load_spec(_T2_SPEC_PATH)["predeclaration_schema"]
    assert set(schema["required"]) == {
        "primary_metric", "critical_strata", "non_inferiority_margins", "improvement_target",
        "resource_budgets", "candidate_commit", "suite_id",
    }
    assert set(schema["properties"]) == set(schema["required"])


def test_t2_spec_json_carries_no_evaluation_content():
    raw = _t2_raw()
    forbidden = {"query", "queries", "targets", "items", "chunk_ids", "answer", "answers"}
    for kind, val in _t2_walk(raw):
        if kind == "key":
            assert val not in forbidden, val
        elif isinstance(val, str):
            assert len(val) <= 200, val[:40]
    assert "items" not in _T2_SPEC_PATH.read_text()


def test_t2_spec_json_is_valid_utf8_json_without_duplicate_keys():
    def hook(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys))
        return dict(pairs)

    json.loads(_T2_SPEC_PATH.read_text(encoding="utf-8"), object_pairs_hook=hook)


# ------------------------------------------------------------ load_spec refusals

def _t2_refused(tmp_path, mutate):
    with pytest.raises(SealRefusal) as exc:
        _t2_load(tmp_path, mutate)
    assert exc.value.code == "MANIFEST_INVALID"


def test_t2_load_refuses_missing_stratum(tmp_path):
    _t2_refused(tmp_path, lambda r: r["strata"].pop(5))


def test_t2_load_refuses_every_single_missing_stratum(tmp_path):
    for i in range(16):
        _t2_refused(tmp_path, lambda r, i=i: r["strata"].pop(i))


def test_t2_load_refuses_duplicate_stratum_id(tmp_path):
    def mut(r):
        r["strata"][3]["id"] = r["strata"][2]["id"]
    _t2_refused(tmp_path, mut)


def test_t2_load_refuses_extra_stratum(tmp_path):
    def mut(r):
        extra = copy.deepcopy(r["strata"][0])
        extra["id"] = "invented"
        r["strata"].append(extra)
    _t2_refused(tmp_path, mut)


def test_t2_load_refuses_reordered_strata(tmp_path):
    def mut(r):
        r["strata"][0], r["strata"][1] = r["strata"][1], r["strata"][0]
    _t2_refused(tmp_path, mut)


def test_t2_load_refuses_extra_unknown_top_level_key(tmp_path):
    _t2_refused(tmp_path, lambda r: r.update({"notes": "hello"}))


def test_t2_load_refuses_missing_top_level_key(tmp_path):
    for key in ("schema", "strata", "totals", "abstain_threshold", "run_policy",
                "predeclaration_schema", "locator_kinds"):
        _t2_refused(tmp_path, lambda r, key=key: r.pop(key))


def test_t2_load_refuses_extra_key_inside_a_stratum(tmp_path):
    _t2_refused(tmp_path, lambda r: r["strata"][0].update({"query": "x"}))


@pytest.mark.parametrize("bad", [9, 0, -1, True, 10.0, "10"])
def test_t2_load_refuses_min_holdout_below_ten_or_wrong_type(tmp_path, bad):
    _t2_refused(tmp_path, lambda r: r["strata"][4].update({"min_holdout": bad}))


@pytest.mark.parametrize("bad", [4, 0, True, 5.0])
def test_t2_load_refuses_min_reserve_below_five_or_wrong_type(tmp_path, bad):
    _t2_refused(tmp_path, lambda r: r["strata"][4].update({"min_reserve": bad}))


@pytest.mark.parametrize("bad", [149, 0, 150.0, True])
def test_t2_load_refuses_holdout_total_below_150(tmp_path, bad):
    _t2_refused(tmp_path, lambda r: r["totals"].update({"min_holdout": bad}))


@pytest.mark.parametrize("bad", [79, 0, 80.0, True])
def test_t2_load_refuses_reserve_total_below_80(tmp_path, bad):
    _t2_refused(tmp_path, lambda r: r["totals"].update({"min_reserve": bad}))


@pytest.mark.parametrize("bad", [7, 9, 0, 100, 8.0, True, "8"])
def test_t2_load_refuses_budget_other_than_eight(tmp_path, bad):
    _t2_refused(tmp_path, lambda r: r["run_policy"].update({"holdout_budget": bad}))


@pytest.mark.parametrize("key,bad", [
    ("latency_resolution_ms", 1), ("metric_precision", 0.01), ("ci_precision", 0.1), ("max_results", 20),
    ("query_timeout_s", 0), ("run_timeout_s", -5), ("bootstrap_resamples", 10), ("query_timeout_s", True),
])
def test_t2_load_refuses_bad_run_policy_values(tmp_path, key, bad):
    _t2_refused(tmp_path, lambda r: r["run_policy"].update({key: bad}))


def test_t2_load_refuses_unknown_run_policy_key(tmp_path):
    _t2_refused(tmp_path, lambda r: r["run_policy"].update({"extra": 1}))


def test_t2_load_refuses_grading_that_differs_from_the_canonical_map(tmp_path):
    _t2_refused(tmp_path, lambda r: r["strata"][11].update({"grading": "rank"}))
    _t2_refused(tmp_path, lambda r: r["strata"][0].update({"grading": "set"}))
    _t2_refused(tmp_path, lambda r: r["strata"][15].update({"grading": "rank"}))
    _t2_refused(tmp_path, lambda r: r["strata"][0].update({"grading": "model"}))


def test_t2_load_refuses_bad_difficulty_targets(tmp_path):
    _t2_refused(tmp_path, lambda r: r["strata"][0]["difficulty_targets"].update({"easy": 0.9}))
    _t2_refused(tmp_path, lambda r: r["strata"][0]["difficulty_targets"].pop("hard"))
    _t2_refused(tmp_path, lambda r: r["strata"][0]["difficulty_targets"].update({"easy": -0.1, "medium": 0.9, "hard": 0.2}))


@pytest.mark.parametrize("bad", ["0.5", True, None, float("nan"), -0.1, 1.5])
def test_t2_load_refuses_bad_abstain_threshold(tmp_path, bad):
    raw = _t2_raw()
    raw["abstain_threshold"] = bad
    text = json.dumps(raw)  # NaN serialises as the bare token NaN, which the loader must also refuse
    with pytest.raises(SealRefusal) as exc:
        sp.load_spec(_t2_write(tmp_path, text))
    assert exc.value.code == "MANIFEST_INVALID"


def test_t2_load_refuses_bad_locator_kinds(tmp_path):
    _t2_refused(tmp_path, lambda r: r.update({"locator_kinds": ["page", "section", "line_range"]}))
    _t2_refused(tmp_path, lambda r: r.update({"locator_kinds": ["page"]}))


def test_t2_load_refuses_evaluation_content_smuggled_into_the_spec(tmp_path):
    _t2_refused(tmp_path, lambda r: r.update({"run_policy": dict(r["run_policy"], query="secret")}))
    _t2_refused(tmp_path, lambda r: r["strata"][0].update({"name": "x" * 201}))
    _t2_refused(tmp_path, lambda r: r["predeclaration_schema"]["properties"]["suite_id"].update({"pattern": "x" * 201}))
    _t2_refused(tmp_path, lambda r: r["predeclaration_schema"]["properties"]["suite_id"].update({"items": []}))


def test_t2_load_refuses_predeclaration_schema_missing_a_canonical_field(tmp_path):
    def mut(r):
        r["predeclaration_schema"]["required"].remove("resource_budgets")
    _t2_refused(tmp_path, mut)

    def mut2(r):
        del r["predeclaration_schema"]["properties"]["critical_strata"]
    _t2_refused(tmp_path, mut2)


def test_t2_load_refuses_wrong_schema_const(tmp_path):
    _t2_refused(tmp_path, lambda r: r.update({"schema": "zurvan.z1b.spec.v2"}))


def test_t2_load_refuses_duplicate_json_keys_and_garbage(tmp_path):
    text = _T2_SPEC_PATH.read_text(encoding="utf-8").replace(
        '"schema": "zurvan.z1b.spec.v1",', '"schema": "zurvan.z1b.spec.v1", "schema": "zurvan.z1b.spec.v1",', 1)
    for payload in (text, "{", "[]", "null", "", '{"schema":1}'):
        with pytest.raises(SealRefusal) as exc:
            sp.load_spec(_t2_write(tmp_path, payload))
        assert exc.value.code == "MANIFEST_INVALID"


def test_t2_load_refuses_missing_file_and_non_utf8(tmp_path):
    with pytest.raises(SealRefusal) as exc:
        sp.load_spec(tmp_path / "nope.json")
    assert exc.value.code == "MANIFEST_INVALID"
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SealRefusal) as exc:
        sp.load_spec(bad)
    assert exc.value.code == "MANIFEST_INVALID"


# ------------------------------------------------------------ validate_item

def _t2_item(sid="semantic", **over):
    spec = sp.load_spec(_T2_SPEC_PATH)
    stratum = sid
    grading = {s["id"]: s["grading"] for s in spec["strata"]}[stratum]
    item = {
        "id": secrets.token_hex(16),
        "stratum": stratum,
        "query": "which page records the retention decision",
        "targets": [] if grading == "abstain" else [
            {"page": "wiki/decisions/retention.md", "source_id": None,
             "locator": {"kind": "page", "value": "wiki/decisions/retention.md"}}],
        "grading": grading,
        "difficulty": "medium",
        "provenance": {"author": "model-a", "reviewer": "model-b"},
    }
    item.update(over)
    return item


def _t2_spec():
    return sp.load_spec(_T2_SPEC_PATH)


def _t2_bad_field(item):
    with pytest.raises(ValueError) as exc:
        sp.validate_item(item, _t2_spec())
    return str(exc.value)


def test_t2_validate_item_accepts_a_well_formed_item_for_every_stratum():
    spec = _t2_spec()
    for sid in _T2_IDS:
        sp.validate_item(_t2_item(sid), spec)


def test_t2_validate_item_accepts_section_locator_and_optional_aliases():
    item = _t2_item(targets=[{"page": "wiki/a/b.md", "source_id": "src-1",
                              "locator": {"kind": "section", "value": "Decision"}}],
                    snapshot_aliases={"snapshot": "a" * 64, "chunk_ids": ["c1", "c2"]})
    sp.validate_item(item, _t2_spec())


def test_t2_validate_item_refuses_line_range_locator_d2():
    item = _t2_item(targets=[{"page": "wiki/a.md", "source_id": None,
                              "locator": {"kind": "line_range", "value": "10-20"}}])
    assert _t2_bad_field(item) == "targets"


def test_t2_validate_item_refuses_bad_stratum_and_stratum_grading_mismatch():
    assert _t2_bad_field(_t2_item(stratum="bogus")) == "stratum"
    assert _t2_bad_field(_t2_item(grading="set")) == "grading"
    assert _t2_bad_field(_t2_item("multi_hop", grading="rank")) == "grading"
    assert _t2_bad_field(_t2_item(stratum_=1)) == "keys"


@pytest.mark.parametrize("field", ["id", "stratum", "query", "targets", "grading", "difficulty", "provenance"])
def test_t2_validate_item_refuses_each_missing_required_field(field):
    item = _t2_item()
    del item[field]
    assert _t2_bad_field(item) == field


def test_t2_validate_item_refuses_empty_or_wrong_targets():
    assert _t2_bad_field(_t2_item(targets=[])) == "targets"
    assert _t2_bad_field(_t2_item(targets=None)) == "targets"
    assert _t2_bad_field(_t2_item(targets="wiki/a.md")) == "targets"
    assert _t2_bad_field(_t2_item(targets=[{"page": "wiki/a.md"}])) == "targets"
    # abstain items have no targets, and must not carry any
    assert _t2_bad_field(_t2_item("not_found", targets=[
        {"page": "wiki/a.md", "source_id": None, "locator": {"kind": "page", "value": "x"}}])) == "targets"


@pytest.mark.parametrize("page", [
    "wiki/../keys/age.key", "../etc/passwd", "wiki/a/../../b.md", "..", "wiki/a/..", "/etc/passwd",
    "wiki/a\x00b.md", "", " ", "x" * 513, 5, None,
])
def test_t2_validate_item_refuses_dotdot_absolute_nul_and_bad_target_page(page):
    item = _t2_item(targets=[{"page": page, "source_id": None, "locator": {"kind": "page", "value": "v"}}])
    assert _t2_bad_field(item) == "targets"


@pytest.mark.parametrize("bad_id", [
    "", "abc", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "0123456789abcdef0123456789abcde\n", "q1", 123, None,
    "a" * 32 + "\n",
])
def test_t2_validate_item_refuses_ids_that_do_not_look_like_32_lowercase_hex(bad_id):
    assert _t2_bad_field(_t2_item(id=bad_id)) == "id"


def test_t2_validate_item_refuses_wrong_types_and_unknown_keys():
    assert _t2_bad_field(_t2_item(query="")) == "query"
    assert _t2_bad_field(_t2_item(query=5)) == "query"
    assert _t2_bad_field(_t2_item(query="q" * 2001)) == "query"
    assert _t2_bad_field(_t2_item(difficulty="trivial")) == "difficulty"
    assert _t2_bad_field(_t2_item(provenance={"author": "a"})) == "provenance"
    assert _t2_bad_field(_t2_item(provenance={"author": "a", "reviewer": ""})) == "provenance"
    assert _t2_bad_field(_t2_item(snapshot_aliases={"snapshot": "short", "chunk_ids": []})) == "snapshot_aliases"
    assert _t2_bad_field(_t2_item(extra="x")) == "keys"
    assert _t2_bad_field("not a dict") == "item"


def test_t2_validate_item_error_never_echoes_item_content():
    secret = "SECRET-QUERY-" + secrets.token_hex(8)
    for bad in (_t2_item(query=secret, difficulty="x"),
                _t2_item(targets=[{"page": "../" + secret, "source_id": None,
                                   "locator": {"kind": "page", "value": "v"}}]),
                _t2_item(id=secret)):
        assert secret not in _t2_bad_field(bad)


# ------------------------------------------------------------ coverage

def _t2_pool(counts, spec=None):
    spec = spec or _t2_spec()
    items = []
    for sid, n in counts.items():
        for _ in range(n):
            items.append(_t2_item(sid))
    return items


def _t2_full_counts(h=10, r=5):
    return {s: h for s in _T2_IDS}, {s: r for s in _T2_IDS}


def test_t2_coverage_ok_when_every_stratum_meets_minimums():
    hc, rc = _t2_full_counts()
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["ok"] is True and cov["totals_ok"] is True and cov["short"] == []
    assert cov["holdout"] == hc and cov["reserve"] == rc


def test_t2_coverage_fifteen_full_one_at_nine_reports_exactly_that_stratum():
    hc, rc = _t2_full_counts()
    hc["contradiction"] = 9
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["ok"] is False
    assert cov["short"] == [{"pool": "holdout", "stratum": "contradiction", "have": 9, "need": 10}]
    assert cov["holdout"]["contradiction"] == 9  # never padded


def test_t2_coverage_reserve_shortfall_is_reported_with_pool_name():
    hc, rc = _t2_full_counts()
    rc["hash"] = 2
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["ok"] is False
    assert cov["short"] == [{"pool": "reserve", "stratum": "hash", "have": 2, "need": 5}]
    assert cov["reserve"]["hash"] == 2


def test_t2_coverage_totals_not_ok_when_160_holdout_spread_but_one_stratum_empty():
    hc, rc = _t2_full_counts()
    hc["not_found"] = 0
    hc["semantic"] = 20  # 20 + 14*10 = 160 in total, but one stratum is empty
    assert sum(hc.values()) == 160
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["totals_ok"] is False
    assert cov["ok"] is False
    assert {"pool": "holdout", "stratum": "not_found", "have": 0, "need": 10} in cov["short"]


def test_t2_coverage_totals_ok_false_when_sum_is_below_150_even_if_strata_not_short_in_a_custom_spec():
    spec = _t2_spec()
    for s in spec["strata"]:
        s["min_holdout"] = 5  # a hand-edited spec cannot lower the total floor
    hc, rc = _t2_full_counts(h=9)
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, spec)
    assert sum(hc.values()) == 144
    assert cov["short"] == []
    assert cov["totals_ok"] is False and cov["ok"] is False


def test_t2_coverage_empty_pools_are_short_in_every_stratum_and_never_padded():
    cov = sp.coverage({}, _t2_spec())
    assert cov["ok"] is False and cov["totals_ok"] is False
    assert len(cov["short"]) == 32
    assert all(v == 0 for v in cov["holdout"].values())
    assert all(v == 0 for v in cov["reserve"].values())
    assert list(cov["holdout"]) == _T2_IDS


def test_t2_coverage_returned_counts_equal_input_counts_even_above_minimum():
    hc, rc = _t2_full_counts()
    hc["code"] = 17
    rc["alias"] = 9
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["holdout"] == hc and cov["reserve"] == rc
    assert cov["ok"] is True


def test_t2_coverage_does_not_count_unknown_strata_toward_anything():
    hc, rc = _t2_full_counts()
    hc["semantic"] = 9
    pool = _t2_pool(hc) + [{"id": "x", "stratum": "invented"}] * 5
    cov = sp.coverage({"holdout": pool, "reserve": _t2_pool(rc)}, _t2_spec())
    assert cov["holdout"]["semantic"] == 9
    assert "invented" not in cov["holdout"]
    assert cov["ok"] is False


def test_t2_coverage_ignores_pools_other_than_holdout_and_reserve():
    hc, rc = _t2_full_counts()
    cov = sp.coverage({"holdout": _t2_pool(hc), "reserve": _t2_pool(rc), "dev": []}, _t2_spec())
    assert cov["ok"] is True
    assert set(cov) == {"holdout", "reserve", "short", "totals_ok", "ok"}
