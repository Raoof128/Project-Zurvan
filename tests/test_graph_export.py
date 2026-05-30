import os
import sqlite3
from scripts.graph_schema import setup_graph_schema
from scripts.graph_export import export_markdown, export_dot

def test_graph_export(tmp_path):
    db_path = str(tmp_path / "test_graph.sqlite")
    setup_graph_schema(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO nodes (node_id, title, path) VALUES ('n1', 'Node 1', 'wiki/n1.md')")
    conn.commit()
    conn.close()
    
    md_path = str(tmp_path / "export.md")
    export_markdown(db_path, md_path)
    assert os.path.exists(md_path)
    with open(md_path, 'r') as f:
        content = f.read()
        assert "Node 1" in content
        
    dot_path = str(tmp_path / "export.dot")
    export_dot(db_path, dot_path)
    assert os.path.exists(dot_path)
    with open(dot_path, 'r') as f:
        content = f.read()
        assert "digraph KnowledgeGraph" in content
