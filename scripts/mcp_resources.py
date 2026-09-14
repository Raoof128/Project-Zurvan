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

def read_file_slice(path: str, offset: int = 0, limit: int = 24000) -> str:
    """Return one character slice of a safe file, with a header naming the next.

    The size guard in resource_file refuses an oversized file outright, which
    made large pages unreadable rather than merely expensive. Slicing keeps the
    per-call bound while leaving every byte reachable.
    """
    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"
    if not is_safe_path(path, allow_raw=allow_raw):
        return f"Error: Path {path} failed safety checks (no absolute paths, no '../', raw/ blocked)."

    abs_path = str(PROJECT_ROOT / path)
    if not os.path.exists(abs_path):
        return f"Error: File {path} not found. Locate it with zurvan_search first."

    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            fh.read(offset)
            chunk = fh.read(limit)
            more = bool(fh.read(1))
    except UnicodeDecodeError:
        return "Error: File appears to be binary."
    except Exception as e:
        return f"Error reading file: {e}"

    if not chunk:
        return f"[end of {path} — offset {offset} is past the end]"
    header = f"[{path} — characters {offset}-{offset + len(chunk)}]"
    if more:
        header += f" More: call again with offset={offset + len(chunk)}."
    return header + "\n" + chunk


def resource_file(path: str) -> str:
    """Dynamic resource reader for safe relative paths (relative to PROJECT_ROOT)."""
    allow_raw = os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"
    if not is_safe_path(path, allow_raw=allow_raw):
        return f"Error: Path {path} failed safety checks (no absolute paths, no '../', raw/ blocked)."

    # Resolve relative to the repo root so reads are CWD-independent.
    abs_path = str(PROJECT_ROOT / path)
    if not os.path.exists(abs_path):
        return f"Error: File {path} not found. Locate it with zurvan_search first."

    # Guard max file size (e.g., 256 KB)
    try:
        size = os.path.getsize(abs_path)
        if size > 256 * 1024:
            return ("Error: File too large (> 256 KB). Read it in slices with "
                    "zurvan_read_page(path, offset=0) and advance the offset it returns.")

        with open(abs_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        return "Error: File appears to be binary."
    except Exception as e:
        return f"Error reading file: {e}"
