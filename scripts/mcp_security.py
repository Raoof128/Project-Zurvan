import os
from pathlib import Path

from scripts.config import PROJECT_ROOT

def is_safe_path(target_path: str, allow_raw: bool = False) -> bool:
    """
    Validates if a path is safe to read/write.

    Paths are interpreted relative to the Zurvan repo root (PROJECT_ROOT), not
    the current working directory, so the check is correct no matter where the
    MCP server process was launched from.
    """
    # 1. No absolute paths
    if os.path.isabs(target_path):
        return False

    # 2. No directory traversal — must resolve to somewhere inside the repo root
    try:
        base_dir = PROJECT_ROOT
        target = (base_dir / target_path).resolve()

        # Must be relative to base_dir
        if not target.is_relative_to(base_dir):
            return False

    except Exception:
        return False

    # 3. No raw/ access unless explicitly allowed
    rel_path = os.path.relpath(str(target), str(base_dir))

    if rel_path == "raw" or rel_path.startswith("raw" + os.sep):
        if not allow_raw:
            return False

    return True

def enforce_read_only(func):
    """
    Decorator to block write tools if MCP is in read-only mode.
    """
    def wrapper(*args, **kwargs):
        readonly = os.environ.get("ZURVAN_MCP_READONLY", "1")
        if readonly == "1":
            return "Error: MCP server is in read-only mode (ZURVAN_MCP_READONLY=1). Write operation blocked."
        return func(*args, **kwargs)
    return wrapper
