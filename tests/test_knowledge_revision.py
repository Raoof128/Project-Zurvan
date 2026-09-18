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


def _write(root, name, body="x"):
    """Publish bytes the way `safe_write` will: plan, write, complete."""
    target = page(root, name)
    ticket = kr.plan_write(target, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    kr.complete_write(ticket, target, root=root)
    return target


def test_a_read_only_operation_creates_no_mutation_row(root):
    """`zurvan search` must not become a registry write, and must not make a
    capture report BUSY while it runs."""
    with kr.operation(writer="cli", root=root):
        pass
    conn = sqlite3.connect(kr.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) FROM mutations").fetchone()[0] == 0
    finally:
        conn.close()
    assert kr.current(root).revision == 0


def test_a_mutation_activates_on_the_first_planned_write(root):
    with kr.operation(writer="cli", root=root):
        assert kr.mutations_in(kr.ACTIVE, root) == []
        _write(root, "a.md")
        assert len(kr.mutations_in(kr.ACTIVE, root)) == 1
    assert kr.current(root).revision == 1


def test_intent_is_recorded_before_the_bytes_land(root):
    """The crash-gap property. Between plan and publish, the ledger must
    already name the path that is about to change."""
    target = page(root, "a.md")
    with kr.operation(writer="cli", root=root):
        ticket = kr.plan_write(target, root=root)
        m = kr.mutations_in(kr.ACTIVE, root)[0]
        assert str(target) in m.planned_paths
        assert m.completed_paths == []
        target.write_text("x")
        kr.complete_write(ticket, target, root=root)


def test_a_crash_between_publishing_and_completing_is_not_read_as_clean(root):
    """Rev 2's defect. The file landed; bookkeeping never recorded it. The
    store must not come back CLEAN just because `completed_paths` is empty."""
    target = page(root, "a.md")
    mid = None
    with pytest.raises(RuntimeError):
        with kr.operation(writer="cli", root=root):
            kr.plan_write(target, root=root)
            target.write_text("x")               # bytes are live
            mid = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
            raise RuntimeError("killed here")
    assert kr.current(root).state == kr.SUSPECT
    m = [x for x in kr.mutations_in(kr.ORPHANED, root) if x.mutation_id == mid]
    assert m and m[0].planned_paths == [str(target)]


def test_three_writes_in_one_operation_are_one_revision(root):
    with kr.operation(writer="cli", root=root):
        for name in ("a.md", "b.md", "log.md"):
            _write(root, name)
    assert kr.current(root).revision == 1


def test_a_nested_operation_joins_rather_than_splitting(root):
    """A single module global let an inner operation clear the active id, so
    the outer operation's later writes silently became implicit."""
    with kr.operation(writer="outer", root=root):
        _write(root, "a.md")
        outer = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
        with kr.operation(writer="inner", root=root):
            _write(root, "b.md")
        assert [m.mutation_id for m in kr.mutations_in(kr.ACTIVE, root)] == [outer]
        _write(root, "c.md")
    assert kr.current(root).revision == 1
    conn = sqlite3.connect(kr.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) FROM mutations").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_nested_operation_on_a_different_store_is_refused(root, tmp_path):
    other = tmp_path / "other"
    (other / "data").mkdir(parents=True)
    (other / "wiki").mkdir()
    kr.ensure_schema(other)
    with kr.operation(writer="outer", root=root):
        with pytest.raises(kr.NestedMutationError):
            with kr.operation(writer="inner", root=other):
                pass


def test_a_clean_exit_commits_rather_than_abandoning(root):
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            _write(root, "a.md")
            raise SystemExit(0)
    assert kr.current(root).revision == 1
    assert kr.current(root).state == kr.CLEAN


def test_a_failing_exit_that_wrote_nothing_does_not_suspect_the_store(root):
    """~20 dispatch branches in cli.py call sys.exit(1) on validation failure,
    before touching the store."""
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            raise SystemExit(1)
    assert kr.current(root).state == kr.CLEAN
    assert kr.current(root).revision == 0


def test_a_failing_exit_after_a_write_is_suspect(root):
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            _write(root, "a.md")
            raise SystemExit(1)
    assert kr.current(root).state == kr.SUSPECT


def test_a_write_outside_the_authoritative_scope_creates_no_mutation(root):
    (root / "docs").mkdir(exist_ok=True)
    with kr.operation(writer="cli", root=root):
        assert kr.plan_write(root / "docs" / "x.md", root=root) is None
    assert kr.current(root).revision == 0


def test_committing_twice_is_refused_rather_than_advancing_twice(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
        mid = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
    assert kr.current(root).revision == 1
    with pytest.raises(kr.MutationStateError):
        kr.commit_mutation(mid, root=root)
    assert kr.current(root).revision == 1


def test_no_observer_sees_a_half_completed_logical_operation(root):
    observed = []

    def observe():
        if kr.mutations_in(kr.ACTIVE, root):
            observed.append("BUSY")
        else:
            observed.append(kr.current(root).revision)

    observe()
    with kr.operation(writer="cli", root=root):
        _write(root, "decisions/foo.md")
        observe()
        _write(root, "log.md")
    observe()

    assert observed == [0, "BUSY", 1]


def test_safe_write_records_intent_before_publishing(root, monkeypatch):
    """If the ledger cannot record the intent, the bytes must not land."""
    from scripts import safe_write

    def refuse(*a, **k):
        raise kr.LedgerError("ledger down")

    monkeypatch.setattr(kr, "plan_write", refuse)
    target = page(root, "a.md")
    with pytest.raises(kr.LedgerError):
        safe_write.write_file_safely(str(target), "alpha\n")
    assert not target.exists()


def test_safe_write_joins_the_active_operation(root):
    from scripts import safe_write
    with kr.operation(writer="cli", root=root):
        safe_write.write_file_safely(str(page(root, "a.md")), "alpha\n")
        safe_write.append_file_safely(str(page(root, "log.md")), "line\n")
        actives = kr.mutations_in(kr.ACTIVE, root)
        assert len(actives) == 1
        assert len(actives[0].completed_paths) == 2
    assert kr.current(root).revision == 1


def test_a_write_outside_any_operation_is_one_implicit_mutation(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "alpha\n")
    assert kr.current(root).revision == 1
    assert len(kr.implicit_mutations(root)) == 1


def test_two_unwrapped_writes_are_two_implicit_mutations(root):
    """The compatibility door, and its limit. An observer sampling between
    them sees no ACTIVE mutation and a coherent-looking revision over a
    half-finished operation. Z0 may detect and report this shape; it may not
    claim atomicity for it."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "decisions/foo.md")), "page\n")
    between = (kr.mutations_in(kr.ACTIVE, root), kr.current(root).revision)
    safe_write.append_file_safely(str(page(root, "log.md")), "line\n")
    assert between == ([], 1)
    assert len(kr.implicit_mutations(root)) == 2


def test_a_write_outside_the_wiki_touches_no_ledger(root):
    from scripts import safe_write
    (root / "docs").mkdir(exist_ok=True)
    safe_write.write_file_safely(str(root / "docs" / "x.md"), "x")
    assert kr.current(root).revision == 0


def test_the_caller_is_the_real_caller_not_the_plumbing(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    # Substring checks are wrong here: this test MODULE is named
    # `test_knowledge_revision`, so "knowledge_revision" appears in a correct
    # answer. The property is which FRAME was recorded — the caller, not the
    # plumbing it went through.
    assert caller.endswith(":test_the_caller_is_the_real_caller_not_the_plumbing")
    assert not caller.startswith("scripts.safe_write")
    assert not caller.startswith("scripts.knowledge_revision")


def test_resolving_a_caller_empties_the_inventory_without_deleting_history(root):
    """The closure gate must be reachable. Counting historical rows forever
    meant `inventory empty` could only be achieved by erasing evidence."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    assert len(kr.implicit_mutations(root)) == 1

    resolved = kr.resolve_implicit(caller, actor="local:raouf", root=root)

    assert resolved == 1
    assert kr.implicit_mutations(root) == []                     # unresolved
    assert len(kr.implicit_mutations(root, unresolved_only=False)) == 1


def test_a_new_implicit_write_from_a_resolved_caller_reopens_the_inventory(root):
    """Resolution is a claim about a caller, and a claim a later write can
    falsify. Closure means no UNRESOLVED implicit callers, not 'we looked'."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    kr.resolve_implicit(caller, actor="local:raouf", root=root)
    assert kr.implicit_mutations(root) == []

    safe_write.write_file_safely(str(page(root, "b.md")), "y")

    assert len(kr.implicit_mutations(root)) == 1


def test_the_caller_summary_is_a_query_not_an_audit(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    safe_write.write_file_safely(str(page(root, "b.md")), "y")
    summary = kr.implicit_callers(root)
    assert sum(summary.values()) == 2
    assert all(":" in k for k in summary)


def test_committing_records_the_manifest_the_revision_represents(root):
    from scripts import knowledge_manifest as km
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    assert kr.current(root).manifest_digest == km.authoritative_manifest(root)


def test_an_existing_store_must_be_bootstrapped_before_drift_means_anything(root):
    """A fresh ledger records no digest, so there is nothing to compare
    against. Answering `None` and feeding it to an UPDATE against a NOT NULL
    column was measured as an IntegrityError."""
    page(root, "a.md").write_text("alpha\n")
    with pytest.raises(kr.LedgerError):
        kr.observe_external_drift(root)


def test_bootstrap_records_the_stable_manifest_as_external_observed(root):
    from scripts import knowledge_manifest as km
    page(root, "a.md").write_text("alpha\n")

    new = kr.bootstrap(root, actor="local:raouf")

    rev = kr.current(root)
    assert new == 1
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.EXTERNAL_OBSERVED
    assert rev.manifest_digest == km.authoritative_manifest(root)


def test_no_drift_is_reported_for_a_store_only_zurvan_touched(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    assert kr.observe_external_drift(root) is None


def test_a_completed_edit_outside_zurvan_mints_an_external_observed_revision(root):
    """The hole the double manifest cannot see: the edit finished long before
    anyone looked, so before/after stability holds perfectly. Ordinary manual
    editing is a supported workflow, so this is CLEAN with a weaker atomicity
    claim, not a broken store."""
    from scripts import knowledge_manifest as km
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    before = kr.current(root).revision

    page(root, "a.md").write_text("edited in Obsidian\n")     # never told Zurvan
    new = kr.observe_external_drift(root)

    rev = kr.current(root)
    assert new == before + 1
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.EXTERNAL_OBSERVED
    assert rev.manifest_digest == km.authoritative_manifest(root)
    assert kr.observe_external_drift(root) is None


def test_a_managed_operation_after_drift_restores_managed_atomicity(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    page(root, "a.md").write_text("edited\n")
    kr.observe_external_drift(root)
    assert kr.current(root).atomicity == kr.EXTERNAL_OBSERVED

    with kr.operation(writer="cli", root=root):
        _write(root, "b.md")

    assert kr.current(root).atomicity == kr.MANAGED


def test_external_observed_claims_stability_not_a_transaction(root):
    """It asserts these bytes formed one stable observed filesystem state. It
    does NOT assert one logical user transaction, and no consumer may read it
    as MANAGED."""
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    page(root, "a.md").write_text("edited\n")
    kr.observe_external_drift(root)
    assert kr.current(root).atomicity != kr.MANAGED


def test_drift_is_refused_while_an_operation_is_in_flight(root):
    """Minting a revision underneath a running mutation would attribute that
    mutation's half-written files to an external observation.

    The first operation is not scene-setting. Without it the store has no
    recorded digest, `observe_external_drift` raises the BOOTSTRAP error first,
    and this test passes while exercising nothing it claims to.
    """
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")                       # commits; digest recorded
    with kr.operation(writer="cli", root=root):
        _write(root, "b.md")                       # now in flight
        with pytest.raises(kr.LedgerError, match="in flight"):
            kr.observe_external_drift(root)


def test_drift_is_refused_while_the_store_is_suspect(root):
    """Same trap: bootstrap first, or the bootstrap error answers instead."""
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")                       # commits; digest recorded
    with pytest.raises(RuntimeError):
        with kr.operation(writer="cli", root=root):
            _write(root, "b.md")
            raise RuntimeError("torn")
    assert kr.current(root).state == kr.SUSPECT
    with pytest.raises(kr.LedgerError, match="SUSPECT"):
        kr.observe_external_drift(root)


def _orphan(root, planned, completed=()):
    """A process that died mid-mutation: an ACTIVE row whose owner identity
    belongs to no live process. Nothing is killed for real — a test that kills
    processes to check a database column is testing the wrong thing.

    Every path is absolute and under this test's root.
    """
    op = kr.Operation(root=root, project="other", writer="test")
    mid = kr._insert_mutation(op)
    for p in planned:
        kr._append_path(mid, "planned_paths", p, root)
    for p in completed:
        kr._append_path(mid, "completed_paths", p, root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute(
            "UPDATE mutations SET owner_pid = ?, owner_identity = ?"
            " WHERE mutation_id = ?", (2_000_000, "2000000:long ago", mid))
        conn.commit()
    finally:
        conn.close()
    return mid


def test_a_dead_owner_that_planned_a_write_is_an_orphan(root):
    a, b = str(page(root, "decisions/x.md")), str(page(root, "log.md"))
    mid = _orphan(root, planned=[a, b], completed=[a])

    orphans = kr.detect_orphans(root)

    assert [m.mutation_id for m in orphans] == [mid]
    assert kr.current(root).state == kr.SUSPECT


def test_a_crash_after_publishing_but_before_completing_still_orphans(root):
    """Keying this on `completed_paths` made it empty in precisely this crash,
    so the store came back CLEAN while the file had already changed."""
    a = str(page(root, "decisions/x.md"))
    _orphan(root, planned=[a], completed=[])

    orphans = kr.detect_orphans(root)

    assert len(orphans) == 1
    assert orphans[0].planned_paths == [a]
    assert kr.current(root).state == kr.SUSPECT


def test_an_orphan_names_the_path_that_never_completed(root):
    a, b = str(page(root, "decisions/x.md")), str(page(root, "log.md"))
    _orphan(root, planned=[a, b], completed=[a])
    orphan = kr.detect_orphans(root)[0]
    assert set(orphan.planned_paths) - set(orphan.completed_paths) == {b}
    assert orphan.orphaned_at


def test_a_dead_owner_that_planned_nothing_is_aborted_not_suspect(root):
    _orphan(root, planned=[])
    assert kr.detect_orphans(root) == []
    assert kr.current(root).state == kr.CLEAN


def test_a_live_owner_is_never_orphaned(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
        assert kr.detect_orphans(root) == []
        assert kr.current(root).state == kr.CLEAN


def test_an_uninspectable_owner_is_left_alone(monkeypatch, root):
    """`ps` failing is not evidence of death. Orphaning on UNKNOWN would mark
    the store SUSPECT because a subprocess timed out."""
    a = str(page(root, "a.md"))
    _orphan(root, planned=[a])
    monkeypatch.setattr(kr, "process_state", lambda *a, **k: kr.UNKNOWN)
    assert kr.detect_orphans(root) == []
    assert kr.current(root).state == kr.CLEAN
    assert len(kr.mutations_in(kr.ACTIVE, root)) == 1


def test_a_mutation_that_commits_during_detection_is_not_reclassified(root,
                                                                     monkeypatch):
    """The read-then-update race: reading the active list on one connection and
    updating on another let a commit in between be overwritten."""
    a = str(page(root, "a.md"))
    mid = _orphan(root, planned=[a])

    def commit_then_report_dead(*args, **kwargs):
        kr.commit_mutation(mid, manifest_digest="x", root=root)
        return kr.DEAD

    monkeypatch.setattr(kr, "process_state", commit_then_report_dead)
    assert kr.detect_orphans(root) == []
    assert kr.mutations_in(kr.COMMITTED, root)[0].mutation_id == mid


def test_live_writes_continue_while_a_revision_is_suspect(root):
    _orphan(root, planned=[str(page(root, "a.md"))])
    kr.detect_orphans(root)
    assert kr.current(root).state == kr.SUSPECT

    with kr.operation(writer="cli", root=root):
        _write(root, "later.md")

    assert kr.current(root).revision == 1


def test_suspect_propagates_across_an_unrelated_later_operation(root):
    """If R held a half-written page and the next operation only appends a
    session log, R+1 still holds the half-written page. Advancing is not
    repair."""
    _orphan(root, planned=[str(page(root, "a.md")), str(page(root, "b.md"))],
            completed=[str(page(root, "a.md"))])
    kr.detect_orphans(root)

    with kr.operation(writer="cli", root=root):
        _write(root, "log.md")

    assert kr.current(root).state == kr.SUSPECT
