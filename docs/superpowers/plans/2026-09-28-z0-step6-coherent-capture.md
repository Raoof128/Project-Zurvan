# Z0 Step 6 — Captures declare what must be coherent, snapshots hold leases, and GC reclaims only the provably abandoned

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A capture states its `purpose` and the components that must be `required` at one knowledge revision. A measurement capture whose derived stores disagree with that revision fails closed with `SNAPSHOT_NOT_READY`, naming the component and the gap. Every snapshot holds a lease, and garbage collection reclaims a snapshot only when its lease has expired **and** its owner is provably dead.

**Architecture:** `scripts/build_snapshot.py` already proves a snapshot is revision R with the double manifest (step 5). It is generalised into `capture_coherent(root, purpose, required, ...)`: it freezes the authoritative inputs plus each required derived store, then reads the `built_from_*` stamps from the **frozen** copies and compares them with the ledger through one shared verdict (`derived_health.stamp_verdict`). Only after that does it publish. The step-5 rebuild path becomes a thin wrapper around it with `required=("wiki",)`. `measurement_snapshot.capture` gains `purpose=` and `required=` and delegates to it. The legacy path is unchanged, except that its manifest now says `UNVERIFIED`. Every capture writes `lease.json` into the snapshot directory before it is published. `sweep_abandoned` becomes lease-aware garbage collection, and the CLI gains `zurvan knowledge snapshots` and `zurvan knowledge gc`.

**Tech Stack:** Python 3.12 stdlib, `sqlite3`, `subprocess` (`ps`, via the existing `knowledge_revision.process_state`). No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md), sections §2, §7.1–7.3, §8, §9, §9.1, §9.2 step 6, and §10.2 items 1, 8, 9, 10, 13 and 19. [Amendments](../specs/2026-09-18-zurvan-2-amendments.md) §10 (amendment 8) is the step-5 record this builds on.

## Global Constraints

- **`required` is explicit at the call site. One function whose definition of "valid" silently changes with its caller is how this class of bug is built.** (Design §2.1.)
- **Step 6 runs only over declared capabilities. An authoritative capture (`required={"wiki"}`) never inspects a derived store.** (Design §7.1; this is what removes the §2.2 deadlock.)
- **A missing stamp must never read as agreement.** (Design §9.1.)
- **No retrieval evaluation may claim a coherent Z0 snapshot while any required component is `UNVERIFIED`.** (Design §9.1.)
- **GC reclaims only when the lease is expired and the owner no longer matches a live process identity.** (Design §8.)
- **If `claims.sqlite` exists and no snapshot adapter is registered, capture fails closed.** (Design §7.3.)
- **`check.sh` gate 6 calls `--path-only` and `--sweep`. Both stay green at every commit in this phase.** (Design §9.)
- **The 14 contracts in `tests/test_measurement_snapshot.py` are not rewritten to accommodate new behaviour. If one must change, that is a finding to record.** (Design §9.)
- **Retry is bounded.** (Design §7.1.)
- **No retrieval, ranking, chunking, embedding or index-format change of any kind.** (Design §11.) Step 6 touches no builder beyond the `purpose` label passed to its capture.
- **Production knowledge state is never a test fixture.** (AGENTS.md.) Every new test runs under `ZURVAN_ROOT=tmp_path`. A test that builds a search index sets `ZURVAN_SEARCH_DB` to a throwaway path.
- **Never run `check.sh` while an index rebuild is in flight.** (CLAUDE.md.)
- **A failed gate is reported as failed.** (CLAUDE.md.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.**

## Amendments to the design, made here and argued (amendment 9)

1. **The CLI is `zurvan knowledge snapshots` and `zurvan knowledge gc`, not `zurvan snapshot list|gc`.** §8 names the latter, but `zurvan snapshot` is the release-archive command (`scripts/cli.py:514`), and it already has a `list` subcommand. The CLI records that sharing that name "already cost a session" (`scripts/cli.py:526`).
2. **Coherence is verified before publication.** §7.1 writes the manifest at step 11, after the checks. Here the checks run on the `.building-<pid>` directory, and a refusal removes it. A snapshot that failed coherence therefore never appears under a final name. This is §1's "refuses to exist when it cannot", taken literally.
3. **`required` must contain `"wiki"`.** A coherent capture pins the authoritative inputs; a caller cannot require a derived store while leaving unpinned the revision it is checked against. `"claims"` cannot be required, because its status is `UNSUPPORTED_BY_DESIGN` until Z2.
4. **GC removes the whole snapshot directory.** §8 says metadata *may* outlive the artifacts. That option is not taken: the lease and manifest are only useful while the bytes exist, and keeping them would grow `data/snapshots/` without bound.
5. **A directory with no lease keeps the pid rule.** This covers `.building-<pid>` directories and snapshots taken before this step. A lease cannot exist before publication, and snapshots from before step 6 have none.
6. **`check.sh` gate 6 stays a legacy capture, and says `UNVERIFIED`.** Requiring `search` there would fail the gate whenever another project writes between gate 5's rebuild and gate 6. That is exactly the failure snapshots were introduced to remove (`scripts/check.sh:115-120`). §9.1 is honoured by labelling, not by refusal: the gate's number is not claimed as a coherent Z0 measurement.
7. **A lease names a TTL, and the default is 3600 s.** §8 lists the fields, not their values. An hour covers a full gate run. Because GC also requires a dead owner, the TTL only bounds how long a dead process's snapshot survives.
8. **A live-stamp pre-check runs before the freeze. It is a fast path, not the proof.** Measured in the gauntlet: refusing a stale live store after freezing it took 11.69 s. Nearly all of that is `index_identity` over the 739 MB index, spent only to say no. With the pre-check the refusal takes 0.17 s. The frozen copies are still verified, because a rebuild may promote between the pre-check and the clone.
9. **The pid rule treats `PermissionError` as alive.** `kill(2)` returns EPERM for a process that exists but belongs to another user. The pre-step-6 sweep caught every `OSError` as death (`except (OSError, ProcessLookupError)`), so it could delete a snapshot owned by a live process. Found by reading the 2026 Python `os.kill`/`signal` documentation, not by a failure.

## Review Focus

1. **`ps` fails or is unavailable while GC runs.** Expected: `process_state` returns UNKNOWN, and nothing is reclaimed on a guess. Pinned in Task 3 (`test_gc_never_reclaims_when_liveness_is_unknown`).
2. **A required graph was built before `graph_meta` existed.** Expected: `SNAPSHOT_NOT_READY` naming `graph`, never a traceback. Pinned in Task 4 (`test_a_graph_from_before_stamps_existed_is_refused_not_a_crash`).
3. **A lease file that does not parse.** Expected: GC falls back to the pid rule, and `knowledge snapshots` still lists the directory. Pinned in Task 3 (`test_a_corrupt_lease_falls_back_to_the_pid_rule_and_never_crashes`).
4. **A measurement capture while an operation is in flight.** Expected: BUSY within the wait budget, naming the writer; never a hang, never a half-state. Pinned in Task 4 (`test_gate_1_a_capture_between_the_two_writes_of_decision_add_is_busy`).
5. **Gate 6's `--path-only` snapshot outlives the process that captured it, while a concurrent `check.sh` runs `--sweep`.** Expected: kept, because the lease is unexpired. Before this step the pid rule would have deleted it under a running measurement. Pinned in Task 3 (`test_a_path_only_snapshot_survives_its_capturing_process`).

---

## Task 0 — Branch and baseline

Nothing is edited.

- [ ] **Step 1: Branch, and record the regression floor**

```bash
git switch -c z0-step6-coherent-capture
M=~/.zurvan/measurements/z0-step6; mkdir -p $M
PYTHONPATH=. python -m pytest -q -p no:cacheprovider 2>&1 | tail -3 | tee $M/pytest-floor.txt
PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_measurement_snapshot.py tests/test_build_snapshot.py tests/test_derived_health.py 2>&1 | tail -1 | tee $M/touched-floor.txt
```

Expected: `0 failed` in both files. The first file records the passed count (1277 on 2026-09-28). A failure here is recorded and investigated before any task starts; it is never absorbed into this step.

---

## Task 1 — One definition of "coherent": `derived_health.stamp_verdict`

**Files:**
- Modify: `scripts/derived_health.py`
- Test: `tests/test_derived_health.py`

**Interfaces:**
- Produces: `stamp_verdict(meta, revision, manifest_digest) -> tuple` returning `(state, reason)`, where `state` is one of `CURRENT` / `STALE` / `INVALID`. It is pure: no I/O. Task 4 calls it on a frozen copy's stamps.
- `store_health(kind, root=None)` keeps its exact return shape `(state, built, now, reason)`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_derived_health.py`)

```python
def test_stamp_verdict_is_the_one_definition_of_coherent():
    """store_health and a measurement capture share this, so "coherent"
    cannot come to mean two things (design §6.2, §7.1 step 6)."""
    d = "digest"
    assert dh.stamp_verdict({}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "x"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "6"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "5",
                             "built_from_manifest": "other"}, 5, d)[0] == dh.INVALID
    assert dh.stamp_verdict({"built_from_revision": "5",
                             "built_from_manifest": d}, 5, d) == (dh.CURRENT, "")
    assert dh.stamp_verdict({"built_from_revision": "3"}, 5, d) == (
        dh.STALE, "2 revision(s) behind")
