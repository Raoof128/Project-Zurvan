import sqlite3
import json
import os
import sys
import math
import time
from typing import List, Dict
from scripts.embed import get_embedding
from scripts.fusion_policy import CURRENT as FUSION_POLICY

#: Stages recorded by `search_hybrid(timings=...)` — observation only.
STAGES = ("fts", "embed_query", "vector_scan", "hydrate", "fuse", "total")


def _ms(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1e6, 3)


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


def _index_identity(cursor):
    """The space the index holds, as an `EmbeddingIdentity`, or None for an
    index with no embeddings (see `embed.read_index_identity`). A corrupt
    `embedding_identity` stamp raises `EmbeddingIdentityCorrupt`."""
    from scripts.embed import read_index_identity
    return read_index_identity(cursor)


def search_hybrid(query: str, limit: int = 10, db_path: str | None = None,
                  timings: dict | None = None) -> List[Dict]:
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

    stage = {}
    t_total = time.perf_counter_ns()
    # read-only: the query path must never create or modify the index
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    from scripts import vec_storage
    vec_storage.load_extension(conn)
    cursor = conn.cursor()
    
    # Keyword search using FTS5 (BM25 rank)
    t = time.perf_counter_ns()
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
    stage["fts"] = _ms(t)

    # Semantic search — embed the query with the SAME provider/model the
    # index was built with (never the env), and PROVE the vector that came
    # back is in the index's space. A model that cannot load raises
    # `EmbeddingUnavailable`; a vector from another space raises
    # `EmbeddingSpaceMismatch`. Neither is answered with hashes (F4).
    index_provider, index_model = _index_embedding_config(cursor)
    expected = _index_identity(cursor)
    t = time.perf_counter_ns()
    try:
        result = get_embedding(query, provider=index_provider, model=index_model)
        if expected is not None:
            from scripts.embed import require_space
            require_space(result, expected)
    finally:
        stage["embed_query"] = _ms(t)
    query_emb = result['vector']

    t = time.perf_counter_ns()
    top = _score_semantic_top_k(cursor, query_emb, fts_scores, limit)
    stage["vector_scan"] = _ms(t)
    t = time.perf_counter_ns()
    scored = _hydrate(cursor, top)
    stage["hydrate"] = _ms(t)
    conn.close()

    t = time.perf_counter_ns()
    fused = _fuse(scored)[:limit]
    stage["fuse"] = _ms(t)
    stage["total"] = _ms(t_total)
    if timings is not None:
        timings.update(stage)
    return fused


class _Ranked:
    """One scored chunk under the canonical retrieval order.

    **Canonical order is `score DESC, chunk_id ASC`, and it is a POLICY.**
    Before this, there was none. The bounded heap displaced only on
    `hybrid > heap[0][0]`, so among chunks with exactly equal scores whichever
    the scan reached FIRST won the last slot, and `_fuse`'s stable sort then
    inherited whatever order SQLite's `WHERE chunk_id IN (...)` fetch returned.
    Equal scores were resolved by iteration order — an implementation accident
    standing in for a ranking rule.

    Phase 1 exposed it. Measured on the 50-query gold set, 2 queries changed
    their 10th result purely because sqlite-vec scans in a different order than
    the old `SELECT chunk_id, vector FROM embeddings` did. In BOTH cases the two
    contenders were byte-identical chunks from duplicate wiki pages
    (`medium_article.md` / `medium_article.md.md`, and a Codex article held
    under two slugs) carrying identical vectors and therefore identical scores.
    Nothing was reranked; an unspecified behaviour merely became visible.

    `__lt__` means "ranks AFTER" — worse — so a `heapq` min-heap holds the entry
    to surrender at its root. `chunk_id` is compared as an opaque string:
    nothing here assumes it is a hash, a hex digest, or of fixed width.
    """

    __slots__ = ("score", "chunk_id", "semantic", "keyword")

    def __init__(self, score, chunk_id, semantic=0.0, keyword=0.0):
        self.score = score
        self.chunk_id = chunk_id
        self.semantic = semantic
        self.keyword = keyword

    def __lt__(self, other):
        if self.score != other.score:
            return self.score < other.score
        # Equal scores: the LARGER chunk_id ranks after, so it is the one a
        # bounded heap gives up first.
        return self.chunk_id > other.chunk_id

    def outranks(self, other) -> bool:
        """True when `self` belongs above `other`.

        The single definition of the order. The heap and `_fuse` both defer to
        it rather than restating `-score, chunk_id` in two places that can drift.
        """
        return other.__lt__(self)


