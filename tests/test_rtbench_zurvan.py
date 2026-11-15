"""RTBench Task 7: the Zurvan adapter (Track S) in a temporary store (R7, R8)."""
import pytest
from pathlib import Path
from scripts.rtbench.zurvan_adapter import ZurvanAdapter
from scripts.rtbench.adapters import OracleAdapter
from scripts.rtbench.generate import DEV_SEEDS, generate_case
from scripts.rtbench.run import run_cases
from test_rtbench_oracle import H1, H2, H3, H4, H5  # tests/ is on sys.path (prepend mode, no package)

def _ask(adapter, events, questions):
    adapter.reset()
    for e in events:
        adapter.ingest(e)
    return [adapter.answer(q) for q in questions]

QS = [{"type": "Q1", "t": 2}, {"type": "Q2", "t": 2, "sid": "s1"}, {"type": "Q3", "t": 1, "slot": "k1"},
      {"type": "Q6", "slot": "k1"}]

@pytest.mark.parametrize("events", [H1, H2, H5], ids=["supersede", "erase", "revise"])
def test_hand_cases_match_the_oracle(tmp_path, events):
    z = ZurvanAdapter(tmp_path)
    try:
        assert _ask(z, events, QS) == _ask(OracleAdapter(), events, QS)
    finally:
        z.close()

def test_bitemporal_matches_the_oracle(tmp_path):
    qs = [{"type": "Q4", "t": t, "slot": "k1", "v": v} for t in (1, 2) for v in ("2026-03-01", "2026-07-01")]
    z = ZurvanAdapter(tmp_path)
    try:
        assert _ask(z, H4, qs) == _ask(OracleAdapter(), H4, qs)
    finally:
        z.close()

def test_abort_reconciles_in_temp_root(tmp_path):
    z = ZurvanAdapter(tmp_path)
    try:
        _ask(z, H3, [])
        from scripts import knowledge_revision as kr
        assert kr.current(z.root).state != kr.SUSPECT
        assert Path(z.root).is_relative_to(tmp_path)
    finally:
        z.close()

def test_zurvan_runs_a_generated_sample(tmp_path):
    cases = [generate_case(s, f"dev-{i:04d}") for i, s in enumerate(DEV_SEEDS[:3])]
    z = ZurvanAdapter(tmp_path)
    try:
        r = run_cases(z, "S", [c for c, _ in cases], [g for _, g in cases], itemised=True)
    finally:
        z.close()
    assert r["n_questions"] > 0 and r["errors"] == 0
