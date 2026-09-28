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
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
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


# ── components ────────────────────────────────────────────────────────────────

FULL_TREE = "full_tree"
SINGLE_FILE = "single_file"
EXCLUDES_DERIVED = "excludes_derived_subtrees"
WHOLE_COMPONENT = "whole_component"

_SIDECARS = ("-journal", "-wal", "-shm")

#: How long a capture waits for an in-flight writer before refusing. Bounded on
#: purpose: an unbounded wait against a continuously written store is a hang,
#: and a hang is worse than a refusal (design §7.1).
_LOCK_TIMEOUT_S = 5.0


class SnapshotComponentError(RuntimeError):
    """A component could not be frozen honestly. Fails closed: a snapshot
    missing a component it claimed would describe a corpus that never was."""


class MissingComponentError(SnapshotComponentError, FileNotFoundError):
    """A component's source does not exist.

    Inherits `FileNotFoundError` deliberately. `capture` has raised that for a
    missing index since 2026-09-17 and a contract pins it; the invariant is
    that the failure is LOUD, not which class carries it, so widening the type
    keeps every existing caller working while the new code can catch one thing.
    """


@dataclass
class Component:
    """One frozen store: how it was frozen, how much of it, and how much of
    that counts.

    `backend` is DIAGNOSTIC. It decides whether the bytes cost anything and it
    makes a snapshot auditable, but no correctness path may branch on it — that
    is what keeps "portable-first" from decaying into "APFS-first with a
    decorative fallback".

    `captured_scope` and `authoritative_scope` are deliberately two fields. The
    wiki clone is a full tree; the authoritative manifest excludes derived
    subtrees. Collapsing them into one would let a reader assume all 4,439
    captured files participated in the revision-coherence proof, when 25 trace
    files do not.

    `coherence_verified` is False for everything this step produces. Step 6
    verifies; until then, saying so is the honest record.
    """

    name: str
    path: Path
    backend: str
    logical_bytes: int
    captured_scope: str
    authoritative_scope: str
    coherence_verified: bool = False


