import sqlite3
import os
import sys
from typing import List, Dict, Any, Set

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.graph_schema import DB_PATH

def expand_graph_context(seeds: List[str], depth: int = 1, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        print(f"Graph index missing. Run: zurvan graph rebuild")
        return []
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get initial node IDs for seeds
    seed_ids = set()
    for s in seeds:
        cursor.execute("SELECT node_id FROM nodes WHERE path = ? OR node_id = ? OR slug = ?", (s, s, s))
        row = cursor.fetchone()
        if row:
            seed_ids.add(row['node_id'])
            
    if not seed_ids:
        conn.close()
        return []

    visited = set(seed_ids)
    expanded_items = []
    
    current_frontier = list(seed_ids)
    
    for d in range(1, depth + 1):
        next_frontier = set()
        for node_id in current_frontier:
            # Outgoing edges
            cursor.execute("""
                SELECT e.edge_type, e.to_node_id, n.path, n.node_type, n.title
                FROM edges e
                JOIN nodes n ON e.to_node_id = n.node_id
                WHERE e.from_node_id = ?
            """, (node_id,))
            
            for row in cursor.fetchall():
                to_id = row['to_node_id']
                if to_id not in visited:
                    visited.add(to_id)
                    next_frontier.add(to_id)
                    expanded_items.append({
                        'path': row['path'],
                        'node_type': row['node_type'],
                        'title': row['title'],
                        'depth': d,
                        'relation': f"outgoing:{row['edge_type']}",
                        'source_id': node_id
                    })
                    
            # Incoming edges
            cursor.execute("""
                SELECT e.edge_type, e.from_node_id, n.path, n.node_type, n.title
                FROM edges e
                JOIN nodes n ON e.from_node_id = n.node_id
                WHERE e.to_node_id = ?
            """, (node_id,))
            
            for row in cursor.fetchall():
                from_id = row['from_node_id']
                if from_id not in visited:
                    visited.add(from_id)
                    next_frontier.add(from_id)
                    expanded_items.append({
                        'path': row['path'],
                        'node_type': row['node_type'],
                        'title': row['title'],
                        'depth': d,
                        'relation': f"incoming:{row['edge_type']}",
                        'source_id': node_id
                    })
                    
        current_frontier = list(next_frontier)
        
    conn.close()
    
    # Sort for deterministic output: decisions/claims first, depth ascending
    def type_rank(nt):
        if nt == 'decision': return 0
        if nt == 'claim': return 1
        if nt == 'contradiction': return 2
        if nt == 'open_question': return 3
        if nt == 'concept': return 4
        if nt == 'source': return 5
        return 6
        
    expanded_items.sort(key=lambda x: (x['depth'], type_rank(x['node_type']), x['title']))
    return expanded_items

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("seeds", nargs="+")
    parser.add_argument("--depth", type=int, default=1)
    args = parser.parse_args()
    
    items = expand_graph_context(args.seeds, args.depth)
    for i in items:
        print(f"[{i['depth']}] {i['title']} ({i['node_type']}) - {i['relation']}")
