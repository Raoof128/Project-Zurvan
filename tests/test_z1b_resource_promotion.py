"""Z1b Rev 3 P1 #12: the resource-only promotion route (canonical s9.5 rule 3, second limb).

A candidate whose quality is EQUIVALENT (non-inferior but not improved) may still be promoted when a
predeclared resource metric materially improves. broker_verdict is exercised directly with crafted rows so
the quality side is controlled (candidate == baseline => non-inferior, no improvement) and the resource
numbers are set explicitly.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import broker as B           # noqa: E402
from scripts.eval_seal import spec as SPECMOD       # noqa: E402

SPEC = SPECMOD.load_spec(ROOT / "eval" / "z1b" / "spec.json")


def _rows(rank=3, n=6):
    return [{"id": ("%032x" % i), "stratum": "exact_identifier", "grading": "rank",
             "rank": rank, "target_missing": False} for i in range(n)]


def _decl(resource_improvement):
    return {"role": "candidate", "suite_id": "0123456789abcdef", "primary_metric": "recall_at_10",
            "critical_strata": ["exact_identifier"],
            "non_inferiority_margins": {"critical": 0.05, "overall": 0.02},
            "improvement_target": {"metric": "mrr", "min_delta": 0.01},
            "resource_budgets": {"latency_p95_ms": 100000, "peak_rss_mb": 4096},
            "resource_improvement": resource_improvement}


def _result(*, rss, p95=10):
    return {"latency_ms": {"p50": p95, "p95": p95}, "peak_rss_mb": rss}


def _verdict(decl, cand_rows, base_rows, result, base_result):
    return B.broker_verdict(decl, cand_rows, base_rows, result, SPEC, seed=1, base_result=base_result)


def test_t12_equivalent_quality_plus_material_resource_gain_promotes():
    # candidate == baseline quality (non-inferior, no improvement); peak RSS drops 1000 -> 800 (>= 20%)
    v = _verdict(_decl({"metric": "peak_rss_mb", "min_delta": 0.2}),
                 _rows(), _rows(), _result(rss=800), _result(rss=1000))
    assert v["rules"]["improvement"] is False and v["rules"]["resource_improved"] is True
    assert v["rules"]["non_inferiority_overall"] and v["rules"]["non_inferiority_critical"]
    assert v["verdict"] == "PASS"                    # promoted via the resource route


def test_t12_equivalent_quality_without_enough_resource_gain_fails():
    # only 800 -> 760 is 5%, short of the declared 20%
    v = _verdict(_decl({"metric": "peak_rss_mb", "min_delta": 0.2}),
                 _rows(), _rows(), _result(rss=760), _result(rss=800))
    assert v["rules"]["resource_improved"] is False and v["rules"]["improvement"] is False
    assert v["verdict"] == "FAIL"


def test_t12_resource_route_requires_quality_non_inferiority():
    # resource improves a lot, but quality REGRESSES (candidate misses every query) -> never promoted
    v = _verdict(_decl({"metric": "peak_rss_mb", "min_delta": 0.2}),
                 [dict(r, rank=None) for r in _rows()], _rows(), _result(rss=100), _result(rss=1000))
    assert v["rules"]["resource_improved"] is True and v["rules"]["non_inferiority_overall"] is False
    assert v["verdict"] == "FAIL"


def test_t12_no_declared_resource_route_means_equivalent_quality_cannot_promote():
    v = _verdict(_decl(None), _rows(), _rows(), _result(rss=100), _result(rss=1000))
    assert v["rules"]["resource_improved"] is False and v["rules"]["improvement"] is False
    assert v["verdict"] == "FAIL"


def test_t12_quality_improvement_route_still_promotes_regardless_of_resource():
    # candidate strictly better (rank 1 vs baseline rank 5) -> improvement route; resource not improved
    v = _verdict(_decl({"metric": "peak_rss_mb", "min_delta": 0.2}),
                 _rows(rank=1), _rows(rank=5), _result(rss=1000), _result(rss=1000))
    assert v["rules"]["improvement"] is True and v["rules"]["resource_improved"] is False
    assert v["verdict"] == "PASS"


def test_t12_latency_route():
    # the route also works on latency p95: 200ms -> 150ms is 25% (>= declared 20%)
    v = _verdict(_decl({"metric": "latency_p95_ms", "min_delta": 0.2}),
                 _rows(), _rows(), _result(rss=100, p95=150), _result(rss=100, p95=200))
    assert v["rules"]["resource_improved"] is True and v["verdict"] == "PASS"
