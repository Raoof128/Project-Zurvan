"""CURRENT, STALE or INVALID for each derived store (design §6.2).

    COHERENT+CURRENT    built_from_revision == knowledge revision
    COHERENT+STALE      built_from_revision <  knowledge revision
    INVALID             no truthful source snapshot, or metadata mismatch

Stale is normal. Only INVALID means "do not trust this store's provenance".
"""

import os
import sqlite3
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


def stamp_verdict(meta, revision, manifest_digest) -> tuple:
    """(state, reason) for one store's stamps against the ledger at `revision`.

    Pure. `store_health` reads the live store; a measurement capture reads the
    FROZEN copy (design §7.1 step 6). Both come here, so a stamp cannot be
    coherent for one and not the other.
    """
    built = meta.get("built_from_revision")
    if built is None:
        return INVALID, "no truthful source snapshot (unstamped build)"
    try:
        built_rev = int(built)
    except ValueError:
        return INVALID, f"stamp {built!r} is not a revision"
    if built_rev > revision:
        return INVALID, "stamp names a revision the ledger has not reached"
    if built_rev == revision:
        stamped = meta.get("built_from_manifest")
        if stamped and manifest_digest and stamped != manifest_digest:
            return INVALID, "stamp's manifest is not the ledger's at that revision"
        return CURRENT, ""
    return STALE, f"{revision - built_rev} revision(s) behind"


def store_health(kind, root=None) -> tuple:
    base = ks.store_root(root)
    try:
        path, meta = _meta(kind, base)
    except (sqlite3.Error, OSError) as exc:
        # A graph built before graph_meta existed, a corrupt file: never a
        # crash in `knowledge status` (final review, 2026-09-28).
        return INVALID, None, None, f"{kind} store unreadable: {exc}"
    if meta is None:
        return INVALID, None, None, f"{path.name} is missing"
    built = meta.get("built_from_revision")
    try:
        ledger = kr.current(base)
    except kr.LedgerError:
        return INVALID, built, None, "no ledger to compare against"
    now = ledger.revision
    state, reason = stamp_verdict(meta, now, ledger.manifest_digest)
    if state == INVALID:
        return INVALID, built, now, reason
    note = "; ledger is SUSPECT - reconcile" if ledger.state != kr.CLEAN else ""
    return state, built, now, (reason + note).lstrip("; ")