```

- [ ] **Step 2: Run it and watch it fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_derived_health.py::test_stamp_verdict_is_the_one_definition_of_coherent
```

Expected: FAIL with `AttributeError: module 'scripts.derived_health' has no attribute 'stamp_verdict'`.

- [ ] **Step 3: Implement.** Replace everything in `store_health` from `built = meta.get("built_from_revision")` to the end of the function, and add `stamp_verdict` above it:

```python
def stamp_verdict(meta, revision, manifest_digest) -> tuple:
    """(state, reason) for one store's stamps against the ledger at `revision`.

    Pure. `store_health` reads the live store; a measurement capture reads the
    FROZEN copy (design §7.1 step 6). Both come here, so a stamp cannot be
    coherent for one and not the other.
    """
    built = meta.get("built_from_revision")
    if built is None:
        return INVALID, "no truthful source snapshot (unstamped build)"
    try:
        built_rev = int(built)
    except ValueError:
        return INVALID, f"stamp {built!r} is not a revision"
    if built_rev > revision:
        return INVALID, "stamp names a revision the ledger has not reached"
    if built_rev == revision:
        stamped = meta.get("built_from_manifest")
        if stamped and manifest_digest and stamped != manifest_digest:
            return INVALID, "stamp's manifest is not the ledger's at that revision"
        return CURRENT, ""
    return STALE, f"{revision - built_rev} revision(s) behind"
```

and the tail of `store_health` becomes:

```python
    built = meta.get("built_from_revision")
    try:
        ledger = kr.current(base)
    except kr.LedgerError:
        return INVALID, built, None, "no ledger to compare against"
    now = ledger.revision
    state, reason = stamp_verdict(meta, now, ledger.manifest_digest)
    if state == INVALID:
        return INVALID, built, now, reason
    note = "; ledger is SUSPECT - reconcile" if ledger.state != kr.CLEAN else ""
    return state, built, now, (reason + note).lstrip("; ")
```

- [ ] **Step 4: Run the whole file.** The old tests are the regression check for the refactor.

```bash
PYTHONPATH=. python -m pytest -q tests/test_derived_health.py
```

Expected: all pass, including every test that existed before this task.

- [ ] **Step 5: Commit**

```bash
git add scripts/derived_health.py tests/test_derived_health.py
git commit -m "zurvan: one definition of coherent - stamp_verdict shared by health and capture"
```

---

## Task 2 — Every capture takes a lease, says whether it is verified, and refuses an unadapted claim ledger

**Files:**
- Modify: `scripts/measurement_snapshot.py`
- Test: `tests/test_snapshot_leases.py` (create)

**Interfaces:**
- Produces, in `measurement_snapshot`:
  - `LEASE_NAME = "lease.json"` and `DEFAULT_LEASE_TTL_S = 3600`.
  - `CLAIMS_STATUS` and `LEGACY_COHERENCE` (dicts).
  - `assert_no_unadapted_claims_ledger(base) -> None`, which raises `SnapshotComponentError`.
  - `_freeze(base, label, deep, names, purpose, revision, lease_ttl_s, verify) -> Snapshot`. `verify` is `None`, or a callable taking the building directory and returning the coherence dict to embed. It may raise; any raise discards the building directory.
  - `capture(root=None, label="measurement", deep=False, components=None, purpose=None, required=None, lease_ttl_s=DEFAULT_LEASE_TTL_S) -> Snapshot`. Task 4 fills in the `required` branch.
  - `Snapshot.lease_path` (property) and `Snapshot.heartbeat() -> None`.