@contextmanager
def read_locked(db_path):
    """Hold a SQLite SHARED lock for the duration of a capture.

    Without this, `assert_self_contained` inspects one moment and the clone
    copies a different one: the graph is still written in place in this step,
    so a writer may begin after the check and during the copy, and the frozen
    bytes would be a state that never committed.

    A SHARED lock closes that window. In rollback-journal mode a writer must
    hold EXCLUSIVE before it modifies the database FILE, and it cannot while any
    reader holds SHARED — so the bytes cannot move under the clone. `BEGIN`
    alone is deferred and takes no lock; the `SELECT` is what acquires it.

    Opening `mode=ro` is a second, free check: a database with a hot journal
    needs rollback before it can be read, rollback needs write access, and a
    read-only connection therefore FAILS rather than silently reading a
    mid-transaction state.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                           timeout=_LOCK_TIMEOUT_S, isolation_level=None)
    try:
        conn.execute("BEGIN")
        conn.execute("SELECT count(*) FROM sqlite_master")   # acquires SHARED
        yield conn
    finally:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        conn.close()


def assert_self_contained(db_path) -> None:
    """The published file must BE the database (design §4.3).

    A capture-time guard, not Step 4's publication assertion. A clone pins one
    file; if committed state lives in a sidecar, that file is not the whole
    database and freezing it would capture a state that never committed.

    Measured 2026-09-19: every live store reports `journal_mode=delete` with no
    sidecar on disk, so this locks in a property that currently holds rather
    than fixing a live defect.
    """
    db_path = Path(db_path)
    for suffix in _SIDECARS:
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            raise SnapshotComponentError(
                f"{db_path.name} has a mutable {suffix.lstrip('-')} sidecar; "
                "its committed state is not in the file, so freezing the file "
                "would capture a state that never committed")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    if str(mode).lower() == "wal":
        raise SnapshotComponentError(
            f"{db_path.name} is in WAL mode; committed state can live outside "
            "the file, which would silently invalidate every snapshot taken")


def capture_wiki(root, into) -> Component:
    """Freeze the authoritative corpus.

    Deliberately a superset of the authoritative manifest: `wiki/traces/` is
    excluded from the manifest because it bypasses `safe_write`, and captured
    anyway because the clone costs nothing and a snapshot that excluded it
    could not answer a forensic question later (design §4.1).
    """
    from scripts.immutable_bytes import ImmutableBytesError, clone_tree
    source = Path(root) / "wiki"
    if not source.is_dir():
        raise MissingComponentError(f"no wiki to capture at {source}")
    destination = Path(into) / "wiki"
    try:
        mechanism = clone_tree(source, destination)
    except ImmutableBytesError as exc:
        raise SnapshotComponentError(f"wiki capture failed: {exc}") from exc
    total = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    return Component(name="wiki", path=destination, backend=mechanism,
                     logical_bytes=total,
                     captured_scope=FULL_TREE,
                     authoritative_scope=EXCLUDES_DERIVED)


def capture_graph(root, into) -> Component:
    """Freeze the graph by CLONE, never by hardlink, under a read lock.

    `scripts/graph_build.py:186` mutates the canonical inode in place, so a
    hardlink would make snapshot and live store the same bytes — the illegal
    case of design §4.2. Step 4 makes the graph atomic; until then the clone is
    what makes capturing it legal at all, and the read lock is what makes the
    cloned bytes coherent rather than merely separate.
    """
    from scripts.immutable_bytes import ImmutableBytesError, clone_file
    source = Path(root) / "data" / "graph.sqlite"
    if not source.is_file():
        raise MissingComponentError(f"no graph to capture at {source}")
    destination = Path(into) / "graph.sqlite"
    try:
        with read_locked(source):
            assert_self_contained(source)
            mechanism = clone_file(source, destination)
    except sqlite3.Error as exc:
        raise SnapshotComponentError(
            f"graph is not readable as a committed database: {exc}") from exc
    except ImmutableBytesError as exc:
        raise SnapshotComponentError(f"graph capture failed: {exc}") from exc
    return Component(name="graph", path=destination, backend=mechanism,
                     logical_bytes=destination.stat().st_size,
                     captured_scope=SINGLE_FILE,
                     authoritative_scope=WHOLE_COMPONENT)


def capture_search(root, into) -> Component:
    """Freeze the search index by HARDLINK — legal only because
    `rebuild_search_index` builds to a temp file and promotes with one
    `os.replace()`, so the published inode is never modified in place.
    """
    from scripts.immutable_bytes import COPY, HARDLINK
    source = _canonical_index(root)
    if not source.is_file():
        raise MissingComponentError(
            f"no search index to snapshot at {source}; build one before measuring")
    assert_self_contained(source)
    from scripts.sqlite_publish import PublicationError, assert_published_immutable
    try:
        assert_published_immutable(source)
    except PublicationError as exc:
        raise SnapshotComponentError(str(exc)) from exc
    destination = Path(into) / INDEX_NAME
    try:
        os.link(source, destination)
        mechanism = HARDLINK
    except OSError:
        shutil.copy2(source, destination)
        mechanism = COPY
    return Component(name="search", path=destination, backend=mechanism,
                     logical_bytes=destination.stat().st_size,
                     captured_scope=SINGLE_FILE,
                     authoritative_scope=WHOLE_COMPONENT)


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


_COMPONENTS = {
    "wiki": capture_wiki,
    "search": capture_search,
    "graph": capture_graph,
}

DEFAULT_COMPONENTS = ("search",)


def _fsync_dir(directory) -> None:
    """Make the rename durable. Bounds the crash window; does not remove it.

    Stated honestly because "atomic" invites more confidence than a rename
    deserves on its own: without this, a crash shortly after the rename can
    still lose it. With it, a snapshot that is visible has been published.
    """
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def capture(root=None, label="measurement", deep=False, components=None) -> Snapshot:
    """Freeze the declared components. Returns a `Snapshot`.

    `components=None` means `DEFAULT_COMPONENTS`, which is today's behaviour
    exactly: `check.sh` gate 6 calls this on every run and reads only the
    index, so cloning 135 MB of wiki would be a 47 ms tax on bytes nobody
    opens.

    This is NOT Step 6's `required`. `components` says which bytes to freeze;
    `required` will add coherence verification and `SNAPSHOT_NOT_READY` on top.
    They are kept apart so the seam is reviewable.

    Captured into `<id>.building-<pid>` and published with one rename. Python
    cleanup handles an exception; it cannot handle SIGKILL or power loss, and
    those would otherwise leave a directory under the FINAL name holding some
    of the components — indistinguishable from a whole snapshot to every later
    reader. Any failure discards the partial capture (design §7.1).
    """
    base = _root(root)
    names = tuple(components) if components is not None else DEFAULT_COMPONENTS
    unknown = [n for n in names if n not in _COMPONENTS]
    if unknown:
        raise SnapshotComponentError(
            f"no capture adapter registered for {unknown}; refusing rather "
            "than silently omitting a component the caller believes it froze")
    if len(set(names)) != len(names):
        # Otherwise the second capture of the same name fails deep inside
        # `clone_tree` with "destination already exists", which reads as a
        # filesystem problem rather than a caller mistake.
        raise SnapshotComponentError(f"duplicate component names in {names}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_id = f"{label}-{stamp}-{os.getpid()}"
    final = snapshots_dir(base) / snapshot_id
    working = snapshots_dir(base) / f"{snapshot_id}.building-{os.getpid()}"
    working.mkdir(parents=True, exist_ok=False)
    index_path = None
    try:
        captured = {}
        for name in names:
            captured[name] = _COMPONENTS[name](base, working)
        # A component's ABSENCE from this mapping means it was not captured.
        # Recording only the names that were frozen is what stops a later
        # reader treating a missing key as "captured and verified".
        identity = {
            "snapshot_id": snapshot_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "components": {
                name: {
                    "capture_backend": component.backend,
                    "captured_scope": component.captured_scope,
                    "authoritative_scope": component.authoritative_scope,
                    "coherence_verified": component.coherence_verified,
                    "logical_bytes": component.logical_bytes,
                }
                for name, component in captured.items()
            },
            "corpus_manifest_sha": _corpus_manifest_sha(base),
        }
        if "search" in captured:
            index_path = captured["search"].path
            identity["index_path"] = str(_canonical_index(root))
            identity["index_inode"] = index_path.stat().st_ino
            identity["link"] = captured["search"].backend
            identity.update(index_identity(index_path, deep=deep))
        (working / "manifest.json").write_text(
            json.dumps(identity, indent=2, default=str), encoding="utf-8")
        os.rename(working, final)
        _fsync_dir(final.parent)
    except BaseException:
        shutil.rmtree(working, ignore_errors=True)
        raise
    if index_path is not None:
        index_path = final / INDEX_NAME
    return Snapshot(snapshot_id=snapshot_id, path=final,
                    index_path=index_path, identity=identity)


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
