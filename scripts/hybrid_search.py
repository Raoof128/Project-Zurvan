import sqlite3
import json
import os
import sys
import math
from typing import List, Dict
from scripts.embed import get_embedding

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return dot / (norm_v1 * norm_v2)

def _index_embedding_config(cursor) -> tuple:
    """Return the (provider, model) the index was built with, or (None, None).

    The index is the source of truth for query-time embedding: mixing an index
    built with one provider and queries embedded with another produces
    meaningless similarity scores, so the query always follows the index.
    """
    try:
        row = cursor.execute("SELECT provider, model FROM embeddings LIMIT 1").fetchone()
        return (row[0], row[1]) if row else (None, None)
    except sqlite3.Error:
        return (None, None)


def search_hybrid(query: str, limit: int = 10, db_path: str | None = None) -> List[Dict]:
    from scripts.config import get_search_db_path
    if db_path is None:
        db_path = str(get_search_db_path())
    if not os.path.exists(db_path):
        # stderr, not stdout: callers run with --json and parse stdout, so a
        # diagnostic printed there corrupts the payload into unparseable text.
        print(
            f"Error: Search index not found at {db_path}. Run 'zurvan index rebuild' first.",
            file=sys.stderr,
        )
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Keyword search using FTS5 (BM25 rank)
    import re
    terms = [t for t in re.split(r'\W+', query) if t]
    if terms:
        # Each term is quoted as an FTS5 string: identical matching for plain
        # tokens, but bareword keywords (AND/OR/NOT/NEAR) no longer raise
        # "fts5: syntax error".
        fts_query = " OR ".join(f'"{t}"' for t in terms)
        cursor.execute("""
            SELECT chunk_id, bm25(chunks_fts) as bm25_score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
        """, (fts_query,))
        fts_results = cursor.fetchall()
    else:
        fts_results = []
    
    # Normalize BM25 scores (lower bm25 value is better in SQLite FTS5)
    # Actually SQLite bm25 returns negative values. Let's invert and normalize.
    fts_scores = {}
    if fts_results:
        min_score = min(row[1] for row in fts_results)
        max_score = max(row[1] for row in fts_results)
        for chunk_id, score in fts_results:
            # Normalize between 0 and 1
            if max_score == min_score:
                fts_scores[chunk_id] = 1.0
            else:
                # since scores are typically negative, smaller is better.
                # mapping max_score (worst) -> 0, min_score (best) -> 1
                norm_score = (max_score - score) / (max_score - min_score)
                fts_scores[chunk_id] = norm_score

    # Semantic search — embed the query with the SAME provider/model the
    # index was built with (never the env), so scores stay meaningful.
    index_provider, index_model = _index_embedding_config(cursor)
    query_emb = get_embedding(query, provider=index_provider, model=index_model)['vector']
    
    top = _score_semantic_top_k(cursor, query_emb, fts_scores, limit)
    scored = _hydrate(cursor, top)
    conn.close()

    return _fuse(scored)[:limit]


# Vectors are scored in batches of this many. Bounds peak memory independently
# of corpus size: 5000 x 384 float64 is ~15 MB.
SCORE_BATCH = 5000


def _score_semantic_top_k(cursor, query_emb, fts_scores, limit):
    """Rank every chunk against the query, keeping only the best `limit`.

    The previous implementation ran
    ``SELECT ... c.text, e.vector FROM chunks JOIN embeddings`` with
    ``fetchall()`` and then a pure-Python cosine per row — it pulled the ENTIRE
    corpus, chunk text included, into memory on every query. At 43k chunks that
    was merely wasteful; at 199k it is ~2 GB marshalled per search, which is
    both unusably slow and a repeat of the allocation pattern that OOM-killed
    the indexer.

    Here only (chunk_id, vector) is read, streamed in batches, scored with a
    single numpy matrix-vector product, and reduced through a bounded heap. The
    arithmetic is identical to ``cosine_similarity`` followed by ``_fuse``, so
    the ranking is unchanged; only the cost is.
    """
    import heapq
    import numpy as np
    from scripts import vector_cache

    q = np.asarray(query_emb, dtype=np.float64)
    q_norm = float(np.linalg.norm(q))

    heap = []  # (hybrid_score, chunk_id, semantic_score, keyword_score)
    for ids, mat in _vector_batches(cursor, vector_cache, np):
        if q_norm == 0:
            sims = np.zeros(len(ids))
        else:
            norms = np.linalg.norm(mat, axis=1)
            # A zero-norm vector scores 0, matching cosine_similarity's guard.
            with np.errstate(divide="ignore", invalid="ignore"):
                sims = np.where(norms == 0, 0.0, mat @ q / (norms * q_norm))
        # Cosine is [-1, 1]; map to [0, 1] for reporting only. Note this puts an
        # unrelated chunk at ~0.5, which is exactly why it must NOT be summed
        # against a keyword score — see the fusion comment below.
        sems = np.maximum(0.0, (sims + 1.0) / 2.0)
        for chunk_id, semantic_score in zip(ids, sems.tolist()):
            keyword_score = fts_scores.get(chunk_id, 0.0)
            hybrid = KEYWORD_WEIGHT * keyword_score + (1.0 - KEYWORD_WEIGHT) * semantic_score
            entry = (hybrid, chunk_id, semantic_score, keyword_score)
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif hybrid > heap[0][0]:
                heapq.heapreplace(heap, entry)
    return heap


