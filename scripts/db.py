import sqlite3
import os

#: An explicit override (tests set it). None means: resolve per call from the
#: store root, which follows ZURVAN_ROOT. Until 2026-09-28 this was pinned to
#: the repo's own data/registry.sqlite, so `sources` ignored ZURVAN_ROOT while
#: the knowledge ledger in the same file followed it - and a subprocess ingest
#: test wrote a row into the LIVE registry.
DB_PATH = None


def _db_path() -> str:
    if DB_PATH is not None:
        return DB_PATH
    from scripts import knowledge_scope as ks
    return str(ks.store_root() / "data" / "registry.sqlite")


def get_db():
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            hash TEXT NOT NULL,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def find_source_by_hash(file_hash):
    """Return the path a given content hash was ingested under, or None.

    Dedup is documented as being by content hash, so the same document is a
    no-op even when it arrives under a different filename.
    """
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT path FROM sources WHERE hash = ?', (file_hash,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def register_source(path, file_hash):
    conn = get_db()
    cursor = conn.cursor()
    try:
        # Refuse on identical CONTENT, not just an identical path: the table's
        # UNIQUE constraint is on path alone, so the same document copied to a
        # second filename used to ingest twice.
        if cursor.execute(
            'SELECT 1 FROM sources WHERE hash = ?', (file_hash,)
        ).fetchone():
            return False
        cursor.execute('INSERT INTO sources (path, hash) VALUES (?, ?)', (path, file_hash))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Already ingested or path exists.
        return False
    finally:
        conn.close()

def unregister_source(path):
    """Drop a source's registration so a failed ingest can be retried.

    ``register_source`` claims the path up front to guard against a double
    ingest, but the page/log/index writes happen after that. Without this
    rollback a crash mid-ingest leaves the path registered with no source page,
    and every retry is refused with "has already been ingested".
    """
    conn = get_db()
    try:
        conn.execute('DELETE FROM sources WHERE path = ?', (path,))
        conn.commit()
    finally:
        conn.close()
