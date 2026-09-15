#!/usr/bin/env python3
"""Phase 1 — vector storage on sqlite-vec, as a RANKING-INVARIANT swap.

Vectors were stored as JSON **text** and parsed on every query. That parse, not
the cosine arithmetic, was the cost: `scripts/vector_cache.py` existed purely to
move it off the query path, and a 283 MB `.npy` sidecar existed purely to hold
the result. Both are workarounds for the storage format.

Two decisions carry this module.

**Full scan, never the vec0 KNN path.** Today every chunk receives a fused score,
so a chunk with a strong keyword score but mediocre semantics can still reach the
top-10 (`fts_scores.get(chunk_id, 0.0)` applies corpus-wide). Semantic KNN with
`k = N` would make chunks outside the semantic top-K invisible **even on a
perfect keyword match** — a candidate-policy change wearing a storage
migration's clothes. `vec_distance_cosine()` as a scalar over a full scan is
still exact and still SIMD-accelerated, and it preserves the candidate set,
which is what makes Gate A's invariance achievable at all.

*Deviation from the spec's wording, stated plainly:* the spec said "a `vec0`
table". `vec0` is a virtual table built for KNN, and this module does not use
KNN, so the vectors live in the existing `embeddings` table with the JSON text
column replaced by a float32 BLOB. The load-bearing requirement — a full scan
with sqlite-vec's scalar distance — is preserved exactly. Nothing here precludes
adding a `vec0` table later for Phase 1B, which is where KNN belongs.

**Distance is not similarity.** `vec_distance_cosine` returns cosine DISTANCE;
`_fuse` consumes a similarity mapped through `(cos + 1) / 2`. The conversion is
explicit and separately tested, because a silent scale error here looks exactly
like a ranking regression and gets debugged in the wrong place.
"""
import importlib.metadata as _md
import sqlite3
import struct

VECTOR_COLUMN = "vector"


def load_extension(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Load sqlite-vec into a connection, leaving extension loading disabled."""
    import sqlite_vec

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    return conn


def versions(conn: sqlite3.Connection):
    """(python package version, loaded extension version).

    Both are recorded in the frozen Phase 1 report. They are asserted equal in
    tests: a package shipping a differently-built native extension is a
    reproducibility trap worth catching at the gate rather than in a result.
    """
    pkg = _md.version("sqlite-vec")
    ext = conn.execute("select vec_version()").fetchone()[0]
    return pkg, ext


def encode(vector) -> bytes:
    """float32 BLOB — 4 bytes per dimension, no JSON, no per-query parse."""
    return struct.pack(f"{len(vector)}f", *(float(x) for x in vector))


def decode(blob) -> list:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def distance_to_similarity(distance: float) -> float:
    """cosine similarity from sqlite-vec's cosine DISTANCE."""
    return 1.0 - distance


def similarity_to_fusion_scale(similarity: float) -> float:
    """The existing mapping `_fuse` consumes: max(0, (cos + 1) / 2).

    Unchanged from the previous implementation, deliberately — Phase 1 alters
    where vectors are stored, never how a score is formed.
    """
    return max(0.0, (similarity + 1.0) / 2.0)


def scan_similarities(conn: sqlite3.Connection, query_vector, batch=None):
    """Yield (chunk_id, cosine_similarity) for EVERY chunk, in UNSPECIFIED order.

    **Order is deliberately not a promise of this layer.** An earlier version
    sorted with `ORDER BY d ASC, chunk_id ASC`, which made SQLite sort all
    184,171 rows on every query — measured at 2.92 s against 1.37 s for the same
    scan without it, so roughly half the query cost bought an ordering the
    caller immediately discards: `_score_semantic_top_k` keeps only the best
    `limit` through a bounded heap and sorts those.

    The sort was also doing something it should never have been trusted with.
    Equal-scoring chunks exist — duplicate wiki pages embed identically — and
    while this function sorted them, it was the only thing making the result
    deterministic. That is a ranking rule living in a storage function. It now
    lives in `hybrid_search._Ranked` (score DESC, chunk_id ASC), which applies
    the same rule whatever order rows arrive in, so removing the sort here
    cannot change a result. Gate A re-run proves it: 50/50 identical top-10.
    """
    q = encode(query_vector)
    sql = (
        f"SELECT chunk_id, vec_distance_cosine({VECTOR_COLUMN}, ?) AS d "
        "FROM embeddings"
    )
    cur = conn.execute(sql, (q,))
    while True:
        rows = cur.fetchmany(batch or 5000)
        if not rows:
            break
        for chunk_id, distance in rows:
            yield chunk_id, distance_to_similarity(distance)
