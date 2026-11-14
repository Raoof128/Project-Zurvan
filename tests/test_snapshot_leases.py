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
        assert coherence["components"]["claims"]["status"] == "NOT_REQUIRED"   # Z2
        on_disk = json.loads((s.path / "manifest.json").read_text())
        assert on_disk["coherence"]["status"] == "UNVERIFIED"


def test_gate_19_an_unadapted_claims_ledger_fails_capture_closed(store, monkeypatch):
    """Gate 19 after Z2: the claim ledger HAS an adapter (`capture_claims`), so
    the guarantee is now (a) a damaged ledger fails a capture that requires it,
    leaving no snapshot, and (b) the original guard still fires if the adapter
    is ever unregistered - a snapshot is never silently blind to claims."""
    (store / "data" / "claims.sqlite").write_bytes(b"not a database")
    with pytest.raises(ms.SnapshotComponentError, match="claim"):
        ms.capture(root=store, components=("wiki", "claims"))
    snapshots = ms.snapshots_dir(store)
    assert not snapshots.exists() or list(snapshots.iterdir()) == []
    monkeypatch.delitem(ms._COMPONENTS, "claims")
    with pytest.raises(ms.SnapshotComponentError, match="claims"):
        _capture(store)
    assert not snapshots.exists() or list(snapshots.iterdir()) == []


def test_gc_reclaims_an_expired_lease_whose_owner_is_dead(store):
    s = _capture(store)
    _rewrite_lease(s, owner_pid=_dead_pid(), process_start_identity="gone",
                   last_heartbeat=_ago(7200))
    assert ms.collect_garbage(store) == [str(s.path)]
    assert not s.path.exists()


def test_gc_keeps_a_dead_owners_unexpired_lease(store):
    """Design §8: crash cleanup must not delete a snapshot because a process
    vanished moments ago."""
    s = _capture(store)
    _rewrite_lease(s, owner_pid=_dead_pid(), process_start_identity="gone")
    assert ms.collect_garbage(store) == []
    assert s.path.exists()
    s.release()


def test_gc_keeps_an_expired_lease_whose_owner_is_alive(store):
    """A long measurement that has not heartbeated is still a measurement."""
    with _capture(store) as s:
        _rewrite_lease(s, last_heartbeat=_ago(7200))
        assert ms.collect_garbage(store) == []
        assert s.path.exists()


def test_gc_never_reclaims_when_liveness_is_unknown(store, monkeypatch):
    s = _capture(store)
    _rewrite_lease(s, last_heartbeat=_ago(7200))
    monkeypatch.setattr(kr, "process_state", lambda pid, identity: kr.UNKNOWN)
    assert ms.collect_garbage(store) == []
    s.release()


def test_gc_dry_run_reports_without_deleting(store):
    s = _capture(store)
    _rewrite_lease(s, owner_pid=_dead_pid(), process_start_identity="gone",
                   last_heartbeat=_ago(7200))
    assert ms.collect_garbage(store, dry_run=True) == [str(s.path)]
    assert s.path.exists()
    s.release()


def test_a_corrupt_lease_falls_back_to_the_pid_rule_and_never_crashes(store):
    s = _capture(store)
    s.lease_path.write_text("{not json")
    [row] = ms.list_snapshots(store)
    assert row["gc"] == ms.KEEP            # the name's pid is this live process
    assert row["purpose"] is None
    s.release()


def test_a_dead_builders_partial_directory_is_reclaimed(store):
    ghost = ms.snapshots_dir(store) / f"t-20260101T000000Z-1.building-{_dead_pid()}"
    ghost.mkdir(parents=True)
    assert str(ghost) in ms.collect_garbage(store)
    assert not ghost.exists()


def test_the_pid_rule_keeps_a_process_it_cannot_signal(store):
    """kill(2): EPERM means the process EXISTS. Before step 6 the sweep read
    any OSError as death. pid 1 (launchd/init) is another user's process
    unless the suite runs as root, and then it is signalable and kept anyway."""
    ghost = ms.snapshots_dir(store) / "t-20260101T000000Z-1"
    ghost.mkdir(parents=True)
    assert ms.collect_garbage(store) == []
    assert ghost.exists()


def test_a_path_only_snapshot_survives_its_capturing_process(store):
    """Review focus 5: check.sh gate 6 captures in a short-lived process and
    measures in the shell. A concurrent check.sh's --sweep must not delete it,
    and the pid rule alone would have."""
    out = subprocess.run(
        [sys.executable, "-c",
         "from scripts import measurement_snapshot as ms\n"
         f"s = ms.capture(root={str(store)!r}, label='check', components=('wiki',))\n"
         "print(s.path)\n"],
        capture_output=True, text=True, env={**os.environ, "PYTHONPATH": "."})
    assert out.returncode == 0, out.stderr
    from pathlib import Path
    path = Path(out.stdout.strip())
    assert ms.sweep_abandoned(store) == []
    assert path.exists()
    lease = json.loads((path / ms.LEASE_NAME).read_text())
    lease["last_heartbeat"] = _ago(7200)
    (path / ms.LEASE_NAME).write_text(json.dumps(lease))
    assert ms.sweep_abandoned(store) == [str(path)]


