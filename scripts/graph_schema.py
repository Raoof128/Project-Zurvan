import sqlite3
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.config import PROJECT_ROOT
DB_PATH = str(PROJECT_ROOT / "data" / "graph.sqlite")

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
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    setup_graph_schema()
    print("Graph schema initialized.")
