"""Who wrote this, and whose knowledge did it change — asked as two questions.

The ledger shipped with one `project` column filled from
`project_registry.get_current_project()`. That value is the registry's
`current`: a single file shared by every concurrent session on the machine, so
it cannot name a session. On 2026-09-19 a `decision add` run inside the Zurvan
repo recorded `project: otherproj` while the decision page it wrote correctly
stamped `project: zurvan` — the ledger and the page disagreeing about the same
write.

Four fields replace it, each named for what its value can actually support:

    store_project      the SUBJECT stamped on the page — the publication
                       control `public_repo_guard` reads. Whose knowledge moved.
    execution_project  the registered project containing the working directory.
                       Where execution happened, which for MCP is the server's
                       workspace and NOT necessarily the requester.
    selected_project   the registry's `current`. Diagnostic only, and never
                       consulted for ownership, orphan attribution or routing.
    caller_project     reserved, and deliberately left NULL until a caller can
                       supply a trustworthy project identity of its own.
"""

import json
import os
import sqlite3
import subprocess
import sys
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


def _columns(root) -> set:
    conn = sqlite3.connect(kr.db_path(root))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(mutations)")}
    finally:
        conn.close()


def _row(root, mutation_id=None) -> sqlite3.Row:
    conn = sqlite3.connect(kr.db_path(root))
    conn.row_factory = sqlite3.Row
    try:
        if mutation_id:
            return conn.execute("SELECT * FROM mutations WHERE mutation_id = ?",
                                (mutation_id,)).fetchone()
        return conn.execute("SELECT * FROM mutations").fetchone()
    finally:
        conn.close()


# ── the schema ────────────────────────────────────────────────────────────────

def test_the_overloaded_project_column_is_gone(root):
    """One column cannot hold two facts. Leaving it in place beside the new
    ones would let a future reader pick the ambiguous one."""
    assert "project" not in _columns(root)


def test_all_four_attribution_columns_exist(root):
    assert {"store_project", "execution_project", "selected_project",
            "caller_project"} <= _columns(root)


# ── migrating a ledger that is already live ───────────────────────────────────

def _old_schema(root):
    """The shipped shape, as `4d1c21f` created it, holding one real row."""
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS mutations;
            CREATE TABLE mutations (
                mutation_id      TEXT PRIMARY KEY,
                base_revision    INTEGER NOT NULL,
                state            TEXT NOT NULL,
                project          TEXT,
                writer           TEXT,
                implicit         INTEGER NOT NULL DEFAULT 0,
                caller           TEXT,
                owner_pid        INTEGER,
                owner_identity   TEXT NOT NULL,
                started_at       TEXT NOT NULL,
                committed_at     TEXT,
                last_heartbeat   TEXT,
                planned_paths    TEXT NOT NULL DEFAULT '[]',
                completed_paths  TEXT NOT NULL DEFAULT '[]',
                orphan_reason    TEXT,
                orphaned_at      TEXT,
                resolved_at      TEXT,
                resolution_actor TEXT,
                resolution_reason TEXT,
                resolution_revision INTEGER
            );
            INSERT INTO mutations (mutation_id, base_revision, state, project,
                                   writer, owner_identity, started_at)
            VALUES ('mlive', 4, 'COMMITTED', 'otherproj', 'cli', '1:x', 'then');
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_the_old_column_migrates_into_selected_project(root):
    """The recorded values ARE registry selections — that is what the code put
    there. They move to the field that means selection, and nothing else is
    invented for them."""
    _old_schema(root)

    kr.ensure_schema(root)

    row = _row(root, "mlive")
    assert row["selected_project"] == "otherproj"


def test_the_migration_invents_no_store_project_for_historical_rows(root):
    """Nothing in the old row records what subject was written. A backfill
    would be a guess wearing the authoritative field's name."""
    _old_schema(root)

    kr.ensure_schema(root)

    row = _row(root, "mlive")
    assert row["store_project"] is None
    assert row["execution_project"] is None
    assert row["caller_project"] is None


def test_the_migration_is_idempotent(root):
    _old_schema(root)
    kr.ensure_schema(root)
    kr.ensure_schema(root)
    assert _row(root, "mlive")["selected_project"] == "otherproj"
    assert "project" not in _columns(root)


def test_the_migration_preserves_every_other_column(root):
    _old_schema(root)
    kr.ensure_schema(root)
    row = _row(root, "mlive")
    assert (row["base_revision"], row["state"], row["writer"]) == (
        4, "COMMITTED", "cli")


