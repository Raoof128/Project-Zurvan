import os
from scripts import mcp_security
from scripts.mcp_security import readable_target
from scripts.graph_query import get_stats
from scripts.config import PROJECT_ROOT

UNSAFE_PATH_ERROR = "Error: Path {} failed safety checks (no absolute paths, no '../', raw/ blocked)."


def _allow_raw() -> bool:
    return os.environ.get("ZURVAN_MCP_ALLOW_RAW_READ", "0") == "1"


def get_static_resource(path: str) -> str:
    """Reads a static text file if it exists.

    A repo-relative `path` is held to the read allow-list and opened by its
    canonical path, like every other reader: a symlink planted at
    `wiki/log.md` used to be served whatever it pointed at."""
    if os.path.isabs(path):
        abs_path = path
        target = path if os.path.exists(path) else None
    else:
        abs_path = str(PROJECT_ROOT / path)
        if not os.path.lexists(abs_path):
            return f"Error: Resource {abs_path} not found."
        target = readable_target(path, False, PROJECT_ROOT)
        if target is None:
            return UNSAFE_PATH_ERROR.format(path)
    if target is None or not os.path.exists(target):
        return f"Error: Resource {abs_path} not found."
    try:
        with open(target, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading resource: {e}"

def resource_wiki_index() -> str:
    return get_static_resource("wiki/index.md")

def resource_wiki_log() -> str:
    return get_static_resource("wiki/log.md")

def resource_wiki_overview() -> str:
    return get_static_resource("wiki/overview.md")

def resource_wiki_open_questions() -> str:
    return get_static_resource("wiki/open-questions.md")

def resource_graph_stats() -> str:
    try:
        stats = get_stats()
        return f"Graph stats: {stats['nodes']} nodes, {stats['edges']} edges"
    except Exception as e:
        return f"Error: {e}"

def resource_eval_baseline() -> str:
    return get_static_resource("eval/README.md")

def read_file_slice(path: str, offset: int = 0, limit: int = 24000) -> str:
    """Return one character slice of a safe file, with a header naming the next.

    The size guard in resource_file refuses an oversized file outright, which
    made large pages unreadable rather than merely expensive. Slicing keeps the
    per-call bound while leaving every byte reachable.
    """
    abs_path = readable_target(path, _allow_raw(), PROJECT_ROOT)
    if abs_path is None:
        return UNSAFE_PATH_ERROR.format(path)
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
    # The allow-list, judged on stored names; the canonical path is opened.
    abs_path = readable_target(path, _allow_raw(), PROJECT_ROOT)
    if abs_path is None:
        return UNSAFE_PATH_ERROR.format(path)
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
