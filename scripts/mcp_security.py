import os
from pathlib import Path

def is_safe_path(target_path: str, allow_raw: bool = False) -> bool:
    """
    Validates if a path is safe to read/write.
    """
    # 1. No absolute paths
    if os.path.isabs(target_path):
        return False
        
    # 2. No directory traversal
    try:
        base_dir = Path(".").resolve()
        target = Path(target_path).resolve()
        
        # Must be relative to base_dir
        if not target.is_relative_to(base_dir):
            return False
            
    except Exception:
        return False
        
    # 3. No raw/ access unless explicitly allowed
    target_str = str(target)
    base_str = str(base_dir)
    rel_path = os.path.relpath(target_str, base_str)
    
    if rel_path.startswith("raw/") or rel_path == "raw":
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