def _score_semantic_top_k(cursor, query_emb, fts_scores, limit):
    """Rank every chunk against the query, keeping only the best `limit`.

    Phase 1 (2026-09-14): scoring moved from Python to sqlite-vec. Vectors were
    JSON text parsed on every query — `vector_cache.py` and a 283 MB `.npy`
    sidecar existed only to move that parse off the query path. With float32
    BLOBs and `vec_distance_cosine()` there is no parse to move.

    **Full scan, never KNN.** Every chunk still receives a fused score, so a
    chunk with a strong keyword score but mediocre semantics can still reach the
    top-10. Semantic KNN would hide those — a candidate-policy change wearing a
    storage migration's clothes, and the reason Gate A demands identical
    per-query ordering rather than merely equal summary metrics.

    The arithmetic is unchanged: cosine, then `(cos + 1) / 2`, then the same
    fusion weights. Equal scores are resolved by `_Ranked`'s canonical order, so
    the result cannot depend on the order the scan happens to deliver rows in.
    """
    import heapq

    from scripts import vec_storage

    heap = []  # _Ranked, worst at the root
    for chunk_id, similarity in vec_storage.scan_similarities(cursor.connection, query_emb):
        # Cosine is [-1, 1]; map to [0, 1] for reporting only. Note this puts an
        # unrelated chunk at ~0.5, which is exactly why it must NOT be summed
        # against a keyword score — see the fusion comment below.
        semantic_score = vec_storage.similarity_to_fusion_scale(similarity)
        keyword_score = fts_scores.get(chunk_id, 0.0)
        hybrid = FUSION_POLICY.score(keyword_score, semantic_score)
        entry = _Ranked(hybrid, chunk_id, semantic_score, keyword_score)
        if len(heap) < limit:
            heapq.heappush(heap, entry)
        elif entry.outranks(heap[0]):
            heapq.heapreplace(heap, entry)
    return heap


def _hydrate(cursor, top):
    """Fetch source_path/heading/text for the surviving chunk_ids only."""
    if not top:
        return []
    by_id = {e.chunk_id: (e.semantic, e.keyword) for e in top}
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
#
# The value itself lives in scripts/fusion_policy.py, shared with the trace
# writer (F5): these names are kept for callers and tests.
KEYWORD_WEIGHT = FUSION_POLICY.keyword_weight

# Floor for inclusion. Cosine mapped through (x+1)/2 puts an unrelated chunk at
# ~0.5, so with the weight above every chunk starts near 0.4 — this threshold
# only sheds the genuinely empty tail; `limit` does the real truncation.
MIN_HYBRID_SCORE = FUSION_POLICY.min_score


def _fuse(scored: List[Dict]) -> List[Dict]:
    """Blend the keyword and semantic scores into one ranking."""
    results = []
    for chunk in scored:
        hybrid_score = FUSION_POLICY.score(chunk["keyword_score"], chunk["semantic_score"])
        if hybrid_score > MIN_HYBRID_SCORE:
            results.append({**chunk, "hybrid_score": hybrid_score})
    # `reverse=True` with `_Ranked.__lt__` ("ranks after") sorts best-first. A
    # plain stable sort on the score alone left tied chunks in `_hydrate`'s
    # SQLite fetch order, which is the same unspecified-tie defect the heap had.
    results.sort(key=lambda c: _Ranked(c["hybrid_score"], c["chunk_id"]), reverse=True)
    return results