def test_list_snapshots_reports_purpose_revision_and_gc_verdict(store):
    with _capture(store) as s:
        [row] = ms.list_snapshots(store)
        assert row["snapshot_id"] == s.snapshot_id
        assert (row["purpose"], row["coherence"], row["gc"]) == (
            "legacy", "UNVERIFIED", ms.KEEP)


def _cli(store, *args):
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": "."}
    return subprocess.run([sys.executable, "scripts/cli.py", "knowledge", *args],
                          capture_output=True, text=True, env=env)


def test_knowledge_snapshots_lists_leases(store):
    s = _capture(store)
    try:
        out = _cli(store, "snapshots")
        assert out.returncode == 0, out.stderr
        assert s.snapshot_id in out.stdout
        assert "purpose=legacy" in out.stdout and "gc=KEEP" in out.stdout
    finally:
        s.release()


def test_knowledge_snapshots_on_an_empty_store(store):
    out = _cli(store, "snapshots")
    assert out.returncode == 0, out.stderr
    assert "no snapshots" in out.stdout


def test_knowledge_gc_reclaims_only_what_the_rule_allows(store):
    keep = _capture(store, label="keep")
    dead = _capture(store, label="dead")
    _rewrite_lease(dead, owner_pid=_dead_pid(), process_start_identity="gone",
                   last_heartbeat=_ago(7200))
    out = _cli(store, "gc", "--dry-run")
    assert out.returncode == 0, out.stderr
    assert "would reclaim" in out.stdout and dead.path.exists()
    out = _cli(store, "gc")
    assert out.returncode == 0, out.stderr
    assert "reclaimed" in out.stdout
    assert not dead.path.exists() and keep.path.exists()
    keep.release()


# ── final review fixes ──────────────────────────────────────────────────────

def test_a_lease_with_a_naive_timestamp_is_kept_and_never_stops_gc(store):
    """Final review I1: a heartbeat with no timezone parses, then the
    subtraction raised TypeError. GC walks in sorted order, so every snapshot
    after it was never reclaimed, and check.sh's `--sweep || true` hid it."""
    bad = _capture(store, label="bad")
    later = _capture(store, label="zz-dead")
    _rewrite_lease(bad, owner_pid=_dead_pid(), process_start_identity="gone",
                   last_heartbeat="2026-01-01T00:00:00")
    _rewrite_lease(later, owner_pid=_dead_pid(), process_start_identity="gone",
                   last_heartbeat=_ago(7200))
    assert ms.collect_garbage(store) == [str(later.path)]
    [row] = ms.list_snapshots(store)
    assert row["gc"] == ms.KEEP
    bad.release()


def test_an_identity_recorded_while_ps_failed_is_never_read_as_dead(store):
    """Final review I2(a): own_identity() caches "<pid>:unknown" when ps fails
    once. A live owner then never matches its own lease, and an expired lease
    was reclaimed under a running measurement."""
    with _capture(store) as s:
        _rewrite_lease(s, process_start_identity=f"{os.getpid()}:unknown",
                       last_heartbeat=_ago(7200))
        assert ms.collect_garbage(store) == []
        assert s.path.exists()


def test_a_ps_that_runs_and_fails_is_unknown_not_dead(monkeypatch):
    """Final review I2(b): empty stdout was read as "process absent" whatever
    the exit status. Measured 2026-09-28: an absent pid gives rc 1 with EMPTY
    stderr; a failing ps says why on stderr."""
    import subprocess as sp

    def failing_ps(args, **kwargs):
        return sp.CompletedProcess(args, 1, stdout="", stderr="ps: sysctl failed\n")

    monkeypatch.setattr(kr.subprocess, "run", failing_ps)
    assert kr.process_state(os.getpid(), "anything") == kr.UNKNOWN


def test_a_certainly_absent_pid_is_still_dead(monkeypatch):
    """The fix must not make death unprovable: ESRCH from the kernel is
    evidence even when ps is broken."""
    import subprocess as sp

    def failing_ps(args, **kwargs):
        return sp.CompletedProcess(args, 1, stdout="", stderr="ps: sysctl failed\n")

    monkeypatch.setattr(kr.subprocess, "run", failing_ps)
    assert kr.process_state(_dead_pid(), "gone") == kr.DEAD
