import sqlite3
from typing import Dict, Any, List, Optional
import json

DB_PATH = "data/graph.sqlite"

def get_stats(db_path: str = DB_PATH) -> Dict[str, int]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM nodes")
    node_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM edges")
    edge_count = cursor.fetchone()[0]
    
    conn.close()
    return {"nodes": node_count, "edges": edge_count}

def get_node(path_or_id: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM nodes WHERE node_id = ? OR path = ? OR slug = ?", 
                   (path_or_id, path_or_id, path_or_id))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None

def get_neighbours(path_or_id: str, depth: int = 1, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    node = get_node(path_or_id, db_path)
    if not node:
        return []
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Incoming and outgoing edges for depth 1
    cursor.execute("""
        SELECT e.edge_type, e.from_node_id, e.to_node_id, 
               n1.path as from_path, n1.title as from_title, 
               n2.path as to_path, n2.title as to_title
        FROM edges e
        JOIN nodes n1 ON e.from_node_id = n1.node_id
        JOIN nodes n2 ON e.to_node_id = n2.node_id
        WHERE e.from_node_id = ? OR e.to_node_id = ?
    """, (node['node_id'], node['node_id']))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def trace_node(path_or_id: str, depth: int = 2, db_path: str = DB_PATH) -> Dict[str, Any]:
    # Placeholder for tracing, we can just return neighbours recursively
    # For Phase 5, we keep it simple
    visited = set()
    result_edges = []
    
    def dfs(current_id, current_depth):
        if current_depth > depth or current_id in visited:
            return
        visited.add(current_id)
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT e.edge_type, e.from_node_id, e.to_node_id
            FROM edges e
            WHERE e.from_node_id = ? OR e.to_node_id = ?
        """, (current_id, current_id))
        
        edges = cursor.fetchall()
        conn.close()
        
        for edge in edges:
            edge_dict = dict(edge)
            edge_sig = (edge_dict['from_node_id'], edge_dict['to_node_id'], edge_dict['edge_type'])
            if edge_sig not in [ (e['from_node_id'], e['to_node_id'], e['edge_type']) for e in result_edges ]:
                result_edges.append(edge_dict)
            
            # Recurse
            next_id = edge_dict['to_node_id'] if edge_dict['from_node_id'] == current_id else edge_dict['from_node_id']
            dfs(next_id, current_depth + 1)
            
    start_node = get_node(path_or_id, db_path)
    if start_node:
        dfs(start_node['node_id'], 1)
        
    return {"start_node": path_or_id, "edges": result_edges, "nodes_visited": len(visited)}
