"""Design §7.1-7.2: a measurement capture proves every REQUIRED component is
at one knowledge revision, or it refuses - scoped to what the caller claimed."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_snapshot as bsn
from scripts import knowledge_revision as kr
from scripts import measurement_snapshot as ms

MEASURE = ("wiki", "search", "graph")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    # Not data/search.sqlite: under ZURVAN_ROOT that is the CANONICAL path,
    # which refuses mock embeddings. The override also moves capture_search
    # and derived_health with it.
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "data" / "s.sqlite"))
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n\nalpha page\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _write(store, name, body="x\n"):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=store):
        write_file_safely(str(store / "wiki" / name), body)


def _rebuild(store, kinds=("search", "graph")):
    import scripts.graph_build as gb
    from scripts.rebuild_search_index import rebuild_search_index
    with bsn.capture_build_inputs(store) as bs:
        if "search" in kinds:
            rebuild_search_index(os.environ["ZURVAN_SEARCH_DB"], snapshot=bs)
        if "graph" in kinds:
            gb.build_graph(str(store / "data" / "graph.sqlite"), snapshot=bs)


def _snapshot_dirs(store):
    d = ms.snapshots_dir(store)
    return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


def _sources(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT source_path FROM chunks")}
    finally:
        conn.close()


def test_a_measurement_capture_of_a_current_store_is_verified(store):
    _rebuild(store)
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as bs:
        c = bs.snapshot.identity["coherence"]
        assert c["status"] == "VERIFIED"
        assert c["revision"] == kr.current(store).revision
        assert c["components"]["search"]["status"] == "VERIFIED"
        assert c["components"]["graph"]["status"] == "VERIFIED"
        assert c["components"]["claims"]["status"] == "UNSUPPORTED_BY_DESIGN"
        assert (bs.path / "search.sqlite").is_file()
        assert (bs.path / "graph.sqlite").is_file()
        lease = json.loads((bs.path / ms.LEASE_NAME).read_text())
        assert lease["purpose"] == "measurement"
        assert lease["knowledge_revision"] == bs.revision


def test_gate_8_disagreeing_components_fail_closed_naming_the_gap(store):
    _rebuild(store)
    _write(store, "b.md")
    _rebuild(store, kinds=("graph",))             # graph current, search one behind
    with pytest.raises(bsn.SnapshotNotReady) as exc:
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    assert str(exc.value).startswith("SNAPSHOT_NOT_READY:")
    assert exc.value.component == "search"
    assert "1 revision(s) behind" in str(exc.value)
    assert _snapshot_dirs(store) == []


def test_refusal_is_scoped_to_what_the_caller_required(store):
    """Design §7.2: the same state captures fine for a caller that did not
    require the stale store."""
    _rebuild(store)
    _write(store, "b.md")
    _rebuild(store, kinds=("graph",))
    with bsn.capture_coherent(store, purpose="graph-only",
                              required=("wiki", "graph")) as bs:
        components = bs.snapshot.identity["coherence"]["components"]
        assert components["search"]["status"] == "NOT_REQUIRED"
        assert not (bs.path / "search.sqlite").exists()


def test_gate_9_no_deadlock_from_a_stale_index(store):
    _rebuild(store)
    _write(store, "b.md")
    with pytest.raises(bsn.SnapshotNotReady):
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    _rebuild(store)                  # its authoritative capture succeeds on a stale store
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as bs:
        assert bs.revision == kr.current(store).revision


def test_an_unstamped_required_store_is_unverified_not_agreement(store):
    """Design §9.1: a missing stamp must never read as agreement."""
    import scripts.graph_build as gb
    _rebuild(store, kinds=("search",))
    gb.build_graph(str(store / "data" / "graph.sqlite"))          # live, unstamped
    with pytest.raises(bsn.SnapshotNotReady) as exc:
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    assert exc.value.component == "graph"
    assert exc.value.state == bsn.UNVERIFIED


def test_a_graph_from_before_stamps_existed_is_refused_not_a_crash(store):
    _rebuild(store)
    db = store / "data" / "graph.sqlite"
    os.chmod(db, 0o644)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE graph_meta")
    conn.commit()
    conn.close()
    os.chmod(db, 0o444)
    with pytest.raises(bsn.SnapshotNotReady) as exc:
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    assert exc.value.component == "graph"


def test_stamps_are_read_from_the_frozen_copy_not_the_live_store(store, monkeypatch):
    """The live index reads CURRENT at the pre-check and at verification, but
    the bytes frozen are an older build. Only a check of the FROZEN copy can
    refuse, and it must: the frozen copy is what a measurement reads.
    Gauntlet 2026-09-28: without this test, reading stamps from the live
    store passed every other test in this file."""
    from scripts import derived_health as dh
    live = Path(os.environ["ZURVAN_SEARCH_DB"])
    _rebuild(store)
    old = store / "old.sqlite"
    shutil.copy2(live, old)
    _write(store, "b.md")
    _rebuild(store)
    cur = store / "cur.sqlite"
    shutil.copy2(live, cur)
    real = ms._COMPONENTS["search"]

    def freeze_an_older_build(root, into):
        os.replace(old, live)
        try:
            return real(root, into)
        finally:
            os.replace(cur, live)

    monkeypatch.setitem(ms._COMPONENTS, "search", freeze_an_older_build)
    with pytest.raises(bsn.SnapshotNotReady) as exc:
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    assert exc.value.component == "search"
    assert dh.store_health("search", store)[0] == dh.CURRENT


def test_a_certain_refusal_freezes_nothing(store, monkeypatch):
    """A stale live store is refused before any byte is frozen (11.7 s ->
    0.17 s on the live store, gauntlet 2026-09-28)."""
    _rebuild(store)
    _write(store, "b.md")

    def must_not_run(*args, **kwargs):
        raise AssertionError("froze bytes for a refusal the live stamps already decided")

    monkeypatch.setattr(ms, "_freeze", must_not_run)
    with pytest.raises(bsn.SnapshotNotReady) as exc:
        bsn.capture_coherent(store, purpose="measurement", required=MEASURE)
    assert exc.value.component == "search"


def test_gate_1_a_capture_between_the_two_writes_of_decision_add_is_busy(store, monkeypatch):
    """The page is written and log.md is not yet appended: a snapshot here
    would be a half-operation. It must be BUSY, never a half-state."""
    import scripts.memory as memory
    import scripts.safe_write as safe_write
    monkeypatch.setattr(memory, "PROJECT_ROOT", store)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: store)
    monkeypatch.chdir(store)
    seen = {}
    real = memory.append_to_log

    def capture_first(action, details):
        with pytest.raises(bsn.BuildSnapshotBusy) as exc:
            bsn.capture_coherent(store, purpose="measurement",
                                 required=("wiki",), wait_s=0)
        seen["busy"] = str(exc.value)
        real(action, details)

    monkeypatch.setattr(memory, "append_to_log", capture_first)
    with kr.operation(writer="decision add", root=store):
        assert memory.add_decision("T", "R", "accepted", ["t"], project="zurvan")
    assert "decision add" in seen["busy"]
    assert _snapshot_dirs(store) == []


def test_gate_13_a_snapshot_of_an_external_revision_says_so(store):
    _rebuild(store)
    (store / "wiki" / "a.md").write_text("# a\n\nhand edit\n")
    _rebuild(store)                  # its capture mints EXTERNAL_OBSERVED first
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as bs:
        assert bs.atomicity == kr.EXTERNAL_OBSERVED
        assert bs.snapshot.identity["coherence"]["atomicity"] == kr.EXTERNAL_OBSERVED


@pytest.mark.parametrize("required, match", [
    (("search",), "must include 'wiki'"),
    (("wiki", "vectors"), "unknown required"),
    (("wiki", "claims"), "UNSUPPORTED_BY_DESIGN"),
])
def test_required_is_validated_at_the_call_site(store, required, match):
    with pytest.raises(ValueError, match=match):
        bsn.capture_coherent(store, purpose="measurement", required=required)


def test_measurement_capture_is_reachable_through_capture(store):
    _rebuild(store)
    with ms.capture(root=store, purpose="measurement", required=MEASURE) as s:
        assert s.identity["coherence"]["status"] == "VERIFIED"
        assert s.index_path == s.path / "search.sqlite"


def test_components_and_required_are_exclusive(store):
    with pytest.raises(ms.SnapshotComponentError, match="not both"):
        ms.capture(root=store, components=("wiki",), required=("wiki",))


def test_a_rebuild_snapshot_records_its_purpose_in_its_lease(store):
    with bsn.capture_build_inputs(store, label="rebuild_graph") as bs:
        lease = json.loads((bs.path / ms.LEASE_NAME).read_text())
        assert lease["purpose"] == "rebuild_graph"
        assert lease["knowledge_revision"] == bs.revision
        assert bs.snapshot.identity["coherence"]["components"]["search"]["status"] == "NOT_REQUIRED"


def test_the_script_can_take_a_coherent_capture(store):
    _rebuild(store)
    out = subprocess.run(
        [sys.executable, "scripts/measurement_snapshot.py", "--label", "m",
         "--require", "wiki,search,graph", "--path-only"],
        capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    assert out.returncode == 0, out.stderr
    path = Path(out.stdout.strip())
    assert path.name == "search.sqlite"
    manifest = json.loads((path.parent / "manifest.json").read_text())
    assert manifest["coherence"]["status"] == "VERIFIED"
    assert manifest["purpose"] == "m"
    shutil.rmtree(path.parent)


def test_gate_10_a_measurement_keeps_resolving_to_E_while_live_moves_to_E1(store):
    """Canonical §8.7: every measurement input resolves to E while live is B
    at E+1; no A/B hybrid is observable; a second snapshot sees E+1."""
    from scripts.graph_schema import read_graph_meta
    from scripts.rebuild_search_index import read_index_meta
    _rebuild(store)
    E = kr.current(store).revision
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as S:
        _write(store, "b.md", "# b\n\nbeta page\n")
        _rebuild(store)                                   # live is now B at E+1
        assert kr.current(store).revision == E + 1
        assert read_index_meta(S.path / "search.sqlite")["built_from_revision"] == str(E)
        assert read_graph_meta(S.path / "graph.sqlite")["built_from_revision"] == str(E)
        assert not (S.path / "wiki" / "b.md").exists()
        assert "wiki/b.md" not in _sources(S.path / "search.sqlite")
        assert "wiki/b.md" in _sources(os.environ["ZURVAN_SEARCH_DB"])
        with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as S2:
            assert S2.revision == E + 1


def test_eval_reports_the_coherence_of_the_snapshot_it_scored(store):
    """Gate item 13 at the consumer: an EXTERNAL_OBSERVED input is reported as
    such, never as MANAGED."""
    from scripts.eval_search import _coherence_of
    (store / "wiki" / "a.md").write_text("# a\n\nhand edit\n")
    _rebuild(store)
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as bs:
        c = _coherence_of(str(bs.path / "search.sqlite"))
    assert c["status"] == "VERIFIED"
    assert c["atomicity"] == kr.EXTERNAL_OBSERVED


def test_eval_of_a_legacy_snapshot_or_the_live_index_is_unverified(store):
    """Design §9.1: no retrieval evaluation claims coherence it was not given."""
    from scripts.eval_search import _coherence_of
    _rebuild(store)
    assert _coherence_of(None)["status"] == "UNVERIFIED"
    assert _coherence_of(os.environ["ZURVAN_SEARCH_DB"])["status"] == "UNVERIFIED"
    with ms.capture(root=store, label="legacy") as s:
        assert _coherence_of(str(s.index_path))["status"] == "UNVERIFIED"


def test_the_metrics_carry_snapshot_coherence(store, tmp_path):
    from scripts import eval_search
    _rebuild(store)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"query": "alpha page",
                                "expected_paths": [str((store / "wiki" / "a.md").resolve())]}) + "\n")
    with bsn.capture_coherent(store, purpose="measurement", required=MEASURE) as bs:
        metrics = eval_search.run_search_evaluation(
            gold_file=str(gold), hybrid=True, as_json=True,
            index_path=str(bs.path / "search.sqlite"))
    assert metrics["snapshot_coherence"]["status"] == "VERIFIED"


def test_gate_4_a_killed_writer_blocks_snapshots_not_writes_until_reconciled(store):
    """§10.1 item 4 as one sequence, with a REAL kill: the revision becomes
    SUSPECT, snapshots of it are refused, live writes continue, SUSPECT
    propagates across a later unrelated operation, and only reconcile clears
    it. Before this, the parts were proven separately and the refusal only
    against a simulated SUSPECT (`_mark_suspect`)."""
    import signal
    from scripts import knowledge_reconcile as krec
    child = subprocess.run([sys.executable, "-c", (
        "import os, signal\n"
        "from scripts import knowledge_revision as kr\n"
        "from scripts.safe_write import write_file_safely\n"
        f"with kr.operation(writer='doomed', root={str(store)!r}):\n"
        f"    write_file_safely({str(store / 'wiki' / 'torn.md')!r}, 'x')\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n")],
        env={**os.environ, "PYTHONPATH": "."})
    assert child.returncode == -signal.SIGKILL
    kr.detect_orphans(store)
    suspect = kr.current(store)
    assert suspect.state == kr.SUSPECT
    with pytest.raises(bsn.BuildSnapshotRefused, match="SUSPECT"):
        bsn.capture_coherent(store, purpose="measurement", required=("wiki",))
    _write(store, "unrelated.md")                  # live writes continue
    later = kr.current(store)
    assert later.revision == suspect.revision + 1
    assert later.state == kr.SUSPECT               # advancing is not repair
    result = krec.reconcile(root=store, accept=True, actor="test", reason="gate 4")
    assert result["status"] == "CLEAN"
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki",)) as bs:
        assert bs.revision == kr.current(store).revision


def test_gate_7_a_kill_between_the_bytes_and_the_stamp_is_investigable(store):
    """§10.1 item 7 with a REAL kill at exactly that point: the file is on
    disk, `completed_paths` never received it, and `planned_paths` still names
    it. Before this the item was proven only against a fabricated orphan row
    (closure audit, 2026-09-28)."""
    import signal
    target = store / "wiki" / "torn.md"
    child = subprocess.run([sys.executable, "-c", (
        "import os, signal\n"
        "from scripts import knowledge_revision as kr\n"
        "from scripts.safe_write import write_file_safely\n"
        "kr.complete_write = lambda *a, **k: os.kill(os.getpid(), signal.SIGKILL)\n"
        f"with kr.operation(writer='doomed', root={str(store)!r}):\n"
        f"    write_file_safely({str(target)!r}, 'x')\n")],
        env={**os.environ, "PYTHONPATH": "."})
    assert child.returncode == -signal.SIGKILL
    assert target.read_text() == "x"                        # the bytes landed
    [orphan] = kr.detect_orphans(store)
    assert [Path(p).resolve() for p in orphan.planned_paths] == [target.resolve()]
    assert orphan.completed_paths == []
    assert kr.current(store).state == kr.SUSPECT
