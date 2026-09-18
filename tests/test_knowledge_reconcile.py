"""Reconciliation reaches CLEAN without rebuilding, and leaves a record.

The approved design (rev 2, §3.5) requires a NEW CLEAN revision:

    SUSPECT R -> inspect/repair -> explicit decision -> CLEAN R'

Flipping `state = CLEAN` in place leaves the pre-orphan manifest attached to a
revision now declared coherent, which is the laundering the design forbids.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_manifest as km
from scripts import knowledge_reconcile as krec
from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _orphan(root, planned, completed=()):
    op = kr.Operation(root=root, project="instagram", writer="test")
    mid = kr._insert_mutation(op)
    for p in planned:
        kr._append_path(mid, "planned_paths", p, root)
    for p in completed:
        kr._append_path(mid, "completed_paths", p, root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE mutations SET owner_pid = ?, owner_identity = ?"
                     " WHERE mutation_id = ?",
                     (2_000_000, "2000000:gone", mid))
        conn.commit()
    finally:
        conn.close()
    kr.detect_orphans(root)
    return mid


def _page(root, name="decisions/x.md", body="---\ntitle: X\n---\n\nbody\n"):
    p = root / "wiki" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_an_incomplete_operation_stops_for_review(root):
    """The page parses perfectly and the log line never arrived. Markdown
    syntax is not evidence that a two-file operation finished."""
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert kr.current(root).state == kr.SUSPECT
    assert result["reports"][0].missing_paths == [str(log)]
    assert result["reports"][0].complete is False


def test_reconcile_never_deletes_anything(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])
    krec.reconcile(root=root)
    assert page.exists()


def test_a_complete_operation_reconciles_into_a_new_clean_revision(root):
    """Every planned path landed; the owner died before committing."""
    page = _page(root)
    _orphan(root, planned=[str(page)], completed=[str(page)])
    before = kr.current(root).revision

    result = krec.reconcile(root=root, actor="local:raouf")

    rev = kr.current(root)
    assert result["status"] == "CLEAN"
    assert rev.state == kr.CLEAN
    assert rev.revision == before + 1                 # a NEW revision, not a flag
    assert rev.manifest_digest == km.authoritative_manifest(root)


def test_the_reconciled_revision_is_external_observed(root):
    """Zurvan did not witness the operation that produced these bytes, so it
    may not claim they were one managed transaction."""
    page = _page(root)
    _orphan(root, planned=[str(page)], completed=[str(page)])
    krec.reconcile(root=root, actor="local:raouf")
    assert kr.current(root).atomicity == kr.EXTERNAL_OBSERVED


def test_an_accepted_reconciliation_clears_an_incomplete_orphan(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired by hand\n")
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root, accept=True, actor="local:raouf")

    assert result["status"] == "CLEAN"
    assert kr.current(root).state == kr.CLEAN


def test_a_reconciled_orphan_reaches_a_terminal_state(root):
    """`knowledge doctor` must stop reporting a resolved incident."""
    page = _page(root)
    mid = _orphan(root, planned=[str(page)], completed=[str(page)])
    krec.reconcile(root=root, actor="local:raouf")

    assert krec.inspect(root) == []
    resolved = kr.mutations_in(kr.RESOLVED, root)
    assert [m.mutation_id for m in resolved] == [mid]
    assert resolved[0].resolved_at
    assert resolved[0].resolution_revision == kr.current(root).revision


def test_reconciliation_records_who_accepted_it_and_what_they_saw(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired\n")
    mid = _orphan(root, planned=[str(page), str(log)], completed=[str(page)])
    before_digest = kr.current(root).manifest_digest

    krec.reconcile(root=root, accept=True, actor="local:raouf",
                   reason="log line restored by hand")

    conn = sqlite3.connect(kr.db_path(root))
    try:
        row = conn.execute(
            "SELECT actor, reason, mutation_ids, manifest_before,"
            " manifest_after, from_revision, to_revision FROM reconciliations"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "local:raouf"
    assert row[1] == "log line restored by hand"
    assert mid in json.loads(row[2])
    assert row[3] == before_digest
    assert row[4] == km.authoritative_manifest(root)
    assert row[6] == row[5] + 1


def test_the_report_reflects_the_resulting_status_not_the_original(root):
    """An earlier design printed `SUSPECT revision ...` above `status: CLEAN`."""
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired\n")
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root, accept=True, actor="local:raouf")
    text = krec.format_report(result["reports"], result["status"],
                              result["revision"])

    assert "SUSPECT" not in text
    assert "CLEAN" in text


def test_the_review_report_names_the_path_that_never_completed(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root)
    text = krec.format_report(result["reports"], result["status"],
                              result["revision"])

    assert "SUSPECT" in text
    assert "log.md" in text
    assert "UNPROVEN" in text


def test_a_completed_path_that_no_longer_parses_stops_for_review(root):
    page = _page(root, body="---\ntitle: [unclosed\n")        # torn frontmatter
    _orphan(root, planned=[str(page)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["reports"][0].parse_failures == [str(page)]


def test_validation_uses_the_repositorys_existing_frontmatter_parser(root):
    """Two parsers become two definitions of valid. Reconciliation must agree
    with ingestion about what Zurvan can read."""
    import inspect as _inspect
    source = _inspect.getsource(krec)
    assert "parse_frontmatter" in source
    assert "yaml.safe_load" not in source
