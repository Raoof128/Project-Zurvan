"""Phase 1B-FTS — bounded lexical candidates: the experiment's gauntlet.

Rows 1-4 are RED by design: bounded FTS does not exist yet. The live tests at the
bottom run TODAY and guard properties this project already paid for.

Spec: `docs/superpowers/specs/2026-09-15-phase-1b-fts-design.md`

Contract these tests define:

    hybrid_search.search_hybrid(query, limit, fts_limit=None)
        fts_limit=None  -> today's unbounded behaviour
        fts_limit=N     -> ORDER BY rank ASC, chunk_id ASC LIMIT N
"""
import json
import os
import sqlite3
import sys

import pytest

from scripts import hybrid_search as hs


def _bounded_fts_available():
    import inspect
    return "fts_limit" in inspect.signature(hs.search_hybrid).parameters


bounded = pytest.mark.skipif(
    not _bounded_fts_available(),
    reason="bounded FTS candidate policy is specced but not implemented — RED by design",
)


# ── row 1 ─────────────────────────────────────────────────────────────────────
@bounded
def test_fts_limit_above_total_matches_is_identical_to_the_unbounded_path(tmp_path):
    """Proves the bound is inert when it cannot bite.

    If N >= every matching row and the result still differs, the change is not a
    candidate-policy change — it is a scoring bug wearing one.
    """
    q = "containment attestation"
    base = hs.search_hybrid(q, limit=10)
    wide = hs.search_hybrid(q, limit=10, fts_limit=10_000_000)
    assert [r["chunk_id"] for r in base] == [r["chunk_id"] for r in wide]
    for a, b in zip(base, wide):
        assert a["hybrid_score"] == pytest.approx(b["hybrid_score"], abs=1e-12)


# ── row 2 ─────────────────────────────────────────────────────────────────────
@bounded
def test_equal_bm25_at_the_cutoff_admits_a_deterministic_candidate_set():
    """Proves the lexical tie rule bites BEFORE fusion.

    Duplicate pages produce identical BM25 at the N boundary. Without
    `chunk_id ASC` in the SQL, which duplicate survives depends on scan order —
    the same defect that moved two queries' top-10 on 2026-09-15, one layer lower.
    """
    q = "duplicate page probe"
    runs = [tuple(r["chunk_id"] for r in hs.search_hybrid(q, limit=10, fts_limit=25))
            for _ in range(5)]
    assert len(set(runs)) == 1, f"cutoff admitted different candidates: {set(runs)}"


# ── row 3 ─────────────────────────────────────────────────────────────────────
@bounded
def test_no_new_top3_failure_and_aggregates_hold_their_declared_floor():
    """Proves the quality gate, including the aggregate floor.

    A Top-3-only gate passes while every rank-1 answer slides to rank 3: Top-3
    still reads 0.74 while Top-1 and MRR collapse.
    """
    from scripts.eval_search import load_gold_dataset, DEFAULT_GOLD
    gold = load_gold_dataset(DEFAULT_GOLD)

    def ranks(fts_limit):
        out = []
        for item in gold:
            got = [r["source_path"] for r in
                   hs.search_hybrid(item["query"], limit=10, fts_limit=fts_limit)]
            out.append(next((i for i, p in enumerate(got, 1)
                             if any(p == e or p.endswith(e) for e in item["expected_paths"])), None))
        return out

    before, after = ranks(None), ranks(25)
    newly_failing = [i for i, (b, a) in enumerate(zip(before, after), 1)
                     if b and b <= 3 and not (a and a <= 3)]
    assert not newly_failing, f"gold items left Top-3: {newly_failing}"
    top1 = sum(1 for r in after if r == 1) / len(after)
    mrr = sum(1 / r for r in after if r) / len(after)
    assert top1 >= 0.68, f"Top-1 {top1:.2f} below declared floor"
    assert mrr >= 0.71, f"MRR {mrr:.3f} below declared floor"


# ── row 4 ─────────────────────────────────────────────────────────────────────
@bounded
def test_timing_is_measured_in_a_fresh_process_per_variant():
    """Proves the measurement harness is not self-contaminating.

    `ru_maxrss` is a high-water mark that never falls, so sequential variants in
    one process inherit every earlier peak. This asserts the harness spawns.
    """
    from scripts import bench_fts_candidates as bench
    assert bench.runs_in_fresh_process is True


# ── LIVE: properties already paid for, guarded from here on ───────────────────
def test_evaluation_reports_are_not_indexed():
    """Guards the 2026-09-15 fix: a report about retrieval must not feed retrieval.

    The Step 0 report put 7 chunks into the index whose baseline it had just
    frozen. Runs today; fails if the exclusion is ever removed.
    """
    from scripts.chunk import scan_markdown_files
    indexed = {f.replace(os.sep, "/") for f in scan_markdown_files()}
    assert not any(f.startswith("docs/evaluation/retrieval/") for f in indexed)


def test_instrument_text_is_absent_from_the_live_index():
    """Instrument exposure must stay at zero.

    On the morning of 2026-09-15 exposure was 1/50 and cost 2 Top-1 points. This
    measures the real index and fails the moment an item is echoed back into it.
    """
    from scripts.config import get_search_db_path
    db = str(get_search_db_path())
    if not os.path.exists(db):
        pytest.skip("no live index")
    conn = sqlite3.connect(db)
    gold = [json.loads(l)["query"] for l in open(__import__("scripts.instruments", fromlist=["x"]).gold_path()) if l.strip()]
    exposed = [i for i, q in enumerate(gold, 1) if conn.execute(
        "select 1 from chunks where lower(text) like '%'||?||'%' limit 1",
        (q.lower(),)).fetchone()]
    assert not exposed, f"instrument exposure: gold items {exposed} echo in the corpus"