- The manifest's identity gains `"coherence"` and `"purpose"`. Every existing key keeps its meaning.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_snapshot_leases.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py
```

Expected: 4 FAIL. The first two fail with `AttributeError: 'Snapshot' object has no attribute 'lease_path'`, the third with `KeyError: 'coherence'`, and gate 19 with `DID NOT RAISE`.

- [ ] **Step 3: Implement in `scripts/measurement_snapshot.py`**

(a) Directly after `DEFAULT_COMPONENTS = ("search",)`, add:

```python
LEASE_NAME = "lease.json"

#: Amendment 9.7: a full gate run fits inside an hour. GC also requires the
#: owner DEAD, so this bounds only how long a dead process's snapshot lives.
DEFAULT_LEASE_TTL_S = 3600

CLAIMS_LEDGER = ("data", "claims.sqlite")

CLAIMS_STATUS = {"status": "UNSUPPORTED_BY_DESIGN",
                 "reason": "structured claim ledger introduced in Z2"}

LEGACY_COHERENCE = {
    "status": "UNVERIFIED",
    "reason": "legacy capture: no required components declared",
    "components": {"claims": CLAIMS_STATUS},
}


def assert_no_unadapted_claims_ledger(base) -> None:
    """Design §7.3: the structured claim ledger is Z2. If one appears before it
    registers a capture adapter, every snapshot would be silently blind to it,
    so capture fails closed instead."""
    path = Path(base).joinpath(*CLAIMS_LEDGER)
    if path.exists() and "claims" not in _COMPONENTS:
        raise SnapshotComponentError(
            f"{path} exists and no claims capture adapter is registered; "
            "refusing a snapshot that would be blind to it (design §7.3)")


def _write_json_atomic(path, data) -> None:
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _new_lease(snapshot_id, purpose, revision, names, ttl_s) -> dict:
    """Design §8's fields. `process_start_identity` pairs the pid with its
    start time, because a pid alone is recycled."""
    from scripts.knowledge_revision import own_identity
    now = datetime.now(timezone.utc).isoformat()
    return {
        "snapshot_id": snapshot_id,
        "knowledge_revision": revision,
        "purpose": purpose,
        "created_at": now,
        "last_heartbeat": now,
        "ttl_s": ttl_s,
        "owner_pid": os.getpid(),
        "process_start_identity": own_identity(),
        "state": "HELD",
        "referenced_artifacts": list(names),
    }
```

(b) In `class Snapshot`, after the `released` property, add:

```python
    @property
    def lease_path(self) -> Path:
        return self.path / LEASE_NAME

    def heartbeat(self) -> None:
        """Renew the lease. A measurement that outlives its TTL calls this; GC
        still needs the owner DEAD as well as the lease expired (design §8)."""
        if self._released:
            raise SnapshotComponentError("cannot heartbeat a released snapshot")
        lease = json.loads(self.lease_path.read_text(encoding="utf-8"))
        lease["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(self.lease_path, lease)
```

(c) Replace the whole of `def capture(...)` with `_validate_names`, `_freeze` and a new `capture`:

```python
def _validate_names(names) -> tuple:
    names = tuple(names)
    unknown = [n for n in names if n not in _COMPONENTS]
    if unknown:
        raise SnapshotComponentError(
            f"no capture adapter registered for {unknown}; refusing rather "
            "than silently omitting a component the caller believes it froze")
    if len(set(names)) != len(names):
        # Otherwise the second capture of the same name fails deep inside
        # `clone_tree` with "destination already exists", which reads as a
        # filesystem problem rather than a caller mistake.
        raise SnapshotComponentError(f"duplicate component names in {names}")
    return names


def _freeze(base, label, deep, names, purpose, revision, lease_ttl_s, verify):
    """Freeze `names` into `<id>.building-<pid>`, verify, lease, publish.

    Published with one rename. Python cleanup handles an exception; it cannot
    handle SIGKILL or power loss, and those would otherwise leave a directory
    under the FINAL name holding some of the components, indistinguishable
    from a whole snapshot to every later reader. Any failure, including a
    `verify` refusal, discards the partial capture (design §7.1), so a
    snapshot that failed coherence never exists under a final name
    (amendment 9.2).
    """
    assert_no_unadapted_claims_ledger(base)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_id = f"{label}-{stamp}-{os.getpid()}"
    final = snapshots_dir(base) / snapshot_id
    working = snapshots_dir(base) / f"{snapshot_id}.building-{os.getpid()}"
    working.mkdir(parents=True, exist_ok=False)
    index_path = None
    try:
        captured = {}
        for name in names:
            captured[name] = _COMPONENTS[name](base, working)
        # A component's ABSENCE from this mapping means it was not captured.
        # Recording only the names that were frozen is what stops a later
        # reader treating a missing key as "captured and verified".
        identity = {
            "snapshot_id": snapshot_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "purpose": purpose,
            "components": {
                name: {
                    "capture_backend": component.backend,
                    "captured_scope": component.captured_scope,
                    "authoritative_scope": component.authoritative_scope,
                    "coherence_verified": component.coherence_verified,
                    "logical_bytes": component.logical_bytes,
                }
                for name, component in captured.items()
            },
            "corpus_manifest_sha": _corpus_manifest_sha(base),
        }
        if "search" in captured:
            index_path = captured["search"].path
            identity["index_path"] = str(_canonical_index(base))
            identity["index_inode"] = index_path.stat().st_ino
            identity["link"] = captured["search"].backend
            identity.update(index_identity(index_path, deep=deep))
        identity["coherence"] = (verify(working) if verify is not None
                                 else json.loads(json.dumps(LEGACY_COHERENCE)))
        (working / "manifest.json").write_text(
            json.dumps(identity, indent=2, default=str), encoding="utf-8")
        _write_json_atomic(working / LEASE_NAME, _new_lease(
            snapshot_id, purpose, revision, names, lease_ttl_s))
        os.rename(working, final)
        _fsync_dir(final.parent)
    except BaseException:
        shutil.rmtree(working, ignore_errors=True)
        raise
    if index_path is not None:
        index_path = final / INDEX_NAME
    return Snapshot(snapshot_id=snapshot_id, path=final,
                    index_path=index_path, identity=identity)


def capture(root=None, label="measurement", deep=False, components=None,
            purpose=None, required=None, lease_ttl_s=DEFAULT_LEASE_TTL_S) -> Snapshot:
    """Freeze the measurement input. Returns a `Snapshot`.

    Two contracts, kept apart so the seam is reviewable:

    - `components=` (legacy): which bytes to freeze. `None` means
      `DEFAULT_COMPONENTS`, which is exactly today's behaviour: `check.sh`
      gate 6 calls this on every run and reads only the index. Its manifest
      says `coherence: UNVERIFIED`, because it proves no revision
      (design §9.1).
    - `required=` (design §2.1, step 6): the components that must describe
      ONE knowledge revision. Delegates to `build_snapshot.capture_coherent`,
      which refuses with `SnapshotNotReady` rather than publish a disagreement.
    """
    if required is not None:
        if components is not None:
            raise SnapshotComponentError(
                "pass components OR required, not both: `required` decides "
                "what a coherent capture freezes")
        from scripts.build_snapshot import capture_coherent
        return capture_coherent(_root(root), purpose=purpose or label,
                                required=required, label=label, deep=deep,
                                lease_ttl_s=lease_ttl_s).snapshot
    names = _validate_names(components if components is not None
                            else DEFAULT_COMPONENTS)
    return _freeze(_root(root), label, deep, names, purpose or "legacy", None,
                   lease_ttl_s, None)
```

`capture_coherent` does not exist until Task 4. It is imported inside the `required` branch only, so nothing in this task reaches it.

- [ ] **Step 4: Run the new tests and the legacy contracts**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py tests/test_measurement_snapshot.py tests/test_build_snapshot.py
```

Expected: all pass, with no edit to `tests/test_measurement_snapshot.py`. If a legacy contract fails, stop: design §9 makes that a finding to record, not a test to rewrite.

- [ ] **Step 5: Commit**

```bash
git add scripts/measurement_snapshot.py tests/test_snapshot_leases.py
git commit -m "zurvan: every snapshot holds a lease and says whether it is verified; claims.sqlite without an adapter fails closed"
```

---

## Task 3 — GC reclaims only an expired lease with a dead owner

**Files:**
- Modify: `scripts/measurement_snapshot.py`
- Test: `tests/test_snapshot_leases.py`

**Interfaces:**
- Consumes: `LEASE_NAME` and `Snapshot.lease_path` (Task 2), plus `kr.process_state(pid, identity)`, `kr.ALIVE`, `kr.DEAD` and `kr.UNKNOWN` (existing).
- Produces:
  - `KEEP = "KEEP"` and `RECLAIM = "RECLAIM"`.
  - `gc_verdict(child, now=None) -> tuple`, returning `(KEEP|RECLAIM, reason)`.
  - `collect_garbage(root=None, dry_run=False, now=None) -> list`, the reclaimed (or would-be-reclaimed) paths as `str`.
  - `list_snapshots(root=None, now=None) -> list`, one dict per directory with keys `snapshot_id`, `purpose`, `knowledge_revision`, `coherence`, `owner_pid`, `gc` and `reason`.
  - `sweep_abandoned(root=None) -> list` keeps its name and signature, and now returns `collect_garbage(root)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_snapshot_leases.py`)

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py
```

Expected: the new tests fail with `AttributeError: module 'scripts.measurement_snapshot' has no attribute 'collect_garbage'` (or `list_snapshots`). `test_a_path_only_snapshot_survives_its_capturing_process` fails at `assert ms.sweep_abandoned(store) == []`, because the pid rule deletes the directory.

- [ ] **Step 3: Implement.** Replace the whole of `def sweep_abandoned(...)` with:

```python
KEEP = "KEEP"
RECLAIM = "RECLAIM"


