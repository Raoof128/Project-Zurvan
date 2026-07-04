import os
import glob
import sys
import json
import datetime
import hashlib
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.graph_context import expand_graph_context
from scripts.config import PROJECT_ROOT
from scripts.trace_schema import TraceEvent, TraceRecord, create_trace_id, utc_now
from scripts.trace_writer import TraceStore

def _search_internal(
    query: str, hybrid: bool = False, limit: int = 10, root: str | Path | None = None
) -> List[Dict[str, Any]]:
    """Search one project's knowledge base. `root` defaults to this repo;
    federation passes another registered project's root, so knowledge-only
    projects (wiki/ + docs/, no embedded Zurvan engine) are searchable too."""
    base = Path(root) if root is not None else PROJECT_ROOT
    if hybrid:
        from scripts.hybrid_search import search_hybrid
        return search_hybrid(query, limit, db_path=str(base / "data" / "search.sqlite"))

    # Scan the same corpus the search index does (wiki/ + docs/): keyword mode
    # previously globbed wiki/ only, so `zurvan search <term>` silently could
    # not find any docs/ page that hybrid mode surfaces. source_path is returned
    # repo-relative to match hybrid results (and to avoid leaking absolute
    # machine paths into stdout / saved syntheses).
    files = []
    for directory in ("wiki", "docs"):
        files += glob.glob(str(base / directory / "**" / "*.md"), recursive=True)
    matches = []
    keywords = query.lower().split()

    for filepath in files:
        rel = os.path.relpath(filepath, base)
        # Skip derived trace mirrors: self-referential, pollute retrieval.
        if "traces" in rel.split(os.sep):
            continue
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            score = sum(1 for k in keywords if k in content.lower())
            if score > 0:
                matches.append({
                    'source_path': rel,
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
    synth_dir = str(PROJECT_ROOT / "wiki" / "syntheses")
    os.makedirs(synth_dir, exist_ok=True)

    # Microsecond timestamp makes collision extremely unlikely; loop is safety net
    candidate = os.path.join(synth_dir, f"{datetime.date.today().isoformat()}-{timestamp}-{slug}.md")
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(synth_dir, f"{datetime.date.today().isoformat()}-{timestamp}-{slug}-{counter}.md")
        counter += 1

    # YAML-safe: quote query value to handle colons, hashes, pipes
    safe_topic = topic.replace('"', '\\"')
    # Keyword search returns absolute paths; store them repo-relative so the
    # tracked wiki page never leaks machine-specific absolute paths.
    rel_sources = [_trace_source_path(str(p)) for p in source_paths]
    fm = (
        f'---\n'
        f'type: synthesis\n'
        f'query: "{safe_topic}"\n'
        f'sources: {", ".join(rel_sources)}\n'
        f'created_at: {datetime.datetime.now().isoformat()}\n'
        f'tags: synthesis, query-derived\n'
        f'---\n\n'
    )
    with open(candidate, "w", encoding="utf-8") as f:
        f.write(fm + markdown_content)
    append_log_save(slug)


def _format_table(results: list) -> str:
    if not results:
        return "No results found.\n"
    rows = ["| Source | Score | Excerpt |", "|---|---|---|"]
    for r in results:
        source = r["source_path"]
        score = f"{r.get('hybrid_score', 0):.2f}"
        excerpt = r["text"][:120].replace("\n", " ").replace("|", "\\|")
        rows.append(f"| {source} | {score} | {excerpt} |")
    return "\n".join(rows)


def _format_marp(topic: str, results: list) -> str:
    if not results:
        return f"---\nmarp: true\n---\n\n# Context: {topic}\n\nNo results found.\n"
    slides = ["---\nmarp: true\n---", f"\n# Context: {topic}\n"]
    for r in results:
        path = r["source_path"]
        score = r.get("hybrid_score", 0)
        excerpt = r["text"][:300].replace("\n", " ")
        slides.append(f"\n---\n\n## {path} ({score:.2f})\n\n{excerpt}\n")
    return "\n".join(slides)


def _trace_source_path(path: str) -> str:
    try:
        resolved = Path(path).resolve()
        root = PROJECT_ROOT.resolve()
        if resolved == root or root in resolved.parents:
            return str(resolved.relative_to(root))
    except (OSError, ValueError):
        pass
    return path


def _result_trace_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    chunk_id = _result_chunk_id(result)
    payload = {
        "source_path": _trace_source_path(str(result.get("source_path", ""))),
        "hybrid_score": result.get("hybrid_score", 0),
    }
    if chunk_id is not None:
        payload["chunk_id"] = chunk_id
    for key in ("chunk_id", "heading", "keyword_score", "semantic_score"):
        if key in result and key not in payload:
            payload[key] = result[key]
    return payload


def _graph_trace_payload(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "path": _trace_source_path(str(node.get("path", ""))),
        "node_type": node.get("node_type", ""),
        "title": node.get("title", ""),
        "depth": node.get("depth", 0),
        "relation": node.get("relation", ""),
        "source_id": node.get("source_id", ""),
    }


def _result_chunk_id(result: Dict[str, Any]) -> str | None:
    chunk_id = result.get("chunk_id")
    if chunk_id is not None:
        return str(chunk_id)
    source_path = _trace_source_path(str(result.get("source_path", "")))
    text = result.get("text")
    if not source_path or text is None:
        return None
    encoded = f"{source_path}::root::{text}".encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Observed fusion weights from scripts.hybrid_search.search_hybrid
# (hybrid_score = 0.6 * keyword_score + 0.4 * semantic_score). Recorded, never applied.
_FUSION_WEIGHTS = {"fts": 0.6, "embedding": 0.4}


def _dedupe_sources(
    matches: List[Dict[str, Any]], max_per_source: int
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Cap ranked matches per source file BEFORE the limit budget is applied.

    R4b: the R1B miss analysis showed a single source's chunks taking 3 of 5
    context slots and pushing other expected sources below the cutoff. Order
    within the ranking is preserved; excess chunks are recorded as dropped
    with reason ``source_dedupe``. ``max_per_source <= 0`` disables capping.
    """
    if max_per_source <= 0:
        return list(matches), []
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for match in matches:
        source = str(match.get("source_path", ""))
        counts[source] = counts.get(source, 0) + 1
        if counts[source] > max_per_source:
            dropped.append({"chunk_id": _result_chunk_id(match), "reason": "source_dedupe"})
        else:
            kept.append(match)
    return kept, dropped


def _apply_budget(
    matches: List[Dict[str, Any]], limit: int
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split ranked matches into included (top `limit`) and budget-dropped.

    Observe-only: the included slice is identical to `matches[:limit]`, so
    ranking/selection is unchanged. The over-budget remainder is recorded as
    genuinely dropped with reason ``budget``.
    """
    included = matches[:limit]
    dropped = [
        {"chunk_id": _result_chunk_id(match), "reason": "budget"}
        for match in matches[limit:]
    ]
    return included, dropped


def _fusion_payload(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Record the existing hybrid fusion (weights + per-chunk ranks). Observe-only."""
    ranked = []
    for rank, result in enumerate(results, start=1):
        ranked.append(
            {
                "chunk_id": _result_chunk_id(result),
                "fts_score": result.get("keyword_score", 0),
                "embedding_score": result.get("semantic_score", 0),
                "fused_score": result.get("hybrid_score", 0),
                "rank": rank,
            }
        )
    return {
        "mode": "hybrid",
        "fusion": "weighted_sum",
        "weights": dict(_FUSION_WEIGHTS),
        "ranked": ranked,
    }


def _assembled_context_payload(
    results: List[Dict[str, Any]], dropped: List[Dict[str, Any]] | None = None
) -> Dict[str, Any]:
    dropped = dropped or []
    payload: Dict[str, Any] = {
        "included_chunk_ids": [
            chunk_id
            for chunk_id in (_result_chunk_id(result) for result in results)
            if chunk_id is not None
        ],
        "dropped": list(dropped),
    }
    if not dropped:
        # Empty must be asserted, not incidental: distinguishes "nothing dropped"
        # from "drop tracking absent".
        payload["dropped_reason"] = "no_dropped_context"
    return payload


def _write_retrieval_trace(
    *,
    command: str,
    query: str,
    mode: str,
    limit: int,
    results: List[Dict[str, Any]],
    dropped: List[Dict[str, Any]] | None = None,
    graph_enabled: bool = False,
    graph_depth: int = 0,
    graph_nodes: List[Dict[str, Any]] | None = None,
    trace_id: str | None = None,
) -> str:
    final_trace_id = create_trace_id(trace_id)
    events = [
        TraceEvent(
            event_id="evt-001",
            event_type="retrieval.query",
            timestamp=utc_now(),
            actor="zurvan",
            payload={
                "command": command,
                "query": query,
                "mode": mode,
                "limit": limit,
            },
        ),
        TraceEvent(
            event_id="evt-002",
            event_type="retrieval.result",
            timestamp=utc_now(),
            actor="zurvan",
            payload={
                "command": command,
                "result_count": len(results),
                "results": [_result_trace_payload(result) for result in results],
            },
        ),
    ]

    next_event_number = 3
    if mode == "hybrid":
        events.append(
            TraceEvent(
                event_id=f"evt-{next_event_number:03d}",
                event_type="retrieval.fusion",
                timestamp=utc_now(),
                actor="zurvan",
                payload=_fusion_payload(results),
            )
        )
        next_event_number += 1

    if command == "context":
        events.append(
            TraceEvent(
                event_id=f"evt-{next_event_number:03d}",
                event_type="context.assembled",
                timestamp=utc_now(),
                actor="zurvan",
                payload=_assembled_context_payload(results, dropped=dropped),
            )
        )
        next_event_number += 1

    if graph_enabled:
        nodes = graph_nodes or []
        events.append(
            TraceEvent(
                event_id=f"evt-{next_event_number:03d}",
                event_type="graph_context",
                timestamp=utc_now(),
                actor="zurvan",
                payload={
                    "enabled": True,
                    "depth": graph_depth,
                    "node_count": len(nodes),
                    "nodes": [_graph_trace_payload(node) for node in nodes],
                },
            )
        )

    record = TraceRecord(
        trace_id=final_trace_id,
        title=f"Retrieval context: {query}",
        summary=f"Opt-in retrieval trace for zurvan {command}.",
        events=events,
    )
    paths = TraceStore(project_root=PROJECT_ROOT).write(record)
    return str(paths.json_path)


def _compact_result(res: Dict[str, Any], snippet_len: int = 200) -> Dict[str, Any]:
    """Small, machine-parseable view of one search result (repo-relative path,
    scores, single-line snippet) — built for agent/LLM consumers."""
    compact = {
        "source_path": _trace_source_path(str(res.get("source_path", ""))),
        "hybrid_score": res.get("hybrid_score", 0),
        "snippet": " ".join((res.get("text") or "").split())[:snippet_len],
    }
    for key in ("heading", "keyword_score", "semantic_score"):
        if key in res:
            compact[key] = res[key]
    return compact


def search_memory(
    query: str,
    hybrid: bool = False,
    save: bool = False,
    trace: bool = False,
    trace_id: str | None = None,
    as_json: bool = False,
):
    """
    Search wiki and print list of matches (human text, or JSON with as_json).
    """
    results = _search_internal(query, hybrid, limit=10)
    lines = []
    for i, res in enumerate(results, 1):
        lines.append(
            f"{i}. {res['source_path']} | Score: {res.get('hybrid_score', 'N/A')} | Snippet: {res['text'][:100]}..."
        )
    if save:
        source_paths = [r["source_path"] for r in results]
        _save_synthesis(query, "\n".join(lines), source_paths)
    trace_path = None
    if trace:
        trace_path = _write_retrieval_trace(
            command="search",
            query=query,
            mode="hybrid" if hybrid else "keyword",
            limit=10,
            results=results,
            trace_id=trace_id,
        )
    if as_json:
        payload: Dict[str, Any] = {
            "query": query,
            "results": [_compact_result(r) for r in results],
        }
        if trace_path:
            payload["trace_path"] = trace_path
        print(json.dumps(payload, indent=2))
    else:
        print(f"Found {len(results)} matches for '{query}':\n")
        for line in lines:
            print(line)
        if trace_path:
            print(f"Trace written: {trace_path}")

def export_context(
    topic: str,
    limit: int = 10,
    hybrid: bool = False,
    graph: bool = False,
    depth: int = 1,
    save: bool = False,
    fmt: str = "markdown",
    trace: bool = False,
    trace_id: str | None = None,
    max_per_source: int = 2,
) -> str:
    """Exports a Markdown context bundle based on search results."""

    # Fetch a wider candidate pool, cap chunks per source (R4b), then budget
    # down to `limit`. Both drop classes are observable in the trace with
    # their own reasons (source_dedupe / budget).
    candidates = _search_internal(topic, hybrid, limit * 3)
    deduped, dedupe_dropped = _dedupe_sources(candidates, max_per_source)
    results, budget_dropped = _apply_budget(deduped, limit)
    dropped = dedupe_dropped + budget_dropped

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
            
    graph_nodes = []
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

    trace_line = ""
    trace_path = None
    if trace:
        trace_path = _write_retrieval_trace(
            command="context",
            query=topic,
            mode="hybrid" if hybrid else "keyword",
            limit=limit,
            results=results,
            dropped=dropped,
            graph_enabled=graph,
            graph_depth=depth if graph else 0,
            graph_nodes=graph_nodes,
            trace_id=trace_id,
        )
        trace_line = f"\n\nTrace written: {trace_path}"

    if fmt == "json":
        payload: Dict[str, Any] = {
            "topic": topic,
            "results": [_compact_result(r, snippet_len=300) for r in results],
            "graph": [_graph_trace_payload(n) for n in graph_nodes],
            "dropped_count": len(dropped),
        }
        if trace_path:
            payload["trace_path"] = trace_path
        return json.dumps(payload, indent=2)
    if fmt == "table":
        return _format_table(results) + trace_line
    elif fmt == "marp":
        return _format_marp(topic, results) + trace_line
    return base_output + trace_line

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
