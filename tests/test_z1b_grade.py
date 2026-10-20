"""Z1b Task 4 tests: deterministic grader, aggregation, paired bootstrap (contract s3.3)."""
import math
from pathlib import Path

import pytest

from scripts.eval_seal import grade as gr
from scripts.eval_seal import output as out
from scripts.eval_seal.spec import STRATA, load_spec

_T4_SPEC_PATH = Path(__file__).resolve().parent.parent / "eval" / "z1b" / "spec.json"
_T4_SPEC = load_spec(_T4_SPEC_PATH)


def _t4_target(page, kind="page", value=""):
    return {"page": page, "source_id": None, "locator": {"kind": kind, "value": value or page}}


def _t4_item(stratum="semantic", pages=("wiki/a.md",), grading=None, targets=None):
    grading = grading or {"multi_hop": "set", "synthesis": "set", "not_found": "abstain"}.get(stratum, "rank")
    if targets is None:
        targets = [] if grading == "abstain" else [_t4_target(p) for p in pages]
    return {"id": "0" * 32, "stratum": stratum, "query": "q", "targets": targets, "grading": grading}


def _t4_res(page, heading=None, score=0.9):
    return {"page": page, "heading": heading, "score": score}


def _t4_pages(*names):
    return [_t4_res("wiki/%s.md" % n) for n in names]


# ------------------------------------------------------------ hit and path normalisation

def test_t4_version_constant():
    assert gr.EVALUATOR_VERSION == "1.0.0"


def test_t4_page_hit_requires_equal_page():
    t = _t4_target("wiki/a.md")
    assert gr.hit(_t4_res("wiki/a.md"), t) is True
    assert gr.hit(_t4_res("wiki/b.md"), t) is False
    assert gr.hit(_t4_res("wiki/a.md.bak"), t) is False
    assert gr.hit(_t4_res("WIKI/A.MD"), t) is False
    assert gr.hit(_t4_res("a.md"), t) is False


def test_t4_page_hit_ignores_heading():
    t = _t4_target("wiki/a.md")
    assert gr.hit(_t4_res("wiki/a.md", "Anything"), t) is True


@pytest.mark.parametrize("variant", [
    "./wiki/a.md", "wiki//a.md", "wiki/./a.md", "wiki/a.md/", ".//wiki///a.md", "wiki/a.md//",
])
def test_t4_normalisation_collapses_dot_slash_and_duplicate_slashes(variant):
    assert gr.hit(_t4_res(variant), _t4_target("wiki/a.md")) is True
    assert gr.hit(_t4_res("wiki/a.md"), _t4_target(variant)) is True


@pytest.mark.parametrize("variant", [
    "wiki/../wiki/a.md", "../wiki/a.md", "wiki/x/../a.md", "wiki/a.md/..", "..", "wiki/..",
])
def test_t4_dotdot_never_matches_even_when_it_resolves_to_the_target(variant):
    assert gr.hit(_t4_res(variant), _t4_target("wiki/a.md")) is False
    assert gr.hit(_t4_res(variant), _t4_target(variant)) is False  # same bad string on both sides
    assert gr.hit(_t4_res("wiki/a.md"), _t4_target(variant)) is False


@pytest.mark.parametrize("bad", ["/wiki/a.md", "//wiki/a.md", "", ".", "./", "/", "wiki/a\x00.md", 5, None, b"x"])
def test_t4_absolute_empty_nul_and_non_str_pages_never_match(bad):
    assert gr.hit(_t4_res(bad), _t4_target("wiki/a.md")) is False
    assert gr.hit(_t4_res("wiki/a.md"), _t4_target(bad)) is False
    assert gr.hit(_t4_res(bad), _t4_target(bad)) is False


