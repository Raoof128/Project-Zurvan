"""Z1b Task 19 tests: the public projection of a run result (G20, decision 8 option (a)).

The sealed ledger keeps the full result (the verdict and the baseline logic need it). What reaches the
development side is a projection: no per-stratum block, no bootstrap intervals, and the latency and memory
figures snapped to a coarse ladder. The client refuses any result document that is not a projection.
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import output as O  # noqa: E402
from scripts.eval_seal.codes import SealRefusal  # noqa: E402
from scripts import z1b_client as C  # noqa: E402


def _t19_full_result(**over):
    m = {"recall_at_1": 0.25, "recall_at_3": 0.5, "recall_at_10": 0.75, "mrr": 0.412}
    doc = {
        "ok": True, "schema": O.RESULT_SCHEMA_ID, "suite": "holdout", "suite_id": "0123456789abcdef",
        "n_items": 160, "corpus_sha256": "a" * 64, "suite_manifest_sha256": "b" * 64,
        "evaluator_version": "1.0.0", "broker_sha": "c" * 40, "candidate_commit": "d" * 40,
        "metrics": dict(m, ci={k: [0.11, 0.93] for k in O.METRICS}),
        "per_stratum": {"path": dict(m, n=10), "hash": dict(m, n=10)},
        "target_missing": 0, "abstain": {"passed": 3, "n": 10},
        "latency_ms": {"p50": 1230, "p95": 4870}, "peak_rss_mb": 1543,
        "seal_valid": True, "cached": False, "budget_remaining": 7,
        "promotion": {"declared": True, "verdict": "PASS",
                      "rules": {r: True for r in O.PROMOTION_RULES}},
    }
    doc.update(over)
    return doc


def test_t19_projection_drops_per_stratum_and_intervals_and_keeps_the_verdict():
    pub = O.public_projection(_t19_full_result())
    assert pub["per_stratum"] is None and pub["metrics"]["ci"] is None
    assert pub["promotion"]["verdict"] == "PASS" and pub["metrics"]["mrr"] == 0.412


@pytest.mark.parametrize("value,snapped", [(0, 10), (10, 10), (11, 20), (1230, 2000), (4870, 5000),
                                           (3600000, 3600000)])
def test_t19_latency_snaps_up_to_the_ladder(value, snapped):
    pub = O.public_projection(_t19_full_result(latency_ms={"p50": value, "p95": value}))
    assert pub["latency_ms"] == {"p50": snapped, "p95": snapped}


@pytest.mark.parametrize("value,snapped", [(0, 64), (64, 64), (65, 128), (1543, 2048), (10 ** 6, 10 ** 6)])
def test_t19_peak_rss_snaps_up_to_the_ladder(value, snapped):
    assert O.public_projection(_t19_full_result(peak_rss_mb=value))["peak_rss_mb"] == snapped


def test_t19_projection_does_not_mutate_the_sealed_result():
    full = _t19_full_result()
    before = json.dumps(full, sort_keys=True)
    O.public_projection(full)
    assert json.dumps(full, sort_keys=True) == before


def test_t19_public_serialisation_carries_no_per_stratum_or_interval_values():
    text = O.serialize_public_result(_t19_full_result())
    doc = json.loads(text)
    assert doc["per_stratum"] is None and doc["metrics"]["ci"] is None
    assert '"path"' not in text and "0.93" not in text and "0.11" not in text


@pytest.mark.parametrize("bad", [
    {"per_stratum": {"path": {"recall_at_1": 0.1, "recall_at_3": 0.1, "recall_at_10": 0.1, "mrr": 0.1, "n": 10}}},
    {"latency_ms": {"p50": 1230, "p95": 5000}},
    {"peak_rss_mb": 1543},
])
def test_t19_assert_public_refuses_anything_that_is_not_a_projection(bad):
    doc = O.public_projection(_t19_full_result())
    doc.update(bad)
    with pytest.raises(ValueError):
        O.assert_public_result(doc)


def test_t19_assert_public_refuses_intervals():
    doc = O.public_projection(_t19_full_result())
    doc["metrics"] = dict(doc["metrics"], ci={k: [0.1, 0.9] for k in O.METRICS})
    with pytest.raises(ValueError):
        O.assert_public_result(doc)


def test_t19_client_refuses_a_full_result_from_a_broker_that_did_not_project():
    text = O.serialize_result(_t19_full_result())

    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout=text.encode())

    with pytest.raises(C.ClientRefusal) as exc:
        C.call_broker(["run"], runner=runner)
    assert exc.value.code == "INTERNAL"


def test_t19_client_accepts_a_projection():
    text = O.serialize_public_result(_t19_full_result())

    def runner(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout=text.encode())

    assert C.call_broker(["run"], runner=runner)["per_stratum"] is None


def test_t19_the_full_result_still_serialises_for_the_sealed_ledger():
    json.loads(O.serialize_result(_t19_full_result()))
    with pytest.raises(SealRefusal):
        O.serialize_result(_t19_full_result(latency_ms={"p50": 1231, "p95": 4870}))
