"""Rebuild the chunk / FTS / embedding index — atomically, and never with mock.

The rebuild used to move the live index to `<db>.prev` and write the replacement
AT the canonical path. That made every rebuild a window in which the production
index was incomplete, and on 2026-09-17 a session-start hook was killed 43
seconds into one, leaving 2,000 of 184,231 chunks at the canonical path with the
only complete copy in a `.prev` file nothing would ever promote back.

Now the replacement is built at `<db>.building-<pid>`, validated while it is
still a temporary file, and moved into place with a single `os.replace()`. The
old index stays open (read-only) for embedding reuse the whole time. Whatever
kills the rebuild — exception, SIGINT, SIGKILL, a closed laptop — the canonical
path either holds the old index or the new one, never a half-built one, and
`.prev` no longer carries any correctness weight.

The second guard is on the vectors themselves. `get_embedding` falls back to
deterministic hashes when the provider is unset OR when SentenceTransformers
fails to import, and returns them as a normal successful result. A canonical
rebuild now asserts that the space it is about to write is the production one
and aborts otherwise, because an index of hashes answers every query with
confident noise.
"""

import json
import os
import sqlite3
import sys

from scripts.chunk import iter_all_markdown_chunks
from scripts.config import PROJECT_ROOT
from scripts.embed import EmbeddingIdentity, EmbeddingIdentityCorrupt, get_embedding, read_index_identity
from scripts.index_health import (
    CANONICAL_DIMENSION,
    CANONICAL_MODEL,
    CANONICAL_PROVIDER,
    check_index_health,
    is_canonical_index,
)

# Commit every this many chunks. Bounds the journal and SQLite's page cache.
# It no longer has anything to do with crash survival — the temporary build
# file is discarded whole — but a 184k-row single transaction is a memory
# profile nobody needs.
COMMIT_EVERY = 2000

from scripts import sqlite_publish as _publication

_BUILD_SUFFIX = _publication.BUILD_SUFFIX
# Kept under its old name: tests/test_index_integrity.py calls it directly.
_sweep_abandoned_builds = _publication.sweep_abandoned_builds


class _PriorIndex:
    """Read-only lookup of previously stored embeddings, kept ON DISK.

    chunk_id is a hash of path+heading+text, so an unchanged chunk_id implies
    identical content and its stored vector can be reused instead of recomputed
    — the expensive step under a real provider.

    This reads the existing index IN PLACE. It used to move the file to `.prev`
    first, purely so the canonical path would be free to build at; building
    elsewhere removes the reason, and with it the window where the live index
    was a partial one. It also used to load every vector into a dict, which at
    this corpus's size was over a gigabyte resident before the first row was
    written and one of three reasons a rebuild got OOM-killed; per-chunk lookup
    on an indexed primary key is O(1) memory and cheap.
    """

    def __init__(self, db_path: str):
        self._conn = None
        self.identity_problem = None
        if not os.path.exists(db_path):
            return
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            from scripts.vec_storage import load_extension
            load_extension(conn)
            conn.execute("SELECT 1 FROM embeddings LIMIT 1")
        except (sqlite3.Error, OSError):
            return
        self._conn = conn

    def reusable_for(self, identity: EmbeddingIdentity) -> bool:
        """Whether this index's vectors may be carried into a build in `identity`'s
        space. The index-level identity (the F4 stamp, or provider/model/dimension
        from its first row when it predates the stamp) must be the same space under
        the rule `require_space` applies to a query vector. A stamp that cannot be
        read means the prior space is unknown, so nothing is reusable; the reason
        is kept in `identity_problem` for the caller to report."""
        if self._conn is None:
            return False
        try:
            prior = read_index_identity(self._conn)
        except EmbeddingIdentityCorrupt as exc:
            self.identity_problem = str(exc)
            return False
        return prior is not None and prior.same_space_as(identity)

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


