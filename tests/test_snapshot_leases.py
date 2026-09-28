"""Design §8: a snapshot holds a lease, and GC reclaims it only when the lease
has expired AND its owner is provably dead. Either alone is not enough."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from scripts import knowledge_revision as kr
from scripts import measurement_snapshot as ms


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    return tmp_path


def _capture(store, label="t"):
    return ms.capture(root=store, label=label, components=("wiki",))


def _dead_pid():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _rewrite_lease(snapshot, **changes):
    lease = json.loads(snapshot.lease_path.read_text())
    lease.update(changes)
    snapshot.lease_path.write_text(json.dumps(lease))


def _ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def test_a_capture_takes_a_lease_naming_its_owner(store):
    with _capture(store) as s:
        lease = json.loads(s.lease_path.read_text())
        assert lease["snapshot_id"] == s.snapshot_id
        assert lease["owner_pid"] == os.getpid()
        assert lease["process_start_identity"] == kr.own_identity()
        assert lease["purpose"] == "legacy"
        assert lease["knowledge_revision"] is None
        assert lease["referenced_artifacts"] == ["wiki"]
        assert lease["ttl_s"] == ms.DEFAULT_LEASE_TTL_S
        assert lease["state"] == "HELD"


def test_heartbeat_renews_the_lease(store):
    with _capture(store) as s:
        _rewrite_lease(s, last_heartbeat=_ago(7200))
        s.heartbeat()
        beat = datetime.fromisoformat(json.loads(s.lease_path.read_text())["last_heartbeat"])
        assert (datetime.now(timezone.utc) - beat).total_seconds() < 60


def test_a_legacy_capture_says_it_is_unverified(store):
    """Design §9.1: a legacy capture proves no revision, and says so."""
    with _capture(store) as s:
        coherence = s.identity["coherence"]
        assert coherence["status"] == "UNVERIFIED"
        assert coherence["components"]["claims"]["status"] == "UNSUPPORTED_BY_DESIGN"
        on_disk = json.loads((s.path / "manifest.json").read_text())
        assert on_disk["coherence"]["status"] == "UNVERIFIED"


def test_gate_19_an_unadapted_claims_ledger_fails_capture_closed(store):
    (store / "data" / "claims.sqlite").write_bytes(b"")
    with pytest.raises(ms.SnapshotComponentError, match="claims"):
        _capture(store)
    snapshots = ms.snapshots_dir(store)
    assert not snapshots.exists() or list(snapshots.iterdir()) == []