def test_t4_section_hit_requires_page_and_exact_heading():
    t = _t4_target("wiki/a.md", "section", "Decision")
    assert gr.hit(_t4_res("wiki/a.md", "Decision"), t) is True
    assert gr.hit(_t4_res("wiki/a.md", "decision"), t) is False
    assert gr.hit(_t4_res("wiki/a.md", "Decision "), t) is False
    assert gr.hit(_t4_res("wiki/a.md", None), t) is False
    assert gr.hit(_t4_res("wiki/a.md", 5), t) is False
    assert gr.hit(_t4_res("wiki/b.md", "Decision"), t) is False
    assert gr.hit(_t4_res("./wiki//a.md", "Decision"), t) is True


def test_t4_hit_is_total_on_malformed_input_and_unknown_locator_kinds():
    t = _t4_target("wiki/a.md")
    for bad in (None, 5, "x", [], {}, {"page": "wiki/a.md"}):
        assert gr.hit(bad, t) is False or isinstance(bad, dict) and bad.get("page") == "wiki/a.md"
    for bad_t in (None, {}, {"page": "wiki/a.md"}, {"page": "wiki/a.md", "locator": None},
                  {"page": "wiki/a.md", "locator": {"kind": "line_range", "value": "1-2"}},
                  {"page": "wiki/a.md", "locator": {"kind": "section"}}):
        assert gr.hit(_t4_res("wiki/a.md", "x"), bad_t) is False


# ------------------------------------------------------------ grade_item

def test_t4_rank_item_reports_first_hit_rank():
    row = gr.grade_item(_t4_item(pages=("wiki/c.md",)), _t4_pages("a", "b", "c", "d"), _T4_SPEC)
    assert row == {"stratum": "semantic", "grading": "rank", "rank": 3, "set_fraction": None,
                   "set_fraction_at": None, "abstained": None, "target_missing": False}


def test_t4_rank_item_without_a_hit_has_rank_none_and_empty_results_are_fine():
    assert gr.grade_item(_t4_item(), _t4_pages("x", "y"), _T4_SPEC)["rank"] is None
    assert gr.grade_item(_t4_item(), [], _T4_SPEC)["rank"] is None


def test_t4_rank_uses_the_best_of_several_targets():
    item = _t4_item(pages=("wiki/z.md", "wiki/b.md"))
    assert gr.grade_item(item, _t4_pages("a", "b", "z"), _T4_SPEC)["rank"] == 2


def test_t4_results_beyond_max_results_are_ignored():
    results = _t4_pages(*["r%d" % i for i in range(10)]) + _t4_pages("a")  # target would be rank 11
    assert gr.grade_item(_t4_item(), results, _T4_SPEC)["rank"] is None
    results = _t4_pages(*["r%d" % i for i in range(9)]) + _t4_pages("a")  # rank 10 counts
    assert gr.grade_item(_t4_item(), results, _T4_SPEC)["rank"] == 10


def test_t4_truncation_happens_before_deduplication():
    # a duplicate inside the first ten must not pull an eleventh result into the window
    results = _t4_pages(*["r%d" % i for i in range(9)]) + [_t4_res("wiki/r0.md")] + _t4_pages("a")
    assert len(results) == 11
    assert gr.grade_item(_t4_item(), results, _T4_SPEC)["rank"] is None


def test_t4_duplicate_results_do_not_double_count_or_inflate_rank():
    row = gr.grade_item(_t4_item(pages=("wiki/b.md",)), _t4_pages("a", "a", "a", "b"), _T4_SPEC)
    assert row["rank"] == 2  # the repeated chunk occupies one slot
    row = gr.grade_item(_t4_item("multi_hop", pages=("wiki/a.md", "wiki/b.md")),
                        _t4_pages("a", "a", "a", "a"), _T4_SPEC)
    assert row["set_fraction"] == 0.5


def test_t4_same_page_different_headings_are_distinct_chunks():
    results = [_t4_res("wiki/a.md", "H1"), _t4_res("wiki/a.md", "H2"), _t4_res("wiki/b.md")]
    assert gr.grade_item(_t4_item(pages=("wiki/b.md",)), results, _T4_SPEC)["rank"] == 3
    assert gr.grade_item(_t4_item(pages=("wiki/a.md",)), results, _T4_SPEC)["rank"] == 1


