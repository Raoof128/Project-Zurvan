"""Capturing a store means capturing all of it, or refusing.

`scripts/graph_build.py:186` mutates `data/graph.sqlite` in place, so a
hardlink to it is illegal (design §4.2) and a clone is used instead. A clone
gets its own inode, which makes capture legal — but it cannot make a mid-write
database coherent. If committed state lives in a `-journal` or `-wal` sidecar,
the cloned file is not the whole database and the snapshot would be a lie.

Checking and then cloning is not enough either: a writer can start in between.
The check and the clone are one critical section under a SQLite SHARED lock.
"""

import sqlite3
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib
from scripts import measurement_snapshot as ms


@pytest.fixture
def store(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "decisions" / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "data").mkdir()
    conn = sqlite3.connect(tmp_path / "data" / "graph.sqlite")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO nodes VALUES (1)")
    conn.commit()
    conn.close()
    return tmp_path


@pytest.fixture
def into(tmp_path):
    destination = tmp_path / "snap"
    destination.mkdir()
    return destination


# ── the graph: cloned, never hardlinked ──────────────────────────────────────

def test_the_graph_is_captured_by_clone_never_by_hardlink(store, into):
    """It is written in place, so a hardlink would make snapshot and live store
    the same bytes — design §4.2's illegal case."""
    import os

    component = ms.capture_graph(store, into)

    assert component.backend in (ib.CLONEFILE, ib.COPY)
    assert component.backend != ib.HARDLINK
    assert (os.stat(component.path).st_ino
            != os.stat(store / "data" / "graph.sqlite").st_ino)


def test_the_captured_graph_is_queryable(store, into):
    """A component nobody can open is not a capture."""
    component = ms.capture_graph(store, into)

    conn = sqlite3.connect(f"file:{component.path}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_clean_delete_mode_graph_is_accepted(store, into):
    """All three live stores report `journal_mode=delete` with no sidecar
    (measured 2026-09-19), so this locks in a property that currently holds."""
    component = ms.capture_graph(store, into)

    assert component.name == "graph"
    assert component.logical_bytes > 0


# ── refusing a store whose committed state is not in the file ────────────────

def test_a_graph_with_a_hot_journal_is_refused_by_sqlite_itself(store, into):
    """TWO guards catch this, and the stronger one fires first.

    Measured: SQLite treats the journal as hot and the read-only connection
    fails with "attempt to write a readonly database", because rolling back
    needs write access. That is a better refusal than the sidecar check — it is
    SQLite's own verdict that the file is not a committed database, not our
    inference from a filename.
    """
    (store / "data" / "graph.sqlite-journal").write_bytes(b"rollback")

    with pytest.raises(ms.SnapshotComponentError) as excinfo:
        ms.capture_graph(store, into)

    assert "not readable as a committed database" in str(excinfo.value)


def test_a_graph_with_an_inert_sidecar_is_refused_by_the_self_containment_check(
        store, into):
    """The second guard, for a sidecar SQLite does not object to. The file may
    open cleanly and still not be the whole database, so the presence of the
    sidecar is itself disqualifying."""
    (store / "data" / "graph.sqlite-shm").write_bytes(b"\x00" * 32)

    with pytest.raises(ms.SnapshotComponentError) as excinfo:
        ms.capture_graph(store, into)

    assert "sidecar" in str(excinfo.value)


def test_no_bytes_are_copied_when_a_sidecar_is_present(store, into):
    """Refuse before writing, not after. A destination left behind would be a
    partial component that a later reader cannot distinguish from a whole one."""
    (store / "data" / "graph.sqlite-journal").write_bytes(b"rollback")

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_graph(store, into)

    assert not (into / "graph.sqlite").exists()


def test_a_graph_in_wal_mode_is_refused(store, into):
    """A hardlink or clone pins ONE file. WAL moves committed state out of it,
    so every snapshot ever taken would silently stop being one."""
    conn = sqlite3.connect(store / "data" / "graph.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO nodes VALUES (2)")
    conn.commit()
    conn.close()

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_graph(store, into)


def test_a_missing_graph_fails_closed_rather_than_capturing_nothing(tmp_path, into):
    """A snapshot of nothing must never read as a snapshot of an empty graph —
    the same rule `capture` already applies to a missing search index."""
    (tmp_path / "data").mkdir(exist_ok=True)

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_graph(tmp_path, into)


# ── the race the read lock closes ────────────────────────────────────────────

def test_a_writer_cannot_change_the_graph_while_a_capture_holds_the_lock(store):
    """The window: check, then clone, with a writer in between. Verifying
    self-containment and then cloning proves only that the store was coherent
    at one instant, not that the frozen bytes are.

    A SHARED lock closes it. In rollback-journal mode a writer must hold
    EXCLUSIVE before it modifies the database FILE, and it cannot while any
    reader holds SHARED.
    """
    graph = store / "data" / "graph.sqlite"

    with ms.read_locked(graph):
        writer = sqlite3.connect(graph, timeout=0.1)
        try:
            with pytest.raises(sqlite3.OperationalError):
                writer.execute("INSERT INTO nodes VALUES (99)")
                writer.commit()
        finally:
            writer.close()


def test_the_lock_is_released_so_writers_are_not_blocked_afterwards(store):
    """A capture that leaked its read lock would freeze the whole store for
    every other session — a shared brain locked by a snapshot is exactly the
    failure `measurement_snapshot` was written to avoid."""
    graph = store / "data" / "graph.sqlite"

    with ms.read_locked(graph):
        pass

    writer = sqlite3.connect(graph, timeout=1.0)
    try:
        writer.execute("INSERT INTO nodes VALUES (99)")
        writer.commit()
    finally:
        writer.close()
    assert True


def test_the_lock_is_released_even_when_the_capture_raises(store):
    graph = store / "data" / "graph.sqlite"

    with pytest.raises(ValueError):
        with ms.read_locked(graph):
            raise ValueError("capture blew up")

    writer = sqlite3.connect(graph, timeout=1.0)
    try:
        writer.execute("INSERT INTO nodes VALUES (99)")
        writer.commit()
    finally:
        writer.close()


# ── the wiki component, and the two scopes ───────────────────────────────────

def test_the_wiki_component_reports_what_it_froze(store, into):
    component = ms.capture_wiki(store, into)

    assert component.name == "wiki"
    assert (component.path / "decisions" / "a.md").read_text(
        encoding="utf-8") == "alpha"


def test_the_wiki_records_captured_scope_and_authoritative_scope_separately(
        store, into):
    """One field would let a reader assume every captured byte participated in
    the revision-coherence proof. 25 of 4,439 live files are derived traces
    that do not, so the snapshot says both things."""
    component = ms.capture_wiki(store, into)

    assert component.captured_scope == ms.FULL_TREE
    assert component.authoritative_scope == ms.EXCLUDES_DERIVED
    assert component.captured_scope != component.authoritative_scope


def test_nothing_this_step_produces_claims_to_be_coherence_verified(store, into):
    """Verification is Step 6. Between now and then a snapshot must not be
    mistakable for a verified one."""
    assert ms.capture_wiki(store, into).coherence_verified is False
    assert ms.capture_graph(store, into).coherence_verified is False


# ── the hardlink hazard: reproduced, NOT enforced (gate 14 stays OPEN) ───────

def _make_index(store):
    index = store / "data" / "search.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO chunks VALUES (1)")
    conn.commit()
    conn.close()
    return index


