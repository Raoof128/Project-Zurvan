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

    # 3. No raw/ access unless explicitly allowed. The comparison is
    # case-insensitive on the top-level component: on a case-insensitive
    # filesystem (macOS default) "Raw/secret.md" resolves to the real raw/
    # directory, so a case-sensitive check would let an agent read untrusted
    # raw content by changing case.
    rel_path = os.path.relpath(str(target), str(base_dir))
    parts = Path(rel_path).parts
    if parts and parts[0].lower() == "raw":
        if not allow_raw:
            return False

    return True

def enforce_read_only(func):
    """
    Decorator to block write tools unless MCP is explicitly in write mode.

    Fails closed: writes are permitted ONLY when ZURVAN_MCP_READONLY is exactly
    "0". Any other value — the "1" default, an unset var, or a well-meaning but
    wrong "true"/"yes"/"" — keeps the server read-only, so a misconfigured
    variable can never silently open write access.
    """
    def wrapper(*args, **kwargs):
        readonly = os.environ.get("ZURVAN_MCP_READONLY", "1").strip()
        if readonly != "0":
            return "Error: MCP server is in read-only mode (set ZURVAN_MCP_READONLY=0 to enable writes). Write operation blocked."
        return func(*args, **kwargs)
    return wrapper
