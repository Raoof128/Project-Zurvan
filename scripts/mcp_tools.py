import os
import sys
from typing import List, Optional

from scripts.mcp_security import enforce_read_only, is_safe_path
from scripts.context_export import search_memory, export_context
from scripts.graph_query import get_stats, get_neighbours
from scripts.graph_context import expand_graph_context
from scripts.memory import add_decision, add_note, add_claim, add_question
import subprocess

def tool_zurvan_search(query: str, hybrid: bool = True, limit: int = 10) -> str:
    """Searches Zurvan memory."""
    try:
        from scripts.context_export import _search_internal
        results = _search_internal(query, hybrid, limit)
        if not results:
            return "No matches found."
        output = []
        for r in results:
            output.append(f"- {r['source_path']} (Score: {r.get('hybrid_score', 'N/A')})")
        return "\n".join(output)
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_context(topic: str, hybrid: bool = True, graph: bool = True, limit: int = 10) -> str:
    """Exports a Markdown context bundle."""
    try:
        return export_context(topic, limit, hybrid, graph, 1)
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_graph_stats() -> str:
    """Returns graph stats."""
    try:
        stats = get_stats()
        return f"Graph stats: {stats['nodes']} nodes, {stats['edges']} edges"
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_graph_neighbours(path_or_node_id: str, depth: int = 1) -> str:
    """Shows neighbours of a node."""
    try:
        neighbours = get_neighbours(path_or_node_id)
        if not neighbours:
            return "No neighbours found."
        output = []
        for n in neighbours:
            output.append(f"{n['from_title']} --[{n['edge_type']}]--> {n['to_title']}")
        return "\n".join(output)
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_graph_expand(path_or_node_id: str, depth: int = 2) -> str:
    """Expands graph neighbours for context."""
    try:
        items = expand_graph_context([path_or_node_id], depth)
        if not items:
            return "No graph neighbours found."
        output = []
        for i in items:
            output.append(f"[{i['depth']}] {i['title']} ({i['node_type']}) - {i['relation']}")
        return "\n".join(output)
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_eval_search(hybrid: bool = True, min_top3: float = 0.6) -> str:
    """Evaluates search retrieval against gold set."""
    try:
        cmd = ["python", "scripts/eval_search.py", "--min-top3", str(min_top3)]
        if hybrid:
            cmd.append("--hybrid")
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"

def tool_zurvan_validate_gold() -> str:
    """Validates gold dataset."""
    try:
        cmd = ["python", "scripts/eval_search.py", "--validate"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return f"Error: {str(e)}"

@enforce_read_only
def tool_zurvan_remember(type: str, title: str, body: str, tags: List[str]) -> str:
    """Remembers a project note."""
    if add_note(title, body, tags):
        return f"Note added successfully: {title}"
    return "Failed to add note."

@enforce_read_only
def tool_zurvan_decision_add(title: str, reason: str, status: str, tags: List[str]) -> str:
    """Manages decisions."""
    if add_decision(title, reason, status, tags):
        return f"Decision added successfully: {title}"
    return "Failed to add decision."

@enforce_read_only
def tool_zurvan_claim_add(text: str, source: str, evidence: str, confidence: str, tags: List[str]) -> str:
    """Manages claims."""
    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"
    if not is_safe_path(source, allow_raw=allow_raw):
        return "Error: Source path fails safety checks (e.g. no absolute paths, no ../, no raw/ without flag)."
    
    if add_claim(text, source, evidence, confidence, tags):
        return f"Claim added successfully: {text}"
    return "Failed to add claim. Ensure evidence exists verbatim in the source file."

@enforce_read_only
def tool_zurvan_question_add(question: str, reason: str, tags: List[str]) -> str:
    """Manages open questions."""
    if add_question(question, reason, tags):
        return f"Question added successfully: {question}"
    return "Failed to add question."