def _assert_embedding_space_is_safe(db_path: str, probe: dict) -> None:
    """Refuse to write vectors that are not what the caller asked for.

    Two distinct failures, one guard. `requested != actual` catches the silent
    fallback (SentenceTransformers fails to import, mock comes back looking
    like success). The canonical clause catches the unpinned shell: whatever
    the environment says, the production index holds one specific embedding
    space or it holds nothing.
    """
    requested_provider = os.environ.get("ZURVAN_EMBED_PROVIDER", "mock").lower()
    actual_provider, actual_model = probe["provider"], probe["model"]

    if actual_provider != requested_provider:
        raise RuntimeError(
            f"embedding provider fell back to {actual_provider!r} after "
            f"{requested_provider!r} was requested — refusing to build an index "
            "of vectors the caller did not ask for. Fix the provider (is "
            "sentence-transformers importable? is the model downloaded?) and "
            "re-run."
        )

    if not is_canonical_index(db_path):
        return

    if (actual_provider, actual_model, probe["dimension"]) != (
        CANONICAL_PROVIDER, CANONICAL_MODEL, CANONICAL_DIMENSION
    ):
        raise RuntimeError(
            f"refusing to rebuild the CANONICAL index with "
            f"{actual_provider}/{actual_model}/{probe['dimension']}: production "
            f"is pinned to {CANONICAL_PROVIDER}/{CANONICAL_MODEL}/"
            f"{CANONICAL_DIMENSION}. A mock index answers every query with "
            "deterministic hashes and measures 0% semantic recall while looking "
            "entirely healthy. Set ZURVAN_EMBED_PROVIDER and ZURVAN_EMBED_MODEL, "
            "or point ZURVAN_SEARCH_DB at a throwaway."
        )

    # Nothing under pytest may write the production index, whatever provider it
    # holds. This is not redundant with the clause above: while mutation-testing
    # that clause on 2026-09-17 I removed it, and the test asserting the
    # canonical rebuild is REFUSED went on to rebuild the production index and
    # delete it on the way out. A guard whose own test can destroy the thing it
    # guards needs a second lock that the same edit does not open. It is checked
    # last so a test about the provider still sees the provider's message.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            "refusing to rebuild the CANONICAL index from inside a test run. "
            "Build at a tmp_path, or set ZURVAN_SEARCH_DB to a throwaway."
        )


INDEX_META = "index_meta"


