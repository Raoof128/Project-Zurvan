import sqlite3
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.graph_schema import DB_PATH
from scripts.config import PROJECT_ROOT

DEFAULT_MD_EXPORT = str(PROJECT_ROOT / "data" / "graph_export.md")
DEFAULT_DOT_EXPORT = str(PROJECT_ROOT / "data" / "graph_export.dot")

def export_markdown(db_path: str = DB_PATH, out_path: str = DEFAULT_MD_EXPORT):
    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found.")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM nodes")
    nodes = cursor.fetchall()
    
    cursor.execute("""
        SELECT e.*, n1.title as from_title, n2.title as to_title 
        FROM edges e
        JOIN nodes n1 ON e.from_node_id = n1.node_id
        JOIN nodes n2 ON e.to_node_id = n2.node_id
    """)
    edges = cursor.fetchall()
    conn.close()
    
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("# Knowledge Graph Export\n\n")
        f.write("## Nodes\n")
        for n in nodes:
            f.write(f"- **{n['title']}** ({n['node_type']}) - `{n['path']}`\n")
            
        f.write("\n## Edges\n")
        for e in edges:
            f.write(f"- {e['from_title']} --[{e['edge_type']}]--> {e['to_title']}\n")
            
    print(f"Exported markdown to {out_path}")

def _dot_escape(value) -> str:
    """Escape a DOT quoted-string label: backslash first, then double-quote,
    so a node/edge title containing " or \\ can't break the generated DOT."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def export_dot(db_path: str = DB_PATH, out_path: str = DEFAULT_DOT_EXPORT):
    if not os.path.exists(db_path):
        print(f"Error: {db_path} not found.")
        return
        
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM nodes")
    nodes = cursor.fetchall()
    
    cursor.execute("""
        SELECT e.*, n1.node_id as from_id, n2.node_id as to_id 
        FROM edges e
        JOIN nodes n1 ON e.from_node_id = n1.node_id
        JOIN nodes n2 ON e.to_node_id = n2.node_id
    """)
    edges = cursor.fetchall()
    conn.close()
    
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write("digraph KnowledgeGraph {\n")
        f.write("  node [shape=box, style=rounded];\n")
        
        for n in nodes:
            f.write(f'  "{_dot_escape(n["node_id"])}" [label="{_dot_escape(n["title"])}\\n({_dot_escape(n["node_type"])})"];\n')

        for e in edges:
            f.write(f'  "{_dot_escape(e["from_id"])}" -> "{_dot_escape(e["to_id"])}" [label="{_dot_escape(e["edge_type"])}"];\n')
            
        f.write("}\n")
        
    print(f"Exported DOT to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=["markdown", "dot"], required=True)
    args = parser.parse_args()
    
    if args.format == "markdown":
        export_markdown()
    elif args.format == "dot":
        export_dot()
