import os
import glob
import sys
from typing import List, Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.graph_context import expand_graph_context

def _search_internal(query: str, hybrid: bool = False, limit: int = 10) -> List[Dict[str, Any]]:
    if hybrid:
        from scripts.hybrid_search import search_hybrid
        return search_hybrid(query, limit)

    wiki_files = glob.glob("wiki/**/*.md", recursive=True)
    matches = []
    keywords = query.lower().split()
    
    for filepath in wiki_files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            score = sum(1 for k in keywords if k in content.lower())
            if score > 0:
                matches.append({
                    'source_path': filepath,
                    'hybrid_score': score,
                    'text': content
                })
        except Exception:
            continue
            
    matches.sort(key=lambda x: x['hybrid_score'], reverse=True)
    return matches[:limit]

def search_memory(query: str, hybrid: bool = False):
    """
    Search wiki and print list of matches.
    """
    results = _search_internal(query, hybrid, limit=10)
    print(f"Found {len(results)} matches for '{query}':\n")
    for i, res in enumerate(results, 1):
        print(f"{i}. {res['source_path']} | Score: {res.get('hybrid_score', 'N/A')} | Snippet: {res['text'][:100]}...")

def export_context(topic: str, limit: int = 10, hybrid: bool = False, graph: bool = False, depth: int = 1) -> str:
    """Exports a Markdown context bundle based on search results."""
    
    results = _search_internal(topic, hybrid, limit)

    output = []
    output.append(f"# Zurvan Context Bundle: {topic}\n")
    output.append("## Search Matches\n")
    
    seed_paths = []
    
    if not results:
        output.append("No matching context found.")
    else:
        for r in results:
            path = r['source_path']
            score = r['hybrid_score']
            content = r['text']
            
            output.append(f"### Source: {path} (Score: {score:.2f})")
            output.append("```markdown")
            output.append(content[:1000] + ("\n...[truncated]..." if len(content) > 1000 else ""))
            output.append("```\n")
            seed_paths.append(path)
            
    if graph and seed_paths:
        graph_nodes = expand_graph_context(seed_paths, depth=depth)
        
        if graph_nodes:
            output.append("## Graph-Related Context\n")
            
            grouped = {}
            for n in graph_nodes:
                grouped.setdefault(n['node_type'], []).append(n)
                
            def render_group(title, ntype):
                if ntype in grouped:
                    output.append(f"### {title}\n")
                    for node in grouped[ntype]:
                        output.append(f"- **{node['title']}** (`{node['path']}`) [Depth: {node['depth']}] ({node['relation']})")
                    output.append("")
                    
            render_group("Related Decisions", "decision")
            render_group("Related Claims", "claim")
            render_group("Related Concepts", "concept")
            render_group("Related Sources", "source")
            render_group("Contradictions", "contradiction")
            render_group("Open Questions", "open_question")
            
            rendered_types = {"decision", "claim", "concept", "source", "contradiction", "open_question"}
            other_nodes = [n for n in graph_nodes if n['node_type'] not in rendered_types]
            if other_nodes:
                output.append("### Other Related Notes\n")
                for node in other_nodes:
                    output.append(f"- **{node['title']}** (`{node['path']}`) [Depth: {node['depth']}] ({node['relation']})")
                output.append("")

    return "\n".join(output)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--graph", action="store_true")
    parser.add_argument("--depth", type=int, default=1)
    args = parser.parse_args()
    
    print(export_context(args.topic, args.limit, args.hybrid, args.graph, args.depth))