def test_t4_section_targets_are_graded_on_heading():
    item = _t4_item(targets=[_t4_target("wiki/a.md", "section", "Decision")])
    results = [_t4_res("wiki/a.md", "Context"), _t4_res("wiki/a.md", "Decision")]
    assert gr.grade_item(item, results, _T4_SPEC)["rank"] == 2


def test_t4_set_item_reports_fraction_of_targets_in_top_ten_and_per_k_fractions():
    item = _t4_item("multi_hop", pages=("wiki/a.md", "wiki/b.md", "wiki/c.md", "wiki/d.md"))
    results = _t4_pages("a", "x1", "x2", "x3", "c", "x4")
    row = gr.grade_item(item, results, _T4_SPEC)
    assert row["grading"] == "set"
    assert row["rank"] == 1
    assert row["set_fraction"] == 0.5
    assert row["set_fraction_at"] == {"1": 0.25, "3": 0.25, "10": 0.5}
    assert row["abstained"] is None


def test_t4_set_item_with_no_hits_and_with_all_hits():
    item = _t4_item("synthesis", pages=("wiki/a.md", "wiki/b.md"))
    none = gr.grade_item(item, _t4_pages("x", "y"), _T4_SPEC)
    assert none["set_fraction"] == 0.0 and none["rank"] is None
    assert none["set_fraction_at"] == {"1": 0.0, "3": 0.0, "10": 0.0}
    allhit = gr.grade_item(item, _t4_pages("b", "a"), _T4_SPEC)
    assert allhit["set_fraction"] == 1.0 and allhit["rank"] == 1
    assert allhit["set_fraction_at"] == {"1": 0.5, "3": 1.0, "10": 1.0}


@pytest.mark.parametrize("score,expected", [
    (0.1, True), (0.4999, True), (0.5, False), (0.50001, False), (0.9, False), (0.0, True), (-1, True),
])
def test_t4_abstain_passes_iff_top_score_is_strictly_below_the_threshold(score, expected):
    assert _T4_SPEC["abstain_threshold"] == 0.5
    row = gr.grade_item(_t4_item("not_found"), [_t4_res("wiki/a.md", score=score)], _T4_SPEC)
    assert row["abstained"] is expected
    assert row["grading"] == "abstain"
    assert row["rank"] is None and row["set_fraction"] is None and row["target_missing"] is False


def test_t4_abstain_uses_the_highest_score_not_the_first_and_empty_results_abstain():
    item = _t4_item("not_found")
    assert gr.grade_item(item, [_t4_res("wiki/a.md", score=0.1), _t4_res("wiki/b.md", score=0.9)],
                         _T4_SPEC)["abstained"] is False
    assert gr.grade_item(item, [], _T4_SPEC)["abstained"] is True


def test_t4_abstain_ignores_scores_beyond_max_results():
    results = [_t4_res("wiki/r%d.md" % i, score=0.1) for i in range(10)] + [_t4_res("wiki/z.md", score=0.99)]
    assert gr.grade_item(_t4_item("not_found"), results, _T4_SPEC)["abstained"] is True


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "0.1", None, True, [0.1], {}])
def test_t4_abstain_with_an_unusable_score_does_not_pass(bad):
    row = gr.grade_item(_t4_item("not_found"), [_t4_res("wiki/a.md", score=bad)], _T4_SPEC)
    assert row["abstained"] is False


def test_t4_target_missing_is_counted_not_failed_or_dropped():
    item = _t4_item(pages=("wiki/gone.md",))
    row = gr.grade_item(item, _t4_pages("gone"), _T4_SPEC, corpus_pages={"wiki/a.md"})
    assert row["target_missing"] is True
    assert row["rank"] is None and row["set_fraction"] is None and row["set_fraction_at"] is None
    row = gr.grade_item(item, _t4_pages("gone"), _T4_SPEC, corpus_pages={"wiki/gone.md"})
    assert row["target_missing"] is False and row["rank"] == 1
    row = gr.grade_item(item, _t4_pages("gone"), _T4_SPEC)  # no corpus given: nothing is declared missing
    assert row["target_missing"] is False and row["rank"] == 1


