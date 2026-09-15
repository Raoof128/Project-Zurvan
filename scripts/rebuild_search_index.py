import sqlite3
import os
import json
from scripts.chunk import iter_all_markdown_chunks
from scripts.embed import get_embedding
from scripts.config import PROJECT_ROOT

# Commit every this many chunks. Bounds the journal and SQLite's page cache,
# and makes a killed run leave a consistent partial index instead of nothing.
COMMIT_EVERY = 2000


class _PriorIndex:
    """Read-only lookup of previously stored embeddings, kept ON DISK.

    chunk_id is a hash of path+heading+text, so an unchanged chunk_id implies
    identical content and its stored vector can be reused instead of recomputed
    — the expensive step under a real provider.

    This used to load every vector into a dict. At 43k chunks that was
    tolerable; at this corpus's size it is over a gigabyte of JSON strings
    resident before the first row is written, and it was one of three reasons a
    full rebuild got OOM-killed. Querying the old file per chunk is O(1) memory
    and, on an indexed primary key, cheap.
    """

    def __init__(self, db_path: str):
        self._conn = None
        self._path = None
        if not os.path.exists(db_path):
            return
        # Move aside rather than delete, so the vectors survive to be reused
        # while the new index is built at the canonical path.
        prev = db_path + ".prev"
        try:
            if os.path.exists(prev):
                os.remove(prev)
            os.replace(db_path, prev)
            conn = sqlite3.connect(f"file:{prev}?mode=ro", uri=True)
            from scripts.vec_storage import load_extension
            load_extension(conn)
            conn.execute("SELECT 1 FROM embeddings LIMIT 1")
        except (sqlite3.Error, OSError):
            self._discard(prev)
            return
        self._conn, self._path = conn, prev

    def get(self, chunk_id):
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT provider, model, dimension, vector FROM embeddings WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        provider, model, dimension, vector = row
        if isinstance(vector, str):
            # A pre-Phase-1 index stores vectors as JSON TEXT. The VALUES are
            # unchanged by the format migration, so convert rather than
            # re-embed — that is the difference between a minutes-long rebuild
            # and a ~50-minute one. Reusing the string verbatim would put an
            # 8kB JSON blob in a BLOB column and make vec_distance_cosine
            # return garbage; the dimension guard on the write path caught
            # exactly that on the first real run.
            from scripts.vec_storage import encode
            try:
                vector = encode(json.loads(vector))
            except (ValueError, TypeError):
                return None
        return provider, model, dimension, vector

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._discard(self._path)
        self._path = None

    @staticmethod
    def _discard(path):
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def rebuild_search_index(db_path: str | None = None, root=None):
    """Rebuild the chunk/FTS/embedding index.

    ``root`` scopes which markdown tree is indexed. It exists for tests: a
    fixture that indexes the LIVE wiki has a runtime proportional to however
    much has been ingested, which at 3,823 pages meant the suite stopped
    finishing at all. Production callers leave it None and index the repo.
    """
    if db_path is None:
        # ZURVAN_SEARCH_DB lets a test build its index somewhere disposable.
        # Without it the smoke test, which runs under the mock embedding
        # provider, rebuilt the REAL index with deterministic-hash vectors and
        # silently destroyed semantic search — measured at 0% recall afterwards.
        from scripts.config import get_search_db_path
        db_path = str(get_search_db_path())
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    # One probe identifies the active provider/model so stored embeddings are
    # only reused when they match the current configuration.
    probe = get_embedding("zurvan embedding provider probe")
    current_provider_model = (probe["provider"], probe["model"])
    # Moves any existing index aside; the canonical path is free afterwards, so
    # the new index is built fresh without an explicit remove.
    reusable = _PriorIndex(db_path)
    if os.path.exists(db_path):
        os.remove(db_path)
    # The sidecar describes the index being replaced; drop it before writing.
    from scripts import vector_cache
    vector_cache.discard(db_path)

    conn = sqlite3.connect(db_path)
    # sqlite-vec provides vec_distance_cosine() at query time; loading it here
    # keeps the write and read paths on one extension contract.
    from scripts import vec_storage
    vec_storage.load_extension(conn)
    cursor = conn.cursor()
    
    # Create chunks table
    cursor.execute("""
        CREATE TABLE chunks (
            chunk_id TEXT PRIMARY KEY,
            source_path TEXT,
            heading TEXT,
            text TEXT,
            content_hash TEXT,
            indexed_at TEXT
        )
    """)
    
    # Create FTS5 table. Porter stemming closes the documented lexical gap
    # (R1B miss analysis: query "citations" scored kw=0.000 against heading
    # "Citation") — plural/inflected query terms now match their stems.
    # R4a ranking change, documented with before/after evals in CHANGELOG.md.
    cursor.execute("""
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, heading, text, content='chunks', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    
    # Create embeddings table
    cursor.execute("""
        CREATE TABLE embeddings (
            chunk_id TEXT PRIMARY KEY,
            provider TEXT,
            model TEXT,
            dimension INTEGER,
            vector BLOB,
            FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
        )
    """)
    
    reused = 0
    computed = 0
    indexed = 0
    for c in iter_all_markdown_chunks(root):
        # Insert chunk — OR IGNORE handles identical content in multiple wiki files
        cursor.execute("""
            INSERT OR IGNORE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (c['chunk_id'], c['source_path'], c['heading'], c['text'], c['content_hash'], c['indexed_at']))
        if cursor.rowcount == 0:
            continue

        # Insert FTS
        cursor.execute("""
            INSERT INTO chunks_fts (rowid, chunk_id, heading, text)
            VALUES (last_insert_rowid(), ?, ?, ?)
        """, (c['chunk_id'], c['heading'], c['text']))

        # Insert Embedding — reuse the stored vector for unchanged chunks
        # (embedding is the expensive step with real providers).
        prior = reusable.get(c['chunk_id'])
        if prior is not None and (prior[0], prior[1]) == current_provider_model:
            provider, model, dimension, vector_blob = prior
            reused += 1
        else:
            emb = get_embedding(c['text'])
            provider, model, dimension = emb['provider'], emb['model'], emb['dimension']
            vector_blob = vec_storage.encode(emb['vector'])
            computed += 1
        # Reject a wrong-width vector rather than storing it: a silently
        # mismatched dimension makes vec_distance_cosine return garbage for
        # that row, which would surface as an unexplainable ranking change.
        if len(vector_blob) != dimension * 4:
            raise ValueError(
                f"chunk {c['chunk_id']}: {len(vector_blob)} bytes for "
                f"{dimension} float32 dimensions (expected {dimension * 4})"
            )
        cursor.execute("""
            INSERT INTO embeddings (chunk_id, provider, model, dimension, vector)
            VALUES (?, ?, ?, ?, ?)
        """, (c['chunk_id'], provider, model, dimension, vector_blob))

        indexed += 1
        if indexed % COMMIT_EVERY == 0:
            conn.commit()
            print(f"  indexed {indexed} chunks (reused {reused}, computed {computed})", flush=True)

    conn.commit()
    conn.close()
    reusable.close()

    # No sidecar is built any more. It existed solely to move the per-query
    # JSON parse off the query path; with vectors stored as float32 BLOBs and
    # scored by sqlite-vec there is no parse to move.

    print(
        f"Search index successfully rebuilt. Chunks indexed: {indexed}. "
        f"Embeddings reused: {reused}, computed: {computed}."
    )

if __name__ == "__main__":
    rebuild_search_index()