def test_an_in_place_write_to_the_published_index_reaches_every_snapshot(
        store, into, monkeypatch):
    """The hazard, reproduced. NOT gate 14.

    A hardlink is legal only because `rebuild_search_index` builds to
    `data/search.sqlite.building-<pid>` and promotes with one `os.replace()`.
    If anyone opens the canonical path for writing instead, the snapshot and
    the live store are the same inode, and every existing snapshot silently
    stops being one.

    This proves the danger is real. It does not prevent it: nothing here makes
    the offending write fail, so **gate 14 remains OPEN** until a publication
    invariant enforces it in steps 4-5.
    """
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    index = _make_index(store)
    component = ms.capture_search(store, into)

    conn = sqlite3.connect(index)
    conn.execute("INSERT INTO chunks VALUES (2)")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(f"file:{component.path}?mode=ro", uri=True)
    try:
        leaked = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    assert leaked == 2, (
        "a hardlink shares the inode, so this MUST leak; if it did not, the "
        "mechanism changed and this test is no longer describing reality")


def test_the_index_is_captured_by_hardlink_which_is_why_the_hazard_exists(
        store, into, monkeypatch):
    """The mechanism is the hazard's precondition. Recorded so that if the
    index ever stops being hardlinked, the test above is revisited rather than
    left asserting a leak that can no longer happen."""
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    _make_index(store)

    component = ms.capture_search(store, into)

    assert component.backend == ib.HARDLINK


def test_capture_refuses_an_index_whose_committed_state_is_not_in_the_file(
        store, into, monkeypatch):
    """What IS enforced today: a capture taken while a writer holds a journal
    is refused. That is narrower than gate 14, which asks for the offending
    write itself to fail loudly."""
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    index = _make_index(store)
    index.with_name(index.name + "-journal").write_bytes(b"mid-write")

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_search(store, into)