def test_t4_set_item_with_one_missing_target_is_target_missing():
    item = _t4_item("multi_hop", pages=("wiki/a.md", "wiki/gone.md"))
    row = gr.grade_item(item, _t4_pages("a"), _T4_SPEC, corpus_pages={"wiki/a.md"})
    assert row["target_missing"] is True and row["set_fraction"] is None


def test_t4_abstain_item_is_never_target_missing():
    row = gr.grade_item(_t4_item("not_found"), [], _T4_SPEC, corpus_pages=set())
    assert row["target_missing"] is False and row["abstained"] is True


def test_t4_grade_item_refuses_malformed_items():
    for bad in (dict(_t4_item(), grading="model"), dict(_t4_item(), targets=[]), dict(_t4_item(), stratum="nope"),
                dict(_t4_item("not_found"), targets=[_t4_target("wiki/a.md")]), "x", None):
        with pytest.raises(ValueError):
            gr.grade_item(bad, _t4_pages("a"), _T4_SPEC)


def test_t4_grade_item_tolerates_malformed_results_without_hitting():
    results = [None, 5, "x", {"page": 5}, {"heading": "h"}, _t4_res("wiki/a.md")]
    assert gr.grade_item(_t4_item(), results, _T4_SPEC)["rank"] == 6


def test_t4_grade_item_does_not_mutate_its_inputs():
    item = _t4_item()
    results = _t4_pages("a", "a")
    before = (repr(item), repr(results))
    gr.grade_item(item, results, _T4_SPEC, corpus_pages={"wiki/a.md"})
    assert (repr(item), repr(results)) == before


# ------------------------------------------------------------ resolve_target_presence

def test_t4_resolve_target_presence():
    item = _t4_item("multi_hop", pages=("wiki/a.md", "./wiki//b.md"))
    assert gr.resolve_target_presence(item, {"wiki/a.md", "wiki/b.md"}) is True
    assert gr.resolve_target_presence(item, {"./wiki/a.md", "wiki//b.md"}) is True
    assert gr.resolve_target_presence(item, {"wiki/a.md"}) is False
    assert gr.resolve_target_presence(item, set()) is False
    assert gr.resolve_target_presence(_t4_item("not_found"), set()) is True
    bad = _t4_item(pages=("wiki/../a.md",))
    assert gr.resolve_target_presence(bad, {"wiki/a.md", "wiki/../a.md"}) is False


# ------------------------------------------------------------ aggregate

def _t4_row(stratum="semantic", rank=None, **kw):
    grading = kw.pop("grading", {"multi_hop": "set", "synthesis": "set", "not_found": "abstain"}.get(stratum, "rank"))
    row = {"stratum": stratum, "grading": grading, "rank": rank, "set_fraction": None, "set_fraction_at": None,
           "abstained": None, "target_missing": False}
    row.update(kw)
    return row


def test_t4_aggregate_rank_metrics_match_hand_computed_values():
    rows = [_t4_row(rank=r) for r in (1, 2, 4, None, 10)]
    agg = gr.aggregate(rows, _T4_SPEC, per_stratum=False)
    assert agg["metrics"] == {"recall_at_1": 0.2, "recall_at_3": 0.4, "recall_at_10": 0.8, "mrr": 0.37}
    assert agg["n_items"] == 5 and agg["n_scored"] == 5
    assert agg["target_missing"] == 0 and agg["abstain"] is None and agg["per_stratum"] is None


def test_t4_aggregate_set_rows_use_the_fraction_at_each_k():
    rows = [
        _t4_row("semantic", rank=1),
        _t4_row("multi_hop", rank=2, set_fraction=1.0, set_fraction_at={"1": 0.0, "3": 0.5, "10": 1.0}),
    ]
    m = gr.aggregate(rows, _T4_SPEC, per_stratum=False)["metrics"]
    assert m == {"recall_at_1": 0.5, "recall_at_3": 0.75, "recall_at_10": 1.0, "mrr": 0.75}


