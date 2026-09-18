#!/usr/bin/env python3
"""Freeze the measurement input, not the store.

(Named `measurement_snapshot`, not `snapshot`: the design spec proposed
`scripts/snapshot.py` and that module already exists — it builds the `.tar.gz`
export behind `zurvan snapshot`. Two unrelated meanings of the same word, and
the CLI imports the other one.)

Two designs in this repository are each correct and jointly impossible: Zurvan
is shared, live, cross-project memory that any session may write to, and Zurvan
measures itself against that same store. Twice — 2026-09-17 at 14:57 and
2026-09-18 at 15:21 — a session working on an unrelated project recorded one
decision, it landed inside a gate's pytest window, the corpus-mutation guard
fired, and a twenty-minute run aborted with nothing wrong in any code under
test. Both times the guard was right: the corpus did move. The gate was still
useless.

Waiting for a quiet store does not scale. It makes a local quality gate depend
on every other project's session and on a human remembering.

## The invariant

> Shared knowledge may remain live and writable; measurements must not depend
> on the shared store remaining stationary.

## Why this is cheap

`eval_search --hybrid` never reads `wiki/`. It reads the index and compares
`source_path` strings. The corpus reaches the measurement through exactly one
file, `data/search.sqlite` — and since 2026-09-17 a rebuild builds into a temp
file and promotes it with a single `os.replace()`, so the canonical path
acquires a NEW INODE and the old bytes are never mutated in place.

A hard link taken before the swap therefore keeps pointing at precisely the
bytes that existed at capture time, for as long as the link exists, while
production moves on. O(1), no copy, no extra disk — the index is 738 MB.

## What this deliberately does NOT do

Block writers. A global write lease is the right tool for an operation that
genuinely needs a stationary store — an atomic production rebuild, a promotion
— and the wrong tool for an ordinary test run. A twenty-minute lock on a shared
brain every time someone runs the suite will be routed around within a week,
and then removed.

## Identity

A result that cannot name its input is not a measurement. Three retrieval
baselines in this project carry chunk counts of 184,002 / 184,231 / 184,241 with
no way to tell whether they scored the same corpus. Every snapshot records what
it froze, and the evaluation reports it alongside the number.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_DIRNAME = "snapshots"
INDEX_NAME = "search.sqlite"


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from scripts.config import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def snapshots_dir(root=None) -> Path:
    return _root(root) / "data" / SNAPSHOT_DIRNAME


def _canonical_index(root=None) -> Path:
    """The index this snapshot would freeze.

    Resolved through the same helper every other component uses, so
    `ZURVAN_SEARCH_DB` moves the snapshot with everything else rather than
    leaving it pointed at the production file during a redirected run.
    """
    from scripts.config import get_search_db_path
    if root is None:
        return Path(get_search_db_path())
    override = os.environ.get("ZURVAN_SEARCH_DB")
    return Path(override) if override else Path(root) / "data" / INDEX_NAME


# ── identity ──────────────────────────────────────────────────────────────────

def _corpus_manifest_sha(root: Path) -> str:
    """(relpath, size, mtime_ns, ino) over every indexable file. Reads no content.

    Delegates to `knowledge_manifest` so there is one implementation: Z0 needs
    this digest shape as a correctness mechanism, and two implementations of
    "has the corpus moved" would drift.

    `st_ino` and a scope-version prefix were added in Z0, so the value changes
    once at that commit; its meaning does not, and no test asserts a literal
    digest. This caller keeps its diagnostic contract: a snapshot must still be
    capturable on a store whose corpus cannot be fully scanned, so the
    fail-closed error is caught HERE and reported as an empty digest rather
    than failing the capture.
    """
    from scripts.knowledge_manifest import ManifestUnavailable, indexable_manifest
    try:
        return indexable_manifest(root)
    except ManifestUnavailable:
        return ""


def index_identity(index_path, deep: bool = False) -> dict:
    """What this index IS, cheaply enough to do on every capture.

    `chunk_id_set_sha` costs ~0.5 s on the 184k-chunk production index and
    proves the set of indexed chunks is unchanged. `vector_bytes_sha` costs
    ~4.6 s because it reads all 738 MB of vectors, so it is opt-in: use it when
    the claim being made is specifically about the vectors.
    """
    path = Path(index_path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ident: dict = {}
        ident["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        ident["sources"] = conn.execute(
            "SELECT COUNT(DISTINCT source_path) FROM chunks").fetchone()[0]
        h = hashlib.sha256()
        for (chunk_id,) in conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id"):
            h.update(str(chunk_id).encode())
            h.update(b"\x00")
        ident["chunk_id_set_sha"] = h.hexdigest()
        spaces = [f"{p}/{m}/{d}" for p, m, d in conn.execute(
            "SELECT DISTINCT provider, model, dimension FROM embeddings ORDER BY 1,2,3")]
        # A list, not a string: more than one space means cosine scores are
        # being compared across incompatible geometries, and a measurement
        # taken there means nothing. Recording it flat would hide that.
        ident["embedding_space"] = spaces
        if deep:
            hv = hashlib.sha256()
            for (vector,) in conn.execute(
                    "SELECT vector FROM embeddings ORDER BY chunk_id"):
                hv.update(vector)
            ident["vector_bytes_sha"] = hv.hexdigest()
        return ident
    finally:
        conn.close()


# ── the snapshot ──────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """An immutable view of the measurement input. Release it when done."""

    snapshot_id: str
    path: Path
    index_path: Path
    identity: dict = field(default_factory=dict)
    _released: bool = False

    def __enter__(self) -> "Snapshot":
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def release(self) -> None:
        """Drop the link. Idempotent, and never touches the canonical index.

        `shutil.rmtree` on the snapshot directory removes the LINK, not the
        inode: the canonical file keeps its own name and the bytes survive for
        as long as anything still references them.
        """
        if self._released:
            return
        self._released = True
        shutil.rmtree(self.path, ignore_errors=True)

    @property
    def released(self) -> bool:
        return self._released


def capture(root=None, label: str = "measurement", deep: bool = False) -> Snapshot:
    """Freeze the current index by hard-linking it. O(1).

    Raises `FileNotFoundError` if there is no index. Failing closed matters
    here: a snapshot of nothing must never read as a snapshot of a clean store.
    """
    base = _root(root)
    index = _canonical_index(root)
    if not index.is_file():
        raise FileNotFoundError(
            f"no search index to snapshot at {index}; build one before measuring")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_id = f"{label}-{stamp}-{os.getpid()}"
    directory = snapshots_dir(base) / snapshot_id
    directory.mkdir(parents=True, exist_ok=False)
    linked = directory / INDEX_NAME
    try:
        os.link(index, linked)
    except OSError:
        # Cross-device, or a filesystem without hard links. Copying is the
        # correct fallback and is honestly slower; it is never silent, because
        # `link` in the identity records which one happened.
        shutil.copy2(index, linked)
        linked_by = "copy"
    else:
        linked_by = "hardlink"

    identity = {
        "snapshot_id": snapshot_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "index_path": str(index),
        "index_inode": linked.stat().st_ino,
        "link": linked_by,
        "corpus_manifest_sha": _corpus_manifest_sha(base),
    }
    identity.update(index_identity(linked, deep=deep))
    return Snapshot(snapshot_id=snapshot_id, path=directory,
                    index_path=linked, identity=identity)


def sweep_abandoned(root=None) -> list:
    """Remove snapshot directories owned by processes that are gone.

    A SIGKILL cannot clean up after itself. Directories owned by a LIVE process
    are left alone — that is a measurement in progress, and deleting its input
    mid-run is exactly the failure this module exists to prevent.

    Only the snapshot directory is removed. The link inside it is a second name
    for the index inode, never the canonical name, so the sweep can never
    delete the production index.
    """
    directory = snapshots_dir(root)
    if not directory.is_dir():
        return []
    swept = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        suffix = child.name.rsplit("-", 1)[-1]
        if not suffix.isdigit():
            continue                       # not ours; leave it rather than guess
        pid = int(suffix)
        if pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
            continue                       # a live measurement owns this
        except (OSError, ProcessLookupError):
            pass
        shutil.rmtree(child, ignore_errors=True)
        swept.append(str(child))
    return swept


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="manual")
    parser.add_argument("--deep", action="store_true",
                        help="also hash every vector (~4.6s on the production index)")
    parser.add_argument("--sweep", action="store_true",
                        help="remove snapshots left by processes that are gone")
    parser.add_argument("--path-only", action="store_true",
                        help="print only the snapshot index path, for shell callers")
    args = parser.parse_args()

    if args.sweep:
        for path in sweep_abandoned():
            print(f"swept {path}")
        raise SystemExit(0)

    s = capture(label=args.label, deep=args.deep)
    if args.path_only:
        # The caller owns the release: a snapshot captured by one process and
        # used by another cannot be freed by a context manager.
        print(s.index_path)
        raise SystemExit(0)
    print(json.dumps(s.identity, indent=2))
    print(f"\nSnapshot index: {s.index_path}")
    print("Release it with:  rm -rf " + str(s.path))
