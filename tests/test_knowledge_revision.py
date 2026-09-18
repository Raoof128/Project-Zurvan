"""A revision is advanced by a logical mutation, never by a file write.

`zurvan decision add` writes the decision page and then appends `wiki/log.md`.
If the revision advanced per write, a snapshot between them would be
byte-perfect and logically half-finished: a decision the log does not know
about. These tests are written against logical operations, not files.

Every path here is absolute and under the test's own root. `Path("a").resolve()`
resolves against the process working directory, which during a test run is the
real repository — measured.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def page(root: Path, name: str) -> Path:
    """Always absolute, always under this test's root."""
    return root / "wiki" / name


def test_a_fresh_store_starts_clean_at_revision_zero(root):
    rev = kr.current(root)
    assert rev.revision == 0
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.MANAGED


def test_ensure_schema_is_idempotent(root):
    kr.ensure_schema(root)
    kr.ensure_schema(root)
    assert kr.current(root).revision == 0


def test_the_ledger_lives_in_registry_sqlite_beside_sources(root):
    assert kr.db_path(root) == root / "data" / "registry.sqlite"
    conn = sqlite3.connect(kr.db_path(root))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"knowledge_state", "mutations", "reconciliations"} <= names


def test_the_ledger_follows_zurvan_root_set_after_import(tmp_path, monkeypatch):
    """The production-contamination regression."""
    import scripts.config
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    assert kr.db_path() == (tmp_path / "data" / "registry.sqlite").resolve()
    assert scripts.config.PROJECT_ROOT != tmp_path


def test_an_invalid_state_is_rejected_by_the_schema(root):
    """Typos must not become valid states."""
    conn = sqlite3.connect(kr.db_path(root))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE knowledge_state SET state = 'CLAEN' WHERE id = 1")
            conn.commit()
    finally:
        conn.close()


def test_the_fingerprint_moves_when_anything_in_the_ledger_moves(root):
    """What the session guard watches. A revision counter alone misses
    CLEAN -> SUSPECT, an atomicity change, and a mutation changing state."""
    before = kr.ledger_fingerprint(root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE knowledge_state SET state = ? WHERE id = 1",
                     (kr.SUSPECT,))
        conn.commit()
    finally:
        conn.close()
    assert kr.ledger_fingerprint(root) != before


def test_our_own_process_is_alive(root):
    assert kr.process_state(os.getpid(), kr.own_identity()) == kr.ALIVE


def test_a_pid_that_does_not_exist_is_dead(root):
    assert kr.process_state(2_000_000, "2000000:whenever") == kr.DEAD


def test_a_recycled_pid_is_dead_not_alive(root):
    """A pid alone is recycled. Pairing it with a start time stops an unrelated
    new process vouching for a dead mutation."""
    assert kr.process_state(os.getpid(), "different identity") == kr.DEAD


def test_an_uninspectable_process_is_unknown_not_dead(monkeypatch, root):
    """`ps` failing is not evidence of death, and orphaning on it would mark
    the store SUSPECT because a subprocess timed out."""
    def explode(*a, **k):
        raise OSError("ps unavailable")
    monkeypatch.setattr(kr.subprocess, "run", explode)
    assert kr.process_state(os.getpid(), "whatever") == kr.UNKNOWN


def test_own_identity_is_cached_per_pid_not_globally(root, monkeypatch):
    """A forked child inherits the parent's module state. Caching without the
    pid would let it answer with its parent's identity."""
    mine = kr.own_identity()
    assert kr._OWN_IDENTITY[0] == os.getpid()
    monkeypatch.setattr(kr.os, "getpid", lambda: 4242)
    monkeypatch.setattr(kr, "process_identity", lambda pid: f"{pid}:pretend")
    assert kr.own_identity() == "4242:pretend"
    assert kr.own_identity() != mine


def test_a_transition_from_the_wrong_state_is_refused(root):
    conn = kr._connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO mutations (mutation_id, base_revision, state,"
            " owner_identity, started_at) VALUES ('m1', 0, ?, 'x', 'now')",
            (kr.COMMITTED,))
        with pytest.raises(kr.MutationStateError):
            kr._transition(conn, "m1", want=kr.ACTIVE, new=kr.COMMITTED)
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def test_a_transition_on_a_missing_row_is_refused(root):
    conn = kr._connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(kr.MutationStateError):
            kr._transition(conn, "nope", want=kr.ACTIVE, new=kr.COMMITTED)
        conn.execute("ROLLBACK")
    finally:
        conn.close()
