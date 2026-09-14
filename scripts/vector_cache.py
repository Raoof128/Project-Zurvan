"""A float32 matrix sidecar for the index's embedding vectors.

Vectors are stored in SQLite as JSON text. Scoring a query means parsing every
one of them: at 199,110 chunks that is ~30-50 s per search, and the parse — not
the cosine arithmetic — is the whole cost. The vectors do not change between
rebuilds, so the parse belongs on the write path, once.

This keeps two files beside the index:

    <db>.vectors.npy    float32 [n_chunks x dim], memory-mapped on read
    <db>.vectors.json   {"ids": [...], "count": n, "dim": d}

The cache is derived, disposable and rebuilt whenever it does not match the
index's embedding count, so a stale or truncated pair can never be used. It is
an optimisation only: `hybrid_search` falls back to scanning the DB when the
cache is absent and cannot be built.
"""
import json
import os
import sqlite3


def _paths(db_path: str):
    return db_path + ".vectors.npy", db_path + ".vectors.json"


def _embedding_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]


def load(db_path: str):
    """Return (ids, matrix) from a cache matching this index, else None."""
    try:
        import numpy as np
    except ImportError:
        return None
    npy_path, meta_path = _paths(db_path)
    if not (os.path.exists(npy_path) and os.path.exists(meta_path)):
        return None
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            live = _embedding_count(conn)
        finally:
            conn.close()
        if meta.get("count") != live:
            return None  # index moved on; the cache is stale
        matrix = np.load(npy_path, mmap_mode="r")
        ids = meta["ids"]
        if matrix.shape[0] != len(ids) or matrix.shape[0] != live:
            return None
        return ids, matrix
    except (OSError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error):
        return None


def build(db_path: str, batch: int = 5000):
    """Parse every stored vector once and write the sidecar. Returns (ids, matrix)."""
    try:
        import numpy as np
    except ImportError:
        return None
    npy_path, meta_path = _paths(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute("SELECT chunk_id, vector FROM embeddings")
        ids, blocks = [], []
        while True:
            rows = cursor.fetchmany(batch)
            if not rows:
                break
            ids.extend(r[0] for r in rows)
            blocks.append(np.asarray([json.loads(r[1]) for r in rows], dtype=np.float32))
    finally:
        conn.close()
    if not blocks:
        return None
    matrix = np.vstack(blocks)
    # Write to temp names first: a half-written pair must never be loadable.
    np.save(npy_path + ".tmp.npy", matrix)
    with open(meta_path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"ids": ids, "count": len(ids), "dim": int(matrix.shape[1])}, fh)
    os.replace(npy_path + ".tmp.npy", npy_path)
    os.replace(meta_path + ".tmp", meta_path)
    return ids, matrix


def load_or_build(db_path: str):
    return load(db_path) or build(db_path)


def discard(db_path: str):
    for p in _paths(db_path):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