def read_index_meta(db_path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            return dict(conn.execute(f"SELECT key, value FROM {INDEX_META}"))
        except sqlite3.OperationalError:
            return {}                      # built before Z0 step 5: unstamped
    finally:
        conn.close()


def _stamp_index(build_path, fields: dict) -> None:
    """Write `fields` (the source stamps, plus `embedding_identity`) and
    `built_at` into `index_meta` in the BUILD file, before publication. The
    retriever never reads the source stamps (proved in Task 6); the query path
    reads `embedding_identity` (F4) and rebuild reuse reads it to decide whether
    a prior vector is in the same space."""
    from datetime import datetime, timezone
    conn = sqlite3.connect(build_path)
    try:
        conn.execute(f"CREATE TABLE {INDEX_META} (key TEXT PRIMARY KEY,"
                     " value TEXT NOT NULL)")
        rows = dict(fields)
        rows["built_at"] = datetime.now(timezone.utc).isoformat()
        conn.executemany(f"INSERT INTO {INDEX_META} (key, value) VALUES (?, ?)",
                         sorted(rows.items()))
        conn.commit()
    finally:
        conn.close()


def rebuild_search_index(db_path: str | None = None, root=None, snapshot=None):
    """Rebuild the chunk/FTS/embedding index into a temp file, then swap it in.

    ``root`` scopes which markdown tree is indexed. It exists for tests: a
    fixture that indexes the LIVE wiki has a runtime proportional to however
    much has been ingested, which at 3,823 pages meant the suite stopped
    finishing. Production callers leave it None and index the repo.
    """
    if snapshot is not None and root is not None:
        raise ValueError("pass snapshot OR root, not both")
    if snapshot is not None:
        # Design §6.1: no implicit live reads once the build starts.
        root = snapshot.path
    if db_path is None:
        # ZURVAN_SEARCH_DB lets a test build its index somewhere disposable.
        # Without it the smoke test, which runs under the mock embedding
        # provider, rebuilt the REAL index with deterministic-hash vectors and
        # silently destroyed semantic search — measured at 0% recall afterwards.
        from scripts.config import get_search_db_path
        db_path = str(get_search_db_path())
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    # One probe identifies the active provider/model, so stored embeddings are
    # only reused when they match — and so the space can be refused BEFORE any
    # file is touched.
    probe = get_embedding("zurvan embedding provider probe")
    _assert_embedding_space_is_safe(db_path, probe)

    live_builders = _sweep_abandoned_builds(db_path)
    if live_builders:
        # Two concurrent rebuilds can no longer corrupt each other — each owns
        # its own build file and the last `os.replace` simply wins — but they do
        # compete for CPU and one of them is always wasted work. `check.sh` has
        # refused to run beside a rebuild since 2026-09-16; the rebuild itself
        # did not, so `zurvan index search` typed twice (or, on 2026-09-17, run
        # accidentally by a shell command substitution) started a second one.
        raise RuntimeError(
            f"another rebuild is already running (pid {sorted(live_builders)}) "
            f"for {os.path.basename(db_path)}. Wait for it, or kill it and "
            "remove its .building-<pid> file."
        )
    build_path = _publication.build_path_for(db_path)
    _publication.discard_build(build_path)

    reusable = _PriorIndex(db_path)
    try:
        indexed, reused, computed = _build(build_path, root, reusable, probe["identity"])

        report = check_index_health(
            build_path,
            expect_provider=probe["provider"],
            expect_model=probe["model"],
            expect_dimension=probe["dimension"],
            deep=True,
        )
        if not report.ok:
            raise RuntimeError(
                "refusing to promote the rebuilt index — validation failed:\n"
                + "\n".join(f"  - {p}" for p in report.problems)
            )

        # The only mutation of the canonical path in this function, and it is
        # atomic: readers see the old index or the new one. Integrity was
        # checked by `check_index_health(deep=True)` above, and a full
        # `integrity_check` over 739 MB would double the cost of publishing.
        # The full vector-space identity travels with the index (F4): the
        # query path compares against it, not against a model name.
        stamp = dict(snapshot.stamp()) if snapshot is not None else {}
        stamp["embedding_identity"] = probe["identity"].to_json()
        _stamp_index(build_path, stamp)
        _publication.publish(build_path, db_path, integrity=False)
    finally:
        reusable.close()
        # The build file AND its sqlite sidecars: a killed rebuild that leaves a
        # `-journal` behind makes the next sweep's job ambiguous.
        _publication.discard_build(build_path)

    # The sidecar describes the index that was just replaced; drop it after the
    # swap, so a failed rebuild does not invalidate a still-live index's cache.
    from scripts import vector_cache
    vector_cache.discard(db_path)

    print(
        f"Search index successfully rebuilt. Chunks indexed: {indexed}. "
        f"Embeddings reused: {reused}, computed: {computed}."
    )
    return {"chunks": indexed, "reused": reused, "computed": computed}


def _build(build_path: str, root, reusable: _PriorIndex, current_identity: EmbeddingIdentity):
    """Write a complete index to `build_path`. Never touches the live index.

    `current_identity` is the full identity the build is stamped with. A stored
    vector is reused only when its index holds that same space (`reusable_for`)
    and the row itself says provider, model and dimension of that space too.
    """
    prior_is_reusable = reusable.reusable_for(current_identity)
    if reusable.identity_problem:
        print(f"  prior index not reused, every chunk will be re-embedded: {reusable.identity_problem}",
              file=sys.stderr, flush=True)
    current_row_space = (current_identity.provider, current_identity.model, current_identity.dimension)
    conn = sqlite3.connect(build_path)
    # sqlite-vec provides vec_distance_cosine() at query time; loading it here
    # keeps the write and read paths on one extension contract.
    from scripts import vec_storage
    vec_storage.load_extension(conn)
    cursor = conn.cursor()

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

    # FTS5 with porter stemming closes the documented lexical gap (R1B miss
    # analysis: query "citations" scored kw=0.000 against heading "Citation").
    # R4a ranking change, documented with before/after evals in CHANGELOG.md.
    cursor.execute("""
        CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id, heading, text, content='chunks', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)

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

    reused = computed = indexed = 0
    try:
        for c in iter_all_markdown_chunks(root):
            # OR IGNORE handles identical content in multiple wiki files.
            cursor.execute("""
                INSERT OR IGNORE INTO chunks (chunk_id, source_path, heading, text, content_hash, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (c['chunk_id'], c['source_path'], c['heading'], c['text'],
                  c['content_hash'], c['indexed_at']))
            if cursor.rowcount == 0:
                continue

            cursor.execute("""
                INSERT INTO chunks_fts (rowid, chunk_id, heading, text)
                VALUES (last_insert_rowid(), ?, ?, ?)
            """, (c['chunk_id'], c['heading'], c['text']))

            # Reuse the stored vector for unchanged chunks — embedding is the
            # expensive step with real providers.
            prior = reusable.get(c['chunk_id']) if prior_is_reusable else None
            if prior is not None and tuple(prior[:3]) == current_row_space:
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
                print(f"  indexed {indexed} chunks (reused {reused}, computed {computed})",
                      flush=True)
        conn.commit()
    finally:
        conn.close()

    return indexed, reused, computed


def _install_sigterm_cleanup():
    """Turn SIGTERM into an exception so the `finally` above actually runs.

    Without this, `kill <pid>` ends the process outright and leaves the build
    file behind. The canonical index is safe either way — that is the point of
    building elsewhere — but a stale build file is litter, and litter is what
    the next run has to reason about. Installed only for the command line:
    `signal.signal` works solely in the main thread and would clobber the
    handlers of any caller that imports this module.
    """
    import signal

    def _raise(signum, _frame):
        raise KeyboardInterrupt(f"received signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _raise)
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    _install_sigterm_cleanup()
    from scripts.build_snapshot import inputs_for_rebuild
    with inputs_for_rebuild(purpose="rebuild_search") as bs:
        if bs is None:
            print("  no ledger baseline: building from the live tree, unstamped")
        rebuild_search_index(snapshot=bs)
