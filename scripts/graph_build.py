import os
import re
import uuid
import hashlib
import sqlite3
import json
import sys
from datetime import datetime
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.graph_schema import setup_graph_schema, DB_PATH
from scripts.config import PROJECT_ROOT

def generate_node_id(path: str) -> str:
    return hashlib.sha256(path.encode('utf-8')).hexdigest()

def get_file_content(path: str) -> str:
    # Node identity is kept relative (e.g. "wiki/foo.md") but content is read
    # from an absolute location so building is CWD-independent.
    abs_path = path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)
    with open(abs_path, 'r', encoding='utf-8') as f:
        return f.read()

def parse_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    frontmatter = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
            for line in fm_text.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    frontmatter[k.strip()] = v.strip().strip('"').strip("'")
    return frontmatter, body

def extract_node_data(path: str, content: str) -> Dict[str, str]:
    fm, body = parse_frontmatter(content)
    
    # Determine type
    node_type = fm.get('type')
    if not node_type:
        if 'claims/' in path: node_type = 'claim'
        elif 'concepts/' in path: node_type = 'concept'
        elif 'decisions/' in path: node_type = 'decision'
        elif 'entities/' in path: node_type = 'entity'
        elif 'syntheses/' in path: node_type = 'synthesis'
        elif 'summaries/' in path: node_type = 'summary'
        elif 'sources/' in path: node_type = 'source'
        elif path.startswith('docs/'): node_type = 'doc'
        else: node_type = 'note'
        
    title = fm.get('title')
    if not title:
        title = os.path.basename(path).replace('.md', '')
        
    tags = fm.get('tags', '')
    
    return {
        'node_id': generate_node_id(path),
        'node_type': node_type,
        'title': title,
        'path': path,
        'slug': os.path.basename(path).replace('.md', ''),
        'content_hash': hashlib.sha256(content.encode()).hexdigest(),
        'tags': tags,
        'created_at': fm.get('created_at', datetime.now().isoformat()),
        'updated_at': fm.get('updated_at', datetime.now().isoformat())
    }

def extract_edges(path: str, content: str, nodes_dict: Dict[str, Dict]) -> List[Dict]:
    edges = []
    fm, body = parse_frontmatter(content)
    from_node_id = generate_node_id(path)
    
    # Helper to add edge
    def add_edge(to_path: str, edge_type: str, evidence: str = ""):
        to_node_id = generate_node_id(to_path)
        if to_node_id in nodes_dict:
            edges.append({
                'edge_id': str(uuid.uuid4()),
                'from_node_id': from_node_id,
                'to_node_id': to_node_id,
                'edge_type': edge_type,
                'evidence': evidence,
                'source_path': path,
                'created_at': datetime.now().isoformat()
            })

    # 1. Wikilinks [[example]], including Obsidian's [[target|alias]] and
    # [[target#heading]] forms — the alias/heading are stripped before matching
    # so an aliased link an Obsidian user writes still resolves to its node.
    wikilinks = re.findall(r'\[\[(.*?)\]\]', body)
    for wl in wikilinks:
        target = wl.split('|', 1)[0].split('#', 1)[0].strip()
        if not target:
            continue
        # naive matching: find node by slug or title
        target_path = None
        for n_id, n_data in nodes_dict.items():
            if n_data['slug'] == target or n_data['title'] == target:
                target_path = n_data['path']
                break
        if target_path:
            add_edge(target_path, 'mentions', f'Wikilink: {wl}')

    # 2. Markdown links (naive)
    md_links = re.findall(r'\[.*?\]\((.*?\.md)\)', body)
    for link in md_links:
        # resolve relative path
        dir_name = os.path.dirname(path)
        abs_link = os.path.normpath(os.path.join(dir_name, link))
        if generate_node_id(abs_link) in nodes_dict:
            add_edge(abs_link, 'mentions', f'Markdown link')

    # 3. YAML fields
    if 'source' in fm: add_edge(f"wiki/sources/{fm['source']}", 'derived_from', 'YAML source')
    if 'source_path' in fm: add_edge(fm['source_path'], 'derived_from', 'YAML source_path')
    if 'source_id' in fm: add_edge(f"wiki/sources/{fm['source_id']}.md", 'derived_from', 'YAML source_id')
    
    # Extracted lines (simple parsing)
    for line in body.splitlines():
        lower_line = line.lower()
        if lower_line.startswith('source:'):
            # Attempt to find link
            links = re.findall(r'\[.*?\]\((.*?\.md)\)', line)
            for link in links:
                abs_link = os.path.normpath(os.path.join(os.path.dirname(path), link))
                add_edge(abs_link, 'derived_from', line)
                
    return edges

def build_graph(db_path: str = DB_PATH):
    setup_graph_schema(db_path)
    
    target_dirs = ['wiki', 'docs']
    target_files = ['AGENTS.md', 'CHANGELOG.md', 'README.md']
    exclude_dirs = ['raw', 'data', '.git', '__pycache__']
    
    nodes_dict = {}
    
    # Pass 1: Gather nodes
    paths_to_process = []
    
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        rel_root = os.path.relpath(root, PROJECT_ROOT)
        top = rel_root.split(os.sep)[0] if rel_root != "." else "."
        # Only descend into the repo root and the target directories.
        if top != "." and top not in target_dirs:
            continue

        for file in files:
            if file.endswith('.md'):
                # Node identity stays relative to the repo root so node_ids
                # remain stable no matter where the process is launched from.
                filepath = os.path.normpath(os.path.join(rel_root, file)) if rel_root != "." else file
                # Skip if it's in root and not in target_files
                if os.path.dirname(filepath) == "" and file not in target_files:
                    continue
                paths_to_process.append(filepath)

    for p in paths_to_process:
        try:
            content = get_file_content(p)
            node_data = extract_node_data(p, content)
            nodes_dict[node_data['node_id']] = node_data
        except Exception as e:
            print(f"Warning: Skipping {p} due to {e}")
            
    # Pass 2: Extract edges
    edges_list = []
    for p in paths_to_process:
        try:
            content = get_file_content(p)
            edges = extract_edges(p, content, nodes_dict)
            edges_list.extend(edges)
        except Exception as e:
            print(f"Warning: Edge extraction failed for {p}: {e}")

    # Write to DB
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM nodes")
    cursor.execute("DELETE FROM edges")
    
    for n in nodes_dict.values():
        cursor.execute("""
            INSERT INTO nodes (node_id, node_type, title, path, slug, content_hash, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (n['node_id'], n['node_type'], n['title'], n['path'], n['slug'], n['content_hash'], n['tags'], n['created_at'], n['updated_at']))
        
    for e in edges_list:
        cursor.execute("""
            INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type, evidence, source_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (e['edge_id'], e['from_node_id'], e['to_node_id'], e['edge_type'], e['evidence'], e['source_path'], e['created_at']))
        
    conn.commit()
    conn.close()
    
    print(f"Graph built successfully.")
    print(f"Nodes: {len(nodes_dict)}")
    print(f"Edges: {len(edges_list)}")

if __name__ == "__main__":
    build_graph()
