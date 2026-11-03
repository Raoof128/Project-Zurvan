"""F5 (audit 2026-10-22): the scorer fused at 0.2/0.8 while the trace declared
0.6/0.4, so replaying a trace reconstructed the wrong score. One versioned
policy object is now shared by scorer and trace."""
import pytest


def test_trace_weights_are_the_scorer_weights():
    from scripts.fusion_policy import CURRENT
    from scripts import hybrid_search
    from scripts.context_export import _fusion_payload
    p = _fusion_payload([])
    assert p["weights"] == {"fts": hybrid_search.KEYWORD_WEIGHT, "embedding": 1 - hybrid_search.KEYWORD_WEIGHT}
    assert p["policy_id"] == CURRENT.policy_id == "weighted_sum_v1_kw0.2"
    assert p["min_score"] == hybrid_search.MIN_HYBRID_SCORE


def test_fused_score_reconstructs_from_recorded_components():
    from scripts.fusion_policy import CURRENT
    from scripts.context_export import _fusion_payload
    rows = [{"source_path": "a", "text": "t", "keyword_score": 1.0, "semantic_score": 0.5,
             "hybrid_score": CURRENT.score(1.0, 0.5)}]
    payload = _fusion_payload(rows)
    rec = payload["ranked"][0]
    assert rec["fused_score"] == pytest.approx(0.6)                              # spec F5 example
    w = payload["weights"]
    assert w["fts"] * rec["fts_score"] + w["embedding"] * rec["embedding_score"] == pytest.approx(rec["fused_score"])


def test_hybrid_search_uses_the_policy():
    from scripts.fusion_policy import CURRENT
    from scripts.hybrid_search import _fuse
    out = _fuse([{"chunk_id": "c", "keyword_score": 1.0, "semantic_score": 0.5}])
    assert out[0]["hybrid_score"] == pytest.approx(CURRENT.score(1.0, 0.5)) == pytest.approx(0.6)


def test_policy_values_are_the_measured_ones():
    # The weights are a MEASURED ranking policy (hybrid_search.py comment);
    # this pins them so a policy edit cannot slip in without an eval re-run.
    from scripts.fusion_policy import CURRENT
    assert (CURRENT.keyword_weight, CURRENT.min_score) == (0.2, 0.2)