# ── what each field is allowed to hold ────────────────────────────────────────

def test_caller_project_is_reserved_and_stays_empty(root):
    """It is here so nothing else claims the name. Populating it from the
    working directory would plant the next attribution bug: an MCP server's cwd
    is its own workspace, not the workspace of whoever called it."""
    with kr.operation(writer="test", root=root,
                      execution_project="zurvan", selected_project="otherproj"):
        kr.plan_write(root / "wiki" / "decisions" / "a.md", root)

    assert _row(root)["caller_project"] is None


def test_an_unresolvable_execution_project_is_null_not_the_string_unknown(root):
    """`resolve_project` answers `"unknown"` because it is a publication
    control and must fail closed. A ledger column has NULL for that, and two
    spellings of the same absence is one too many."""
    with kr.operation(writer="test", root=root, execution_project="unknown"):
        kr.plan_write(root / "wiki" / "decisions" / "a.md", root)

    assert _row(root)["execution_project"] is None


def test_the_store_project_is_recorded_when_the_write_declares_one(root):
    with kr.operation(writer="test", root=root) as op:
        kr.declare_store_project("zurvan", op)
        kr.plan_write(root / "wiki" / "decisions" / "a.md", root)

    assert _row(root)["store_project"] == "zurvan"


def test_a_subject_declared_after_the_row_exists_still_lands(root):
    """The mutation row is created lazily on the first PLANNED write, and
    `add_decision` resolves its subject while writing. The declaration must
    reach a row that already exists."""
    with kr.operation(writer="test", root=root) as op:
        kr.plan_write(root / "wiki" / "decisions" / "a.md", root)
        kr.declare_store_project("zurvan", op)

    assert _row(root)["store_project"] == "zurvan"


def test_two_different_subjects_in_one_operation_fail_closed(root):
    """One mutation, one subject. If a command ever writes two projects' pages
    the operation boundary is drawn wrong, and mis-attributing it quietly is
    worse than stopping."""
    with pytest.raises(kr.LedgerError):
        with kr.operation(writer="test", root=root) as op:
            kr.declare_store_project("zurvan", op)
            kr.declare_store_project("otherproj", op)


def test_declaring_the_same_subject_twice_is_fine(root):
    """`decision add` writes the page and then appends `log.md`; resolving the
    same subject twice is not a conflict."""
    with kr.operation(writer="test", root=root) as op:
        kr.declare_store_project("zurvan", op)
        kr.declare_store_project("zurvan", op)
        kr.plan_write(root / "wiki" / "decisions" / "a.md", root)

    assert _row(root)["store_project"] == "zurvan"


def test_a_declaration_outside_any_operation_is_a_no_op(root):
    """Library code must be callable without a boundary — it becomes an
    implicit mutation, which is recorded as such. It must not raise."""
    kr.declare_store_project("zurvan")


# ── the diagnostic field never becomes an authority ───────────────────────────

def _orphan(root, selected_project):
    op = kr.Operation(root=root, writer="cli", store_project="zurvan",
                      execution_project="zurvan",
                      selected_project=selected_project)
    mid = kr._insert_mutation(op)
    page = root / "wiki" / "decisions" / "a.md"
    page.write_text("---\ntitle: A\n---\n\nbody\n")
    kr._append_path(mid, "planned_paths", str(page), root)
    kr._append_path(mid, "completed_paths", str(page), root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE mutations SET owner_pid = ?, owner_identity = ?"
                     " WHERE mutation_id = ?", (2_000_000, "2000000:gone", mid))
        conn.commit()
    finally:
        conn.close()
    kr.detect_orphans(root)
    return mid


def test_the_selected_project_changes_no_recovery_decision(root, tmp_path,
                                                           monkeypatch):
    """`selected_project` is evidence, not input — never consulted for
    ownership, orphan attribution or routing. Two orphans identical but for
    that field must recover identically, including the resulting revision
    state. The original defect was a stale global deciding an attribution;
    this pins that it can no longer decide anything."""
    from scripts import knowledge_reconcile as krec

    _orphan(root, selected_project="otherproj")
    first = krec.reconcile(root=root, actor="local:raouf")

    second_root = tmp_path / "second"
    (second_root / "wiki" / "decisions").mkdir(parents=True)
    (second_root / "data").mkdir()
    monkeypatch.setenv("ZURVAN_ROOT", str(second_root))
    kr.ensure_schema(second_root)
    _orphan(second_root, selected_project=None)
    second = krec.reconcile(root=second_root, actor="local:raouf")

    assert first["status"] == second["status"] == "CLEAN"
    assert first["revision"] == second["revision"]
    assert (kr.current(root).state, kr.current(root).atomicity) == (
        kr.current(second_root).state, kr.current(second_root).atomicity)


