import sqlite3
import os
from scripts.graph_schema import setup_graph_schema
from scripts.graph_query import get_stats, get_node, get_neighbours

def test_graph_query(tmp_path):
    db_path = str(tmp_path / "test_graph.sqlite")
    setup_graph_schema(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO nodes (node_id, title, path) VALUES ('n1', 'Node 1', 'wiki/n1.md')")
    cursor.execute("INSERT INTO nodes (node_id, title, path) VALUES ('n2', 'Node 2', 'wiki/n2.md')")
    cursor.execute("INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type) VALUES ('e1', 'n1', 'n2', 'mentions')")
    conn.commit()
    conn.close()
    
    stats = get_stats(db_path)
    assert stats['nodes'] == 2
    assert stats['edges'] == 1
    
    node = get_node('wiki/n1.md', db_path)
    assert node['title'] == 'Node 1'
    
    neighbours = get_neighbours('wiki/n1.md', 1, db_path)
    assert len(neighbours) == 1
    assert neighbours[0]['to_path'] == 'wiki/n2.md'
    assert neighbours[0]['edge_type'] == 'mentions'
