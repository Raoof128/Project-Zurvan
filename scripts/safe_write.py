import os
import yaml
from pathlib import Path

def get_project_root() -> Path:
    # Assuming this script is in scripts/, root is one level up.
    # `ZURVAN_ROOT` redirects the write boundary for tests and smokes — see the
    # note on PROJECT_ROOT in scripts/config.py. It must be honoured HERE too:
    # this function is the gate that decides whether a write is inside the
    # store, so a redirected root that this ignored would simply refuse to write.
    return Path(os.environ.get("ZURVAN_ROOT") or Path(__file__).parent.parent).resolve()

def is_safe_path(target_path: Path) -> bool:
    root = get_project_root()
    raw_dir = root / "raw"
    
    try:
        # Resolve path to check for traversal
        resolved_target = target_path.resolve()
        
        # 1. Must be under project root
        if root not in resolved_target.parents and resolved_target != root:
            return False
            
        # 2. Must NOT be under raw/
        if raw_dir in resolved_target.parents or resolved_target == raw_dir:
            return False
            
        return True
    except Exception:
        return False

def escape_yaml_string(s: str) -> str:
    """Safely escape a string for YAML frontmatter, on a single line.

    ``width`` is set very high so PyYAML never line-wraps a long value: the
    frontmatter here is consumed by naive line-by-line parsers
    (``graph_build.parse_frontmatter``, ``wiki_merge._parse_fm``) that would
    truncate a value split across lines.
    """
    if not s:
        return ""
    return yaml.dump(s, default_style='"', width=2**20, allow_unicode=True).strip()

def _stamp(target: Path, action: str) -> None:
    """Record WHO wrote this, so the session guard can tell a test contaminating
    production from another repository's session recording a decision.

    The write has ALREADY succeeded by the time this runs, and the whole call is
    swallowed: a guard that loses someone's decision page in order to record
    that a decision page was written is worse than no guard. `writelog.record`
    is defensive too — this is the second layer, because the recorder is a
    module attribute and can be replaced.
    """
    try:
        from scripts import writelog
        writelog.record(target, action)
    except Exception:
        pass


def write_file_safely(file_path: str, content: str) -> bool:
    """
    Writes content to file_path after checking security rules.
    Returns True on success, False if security violation.
    """
    target = Path(file_path)
    
    if not is_safe_path(target):
        return False
        
    # Ensure directory exists
    target.parent.mkdir(parents=True, exist_ok=True)
    
    with open(target, 'w', encoding='utf-8') as f:
        f.write(content)

    _stamp(target, "write")
    return True

def append_file_safely(file_path: str, content: str) -> bool:
    """
    Appends content to file_path after checking security rules.
    """
    target = Path(file_path)
    
    if not is_safe_path(target):
        return False
        
    target.parent.mkdir(parents=True, exist_ok=True)
    
    with open(target, 'a', encoding='utf-8') as f:
        f.write(content)

    _stamp(target, "append")
        
    return True
