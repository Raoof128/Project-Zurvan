"""Z2 Task 1: the registry records which revision each mutation committed, and
which scope version each stored manifest digest was computed under.

Spec: docs/superpowers/specs/2026-11-14-z2-claim-ledger-design.md §7.2, §7.5.
Every path is absolute and under the test's own root.
"""

import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks
from scripts.safe_write import write_file_safely


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _row(root, mutation_id, *cols):
    conn = sqlite3.connect(kr.db_path(root))
    try:
        return conn.execute(
            f"SELECT {', '.join(cols)} FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
    finally:
        conn.close()


def _state(root, col):
    conn = sqlite3.connect(kr.db_path(root))
    try:
        return conn.execute(f"SELECT {col} FROM knowledge_state WHERE id = 1").fetchone()[0]
    finally:
        conn.close()


def _write(root, name, text, writer="test"):
    with kr.operation(writer=writer, root=root) as op:
        assert write_file_safely(str(root / "wiki" / "decisions" / name), text)
    return op


def test_commit_records_committed_revision(root):
    op = _write(root, "a.md", "one\n")
    (committed,) = _row(root, op.mutation_id, "committed_revision")
    assert committed == op.minted_revision == kr.current(root).revision == 1
    op2 = _write(root, "b.md", "two\n")
    assert _row(root, op2.mutation_id, "committed_revision") == (2,)


def test_belief_revisions_maps_committed_and_resolved(root):
    ok = _write(root, "a.md", "one\n")
    # an aborted mutation (nothing planned) is absent
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root):
            raise RuntimeError("boom before any write")
    # an orphaned mutation (planned, then crashed), reconciled -> RESOLVED
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root) as crashed:
            write_file_safely(str(root / "wiki" / "decisions" / "c.md"), "three\n")
            raise RuntimeError("crash after a write")
    assert kr.current(root).state == kr.SUSPECT
    result = kr.reconcile_revision([crashed.mutation_id], actor="test", root=root)
    mapping = kr.belief_revisions(root)
    assert mapping[ok.mutation_id] == 1
    (resolution,) = _row(root, crashed.mutation_id, "resolution_revision")
    assert mapping[crashed.mutation_id] == resolution == result["to_revision"]
    assert len(mapping) == 2


def test_scope_version_change_is_not_drift(root, monkeypatch):
    _write(root, "a.md", "one\n")
    before = kr.current(root).revision
    assert _state(root, "scope_version") == ks.SCOPE_VERSION
    monkeypatch.setattr(ks, "SCOPE_VERSION", ks.SCOPE_VERSION + 1)
    op = _write(root, "b.md", "two\n")
    # one managed revision for the write; no EXTERNAL_OBSERVED minted for the scope change
    assert op.minted_revision == before + 1
    conn = sqlite3.connect(kr.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) FROM external_observations").fetchone()[0] == 0
    finally:
        conn.close()
    assert _state(root, "scope_version") == ks.SCOPE_VERSION
    # and a real external edit under the NEW scope is still caught
    (root / "wiki" / "decisions" / "a.md").write_text("edited outside\n", encoding="utf-8")
    assert kr.observe_external_drift(root) == op.minted_revision + 1


def test_claims_db_is_authoritative_when_present(root):
    claims = root.joinpath(*ks.CLAIMS_DB_REL)
    assert ks.CLAIMS_DB_REL == ("data", "claims.sqlite")
    assert ks.is_authoritative(claims, root) is True
    assert ks.is_authoritative(root / "data" / "search.sqlite", root) is False
    assert ks.is_authoritative(root / "data" / "registry.sqlite", root) is False
    assert claims not in ks.authoritative_files(root)
    claims.write_bytes(b"x")
    assert claims in ks.authoritative_files(root)
    assert ks.SCOPE_VERSION == 2


def test_at_operation_end_runs_after_commit_and_after_abandon(root):
    seen = []
    with kr.operation(writer="test", root=root) as op:
        kr.at_operation_end(lambda: seen.append(("ok", kr.current(root).revision)))
        write_file_safely(str(root / "wiki" / "decisions" / "a.md"), "one\n")
        assert seen == []
    assert seen == [("ok", op.minted_revision)]          # ran AFTER the commit
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root):
            kr.at_operation_end(lambda: seen.append("abandoned"))
            raise RuntimeError("boom")
    assert seen[-1] == "abandoned"


def test_at_operation_end_joins_the_outermost_operation(root):
    seen = []
    with kr.operation(writer="test", root=root):
        with kr.operation(writer="test", root=root):
            kr.at_operation_end(lambda: seen.append("end"))
        assert seen == []                                 # inner exit is not the end
    assert seen == ["end"]


def test_at_operation_end_requires_an_operation(root):
    with pytest.raises(kr.KnowledgeOperationRequired):
        kr.at_operation_end(lambda: None)


def test_views_on_a_store_without_a_ledger_are_empty(tmp_path):
    """A store with no registry yet has no beliefs; reading it must not raise
    (found by the CLI smoke test: `claim add` printed via `current()`)."""
    from scripts import claim_ledger
    assert kr.belief_revisions(tmp_path) == {}
    assert kr.revision_at("2030-01-01T00:00:00+00:00", tmp_path) is None
    assert claim_ledger.current(tmp_path) == {}
