"""An explicit handle for ONE knowledge store: which project, which root, and
which index files answer for it.

Audit 2026-10-22 F3: federation passed a project's root to `_search_internal`,
but hybrid mode ignored it and resolved the index through the process-global
`ZURVAN_SEARCH_DB` / canonical path — the home index — while the caller
labelled the hits with the requested project. The store a query reads must be
named, not inferred from the environment, so every lane receives this handle
and result provenance is bound to it.

The override's meaning is fixed here: `ZURVAN_SEARCH_DB` says where the HOME
store's index lives. It never applies to another project's root.
"""
from dataclasses import dataclass
from pathlib import Path

from scripts.config import PROJECT_ROOT, get_search_db_path


@dataclass(frozen=True)
class StoreHandle:
    project: str | None
    root: Path
    search_db: Path
    graph_db: Path


def resolve_store(root: str | Path | None = None, project: str | None = None) -> StoreHandle:
    if root is None:
        base = Path(PROJECT_ROOT)
        return StoreHandle(project, base, Path(get_search_db_path()), base / "data" / "graph.sqlite")
    base = Path(root)
    return StoreHandle(project, base, base / "data" / "search.sqlite", base / "data" / "graph.sqlite")