def test_t4_aggregate_set_recall_is_monotone_in_k_by_construction():
    item = _t4_item("synthesis", pages=("wiki/a.md", "wiki/b.md", "wiki/c.md"))
    row = gr.grade_item(item, _t4_pages("x", "a", "y", "b"), _T4_SPEC)
    vals = [row["set_fraction_at"][k] for k in ("1", "3", "10")]
    assert vals == sorted(vals)
    assert vals[-1] == row["set_fraction"]


def test_t4_abstain_rows_are_reported_separately_and_excluded_from_quality_metrics():
    base = [_t4_row(rank=r) for r in (1, 2, 4, None, 10)]
    with_abstain = base + [_t4_row("not_found", abstained=True), _t4_row("not_found", abstained=True),
                           _t4_row("not_found", abstained=False)]
    plain = gr.aggregate(base, _T4_SPEC, per_stratum=False)
    agg = gr.aggregate(with_abstain, _T4_SPEC, per_stratum=False)
    assert agg["metrics"] == plain["metrics"]
    assert agg["abstain"] == {"passed": 2, "n": 3}
    assert agg["n_items"] == 8 and agg["n_scored"] == 5


def test_t4_target_missing_rows_are_counted_and_excluded_from_denominators():
    base = [_t4_row(rank=r) for r in (1, None)]
    rows = base + [_t4_row(target_missing=True), _t4_row("multi_hop", target_missing=True)]
    agg = gr.aggregate(rows, _T4_SPEC, per_stratum=False)
    assert agg["target_missing"] == 2
    assert agg["n_items"] == 4 and agg["n_scored"] == 2
    assert agg["metrics"]["recall_at_1"] == 0.5


def test_t4_aggregate_applies_fixed_precision():
    rows = [_t4_row(rank=1), _t4_row(rank=None), _t4_row(rank=None)]
    m = gr.aggregate(rows, _T4_SPEC, per_stratum=False)["metrics"]
    assert m["recall_at_1"] == 0.333 and m["mrr"] == 0.333
    rows = [_t4_row(rank=3), _t4_row(rank=7), _t4_row(rank=None)]
    m = gr.aggregate(rows, _T4_SPEC, per_stratum=False)["metrics"]
    assert m["mrr"] == round((1 / 3 + 1 / 7 + 0) / 3, 3) == 0.159
    for v in m.values():
        assert v == round(v, 3)


def test_t4_per_stratum_only_when_asked_and_excludes_abstain_and_missing_only_strata():
    rows = [
        _t4_row("semantic", rank=1), _t4_row("semantic", rank=None),
        _t4_row("code", rank=2),
        _t4_row("not_found", abstained=True),
        _t4_row("hash", target_missing=True),
    ]
    assert gr.aggregate(rows, _T4_SPEC, per_stratum=False)["per_stratum"] is None
    ps = gr.aggregate(rows, _T4_SPEC, per_stratum=True)["per_stratum"]
    assert set(ps) == {"semantic", "code"}
    assert ps["semantic"] == {"recall_at_1": 0.5, "recall_at_3": 0.5, "recall_at_10": 0.5, "mrr": 0.5, "n": 2}
    assert ps["code"] == {"recall_at_1": 0.0, "recall_at_3": 1.0, "recall_at_10": 1.0, "mrr": 0.5, "n": 1}
    assert list(ps) == [s for s in STRATA if s in ps]  # canonical stratum order


def test_t4_aggregate_empty_input_is_all_zero_and_says_nothing_was_scored():
    agg = gr.aggregate([], _T4_SPEC, per_stratum=True)
    assert agg["metrics"] == {"recall_at_1": 0.0, "recall_at_3": 0.0, "recall_at_10": 0.0, "mrr": 0.0}
    assert agg["n_items"] == 0 and agg["n_scored"] == 0 and agg["per_stratum"] == {}


def test_t4_aggregate_refuses_unknown_stratum_and_bad_rows():
    for bad in ([_t4_row("invented", rank=1)], [_t4_row(grading="model")], ["x"], [None], [_t4_row(rank=0)],
                [_t4_row(rank=11)], [_t4_row(rank=True)]):
        with pytest.raises(ValueError):
            gr.aggregate(bad, _T4_SPEC, per_stratum=False)


