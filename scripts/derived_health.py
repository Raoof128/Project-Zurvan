"""CURRENT, STALE or INVALID for each derived store (design §6.2).

    COHERENT+CURRENT    built_from_revision == knowledge revision
    COHERENT+STALE      built_from_revision <  knowledge revision
    INVALID             no truthful source snapshot, or metadata mismatch

Stale is normal. Only INVALID means "do not trust this store's provenance".
"""

import os
from pathlib import Path

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks

CURRENT = "COHERENT+CURRENT"
STALE = "COHERENT+STALE"
INVALID = "INVALID"


def _meta(kind, base) -> tuple:
    if kind == "graph":
        from scripts.graph_schema import read_graph_meta
        path = Path(base) / "data" / "graph.sqlite"
        return path, (read_graph_meta(path) if path.is_file() else None)
    if kind == "search":
        from scripts.rebuild_search_index import read_index_meta
        # ZURVAN_SEARCH_DB moves the index (the e2e scripts set it); otherwise
        # the store root resolved NOW. Not config.get_search_db_path(): its
        # PROJECT_ROOT is bound at import, so an explicit root - or a
        # ZURVAN_ROOT set after import - would describe the LIVE index instead
        # (caught by test_a_missing_store_is_invalid, 2026-09-28).
        override = os.environ.get("ZURVAN_SEARCH_DB")
        path = Path(override) if override else Path(base) / "data" / "search.sqlite"
        return path, (read_index_meta(path) if path.is_file() else None)
    raise ValueError(f"unknown derived store {kind!r}")


def store_health(kind, root=None) -> tuple:
    base = ks.store_root(root)
    path, meta = _meta(kind, base)
    if meta is None:
        return INVALID, None, None, f"{path.name} is missing"
    built = meta.get("built_from_revision")
    try:
        now = kr.current(base).revision
    except kr.LedgerError:
        return INVALID, built, None, "no ledger to compare against"
    if built is None:
        return INVALID, None, now, "no truthful source snapshot (unstamped build)"
    if int(built) > now:
        return INVALID, built, now, "stamp names a revision the ledger has not reached"
    if int(built) == now:
        return CURRENT, built, now, ""
    return STALE, built, now, f"{now - int(built)} revision(s) behind"
