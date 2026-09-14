#!/usr/bin/env python3
"""Step 0 of the retrieval-core project: anatomy of the failing gold queries.

Answers ONE question before any dependency is installed: **could a reranker
fix these failures at all?** A reranker only reorders documents that retrieval
already produced. If the expected document never enters the candidate pool, no
reranker can rescue it, and building one would be sophisticated effort spent on
a structurally impossible task.

For each failing query it records where the expected document first appears in

  * the lexical (FTS/BM25) ranking,
  * the semantic (embedding) ranking,
  * the fused hybrid ranking,

then classifies the failure and reports a **rerankability rate**.

The decision rule is fixed HERE, in advance, so a marginal result cannot be
rationalised into a build decision after the numbers are known.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

INDEX_COVERAGE_MISS = "index_coverage_miss"
CANDIDATE_MISS = "candidate_miss"
FUSION_RANKING_MISS = "fusion_ranking_miss"
GOLD_ANNOTATION_ISSUE = "gold_annotation_issue"
NEEDS_READING = "needs_reading"
NOT_A_FAILURE = "not_a_failure"

BUILD_2B = "build_2b"
DEFER_2B = "defer_2b_until_after_2a"
DROP_2B = "drop_2b"

POOL = 50           # candidate pool a reranker would see
PASS_RANK = 3       # the gold set's own definition of success


def classify(fts_rank, semantic_rank, fused_rank, in_top50, indexed):
    """Classify one query's outcome. Ranks are 1-based, or None if absent."""
    if not indexed:
        # The expected document is not in the corpus at all. This is an ABSENCE
        # failure: unreachable by reranking, fusion weights, better embeddings
        # or a faster store, because none of them can rank what was never
        # indexed. Distinct from GOLD_ANNOTATION_ISSUE — the gold named real,
        # important documents (README/AGENTS/CLAUDE) and was correct; the
        # corpus was incomplete. Blaming the evaluation set for a corpus bug
        # hides the bug.
        return INDEX_COVERAGE_MISS
    if fused_rank is not None and fused_rank <= PASS_RANK:
        return NOT_A_FAILURE
    if in_top50 or fused_rank is not None:
        # It reached the pool but not the top-3: reordering can fix this.
        return FUSION_RANKING_MISS
    if fts_rank is not None or semantic_rank is not None:
        # One retriever found it; fusion dropped it out of the pool entirely.
        # Rerankable, provided the pool is widened to include it.
        return FUSION_RANKING_MISS
    # Nothing retrieved it. A reranker cannot reorder what was never fetched.
    return CANDIDATE_MISS


def is_rerankable(classification):
    return classification == FUSION_RANKING_MISS


def rerankability_rate(classifications):
    """(rerankable, total) over real retrieval failures."""
    real = [c for c in classifications if c != NOT_A_FAILURE]
    return sum(1 for c in real if is_rerankable(c)), len(real)


def decision(rerankable, total):
    """The rule, fixed before measurement (spec section: Step 0)."""
    if total == 0:
        return DROP_2B
    if rerankable >= 8:
        return BUILD_2B
    if rerankable <= 4:
        return DROP_2B
    return DEFER_2B


# --------------------------------------------------------------------------
# Measurement against the live index
# --------------------------------------------------------------------------

def _rank_of(paths, expected_paths):
    """1-based rank of the first path matching any expected path, else None."""
    for i, p in enumerate(paths, 1):
        if p in expected_paths:
            return i
    return None


def _fts_ranking(conn, query, limit=POOL):
    terms = [t for t in re.split(r"\W+", query) if t]
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    rows = conn.execute(
        "SELECT c.source_path FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id "
        "WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
        (match, limit),
    ).fetchall()
    seen, out = set(), []
    for (p,) in rows:
        if p not in seen:
            seen.add(p); out.append(p)
    return out


def _semantic_ranking(conn, query, limit=POOL):
    from scripts.hybrid_search import _index_embedding_config, _score_semantic_top_k, _hydrate
    cur = conn.cursor()
    provider, model = _index_embedding_config(cur)
    from scripts.embed import get_embedding
    emb = get_embedding(query, provider=provider, model=model)["vector"]
    top = _score_semantic_top_k(cur, emb, {}, limit)   # no keyword scores: pure semantic
    scored = _hydrate(cur, top)
    scored.sort(key=lambda c: c["semantic_score"], reverse=True)
    seen, out = set(), []
    for c in scored:
        if c["source_path"] not in seen:
            seen.add(c["source_path"]); out.append(c["source_path"])
    return out


def analyse(gold_path=None, db_path=None):
    from scripts.config import PROJECT_ROOT, get_search_db_path
    from scripts.eval_search import load_gold_dataset, DEFAULT_GOLD
    from scripts.hybrid_search import search_hybrid

    gold_path = gold_path or DEFAULT_GOLD
    db_path = str(db_path or get_search_db_path())
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    indexed_paths = {r[0] for r in conn.execute("SELECT DISTINCT source_path FROM chunks")}

    rows = []
    for entry in load_gold_dataset(gold_path):
        q = entry["query"]
        expected = set(entry["expected_paths"])
        fused = []
        seen = set()
        for r in search_hybrid(q, limit=POOL, db_path=db_path):
            if r["source_path"] not in seen:
                seen.add(r["source_path"]); fused.append(r["source_path"])

        fused_rank = _rank_of(fused, expected)
        if fused_rank is not None and fused_rank <= PASS_RANK:
            continue  # passing query; Step 0 only anatomises failures

        fts_rank = _rank_of(_fts_ranking(conn, q), expected)
        sem_rank = _rank_of(_semantic_ranking(conn, q), expected)
        indexed = bool(expected & indexed_paths)
        cls = classify(fts_rank, sem_rank, fused_rank, fused_rank is not None, indexed)
        rows.append({
            "query": q, "expected": sorted(expected), "fts_rank": fts_rank,
            "semantic_rank": sem_rank, "fused_rank": fused_rank,
            "in_top50": fused_rank is not None, "indexed": indexed, "class": cls,
        })
    conn.close()
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", dest="as_json", help="Write the full table to this path")
    args = ap.parse_args()

    rows = analyse()
    print(f"{'FTS':>5} {'SEM':>5} {'FUSED':>6} {'TOP50':>6}  CLASS                 QUERY")
    for r in rows:
        f = lambda v: "-" if v is None else str(v)
        print(f"{f(r['fts_rank']):>5} {f(r['semantic_rank']):>5} {f(r['fused_rank']):>6} "
              f"{str(r['in_top50']):>6}  {r['class']:<21} {r['query'][:52]}")

    rerankable, total = rerankability_rate([r["class"] for r in rows])
    d = decision(rerankable, total)
    print(f"\nRERANKABILITY RATE: {rerankable}/{total}")
    from collections import Counter
    for k, v in Counter(r["class"] for r in rows).most_common():
        print(f"  {k:<22} {v}")
    print(f"\nDECISION (rule fixed in advance): {d}")
    print({BUILD_2B: "  >=8/14 -> Phase 2B (cross-encoder rerank) is justified.",
           DROP_2B: "  <=4/14 -> reranking cannot fix these. Drop 2B; fix recall/chunking (2A) first.",
           DEFER_2B: "  5-7/14 -> build 2B only after 2A, then re-run Step 0 against the new index."}[d])

    if args.as_json:
        with open(args.as_json, "w", encoding="utf-8") as fh:
            json.dump({"rows": rows, "rerankable": rerankable, "total": total,
                       "decision": d}, fh, indent=2)
        print(f"\n  table -> {args.as_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
