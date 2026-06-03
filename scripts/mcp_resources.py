import os
from scripts.mcp_security import is_safe_path
from scripts.graph_query import get_stats
from scripts.config import PROJECT_ROOT

def get_static_resource(path: str) -> str:
    """Reads a static text file if it exists."""
    if not os.path.exists(path):
        return f"Error: Resource {path} not found."
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading resource: {e}"

def resource_wiki_index() -> str:
    return get_static_resource(str(PROJECT_ROOT / "wiki" / "index.md"))

def resource_wiki_log() -> str:
    return get_static_resource(str(PROJECT_ROOT / "wiki" / "log.md"))

def resource_wiki_overview() -> str:
    return get_static_resource(str(PROJECT_ROOT / "wiki" / "overview.md"))

def resource_wiki_open_questions() -> str:
    return get_static_resource(str(PROJECT_ROOT / "wiki" / "open-questions.md"))

def resource_graph_stats() -> str:
    try:
        stats = get_stats()
        return f"Graph stats: {stats['nodes']} nodes, {stats['edges']} edges"
    except Exception as e:
        return f"Error: {e}"

def resource_eval_baseline() -> str:
    return get_static_resource(str(PROJECT_ROOT / "eval" / "README.md"))

def resource_file(path: str) -> str:
    """Dynamic resource reader for safe relative paths."""
    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"
    if not is_safe_path(path, allow_raw=allow_raw):
        return f"Error: Path {path} failed safety checks."
    
    if not os.path.exists(path):
        return f"Error: File {path} not found."
        
    # Guard max file size (e.g., 256 KB)
    try:
        size = os.path.getsize(path)
        if size > 256 * 1024:
            return "Error: File too large (> 256 KB)."
            
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        return "Error: File appears to be binary."
    except Exception as e:
        return f"Error reading file: {e}"
