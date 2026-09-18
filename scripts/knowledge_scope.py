#!/usr/bin/env python3
"""What counts as authoritative knowledge. Asked once, answered once.

Revision tracking and manifest generation must share this answer. When they
disagreed, a write into `wiki/traces/` advanced a revision whose manifest was
defined not to change — a revision that means nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Generated subtrees of `wiki/`. A snapshot captures them for forensics, but
#: they do not define a revision: `scripts/trace_writer.py:20` writes
#: `wiki/traces/` with `write_text`, bypassing `safe_write` and the mutation
#: barrier. Measured: 25 of 4,430 wiki files, 0.6%.
DERIVED_SUBDIRS = ("traces",)

#: Bump when the scope changes. Every manifest digest is a property of it, so a
#: silent change would reinterpret every stored revision.
SCOPE_VERSION = 1


def store_root(root=None) -> Path:
    """Resolve the store root AT CALL TIME, never at import.

    `scripts.config.PROJECT_ROOT` is bound when that module is imported, and
    `monkeypatch.setenv("ZURVAN_ROOT", ...)` afterwards does not move it.
    Measured: with the module imported first, `config.PROJECT_ROOT` stays on
    the real repository while `safe_write.get_project_root()` follows the
    override — so a ledger built on the former writes into PRODUCTION during
    the suite, where `tests/conftest.py` (which watches `wiki/`, not `data/`)
    would never see it. This mirrors `safe_write.get_project_root`.
    """
    if root is not None:
        return Path(root).resolve()
    return Path(
        os.environ.get("ZURVAN_ROOT") or Path(__file__).parent.parent
    ).resolve()


def is_authoritative(target, root=None) -> bool:
    """True for a file whose bytes define the knowledge state.

    Raises `ValueError` for a relative path rather than resolving it: measured,
    `Path("a").resolve()` yields a path inside the real repository during a
    test run, so silently accepting one would let a fixture reach production.
    """
    path = Path(target)
    if not path.is_absolute():
        raise ValueError(
            f"authority is decided on absolute paths; got {target!r}. "
            "A relative path resolves against the process working directory, "
            "which during a test run is the real repository.")
    base = store_root(root)
    try:
        rel = path.resolve().relative_to(base)
    except (ValueError, OSError):
        return False
    if len(rel.parts) < 1 or rel.parts[0] != "wiki":
        return False
    return len(rel.parts) < 2 or rel.parts[1] not in DERIVED_SUBDIRS


def authoritative_files(root=None) -> list:
    """Every file whose bytes define the knowledge state.

    Broader than indexable Markdown — attachments count, because a snapshot
    that excluded them cannot answer questions asked of it later. Narrower than
    the whole tree, because generated output is not authoritative. A snapshot's
    clone is deliberately a superset of this set.

    `os.walk` with a structural prune, not `Path.rglob` plus a per-file
    predicate. Measured on the live store: rglob with `resolve()` per file costs
    **238 ms** to list and another **226 ms** to hash, against **31 ms**
    end-to-end for the walk — and `commit_mutation` runs this on every writing
    command. Pruning the derived directories in `dirs[:]` also means their
    contents are never stat'ed at all, which is both faster and exactly
    equivalent to filtering them afterwards.
    """
    base = store_root(root)
    wiki = base / "wiki"
    if not wiki.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(wiki):
        if Path(dirpath) == wiki:
            dirnames[:] = [d for d in dirnames if d not in DERIVED_SUBDIRS]
        dirnames.sort()
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
    return out
