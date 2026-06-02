import os
import glob
import sys
import datetime
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

def _save_synthesis(topic: str, markdown_content: str, source_paths: list) -> None:
    """Write a canonical Markdown synthesis page to wiki/syntheses/. Always saves markdown."""
    from scripts.wiki_merge import append_log_save
    from scripts.filename_utils import sanitize_filename

    slug = sanitize_filename(topic)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    synth_dir = os.path.join("wiki", "syntheses")
    os.makedirs(synth_dir, exist_ok=True)

    # Microsecond timestamp makes collision extremely unlikely; loop is safety net
    candidate = os.path.join(synth_dir, f"{datetime.date.today().isoformat()}-{timestamp}-{slug}.md")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(synth_dir, f"{datetime.date.today().isoformat()}-{timestamp}-{slug}-{counter}.md")
        counter += 1

    # YAML-safe: quote query value to handle colons, hashes, pipes
    safe_topic = topic.replace('"', '\\"')
    fm = (
        f'---\n'
        f'type: synthesis\n'
        f'query: "{safe_topic}"\n'
        f'sources: {", ".join(source_paths)}\n'
        f'created_at: {datetime.datetime.now().isoformat()}\n'
        f'tags: synthesis, query-derived\n'
        f'---\n\n'
    )
    with open(candidate, "w", encoding="utf-8") as f:
        f.write(fm + markdown_content)
    append_log_save(slug)


def search_memory(query: str, hybrid: bool = False, save: bool = False):
    """
    Search wiki and print list of matches.
    """
    results = _search_internal(query, hybrid, limit=10)
    print(f"Found {len(results)} matches for '{query}':\n")
    lines = []
    for i, res in enumerate(results, 1):
        line = f"{i}. {res['source_path']} | Score: {res.get('hybrid_score', 'N/A')} | Snippet: {res['text'][:100]}..."
        print(line)
        lines.append(line)
    if save:
        source_paths = [r["source_path"] for r in results]
        _save_synthesis(query, "\n".join(lines), source_paths)

def export_context(topic: str, limit: int = 10, hybrid: bool = False, graph: bool = False, depth: int = 1, save: bool = False, fmt: str = "markdown") -> str:
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

    base_output = "\n".join(output)

    if save:
        _save_synthesis(topic, base_output, seed_paths)

    return base_output

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
