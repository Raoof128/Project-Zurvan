"""Z2 Task 12: claims snapshot component with a mutation sidecar; belief replay
agrees with snapshots (spec §7.6, gates G4 and G11)."""

import json

import pytest

from scripts import build_snapshot as bsn
from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr
from scripts import measurement_snapshot as ms
from scripts.safe_write import write_file_safely

SRC = "wiki/sources/s.md"
QUOTES = [f"fact number {n} is recorded here" for n in range(4)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text("\n".join(QUOTES) + "\n", encoding="utf-8")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path)
    return tmp_path


def _add(store, n):
    with kr.operation(writer="test", root=store):
        return cl.add(f"claim {n}", SRC, QUOTES[n], confidence="high", actor="a",
                      method="human", root=store)


def test_claims_can_be_required_and_are_captured(store):
    _add(store, 0)
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims")) as bs:
        assert (bs.path / "claims.sqlite").is_file()
        sidecar = json.loads((bs.path / "claims-mutations.json").read_text())
        assert all(v["state"] == "COMMITTED" for v in sidecar.values())
        c = bs.snapshot.identity["coherence"]["components"]["claims"]
        assert c["status"] == "VERIFIED" and c["head"]


def test_snapshot_belief_matches_as_of_revision(store):
    a = _add(store, 0)
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims")) as bs:
        R = bs.revision
        frozen = cl.snapshot_belief(bs.path)
        _add(store, 1)
        with kr.operation(writer="test", root=store):
            cl.retract(a, reason="later", basis=[], actor="a", method="human", root=store)
        assert frozen == cl.as_of_revision(R, store).claims
        assert cl.snapshot_belief(bs.path) == frozen          # the copy never moves
        assert cl.current(store)[a].status == "retracted"


def test_snapshot_includes_rows_of_a_reconciled_mutation(store):
    """A crashed claim write, once reconciled (RESOLVED), is knowledge from its
    resolution revision on; the sidecar carries that, so the snapshot agrees."""
    keep = _add(store, 0)
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=store):
            cl.add("crashed then accepted", SRC, QUOTES[1], confidence="low", actor="a",
                   method="human", root=store)
            raise RuntimeError("crash after the write")
    from scripts import knowledge_reconcile as krec
    krec.reconcile(store, actor="test")
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims")) as bs:
        frozen = cl.snapshot_belief(bs.path)
    assert set(frozen) == set(cl.current(store))
    conn = cs.connect(store, readonly=True)
    total = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    assert total == 2 and keep in frozen


def test_snapshot_sidecar_hides_an_orphaned_row(store, tmp_path):
    """Rows of a mutation the sidecar does not list as COMMITTED/RESOLVED are
    invisible in the snapshot view, exactly as in the live view."""
    _add(store, 0)
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims")) as bs:
        side = bs.path / "claims-mutations.json"
        data = json.loads(side.read_text())
        (mid,) = list(data)
        data[mid]["state"] = "ORPHANED"
        side.chmod(0o644)
        side.write_text(json.dumps(data))
        assert cl.snapshot_belief(bs.path) == {}


def test_require_claims_refuses_while_claim_mutation_active(store):
    _add(store, 0)
    with kr.operation(writer="test", root=store):
        cl.add("in flight", SRC, QUOTES[2], confidence="high", actor="a", method="human",
               root=store)
        with pytest.raises(bsn.BuildSnapshotBusy):
            bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims"),
                                 wait_s=0.3)
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki", "claims")) as bs:
        assert len(cl.snapshot_belief(bs.path)) == 2


def test_claims_not_required_is_reported_as_such(store):
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki",)) as bs:
        assert bs.snapshot.identity["coherence"]["components"]["claims"]["status"] == "NOT_REQUIRED"
        assert not (bs.path / "claims.sqlite").exists()


def test_legacy_capture_never_refuses_on_a_claims_store(store):
    _add(store, 0)
    with ms.capture(root=store, components=("wiki",)) as s:      # the old guard raised here
        assert s.identity["coherence"]["components"]["claims"]["status"] == "NOT_REQUIRED"
