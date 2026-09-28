"""An edit made outside Zurvan becomes its own revision, on the record.

2026-09-28: `commit_mutation` records the manifest of the whole store, so a
hand edit made between two managed writes was absorbed into the second one's
MANAGED revision, and `observe_external_drift` - which detects exactly this -
had no production caller. Reproduced in a throwaway store before any change.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr
from scripts.knowledge_manifest import authoritative_manifest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _managed_write(root, name="b.md"):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / name), f"# {name}\n")


# ── Task 1: the observation is recorded ─────────────────────────────────────

def test_a_manual_observation_is_recorded(root):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    before = kr.current(root)
    minted = kr.observe_external_drift(root)
    [row] = kr.external_observations(root)
    assert row[0] == minted == before.revision + 1
    assert row[1] == "manual"
    assert row[2] == before.manifest_digest
    assert row[3] == authoritative_manifest(root)


def test_no_drift_records_nothing(root):
    assert kr.observe_external_drift(root) is None
    assert kr.external_observations(root) == []


def test_an_unstable_store_raises_the_named_error(root, monkeypatch):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    digests = iter(["one", "two"])
    monkeypatch.setattr("scripts.knowledge_manifest.authoritative_manifest",
                        lambda base=None: next(digests))
    with pytest.raises(kr.DriftUnobservable):
        kr.observe_external_drift(root)


# ── Task 2: activation records drift first ──────────────────────────────────

def test_a_hand_edit_becomes_its_own_revision_before_the_managed_write(root):
    """The reproduced defect. Before: one MANAGED revision covering both."""
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    hand_edited = authoritative_manifest(root)
    start = kr.current(root).revision

    _managed_write(root)

    [row] = kr.external_observations(root)
    assert row[0] == start + 1 and row[1] == "activation"
    assert row[3] == hand_edited                    # the hand edit alone
    now = kr.current(root)
    assert now.revision == start + 2 and now.atomicity == kr.MANAGED
    [m] = kr.mutations_in(kr.COMMITTED, root)
    assert m.base_revision == start + 1             # built on the observation


def test_no_hand_edit_means_one_revision(root):
    start = kr.current(root).revision
    _managed_write(root)
    assert kr.current(root).revision == start + 1
    assert kr.external_observations(root) == []


def test_a_store_with_no_baseline_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    kr.ensure_schema(tmp_path)
    (tmp_path / "wiki" / "x.md").write_text("x")
    _managed_write(tmp_path)
    assert kr.external_observations(tmp_path) == []
    assert kr.current(tmp_path).revision == 1


def test_a_suspect_store_is_left_to_reconcile(root):
    kr._mark_suspect(root)
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    _managed_write(root)
    assert kr.external_observations(root) == []
    assert kr.current(root).state == kr.SUSPECT


def test_another_operation_in_flight_is_not_external_drift(root):
    """Its half-written files would be misattributed (the Global Constraint)."""
    other = kr.Operation(root=root, writer="other")
    other.mutation_id = kr._insert_mutation(other)      # ACTIVE, not ours
    (root / "wiki" / "a.md").write_text("# a\nwritten by the other operation\n")
    _managed_write(root)
    assert kr.external_observations(root) == []


def test_an_unstable_store_is_not_minted_and_the_write_proceeds(root, monkeypatch):
    """Obsidian mid-save. Bound: this edit is absorbed, as before; recorded so
    nobody claims the gap is fully closed."""
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    def unstable(base, trigger="manual"):
        raise kr.DriftUnobservable("the store is still changing (injected)")
    monkeypatch.setattr(kr, "observe_external_drift", unstable)
    start = kr.current(root).revision
    _managed_write(root)
    assert kr.current(root).revision == start + 1
    assert kr.external_observations(root) == []


def test_a_derived_subtree_edit_is_not_drift(root):
    (root / "wiki" / "traces").mkdir()
    (root / "wiki" / "traces" / "t.md").write_text("trace")
    start = kr.current(root).revision
    _managed_write(root)
    assert kr.current(root).revision == start + 1
    assert kr.external_observations(root) == []


def test_the_cli_records_a_hand_edit_end_to_end(root):
    """The 2026-09-24 report, item 3, as a subprocess: the real CLI boundary."""
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO),
           "ZURVAN_CONFIG_DIR": str(root / "config")}
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), "decision", "add",
         "--title", "T", "--reason", "R", "--status", "accepted",
         "--project", "zurvan"],
        cwd=str(root), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    [row] = kr.external_observations(root)
    assert row[1] == "activation"


def test_status_reports_unrecorded_drift_without_writing(root):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    fingerprint = kr.ledger_fingerprint(root)
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO)}
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), "knowledge", "status"],
        cwd=str(root), env=env, capture_output=True, text=True).stdout
    assert "drift:      UNRECORDED" in out
    assert kr.ledger_fingerprint(root) == fingerprint


# ── final review fixes ──────────────────────────────────────────────────────

def test_an_ingest_is_not_recorded_as_an_external_edit(root):
    """Final review, Important 1. `ingest.py` writes its source page and the
    wiki index directly, and reaches `safe_write` only at the log line; with
    lazy activation, its own direct writes were minted as an external edit
    underneath the running operation. Its operation now activates eagerly."""
    raw = root / "raw" / "notes"
    raw.mkdir(parents=True)
    (raw / "note.txt").write_text("A small source document about provenance.\n")
    start = kr.current(root).revision
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO),
           "ZURVAN_EMBED_PROVIDER": "mock", "ZURVAN_EMBED_MODEL": "mock_model"}
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ingest.py"), str(raw / "note.txt")],
        cwd=str(root), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert kr.external_observations(root) == []
    assert kr.current(root).revision == start + 1


def test_a_dead_writers_row_does_not_disable_absorption(root):
    """Final review, Important 2. A crashed writer left an ACTIVE row; every
    later write then skipped absorption and the hand edit was laundered."""
    dead = kr.Operation(root=root, writer="crashed")
    dead.mutation_id = kr._insert_mutation(dead)
    conn = kr._connect(root)
    try:
        conn.execute("UPDATE mutations SET owner_pid = 999999,"
                     " owner_identity = '999999:gone' WHERE mutation_id = ?",
                     (dead.mutation_id,))
        conn.commit()
    finally:
        conn.close()
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    _managed_write(root)
    [row] = kr.external_observations(root)
    assert row[1] == "activation"


def test_status_does_not_promise_a_mint_it_will_not_make(root):
    """Final review, Minor 4 (fixed with Important 2 - same code). On a
    SUSPECT store the next write does not record drift; the line said it did."""
    kr._mark_suspect(root)
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO)}
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), "knowledge", "status"],
        cwd=str(root), env=env, capture_output=True, text=True).stdout
    assert "drift:      UNRECORDED" in out
    assert "reconcile" in out.split("drift:")[1]
    assert "the next write records it" not in out


def test_the_source_registry_follows_zurvan_root(tmp_path, monkeypatch):
    """Found while fixing Important 1: `scripts/db.py` pinned `sources` to the
    repo's `data/registry.sqlite` whatever ZURVAN_ROOT said, while the ledger
    in the same file followed it. A subprocess ingest test therefore wrote a
    row into the LIVE registry (removed by hand; backup in ~/.zurvan/forensics).
    A read is used here so a red run cannot contaminate anything."""
    import scripts.db as db
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    db.find_source_by_hash("no-such-hash")
    assert (tmp_path / "data" / "registry.sqlite").is_file()


def test_an_eager_operation_that_changes_nothing_mints_nothing(root):
    """An ingest that finds its source already ingested writes nothing; its
    eager mutation must end ABORTED, not mint an empty revision. Written after
    the code, so its failure is shown by falsification instead of first-run."""
    start = kr.current(root).revision
    with kr.operation(writer="ingest", root=root, eager=True):
        pass
    assert kr.current(root).revision == start
    assert kr.mutations_in(kr.COMMITTED, root) == []


def test_an_eager_operations_direct_writes_are_its_own_revision(root):
    """The eager case the ingest test exercises end to end, in-process."""
    start = kr.current(root).revision
    with kr.operation(writer="ingest", root=root, eager=True):
        (root / "wiki" / "direct.md").write_text("written without safe_write\n")
    assert kr.current(root).revision == start + 1
    assert kr.current(root).atomicity == kr.MANAGED
    assert kr.external_observations(root) == []