def test_t4_metric_vectors_align_with_scored_rows_only():
    rows = [_t4_row(rank=2), _t4_row("not_found", abstained=True), _t4_row(target_missing=True),
            _t4_row("multi_hop", rank=1, set_fraction=0.5, set_fraction_at={"1": 0.25, "3": 0.5, "10": 0.5}),
            _t4_row(rank=None)]
    vec = gr.metric_vectors(rows, _T4_SPEC)
    assert set(vec) == {"recall_at_1", "recall_at_3", "recall_at_10", "mrr"}
    assert vec["recall_at_1"] == [0.0, 0.25, 0.0]
    assert vec["recall_at_3"] == [1.0, 0.5, 0.0]
    assert vec["recall_at_10"] == [1.0, 0.5, 0.0]
    assert vec["mrr"] == [0.5, 1.0, 0.0]
    agg = gr.aggregate(rows, _T4_SPEC, per_stratum=False)
    for m, values in vec.items():
        assert agg["metrics"][m] == round(sum(values) / len(values), 3)


def test_t4_end_to_end_items_to_serialized_result():
    items = [_t4_item("semantic", pages=("wiki/a.md",)), _t4_item("code", pages=("wiki/b.md",)),
             _t4_item("multi_hop", pages=("wiki/a.md", "wiki/c.md")), _t4_item("not_found")]
    runs = [_t4_pages("a"), _t4_pages("x", "b"), _t4_pages("c", "a"), [_t4_res("wiki/q.md", score=0.1)]]
    rows = [gr.grade_item(i, r, _T4_SPEC, corpus_pages={"wiki/a.md", "wiki/b.md", "wiki/c.md"})
            for i, r in zip(items, runs)]
    agg = gr.aggregate(rows, _T4_SPEC, per_stratum=True)
    vec = gr.metric_vectors(rows, _T4_SPEC)
    ci = {m: list(gr.paired_bootstrap_ci(v, None, resamples=1000, seed=1)) for m, v in vec.items()}
    result = {
        "ok": True, "schema": "zurvan.z1b.result.v1", "suite": "holdout", "suite_id": "0" * 16,
        "n_items": agg["n_items"], "corpus_sha256": "a" * 64, "suite_manifest_sha256": "b" * 64,
        "evaluator_version": gr.EVALUATOR_VERSION, "broker_sha": "c" * 40, "candidate_commit": "d" * 40,
        "metrics": dict(agg["metrics"], ci=ci), "per_stratum": agg["per_stratum"],
        "target_missing": agg["target_missing"], "abstain": agg["abstain"],
        "latency_ms": {"p50": 10, "p95": 20}, "peak_rss_mb": 1, "seal_valid": True, "cached": False,
        "budget_remaining": 7, "promotion": None,
    }
    parsed = out.validate_result_json(out.serialize_result(result))
    assert parsed["abstain"] == {"passed": 1, "n": 1}
    # semantic hits at 1 (1.0), code hits at 2 (0.0), multi_hop has 1 of 2 targets in the top 1 (0.5)
    assert parsed["metrics"]["recall_at_1"] == 0.5
    assert set(parsed["per_stratum"]) == {"semantic", "code", "multi_hop"}


# ------------------------------------------------------------ paired bootstrap

_T4_CAND = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0]
_T4_GOLDEN_UNPAIRED = (0.3, 0.8)
_T4_GOLDEN_PAIRED = (0.0, 0.5)
_T4_BASE = [0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0]


def test_t4_bootstrap_is_deterministic_for_a_seed_and_differs_across_seeds():
    a = gr.paired_bootstrap_ci(_T4_CAND, _T4_BASE, resamples=500, seed=7)
    b = gr.paired_bootstrap_ci(_T4_CAND, _T4_BASE, resamples=500, seed=7)
    c = gr.paired_bootstrap_ci(_T4_CAND, _T4_BASE, resamples=500, seed=8)
    assert a == b
    assert a != c


