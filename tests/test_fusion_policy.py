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


# --- Rust parity (review follow-up 2026-11-07 (e)) ---------------------------
# The Rust server re-implements the fusion as three source-level facts: two
# constants, one formula (used at two sites) and one strict comparison. Nothing
# compared them with this module, so a ranking edit on one side would have
# shipped silently (only the live parity harness would notice, and only on a
# query whose rank happened to move). This reads the Rust SOURCE, so it runs in
# the ordinary suite with no cargo build, and it fails loudly when a constant
# is renamed instead of passing on a missing match.

import re
from pathlib import Path

RUST_SRC = Path(__file__).resolve().parents[1] / "rust" / "zurvan-mcp" / "src"


def _rust(name: str) -> str:
    return (RUST_SRC / name).read_text(encoding="utf-8")


def _rust_const(source: str, name: str) -> float:
    m = re.search(rf"\bconst\s+{name}\s*:\s*f64\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*;", source)
    assert m, f"rust const {name} not found as `const {name}: f64 = <literal>;` - update this test with the rename"
    return float(m.group(1))


def test_rust_keyword_weight_is_the_policys():
    from scripts.fusion_policy import CURRENT
    assert _rust_const(_rust("vectors.rs"), "KEYWORD_WEIGHT") == CURRENT.keyword_weight


def test_rust_min_hybrid_score_is_the_policys():
    from scripts.fusion_policy import CURRENT
    assert _rust_const(_rust("search.rs"), "MIN_HYBRID_SCORE") == CURRENT.min_score


def test_rust_scores_with_the_policys_formula_at_each_site():
    # score = kw*keyword + (1-kw)*semantic, spelled the same way at both sites
    # (the vector scan in vectors.rs, the re-score in search.rs). A site that
    # swapped the operands, dropped the complement or inlined a literal weight
    # does not match. Python's object computes the identical expression.
    from scripts.fusion_policy import CURRENT
    formula = re.compile(
        r"KEYWORD_WEIGHT\s*\*\s*(\w+(?:\.\w+)?)\s*\+\s*\(\s*1\.0\s*-\s*KEYWORD_WEIGHT\s*\)\s*\*\s*(\w+(?:\.\w+)?)")
    assert formula.findall(_rust("vectors.rs")) == [("kw", "semantic")]
    assert formula.findall(_rust("search.rs")) == [("r.keyword", "r.semantic")]
    k, s = 0.7, 0.3
    assert CURRENT.score(k, s) == pytest.approx(CURRENT.keyword_weight * k + (1.0 - CURRENT.keyword_weight) * s)


def test_rust_floor_is_strictly_greater_than_like_python():
    # `hybrid_score > MIN_HYBRID_SCORE`: a score EQUAL to the floor is dropped.
    from scripts import hybrid_search
    assert "hybrid_score > MIN_HYBRID_SCORE" in Path(hybrid_search.__file__).read_text(encoding="utf-8")
    assert re.search(
        r"fn\s+passes_floor\s*\(\s*score\s*:\s*f64\s*\)\s*->\s*bool\s*\{\s*score\s*>\s*MIN_HYBRID_SCORE\s*\}",
        _rust("search.rs")), "rust passes_floor must be `score > MIN_HYBRID_SCORE` (strict)"
