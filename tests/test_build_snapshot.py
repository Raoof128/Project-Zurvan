"""A build snapshot is proven to be knowledge revision R, or it is not taken.

Design §6.1: a rebuild that reads the live corpus while it advances indexed no
single revision, so stamping any revision is false. The proof is §5.1's double
manifest - the live manifest before and after the clone both equal the
ledger's digest at R, with the revision unchanged in between (amendment 2:
a clone has new inodes, so the snapshot's own manifest can never match).
"""

import pytest

from scripts import build_snapshot as bsn
from scripts import knowledge_revision as kr


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _managed_write(root, name):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / name), f"# {name}\n")


def test_a_build_snapshot_names_the_revision_it_holds(store):
    _managed_write(store, "b.md")
    with bsn.capture_build_inputs(store) as bs:
        assert bs.revision == kr.current(store).revision
        assert (bs.path / "wiki" / "b.md").is_file()
        assert bs.stamp()["built_from_revision"] == str(bs.revision)
        assert bs.stamp()["built_from_snapshot"] == bs.snapshot.snapshot_id


def test_gate_18_a_suspect_store_is_refused(store):
    kr._mark_suspect(store)
    with pytest.raises(bsn.BuildSnapshotRefused, match="SUSPECT"):
        bsn.capture_build_inputs(store)


def test_gate_12_capture_mints_a_completed_unmanaged_edit(store):
    (store / "wiki" / "a.md").write_text("# a\nhand edit\n")
    with bsn.capture_build_inputs(store) as bs:
        assert bs.atomicity == kr.EXTERNAL_OBSERVED
        [row] = kr.external_observations(store)
        assert row[0] == bs.revision and row[1] == "capture"


def test_a_write_during_capture_is_retried_then_refused(store, monkeypatch):
    """Gate item 11: a write during capture causes retry, never a mixed state."""
    import scripts.build_snapshot as mod
    calls = {"n": 0}
    real = mod._live_manifest
    def moving(base):
        calls["n"] += 1
        return real(base) + ("" if calls["n"] % 2 else f"-moved-{calls['n']}")
    monkeypatch.setattr(mod, "_live_manifest", moving)
    with pytest.raises(bsn.BuildSnapshotNotReady):
        bsn.capture_build_inputs(store, attempts=3)
    snapshots = store / "data" / "snapshots"
    assert not snapshots.exists() or list(snapshots.iterdir()) == []


def test_one_moving_attempt_then_a_still_one_succeeds(store, monkeypatch):
    import scripts.build_snapshot as mod
    calls = {"n": 0}
    real = mod._live_manifest
    def once(base):
        calls["n"] += 1
        return real(base) + ("-moved" if calls["n"] == 2 else "")
    monkeypatch.setattr(mod, "_live_manifest", once)
    with bsn.capture_build_inputs(store, attempts=3) as bs:
        assert bs.revision == kr.current(store).revision


def test_a_dead_writer_does_not_block_capture(store):
    dead = kr.Operation(root=store, writer="crashed")
    dead.mutation_id = kr._insert_mutation(dead)
    conn = kr._connect(store)
    try:
        conn.execute("UPDATE mutations SET owner_pid = 999999, owner_identity ="
                     " '999999:gone' WHERE mutation_id = ?", (dead.mutation_id,))
        conn.commit()
    finally:
        conn.close()
    with bsn.capture_build_inputs(store) as bs:
        assert bs.revision == kr.current(store).revision


def test_capture_waits_for_a_live_operation_to_finish(store):
    """A real second process holds an eager operation for 1.5 s. Capture must
    wait it out, not give up (the gauntlet reproduced a 0.64 s refusal)."""
    import os, subprocess, sys, time
    holder = subprocess.Popen([sys.executable, "-c", (
        "import time\n"
        "from scripts import knowledge_revision as kr\n"
        f"with kr.operation(writer='long', root={str(store)!r}, eager=True):\n"
        f"    open({str(store / 'wiki' / 'held.md')!r}, 'w').write('x')\n"
        "    time.sleep(1.5)\n")], env={**os.environ, "PYTHONPATH": "."})
    try:
        deadline = time.monotonic() + 10
        while not kr.mutations_in(kr.ACTIVE, store):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with bsn.capture_build_inputs(store) as bs:
            assert (bs.path / "wiki" / "held.md").is_file()
    finally:
        holder.wait()


def test_a_capture_that_waits_too_long_names_who_it_waited_for(store):
    other = kr.Operation(root=store, writer="still-running")
    other.mutation_id = kr._insert_mutation(other)        # owned by THIS live pid
    with pytest.raises(bsn.BuildSnapshotBusy, match="still-running"):
        bsn.capture_build_inputs(store, wait_s=0.5)


def test_a_store_with_no_baseline_yields_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    with bsn.inputs_for_rebuild(tmp_path) as bs:
        assert bs is None


def test_release_removes_the_snapshot(store):
    bs = bsn.capture_build_inputs(store)
    path = bs.path
    bs.release()
    assert not path.exists()


# ── Task 3: the search index ────────────────────────────────────────────────

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")


def _sources(db):
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT source_path FROM chunks")}
    finally:
        conn.close()


def test_gate_17_a_rebuild_stamps_the_revision_it_read(store, mock_env):
    """The corpus advances while the rebuild runs. The stamp is the revision
    that was READ, and the page written afterwards is not in the index -
    proof the build read the snapshot, not live."""
    from scripts.rebuild_search_index import rebuild_search_index, read_index_meta
    # Not data/search.sqlite: under ZURVAN_ROOT that is the CANONICAL path,
    # which refuses mock embeddings (the exact-provider invariant).
    db = store / "data" / "s.sqlite"
    with bsn.capture_build_inputs(store) as bs:
        read_at = bs.revision
        _managed_write(store, "after.md")                # live moves on
        rebuild_search_index(str(db), snapshot=bs)
    meta = read_index_meta(db)
    assert meta["built_from_revision"] == str(read_at)
    assert kr.current(store).revision == read_at + 1
    assert "wiki/after.md" not in _sources(db)
    assert "wiki/a.md" in _sources(db)


def test_snapshot_and_root_cannot_both_be_given(store, mock_env):
    from scripts.rebuild_search_index import rebuild_search_index
    with bsn.capture_build_inputs(store) as bs:
        with pytest.raises(ValueError):
            rebuild_search_index(str(store / "data" / "s.sqlite"), root=store,
                                 snapshot=bs)


# ── Task 4: the graph ───────────────────────────────────────────────────────

def test_the_graph_is_built_from_the_snapshot_and_stamped(store):
    import scripts.graph_build as gb
    from scripts.graph_schema import read_graph_meta
    db = str(store / "data" / "graph.sqlite")
    with bsn.capture_build_inputs(store) as bs:
        read_at = bs.revision
        _managed_write(store, "after.md")
        gb.build_graph(db, snapshot=bs)
    meta = read_graph_meta(db)
    assert meta["built_from_revision"] == str(read_at)
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        paths = {r[0] for r in conn.execute("SELECT path FROM nodes")}
    finally:
        conn.close()
    assert "wiki/a.md" in paths and "wiki/after.md" not in paths
