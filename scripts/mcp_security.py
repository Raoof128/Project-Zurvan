import fcntl
import os
import sys
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


# ---------------------------------------------------------------------------
# The read allow-list (IOU-5), the same surface as the Rust server's
# `rust/zurvan-mcp/src/security.rs`.
#
# On 2026-10-19 this server returned `.git/config`, `.claude/settings.json`,
# `.remember/now.md` and an ingested legal document under `sources/` through
# `zurvan_read_page`: `is_safe_path` is a deny-list of three rules. The
# readable surface is now what the search index covers plus the four repo-root
# documents, and it is judged on the names the filesystem STORES. APFS folds
# case with full Unicode rules (`wiki/trace\u017f` opens `traces`), so any
# check on the requested spelling passes what the filesystem opens.

ROOT_DOCS = ("README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md")
#: Under docs/ but excluded from the index (scripts/chunk.py) so retriever
#: reports cannot feed the retriever; they may quote the retired instrument.
EXCLUDED_DOCS = (("evaluation", "retrieval"),)


def _stored_path(path: str) -> str | None:
    """The existing `path` as the filesystem names it: symlinks followed and,
    on macOS, the stored spelling of every component (F_GETPATH on an open
    descriptor). None when it cannot be opened."""
    if sys.platform == "darwin":
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return None
        try:
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)
        except OSError:
            return None
        finally:
            os.close(fd)
        return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
    return os.path.realpath(path) if os.path.exists(path) else None


def canonical_lenient(path: str) -> str | None:
    """The longest existing ancestor as stored, then the missing remainder
    normalised lexically. None (fail closed) for a dangling or looping
    symlink, and for `..` after a component that does not exist."""
    parts = Path(path).parts
    base = None
    existing = 0
    probe = ""
    for i, part in enumerate(parts):
        probe = os.path.join(probe, part) if probe else part
        if os.path.exists(probe):
            stored = _stored_path(probe)
            if stored is None:
                return None
            base, existing = stored, i + 1
        else:
            if os.path.islink(probe):
                return None
            break
    if base is None:
        return None
    acc = base
    for part in parts[existing:]:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        acc = os.path.join(acc, part)
    return acc


def _named(component: str, name: str) -> bool:
    return component.lower() == name


def _allowed(parts: tuple, allow_raw: bool) -> bool:
    if not parts:
        return False
    first = parts[0]
    if _named(first, "raw"):
        return allow_raw
    if first == "wiki":
        return not any(_named(p, "traces") for p in parts[1:])
    if first == "docs":
        return not any(len(parts) > len(ex) and all(_named(parts[1 + i], n) for i, n in enumerate(ex))
                       for ex in EXCLUDED_DOCS)
    if first == "eval":
        return True
    return len(parts) == 1 and first in ROOT_DOCS


def readable_target(target_path: str, allow_raw: bool = False, root=None) -> str | None:
    """The canonical absolute path a reader may open, or None. Allowed names
    compare exactly (a case variant of `wiki` is judged by what it opens);
    excluded names (`raw`, `traces`, `docs/evaluation/retrieval`) compare
    case-insensitively."""
    if not target_path or os.path.isabs(target_path):
        return None
    base = _stored_path(str(root if root is not None else PROJECT_ROOT))
    if base is None:
        return None
    resolved = canonical_lenient(os.path.join(base, target_path))
    if resolved is None:
        return None
    try:
        rel = Path(resolved).relative_to(base)
    except ValueError:
        return None
    return resolved if _allowed(rel.parts, allow_raw) else None


def is_readable_path(target_path: str, allow_raw: bool = False, root=None) -> bool:
    """Safe AND on the read allow-list: `wiki/` (no `traces` segment),
    `docs/` (minus `docs/evaluation/retrieval/`), `eval/`, the four root
    documents; `raw/` only with the flag."""
    return readable_target(target_path, allow_raw, root) is not None