def _lease_of(directory):
    """The lease, or None when there is none or it does not parse. A torn
    lease cannot come from this module (it is replaced atomically), so an
    unparsable one is treated like a lease-less directory."""
    try:
        lease = json.loads((Path(directory) / LEASE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return lease if isinstance(lease, dict) else None


def _pid_rule(child) -> tuple:
    """The pre-step-6 rule, kept for directories that cannot have a lease:
    `.building-<pid>` (never published) and snapshots taken before leases
    existed (amendment 9.5)."""
    suffix = child.name.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        return KEEP, "not a name this module writes; left alone"
    pid = int(suffix)
    if pid == os.getpid():
        return KEEP, "owned by this process"
    # kill(2): ESRCH means no such process; EPERM means it EXISTS and belongs
    # to someone else. The pre-step-6 sweep caught both as OSError and so
    # deleted snapshots owned by live processes it could not signal.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return RECLAIM, f"no lease; owner pid {pid} is gone"
    except PermissionError:
        return KEEP, f"no lease; pid {pid} exists (another user's process)"
    except OSError as exc:
        return KEEP, f"no lease; liveness of pid {pid} unknown ({exc})"
    return KEEP, f"no lease; owner pid {pid} is running"


def gc_verdict(child, now=None) -> tuple:
    """(KEEP|RECLAIM, reason) for one entry under data/snapshots (design §8).

    A lease is reclaimed only when it has EXPIRED and its owner is provably
    DEAD. An expired lease on a live process is a long measurement that has
    not heartbeated; a dead owner with a fresh lease is a shell still using
    what a short-lived capturer handed it (check.sh gate 6). An owner whose
    liveness cannot be established is never reclaimed on a guess.
    """
    from scripts import knowledge_revision as kr
    child = Path(child)
    if ".building-" in child.name:
        return _pid_rule(child)
    lease = _lease_of(child)
    if lease is None:
        return _pid_rule(child)
    now = now or datetime.now(timezone.utc)
    try:
        beat = datetime.fromisoformat(lease["last_heartbeat"])
        ttl = float(lease["ttl_s"])
    except (KeyError, TypeError, ValueError):
        return KEEP, "lease has no usable heartbeat; not reclaimed on a guess"
    expired = (now - beat).total_seconds() > ttl
    owner = kr.process_state(lease.get("owner_pid"),
                             lease.get("process_start_identity"))
    if owner == kr.ALIVE:
        return KEEP, "owner alive"
    if not expired:
        return KEEP, f"lease unexpired (owner {owner})"
    if owner != kr.DEAD:
        return KEEP, "lease expired but owner liveness UNKNOWN; not reclaimed on a guess"
    return RECLAIM, "lease expired and owner dead"


def collect_garbage(root=None, dry_run=False, now=None) -> list:
    """Remove every snapshot `gc_verdict` reclaims. Only the snapshot
    directory is removed: a search link inside it is a second name for the
    index inode, never the canonical name, so GC cannot delete the production
    index."""
    directory = snapshots_dir(root)
    if not directory.is_dir():
        return []
    reclaimed = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        verdict, _ = gc_verdict(child, now)
        if verdict != RECLAIM:
            continue
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
        reclaimed.append(str(child))
    return reclaimed


def sweep_abandoned(root=None) -> list:
    """Since Z0 step 6 the sweep IS garbage collection. `check.sh` calls
    `--sweep` at start; a SIGKILLed gate's snapshot is now reclaimed once
    its lease expires, not the moment its capturing process is gone."""
    return collect_garbage(root)


def list_snapshots(root=None, now=None) -> list:
    directory = snapshots_dir(root)
    if not directory.is_dir():
        return []
    rows = []
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        lease = _lease_of(child) or {}
        try:
            manifest = json.loads((child / "manifest.json").read_text(encoding="utf-8"))
            coherence = manifest.get("coherence") or {}
        except (OSError, ValueError):
            coherence = {}
        verdict, reason = gc_verdict(child, now)
        rows.append({
            "snapshot_id": child.name,
            "purpose": lease.get("purpose"),
            "knowledge_revision": lease.get("knowledge_revision"),
            "coherence": coherence.get("status", "UNVERIFIED"),
            "owner_pid": lease.get("owner_pid"),
            "gc": verdict,
            "reason": reason,
        })
    return rows
```

- [ ] **Step 4: Run the lease tests and the legacy sweep contracts**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py tests/test_measurement_snapshot.py
PYTHONPATH=. python scripts/measurement_snapshot.py --sweep; echo "sweep exit=$?"
```

Expected: all pass. `test_abandoned_snapshots_from_dead_processes_are_swept` still passes, through the pid rule, because its ghost has no lease. Expect `sweep exit=0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/measurement_snapshot.py tests/test_snapshot_leases.py
git commit -m "zurvan: GC reclaims a snapshot only when its lease expired AND its owner is dead"
```

---

## Task 4 — `capture_coherent`: `required` is verified on the frozen copies, or nothing is published

**Files:**
- Modify: `scripts/build_snapshot.py`, `scripts/rebuild_search_index.py` (its `__main__` only), `scripts/graph_build.py` (its `__main__` only)
- Test: `tests/test_coherent_capture.py` (create)

**Interfaces:**
- Consumes: `derived_health.stamp_verdict(meta, revision, manifest_digest)` (Task 1). From Task 2: `ms._freeze(base, label, deep, names, purpose, revision, lease_ttl_s, verify)`, `ms.CLAIMS_STATUS` and `ms.DEFAULT_LEASE_TTL_S`.
- Produces, in `build_snapshot`:
  - `KNOWN_REQUIRED = ("wiki", "search", "graph")`, and the statuses `VERIFIED`, `UNVERIFIED` and `NOT_REQUIRED`.
  - `SnapshotNotReady(RuntimeError)` with attributes `component`, `state`, `built_from`, `revision` and `reason`. Its message starts `SNAPSHOT_NOT_READY:`.
  - `capture_coherent(root=None, purpose="rebuild", required=("wiki",), label=None, attempts=3, wait_s=300, deep=False, lease_ttl_s=None) -> BuildSnapshot`.
  - `BuildSnapshot` gains `purpose: str = "rebuild"` and `required: tuple = ("wiki",)`.
  - `capture_build_inputs(root=None, label="rebuild", attempts=3, wait_s=300) -> BuildSnapshot` keeps its signature and delegates.
  - `inputs_for_rebuild(root=None, purpose="rebuild")`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_coherent_capture.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py
```

Expected: 16 failed and 1 passed. The failures are `AttributeError: module 'scripts.build_snapshot' has no attribute 'capture_coherent'` (or `SnapshotNotReady`), `assert 'legacy' == 'rebuild_graph'`, and, for the script test, `unrecognized arguments: --require`. `test_components_and_required_are_exclusive` passes already, because Task 2's `capture` raises before it imports anything.

- [ ] **Step 3: Implement in `scripts/build_snapshot.py`**

(a) Below `class BuildSnapshotBusy`, add:

```python
KNOWN_REQUIRED = ("wiki", "search", "graph")
VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
NOT_REQUIRED = "NOT_REQUIRED"


class SnapshotNotReady(RuntimeError):
    """SNAPSHOT_NOT_READY (design §7.2): a REQUIRED derived store is not at the
    revision the snapshot pins. Never retried: staleness does not fix itself
    inside a capture; a rebuild does (§2.2)."""

    def __init__(self, component, state, built_from, revision, reason):
        self.component = component
        self.state = state
        self.built_from = built_from
        self.revision = revision
        self.reason = reason
        super().__init__(
            f"SNAPSHOT_NOT_READY: {component} is {state} (built from "
            f"{built_from}, knowledge revision {revision}): {reason}")


class _Moved(Exception):
    """The live store moved during the clone; the attempt is discarded."""


def _normalise_required(required) -> tuple:
    names = tuple(sorted(set(required)))
    if "claims" in names:
        raise ValueError("claims cannot be required: UNSUPPORTED_BY_DESIGN "
                         "until the Z2 claim ledger (design §7.3)")
    unknown = [n for n in names if n not in KNOWN_REQUIRED]
    if unknown:
        raise ValueError(f"unknown required component(s) {unknown}; "
                         f"known: {KNOWN_REQUIRED}")
    if "wiki" not in names:
        raise ValueError("a coherent capture pins the authoritative inputs: "
                         "required must include 'wiki' (amendment 9.3)")
    return names


def _frozen_meta(kind, directory) -> dict:
    """Stamps read from the FROZEN copy, never the live store: a rebuild may
    promote a new live file between the check and the clone, and the copy is
    what the measurement will read. A store with no stamp table reads as {}:
    unstamped, therefore UNVERIFIED (design §9.1), never a crash."""
    try:
        if kind == "search":
            from scripts.rebuild_search_index import read_index_meta
            return read_index_meta(Path(directory) / "search.sqlite")
        from scripts.graph_schema import read_graph_meta
        return read_graph_meta(Path(directory) / "graph.sqlite")
    except sqlite3.Error:
        return {}


def _verifier(base, required, before, m_before, purpose):
    """What runs on the building directory before it may be published."""
    from scripts import derived_health as dh
    from scripts import measurement_snapshot as ms

    def verify(working) -> dict:
        try:
            if not _unchanged(base, m_before, before):
                raise _Moved()
        except ManifestUnavailable as exc:           # a file vanished: a race
            raise _Moved() from exc
        components = {
            "wiki": {"status": VERIFIED},
            "docs": {"status": "CAPTURED", "note": "git-tracked, outside the ledger (amendment 8)"},
            "roots": {"status": "CAPTURED", "note": "git-tracked, outside the ledger (amendment 8)"},
        }
        for kind in ("search", "graph"):
            if kind not in required:
                components[kind] = {"status": NOT_REQUIRED}
                continue
            meta = _frozen_meta(kind, working)
            state, reason = dh.stamp_verdict(meta, before.revision,
                                             before.manifest_digest)
            if state != dh.CURRENT:
                built = meta.get("built_from_revision")
                raise SnapshotNotReady(kind, UNVERIFIED if built is None else state,
                                       built, before.revision, reason)
            components[kind] = {"status": VERIFIED,
                                "built_from_revision": meta["built_from_revision"],
                                "built_from_snapshot": meta.get("built_from_snapshot")}
        components["claims"] = dict(ms.CLAIMS_STATUS)
        return {"status": VERIFIED, "purpose": purpose, "required": list(required),
                "revision": before.revision, "atomicity": before.atomicity,
                "manifest_digest": before.manifest_digest, "components": components}
    return verify


def _precheck(base, required, before) -> None:
    """Fail fast on the LIVE stamps. Gauntlet 2026-09-28: refusing a stale
    live store AFTER freezing it took 11.7 s, nearly all `index_identity`
    over a 739 MB index, to say no. This is not the proof: a rebuild may
    promote between here and the clone, so the frozen copies are still
    checked. It is only a cheap early no."""
    from scripts import derived_health as dh
    for kind in ("search", "graph"):
        if kind not in required:
            continue
        state, built, now, reason = dh.store_health(kind, base)
        if state != dh.CURRENT and now in (None, before.revision):
            raise SnapshotNotReady(kind, UNVERIFIED if built is None else state,
                                   built, before.revision, reason)
```

Add `import sqlite3` and `from pathlib import Path` to the imports at the top of the module.

(b) Give `BuildSnapshot` two fields after `manifest_digest: str`:

```python
    purpose: str = "rebuild"
    required: tuple = ("wiki",)
```

(c) Replace the whole of `def capture_build_inputs(...)` and `def inputs_for_rebuild(...)` with:

```python
def capture_coherent(root=None, purpose="rebuild", required=("wiki",), label=None,
                     attempts=3, wait_s=300, deep=False, lease_ttl_s=None) -> BuildSnapshot:
    """Design §7.1: freeze the authoritative inputs plus every REQUIRED derived
    store, and publish only if all of them describe knowledge revision R.

    Only the declared capabilities are inspected (§7.1 step 6). An
    authoritative capture, `required=("wiki",)`, never reads a derived stamp,
    which is what removes the §2.2 deadlock. Races are retried a bounded
    number of times; SUSPECT and SNAPSHOT_NOT_READY are not retried.
    """
    from scripts import measurement_snapshot as ms
    required = _normalise_required(required)
    base = ks.store_root(root)
    label = label or purpose
    ttl = ms.DEFAULT_LEASE_TTL_S if lease_ttl_s is None else lease_ttl_s
    names = ms.BUILD_INPUTS + tuple(k for k in ("search", "graph") if k in required)
    for attempt in range(attempts):
        try:
            state = kr.current(base)
        except kr.LedgerError as exc:
            raise NoBaseline(f"no ledger: {exc}") from exc
        if not state.manifest_digest:
            raise NoBaseline("no recorded manifest; run `zurvan knowledge bootstrap`")
        _refuse_if_suspect(state)
        _wait_for_quiet(base, wait_s)
        try:
            kr.observe_external_drift(base, trigger="capture")
        except kr.DriftUnobservable:
            time.sleep(0.1 * (attempt + 1))
            continue
        before = kr.current(base)
        if before.state != kr.CLEAN:
            continue
        try:
            m_before = _live_manifest(base)
        except ManifestUnavailable:
            continue                     # a file vanished mid-manifest: a race
        if m_before != before.manifest_digest:
            continue
        _precheck(base, required, before)
        try:
            snap = ms._freeze(base, label, deep, names, purpose, before.revision,
                              ttl, _verifier(base, required, before, m_before, purpose))
        except _Moved:
            continue
        bs = BuildSnapshot(snap, before.revision, before.atomicity,
                           before.manifest_digest, purpose, required)
        try:
            (snap.path / "build.json").write_text(json.dumps(bs.stamp(), indent=2))
        except BaseException:
            # Only GC sweeps data/snapshots, so a leak here would outlive the
            # process (final review, 2026-09-28).
            snap.release()
            raise
        return bs
    raise BuildSnapshotNotReady(
        f"the store kept changing across {attempts} attempts; nothing was built")


def capture_build_inputs(root=None, label="rebuild", attempts=3, wait_s=300) -> BuildSnapshot:
    """The step-5 contract, unchanged: an authoritative capture for a rebuild."""
    return capture_coherent(root, purpose=label, required=("wiki",), label=label,
                            attempts=attempts, wait_s=wait_s)


@contextmanager
def inputs_for_rebuild(root=None, purpose="rebuild"):
    """A proven snapshot, or None on a store with no baseline (amendment 4).
    SUSPECT and NotReady propagate: a rebuild must not proceed from them."""
    try:
        bs = capture_build_inputs(root, label=purpose)
    except NoBaseline:
        yield None
        return
    try:
        yield bs
    finally:
        bs.release()
```

(d) Name the purpose at both rebuild entry points:

```python
# scripts/rebuild_search_index.py, in __main__
    with inputs_for_rebuild(purpose="rebuild_search") as bs:
# scripts/graph_build.py, in its entry point
    with inputs_for_rebuild(purpose="rebuild_graph") as bs:
```

(e) In the `__main__` of `scripts/measurement_snapshot.py`, add two arguments after `--path-only`:

```python
    parser.add_argument("--purpose", default=None,
                        help="what this snapshot is for; recorded in its lease")
    parser.add_argument("--require", default=None,
                        help="comma-separated components that must be coherent at "
                             "one knowledge revision, e.g. wiki,search,graph; "
                             "refuses with SNAPSHOT_NOT_READY otherwise")
```

and replace `s = capture(label=args.label, deep=args.deep)` with:

```python
    required = tuple(p for p in args.require.split(",") if p) if args.require else None
    s = capture(label=args.label, deep=args.deep, purpose=args.purpose,
                required=required)
```

- [ ] **Step 4: Run every snapshot and rebuild test**

```bash
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py tests/test_build_snapshot.py tests/test_derived_health.py tests/test_measurement_snapshot.py tests/test_snapshot_leases.py
```

Expected: all pass. `test_a_write_during_capture_is_retried_then_refused` still passes, because `_unchanged` now raises `_Moved` inside `verify` and the attempt is discarded before it is published.

- [ ] **Step 5: Falsify the three load-bearing lines, one at a time**

```bash
f=scripts/build_snapshot.py; cp $f /tmp/bs.keep
# 1. Remove the frozen-copy check
sed -i '' 's/            if state != dh.CURRENT:/            if False:/' $f
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py 2>&1 | grep -E 'FAILED|passed|failed'; cp /tmp/bs.keep $f
# 2. Read the search stamp from the LIVE index instead of the frozen copy
sed -i '' 's|Path(directory) / "search.sqlite"|Path(__import__("os").environ["ZURVAN_SEARCH_DB"])|' $f
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py 2>&1 | grep -E 'FAILED|passed|failed'; cp /tmp/bs.keep $f
# 3. Remove the pre-check
sed -i '' 's/^        _precheck(base, required, before)$/        pass/' $f
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py 2>&1 | grep -E 'FAILED|passed|failed'; cp /tmp/bs.keep $f
```

Expected, as measured in the gauntlet: each mutation turns exactly one test red. Mutations 1 and 2 turn `test_stamps_are_read_from_the_frozen_copy_not_the_live_store` red, and mutation 3 turns `test_a_certain_refusal_freezes_nothing` red. Mutation 1 reddens only one test because the pre-check refuses the plainly stale cases first, so the frozen check is the only line of defence for a build that changes underneath the capture, and that one test is what pins it. If a mutation stays green, the test does not prove what it names; stop and fix the test. Record the red counts for the CHANGELOG.

- [ ] **Step 6: Commit**

```bash
git add scripts/build_snapshot.py scripts/measurement_snapshot.py scripts/rebuild_search_index.py scripts/graph_build.py tests/test_coherent_capture.py
git commit -m "zurvan: capture(purpose, required) - required stores verified on the frozen copies or nothing is published"
```

---

## Task 5 — A/B isolation, and the evaluation reports what it measured

**Files:**
- Modify: `scripts/eval_search.py`
- Test: `tests/test_coherent_capture.py`

**Interfaces:**
- Consumes: `capture_coherent` (Task 4), and the `coherence` block in `manifest.json` (Task 2).
- Produces: `eval_search._coherence_of(index_path) -> dict` with keys `status`, `reason`, `purpose`, `revision` and `atomicity`. `run_search_evaluation`'s metrics gain `"snapshot_coherence"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_coherent_capture.py`)

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py -k "gate_10 or eval or metrics"
```

Expected: `gate_10` passes. It pins behaviour that Tasks 2–4 already produce, and it is written here as the named gate test. The other three fail with `ImportError: cannot import name '_coherence_of'` and `KeyError: 'snapshot_coherence'`. If `gate_10` fails, stop: Task 4 is not doing what it claims.

- [ ] **Step 3: Implement in `scripts/eval_search.py`**

Below `_identity_of`, add:

```python
def _coherence_of(index_path) -> dict:
    """The coherence record of the snapshot this index belongs to (design §9.1).

    A live index, or a legacy snapshot, is UNVERIFIED: never read as agreement.
    An EXTERNAL_OBSERVED revision is reported as such, never as MANAGED (gate
    item 13). Reporting, not a gate: this never raises.
    """
    if not index_path:
        return {"status": "UNVERIFIED", "reason": "live index, not a snapshot"}
    manifest = Path(index_path).parent / "manifest.json"
    try:
        coherence = json.loads(manifest.read_text(encoding="utf-8")).get("coherence")
    except (OSError, ValueError):
        coherence = None
    if not coherence:
        return {"status": "UNVERIFIED", "reason": "no coherence record"}
    return {key: coherence.get(key)
            for key in ("status", "reason", "purpose", "revision", "atomicity")}
```

Add `from pathlib import Path` to the imports. In the `metrics = {...}` dict, after `"index_identity": _identity_of(index_path),`, add:

```python
        "snapshot_coherence": _coherence_of(index_path),
```

In the text report, directly before the closing `print("=========================")`, add:

```python
        c = metrics["snapshot_coherence"]
        detail = (f"revision {c['revision']} ({c['atomicity']})"
                  if c.get("revision") is not None else c.get("reason"))
        print(f"Snapshot coherence: {c['status']} — {detail}")
```

- [ ] **Step 4: Run the file, and the existing eval contract**

```bash
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py tests/test_measurement_snapshot.py -k "eval or gate_10 or metrics"
```

Expected: all pass, including `test_eval_search_scores_against_the_index_it_is_given`.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_search.py tests/test_coherent_capture.py
git commit -m "zurvan: gate 10 A/B isolation; the evaluation reports the coherence of what it scored"
```

---

## Task 6 — `zurvan knowledge snapshots` and `zurvan knowledge gc`

**Files:**
- Modify: `scripts/cli.py`
- Test: `tests/test_snapshot_leases.py`

**Interfaces:**
- Consumes: `ms.list_snapshots(root=None, now=None)` and `ms.collect_garbage(root=None, dry_run=False, now=None)` (Task 3).
- Produces: the CLI actions `knowledge snapshots` and `knowledge gc [--dry-run]`. Neither needs a ledger.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_snapshot_leases.py`)

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py -k knowledge
```

Expected: FAIL. argparse exits 2 with `invalid choice: 'snapshots'`.

- [ ] **Step 3: Implement in `scripts/cli.py`**

After the `k_rec.add_argument("--reason", default=None)` line, add:

```python
    # Design §8 names `zurvan snapshot list|gc`; that is the release-archive
    # command above, so these live here (amendment 9.1).
    knowledge_sub.add_parser(
        "snapshots", help="Measurement and rebuild snapshots, their leases and GC verdicts")
    k_gc = knowledge_sub.add_parser(
        "gc", help="Reclaim snapshots whose lease expired AND whose owner is dead")
    k_gc.add_argument("--dry-run", action="store_true")
```

In the `elif args.command == "knowledge":` branch, insert the following directly before the line `        if args.action == "status":`:

```python
        if args.action == "snapshots":
            from scripts import measurement_snapshot as ms
            rows = ms.list_snapshots()
            if not rows:
                print("no snapshots")
                return
            for r in rows:
                print(f"{r['snapshot_id']}  purpose={r['purpose']}  "
                      f"revision={r['knowledge_revision']}  coherence={r['coherence']}  "
                      f"gc={r['gc']} ({r['reason']})")
            return
        if args.action == "gc":
            from scripts import measurement_snapshot as ms
            for path in ms.collect_garbage(dry_run=args.dry_run):
                print(("would reclaim " if args.dry_run else "reclaimed ") + path)
            return
```

- [ ] **Step 4: Run the tests and try the command**

```bash
PYTHONPATH=. python -m pytest -q tests/test_snapshot_leases.py
PYTHONPATH=. python scripts/cli.py knowledge snapshots
PYTHONPATH=. python scripts/cli.py knowledge gc --dry-run
```

Expected: all tests pass. On the live store, both commands exit 0 and list or reclaim nothing unexpected. Read the output, and if `gc --dry-run` would reclaim anything, say what before running it for real.

- [ ] **Step 5: Commit**

```bash
git add scripts/cli.py tests/test_snapshot_leases.py
git commit -m "zurvan: zurvan knowledge snapshots / gc"
```

---

## Task 7 — Full verification, the live measurement, and the record

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md` (header status line, and a new status block after the step-5 block in §10.3), `docs/superpowers/specs/2026-09-18-zurvan-2-amendments.md` (a new §11, "Amendment 9", and a log row), `CLAUDE.md` (the **Z0 is OPEN** bullet), `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`

**Interfaces:**
- Consumes: everything above. Produces no code.

- [ ] **Step 1: The regression floor**

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider 2>&1 | tail -3
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check main...HEAD
PYTHONPATH=. python -m pytest -q tests/test_plan_integrity.py
```

Expected: `0 failed`, and exactly 39 more passes than Task 0's floor: 21 in `test_coherent_capture.py`, 17 in `test_snapshot_leases.py`, and 1 in `test_derived_health.py`. Guard and diff-check are clean. `tests/test_write_attribution.py::test_a_corrupt_write_log_line_does_not_break_the_guard` errored once at setup in a full run during the gauntlet, and passed alone on `main` and on this branch. If it recurs, it is pre-existing, and it is reported rather than folded into this step.

- [ ] **Step 2: The full gate.** Nothing else may be rebuilding the index; check first.

```bash
ls data/search.sqlite.building-* 2>/dev/null && echo "REBUILD IN FLIGHT - STOP" || bash scripts/check.sh 2>&1 | tee ~/.zurvan/measurements/z0-step6/check.txt | tail -5
```

Expected: `check.sh` exits 0 with all 24 gates passing. Gate 6's snapshot is a legacy capture, and its eval output now prints `Snapshot coherence: UNVERIFIED — legacy capture: no required components declared`, which is correct under amendment 9.6. A failed gate is reported as failed.

- [ ] **Step 3: The live measurement (design §10.4), reported whatever it says**

```bash
M=~/.zurvan/measurements/z0-step6
PYTHONPATH=. python scripts/cli.py index search
PYTHONPATH=. python scripts/cli.py graph rebuild
PYTHONPATH=. python scripts/cli.py knowledge status | tee $M/status-before.txt
/usr/bin/time -l env PYTHONPATH=. python scripts/measurement_snapshot.py --label measure \
  --require wiki,search,graph --path-only 2> $M/capture-time.txt | tee $M/capture-path.txt
tail -20 $M/capture-time.txt
python -c "import json,sys,pathlib; p=pathlib.Path(open('$M/capture-path.txt').read().strip()).parent; print(json.dumps(json.load(open(p/'manifest.json'))['coherence'], indent=2)); import shutil; shutil.rmtree(p)"
```

Expected: `knowledge status` shows search and graph `COHERENT+CURRENT`, and the capture prints a `VERIFIED` coherence block. Record the wall time and peak RSS against step 3's 5,904 ms / 8.34 MB. If another session writes between the rebuild and the capture, the capture refuses with `SNAPSHOT_NOT_READY`. That is the feature working, not a failure: record it, rebuild, and measure again.

- [ ] **Step 4: The record.** Write the spec status block (items 1, 8, 9, 10, 13 and 19 each closed, with the named test; item 9 changes from PARTIAL to closed; step 7's inventory is the only Z0 work left). Write amendment 9 with its seven points from this plan's amendments section, and add a log row. Update the CLAUDE.md bullet to "Steps 1–6 … are built. Step 7 (the implicit-mutation inventory driven to empty, §10.1 items 2–3) is NOT BUILT." Add a dated `Raouf:` entry to both AGENTS.md and CHANGELOG.md. The CHANGELOG entry carries the falsification counts from Task 4 Step 5 and the Step 3 measurement. Move AGENTS.md's oldest entry verbatim to `docs/agents-history.md`, so AGENTS.md keeps two.

```bash
PYTHONPATH=. python -m pytest -q tests/test_plan_integrity.py | tail -1
PYTHONPATH=. python scripts/public_repo_guard.py && git diff --check
git add CLAUDE.md AGENTS.md CHANGELOG.md docs/
git commit -m "zurvan: record Z0 step 6 - purpose/required capture, leases, GC, amendment 9"
```

- [ ] **Step 5: Write-back, after every measurement** (per CLAUDE.md)

```bash
PYTHONPATH=. python scripts/cli.py decision add --title "Z0 step 6: coherence verified on frozen copies before publication; GC needs expired lease AND dead owner" --reason "A refused capture must never exist under a final name; a pid alone reclaimed gate 6's snapshot under a live measurement" --status accepted --tags zurvan z0
PYTHONPATH=. python scripts/cli.py agent postedit --summary "Z0 step 6 built: capture(purpose, required), SNAPSHOT_NOT_READY, leases, knowledge snapshots/gc" --files scripts/build_snapshot.py scripts/measurement_snapshot.py scripts/derived_health.py scripts/eval_search.py scripts/cli.py --checks "pytest; check.sh; public_repo_guard"
```

Merging to `main` follows `superpowers:finishing-a-development-branch`. There is no push without Raouf asking for that specific push.