def test_t4_bootstrap_golden_value_is_stable_across_python_versions():
    # recorded under 3.12 and re-checked under the sealed side's 3.9: random() streams are version-stable
    assert gr.paired_bootstrap_ci(_T4_CAND, None, resamples=200, seed=7) == pytest.approx(_T4_GOLDEN_UNPAIRED)
    assert gr.paired_bootstrap_ci(_T4_CAND, _T4_BASE, resamples=200, seed=7) == pytest.approx(_T4_GOLDEN_PAIRED)


def test_t4_unpaired_ci_contains_the_candidate_mean():
    lo, hi = gr.paired_bootstrap_ci(_T4_CAND, None, resamples=1000, seed=3)
    mean = sum(_T4_CAND) / len(_T4_CAND)
    assert lo <= mean <= hi
    assert 0.0 <= lo <= hi <= 1.0


def test_t4_paired_ci_is_on_the_mean_difference_and_contains_it():
    lo, hi = gr.paired_bootstrap_ci(_T4_CAND, _T4_BASE, resamples=1000, seed=3)
    diff = sum(c - b for c, b in zip(_T4_CAND, _T4_BASE)) / len(_T4_CAND)
    assert diff == 0.25
    assert lo <= diff <= hi
    assert -1.0 <= lo <= hi <= 1.0
    assert (lo, hi) != gr.paired_bootstrap_ci(_T4_CAND, None, resamples=1000, seed=3)


def test_t4_bootstrap_degenerate_cases():
    assert gr.paired_bootstrap_ci([1.0] * 10, [0.0] * 10, resamples=100, seed=1) == (1.0, 1.0)
    assert gr.paired_bootstrap_ci([0.25] * 4, None, resamples=100, seed=1) == (0.25, 0.25)
    assert gr.paired_bootstrap_ci([0.5], None, resamples=100, seed=1) == (0.5, 0.5)
    lo, hi = gr.paired_bootstrap_ci([0, 1], None, resamples=1000, seed=1)
    assert (lo, hi) == (0.0, 1.0)


def test_t4_bootstrap_refuses_length_mismatch_and_bad_arguments():
    with pytest.raises(ValueError):
        gr.paired_bootstrap_ci([1, 0, 1], [1, 0], resamples=10, seed=1)
    with pytest.raises(ValueError):
        gr.paired_bootstrap_ci([], None, resamples=10, seed=1)
    with pytest.raises(ValueError):
        gr.paired_bootstrap_ci([], [], resamples=10, seed=1)
    for bad in (0, -1, True, 2.5, "10"):
        with pytest.raises(ValueError):
            gr.paired_bootstrap_ci([1, 0], None, resamples=bad, seed=1)
    for bad in (True, 1.5, "1", None):
        with pytest.raises(ValueError):
            gr.paired_bootstrap_ci([1, 0], None, resamples=10, seed=bad)
    for bad in ([float("nan"), 1.0], [float("inf")], ["a"], [None], [True]):
        with pytest.raises(ValueError):
            gr.paired_bootstrap_ci(bad, None, resamples=10, seed=1)


def test_t4_bootstrap_does_not_touch_the_global_random_state():
    import random
    random.seed(99)
    expect = random.random()
    random.seed(99)
    gr.paired_bootstrap_ci(_T4_CAND, None, resamples=50, seed=1)
    assert random.random() == expect


def test_t4_bootstrap_interval_is_ordered_and_inside_the_data_range():
    import random
    rng = random.Random(5)
    for _ in range(20):
        n = rng.randint(1, 30)
        cand = [rng.random() for _ in range(n)]
        base = [rng.random() for _ in range(n)]
        lo, hi = gr.paired_bootstrap_ci(cand, base, resamples=200, seed=rng.randint(0, 10 ** 6))
        diffs = [c - b for c, b in zip(cand, base)]
        assert lo <= hi
        assert min(diffs) - 1e-12 <= lo and hi <= max(diffs) + 1e-12
        assert math.isfinite(lo) and math.isfinite(hi)
