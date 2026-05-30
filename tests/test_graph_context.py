import sqlite3
import os
from scripts.graph_schema import setup_graph_schema
from scripts.graph_context import expand_graph_context

def test_expand_graph_context(tmp_path):
    db_path = str(tmp_path / "test_graph.sqlite")
    setup_graph_schema(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO nodes (node_id, title, path, node_type) VALUES ('n1', 'Node 1', 'wiki/n1.md', 'note')")
    cursor.execute("INSERT INTO nodes (node_id, title, path, node_type) VALUES ('n2', 'Node 2', 'wiki/n2.md', 'decision')")
    cursor.execute("INSERT INTO nodes (node_id, title, path, node_type) VALUES ('n3', 'Node 3', 'wiki/n3.md', 'claim')")
    cursor.execute("INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e1', 'n1', 'n2', 'mentions')")
    cursor.execute("INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e2', 'n2', 'n3', 'derived_from')")
    conn.commit()
    conn.close()
    
    # Depth 1 from n1 should find n2
    items_d1 = expand_graph_context(['wiki/n1.md'], depth=1, db_path=db_path)
    assert len(items_d1) == 1
    assert items_d1[0]['path'] == 'wiki/n2.md'
    assert items_d1[0]['depth'] == 1
    
    # Depth 2 from n1 should find n2 and n3
    items_d2 = expand_graph_context(['wiki/n1.md'], depth=2, db_path=db_path)
    assert len(items_d2) == 2
    paths = [i['path'] for i in items_d2]
    assert 'wiki/n2.md' in paths
    assert 'wiki/n3.md' in paths
    
    # Duplicate nodes should not be in the output
    # Depth limit respected
    
def test_missing_db_handled(capsys):
    items = expand_graph_context(['wiki/n1.md'], depth=1, db_path="non_existent_db.sqlite")
    assert items == []
    captured = capsys.readouterr()
    assert "Graph index missing" in captured.out
