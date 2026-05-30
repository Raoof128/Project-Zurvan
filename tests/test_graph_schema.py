import os
import sqlite3
from scripts.graph_schema import setup_graph_schema

def test_setup_graph_schema(tmp_path):
    db_path = str(tmp_path / "test_graph.sqlite")
    setup_graph_schema(db_path)
    
    assert os.path.exists(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'")
    assert cursor.fetchone() is not None
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='edges'")
    assert cursor.fetchone() is not None
    
    conn.close()