def _vector_batches(cursor, vector_cache, np):
    """Yield (ids, matrix) batches — from the sidecar cache when it is valid.

    The cache turns the per-query JSON parse of every stored vector (the actual
    cost, ~30-50 s at 199k chunks) into a one-off on the write path. When it is
    absent and cannot be built, fall back to streaming the DB so search still
    works, just slowly.
    """
    db_path = _cursor_db_path(cursor)
    cached = vector_cache.load_or_build(db_path) if db_path else None
    if cached:
        ids, matrix = cached
        for start in range(0, len(ids), SCORE_BATCH):
            stop = start + SCORE_BATCH
            yield ids[start:stop], np.asarray(matrix[start:stop], dtype=np.float64)
        return

    cursor.execute("SELECT chunk_id, vector FROM embeddings")
    while True:
        rows = cursor.fetchmany(SCORE_BATCH)
        if not rows:
            break
        yield [r[0] for r in rows], np.asarray(
            [json.loads(r[1]) for r in rows], dtype=np.float64
        )


def _cursor_db_path(cursor):
    """Filesystem path behind a cursor's connection, or None for in-memory."""
    try:
        for _, name, path in cursor.execute("PRAGMA database_list").fetchall():
            if name == "main":
                return path or None
    except sqlite3.Error:
        pass
    return None


def _hydrate(cursor, top):
    """Fetch source_path/heading/text for the surviving chunk_ids only."""
    if not top:
        return []
    by_id = {chunk_id: (sem, kw) for _, chunk_id, sem, kw in top}
    placeholders = ",".join("?" * len(by_id))
    cursor.execute(
        f"SELECT chunk_id, source_path, heading, text FROM chunks WHERE chunk_id IN ({placeholders})",
        list(by_id),
    )
    scored = []
    for chunk_id, source_path, heading, text in cursor.fetchall():
        semantic_score, keyword_score = by_id[chunk_id]
        scored.append({
            "chunk_id": chunk_id,
            "source_path": source_path,
            "heading": heading,
            "text": text,
            "keyword_score": keyword_score,
            "semantic_score": semantic_score,
        })
    return scored


# Keyword's share of the hybrid score. The original value was 0.6 — backwards.
# Measured over the 50-query gold set with real sentence-transformers
# embeddings, keyword-only scores MRR 0.547 and semantic-only 0.651, so the
# WEAKER retriever was carrying the larger weight:
#
#   w=0.6 (was)  top1 52%  top3 70%  MRR 0.619
#   w=0.3        top1 66%  top3 72%  MRR 0.711
#   w=0.2 (now)  top1 70%  top3 78%  MRR 0.742
#   w=0.15       top1 70%  top3 78%  MRR 0.740
#
# 0.15 and 0.2 are a plateau rather than a spike, so this is a trend and not a
# value overfitted to 50 queries. Reciprocal rank fusion was also tried and lost
# on every metric except top-3 (74% / MRR 0.652): it weights both retrievers
# equally by construction, which over-promotes the weaker keyword ordering here.
KEYWORD_WEIGHT = 0.2

# Floor for inclusion. Cosine mapped through (x+1)/2 puts an unrelated chunk at
# ~0.5, so with the weight above every chunk starts near 0.4 — this threshold
# only sheds the genuinely empty tail; `limit` does the real truncation.
MIN_HYBRID_SCORE = 0.2


def _fuse(scored: List[Dict]) -> List[Dict]:
    """Blend the keyword and semantic scores into one ranking."""
    results = []
    for chunk in scored:
        hybrid_score = (
            KEYWORD_WEIGHT * chunk["keyword_score"]
            + (1.0 - KEYWORD_WEIGHT) * chunk["semantic_score"]
        )
        if hybrid_score > MIN_HYBRID_SCORE:
            results.append({**chunk, "hybrid_score": hybrid_score})
    results.sort(key=lambda c: c["hybrid_score"], reverse=True)
    return results