def test_the_orphan_report_separates_the_three_facts(root):
    """The operator needs to see the divergence, not a single word that hides
    it: the mutation targeted zurvan, ran in zurvan, and the global selector
    was stale at otherproj."""
    from scripts import knowledge_reconcile as krec

    op = kr.Operation(root=root, writer="cli", store_project="zurvan",
                      execution_project="zurvan", selected_project="otherproj")
    mid = kr._insert_mutation(op)
    kr._append_path(mid, "planned_paths",
                    str(root / "wiki" / "decisions" / "a.md"), root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE mutations SET owner_pid = ?, owner_identity = ?"
                     " WHERE mutation_id = ?", (2_000_000, "2000000:gone", mid))
        conn.commit()
    finally:
        conn.close()
    kr.detect_orphans(root)

    text = krec.format_report(krec.inspect(root), "NEEDS_REVIEW",
                              kr.current(root).revision)

    assert "store:     zurvan" in text
    assert "execution: zurvan" in text
    assert "selected:  otherproj" in text


# ── end to end, against the defect as it was observed ─────────────────────────

def _registry(config_dir, projects, current):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "projects.json").write_text(json.dumps(
        {"current": current,
         "projects": {n: {"path": str(p)} for n, p in projects.items()}}),
        encoding="utf-8")


def test_a_stale_global_selection_never_becomes_the_store_project(tmp_path):
    """The 2026-09-19 defect, end to end and hermetic.

    A `decision add` run inside the Zurvan workspace while the registry's
    `current` points at an unrelated project. The page stamps `zurvan`; the
    ledger must agree with the page, and must record the stale selection
    separately rather than in its place.

    A subprocess, not an in-process call: `scripts/memory.py:7` binds
    `PROJECT_ROOT` at import, so an in-process CLI test exercises nothing.
    """
    store = tmp_path / "Zurvan"
    (store / "wiki" / "decisions").mkdir(parents=True)
    (store / "data").mkdir()
    other = tmp_path / "OTHERPROJ"
    other.mkdir()
    config = tmp_path / "config"
    _registry(config, {"zurvan": store, "otherproj": other}, current="otherproj")

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(repo),
           "ZURVAN_CONFIG_DIR": str(config)}
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "cli.py"), "decision", "add",
         "--title", "T", "--reason", "R", "--status", "accepted"],
        cwd=str(store), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    committed = kr.mutations_in(kr.COMMITTED, store)
    assert len(committed) == 1, [m.mutation_id for m in committed]
    m = committed[0]
    assert m.store_project == "zurvan"
    assert m.execution_project == "zurvan"
    assert m.selected_project == "otherproj"
    assert m.caller_project is None
    assert m.writer == "cli"

    page = next((store / "wiki" / "decisions").glob("*.md"))
    assert 'project: "zurvan"' in page.read_text()


def test_an_explicit_subject_diverges_from_the_workspace_that_ran_it(tmp_path):
    """A cross-project write: the subject is `otherproj`, the process is running
    in the Zurvan workspace. Both facts are true at once, and the point of two
    fields is that neither has to be discarded to record the other.

    Exercised through the MCP tool. When this was written the CLI could not
    carry it: `cli.py` popped `--project` from anywhere as the global workspace
    switch, shadowing the subcommand's subject flag. Fixed 2026-09-28 — only a
    leading `--project` switches workspace — and the CLI path is covered in
    `test_fail_closed_writes.py`.
    """
    store = tmp_path / "Zurvan"
    (store / "wiki" / "decisions").mkdir(parents=True)
    (store / "data").mkdir()
    config = tmp_path / "config"
    _registry(config, {"zurvan": store}, current="zurvan")

    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(repo),
           "ZURVAN_CONFIG_DIR": str(config), "ZURVAN_MCP_READONLY": "0"}
    result = subprocess.run(
        [sys.executable, "-c",
         "from scripts.mcp_tools import tool_zurvan_decision_add;"
         " tool_zurvan_decision_add('T', 'R', 'accepted', [],"
         " project='otherproj')"],
        cwd=str(store), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    m = kr.mutations_in(kr.COMMITTED, store)[0]
    assert m.store_project == "otherproj"
    assert m.execution_project == "zurvan"
    assert m.selected_project == "zurvan"
