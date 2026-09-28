import sqlite3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.config import PROJECT_ROOT
DB_PATH = str(PROJECT_ROOT / "data" / "graph.sqlite")

GRAPH_SCHEMA_VERSION = 1

def setup_graph_schema(db_path: str = DB_PATH) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            title TEXT,
            path TEXT,
            slug TEXT,
            content_hash TEXT,
            tags TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS edges (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            evidence TEXT,
            source_path TEXT,
            created_at TEXT,
            FOREIGN KEY(from_node_id) REFERENCES nodes(node_id),
            FOREIGN KEY(to_node_id) REFERENCES nodes(node_id)
        )
    """)
    
    # Create indexes for faster traversal
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_from_node ON edges(from_node_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_to_node ON edges(to_node_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_node_path ON nodes(path)")
    # Stamps live INSIDE the database (design §4.4): a filename is not
    # evidence. Step 5 adds built_from_revision / built_from_snapshot; this
    # step writes only what it can know.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    
    conn.commit()
    conn.close()

def read_graph_meta(db_path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM graph_meta"))
    finally:
        conn.close()


def content_digest(db_path) -> str:
    """What the graph SAYS, without when it was built or what it was called.

    Excluded: timestamps (`extract_node_data` defaults them to now()) and
    `edge_id`, which is `uuid4()` per build (`graph_build.py:84`). Measured
    2026-09-28: two builds of one frozen corpus differ in every edge_id and in
    nothing else. Edges are ordered by content, duplicates kept (7 groups on
    the live corpus), so a lost or doubled edge still changes the digest.
    """
    import hashlib
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        for row in conn.execute(
                "SELECT node_id, node_type, title, path, slug, content_hash, tags"
                " FROM nodes ORDER BY node_id"):
            h.update(repr(row).encode())
        for row in conn.execute(
                "SELECT from_node_id, to_node_id, edge_type, evidence, source_path"
                " FROM edges ORDER BY from_node_id, to_node_id, edge_type,"
                " evidence, source_path"):
            h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        conn.close()


if __name__ == "__main__":
    setup_graph_schema()
    print("Graph schema initialized.")
