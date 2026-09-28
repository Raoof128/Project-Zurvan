# Z0 — An external edit is recorded before a managed write, never absorbed into it

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A wiki edit made outside Zurvan is recorded as its own `EXTERNAL_OBSERVED` revision the moment the next managed operation activates, instead of being silently folded into that operation's `MANAGED` revision.

**Architecture:** `commit_mutation` records the manifest of the whole store, so today any unmanaged byte change lands inside the next managed revision (reproduced 2026-09-28). `observe_external_drift` — built in Z0 step 2 and never called by production code — already detects and mints correctly. This wires it in at the one point every managed operation passes before its first byte (`_insert_mutation`), records each observation in a new `external_observations` table so it survives the managed commit that follows, and makes `zurvan knowledge status` report unrecorded drift without writing.

**Tech Stack:** Python 3.12 stdlib, `sqlite3`. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) §5.2 (revision/manifest binding), §10.2 items 12 and 13.

## Global Constraints

- **Atomicity is not laundered.** A snapshot of an `EXTERNAL_OBSERVED` revision reports that atomicity and no consumer treats it as MANAGED. (Design §10.2 item 13.)
- **`EXTERNAL_OBSERVED` is the strongest honest claim available for a tool that does not participate.** No attempt to make unmanaged editors transactional. (Design §11.)
- **Minting underneath a running operation would attribute its half-written files to an external observation.** (`observe_external_drift` docstring.)
- **This module fails closed.** A write that landed while the ledger failed to record it is a correctness incident. (`knowledge_revision` module docstring.)
- **A write with no operation above it is REFUSED.** (CLAUDE.md, 2026-09-28.)
- **Production knowledge state is never a test fixture.** Throwaway stores under `tmp_path` with `ZURVAN_ROOT`. (AGENTS.md.)
- **A failed gate is reported as failed.** (CLAUDE.md.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.**

## Measured inputs (2026-09-28, before execution)

```text
laundering, throwaway store     bootstrap -> hand edit -> managed write: revision 2 is
                                MANAGED, its digest covers the hand edit, and
                                observe_external_drift afterwards returns None
production callers of           none (grep over scripts/)
  observe_external_drift
authoritative_manifest, live    median 60 ms over 4,605 files (n=7, warm); one cold
                                run measured 174 ms
suite with absorption simulated 1215 passed / 0 failed (plugin calling
                                observe_external_drift before every mutation row)
```

## Review Focus

1. **Obsidian still saving when the CLI writes** — the store is changing between the two manifest reads. Expected: no mint (the bytes are not yet stable), the managed write proceeds, and the unstable case is a stated bound. (Task 2, `test_an_unstable_store_is_not_minted_and_the_write_proceeds`.)
2. **Another process's mutation in flight** — its half-written files are not external. Expected: no mint. (Task 2, `test_another_operation_in_flight_is_not_external_drift`.)
3. **A store never bootstrapped** — no baseline to contradict. Expected: no mint, behaviour unchanged. (Task 2, `test_a_store_with_no_baseline_is_left_alone`.)
4. **A SUSPECT store** — reconcile owns it. Expected: no mint, the write proceeds and SUSPECT persists. (Task 2, `test_a_suspect_store_is_left_to_reconcile`.)
5. **An edit only under `wiki/traces/`** — derived, outside the manifest. Expected: no mint. (Task 2, `test_a_derived_subtree_edit_is_not_drift`.)

---

## Task 1 — A minted observation leaves a record

**Files:**
- Modify: `scripts/knowledge_revision.py` (`ensure_schema`, `observe_external_drift`)
- Test: `tests/test_external_drift.py` (new)

**Interfaces:**
- Produces: `observe_external_drift(root=None, trigger="manual")` — returns the new revision or `None`; on a mint, inserts one `external_observations` row in the same transaction. `external_observations(root=None) -> list` returns `(revision, trigger, manifest_before, manifest_after, observed_at)` tuples, oldest first. `class DriftUnobservable(LedgerError)` for "still changing", "in flight" and "ledger moved".

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run and watch them fail**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_external_drift.py`
Expected: `AttributeError: module 'scripts.knowledge_revision' has no attribute 'external_observations'` (and `DriftUnobservable`).

- [ ] **Step 3: Implement**

In `ensure_schema`'s `executescript`, after the `reconciliations` table:

```python
            CREATE TABLE IF NOT EXISTS external_observations (
                revision        INTEGER PRIMARY KEY,
                trigger         TEXT NOT NULL,
                manifest_before TEXT NOT NULL,
                manifest_after  TEXT NOT NULL,
                observed_at     TEXT NOT NULL
            );
```

After `class NestedMutationError`:

```python
class DriftUnobservable(LedgerError):
    """Drift may exist but cannot be attributed right now: the store is still
    changing, another operation is in flight, or the ledger moved."""
```

`observe_external_drift` gains `trigger="manual"`; its three transient refusals raise `DriftUnobservable` instead of `LedgerError`; and inside the minting transaction, after `_set_revision(...)`:

```python
            conn.execute(
                "INSERT INTO external_observations (revision, trigger,"
                " manifest_before, manifest_after, observed_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (new_revision, trigger, recorded, second, _now()))
```

And the reader:

```python
def external_observations(root=None) -> list:
    """Every minted EXTERNAL_OBSERVED revision, oldest first. The record
    outlives the managed commit that follows, which overwrites atomicity."""
    conn = _connect_ro(root)
    try:
        return conn.execute(
            "SELECT revision, trigger, manifest_before, manifest_after,"
            " observed_at FROM external_observations ORDER BY revision"
        ).fetchall()
    finally:
        conn.close()
```

- [ ] **Step 4: Run** — Expected: 3 passed. Also `tests/test_knowledge_revision.py` stays green (its drift tests expect `LedgerError`; `DriftUnobservable` is a subclass).

- [ ] **Step 5: Commit** — `git commit -m "zurvan: an external observation leaves a record"`

---

## Task 2 — A managed operation records drift before its first byte

**Files:**
- Modify: `scripts/knowledge_revision.py` (`_insert_mutation`, new `_absorb_external_drift`)
- Test: `tests/test_external_drift.py`

**Interfaces:**
- Consumes: `observe_external_drift(root=None, trigger="manual")`, `DriftUnobservable`, `external_observations(root=None) -> list`
- Produces: `_absorb_external_drift(base) -> int | None`, called at the top of `_insert_mutation`.

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run and watch them fail** — Expected: the first test fails (`external_observations` is empty: the defect), the CLI test likewise; the "left alone" tests pass already (they pin non-behaviour).

- [ ] **Step 3: Implement**

```python
def _absorb_external_drift(base):
    """Record an external edit as its own revision before a managed write.

    Without this, `commit_mutation` records the whole store's manifest and
    the edit is laundered into the managed revision (reproduced 2026-09-28).
    Skipped - and bounded, not fixed - when drift cannot be attributed: no
    baseline, SUSPECT (reconcile owns it), another operation in flight, or
    the store still changing.
    """
    try:
        state = current(base)
    except LedgerError:
        return None
    if not state.manifest_digest or state.state != CLEAN:
        return None
    if mutations_in(ACTIVE, base):
        return None
    try:
        return observe_external_drift(base, trigger="activation")
    except DriftUnobservable:
        return None
```

At the top of `_insert_mutation`, after `ensure_schema(op.root)`:

```python
    _absorb_external_drift(op.root)
```

- [ ] **Step 4: Run** `tests/test_external_drift.py tests/test_knowledge_revision.py tests/test_knowledge_attribution.py tests/test_fail_closed_writes.py` — all pass.

- [ ] **Step 5: Commit** — `git commit -m "zurvan: an external edit is recorded before a managed write, not absorbed"`

---

## Task 3 — `knowledge status` says when the ledger is behind the bytes

**Files:** Modify `scripts/cli.py` (the `status` branch); Test `tests/test_external_drift.py`

- [ ] **Step 1: Failing test** (append)

```python
def test_status_reports_unrecorded_drift_without_writing(root):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    fingerprint = kr.ledger_fingerprint(root)
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO)}
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "cli.py"), "knowledge", "status"],
        cwd=str(root), env=env, capture_output=True, text=True).stdout
    assert "drift:      UNRECORDED" in out
    assert kr.ledger_fingerprint(root) == fingerprint
```

- [ ] **Step 2: Watch it fail** — no `drift:` line.

- [ ] **Step 3: Implement** — in the `status` branch, after the `baseline:` line:

```python
            if rev.manifest_digest:
                from scripts.knowledge_manifest import authoritative_manifest
                live = authoritative_manifest()
                print("drift:      " + ("none" if live == rev.manifest_digest else
                      "UNRECORDED — the wiki changed outside Zurvan; the next "
                      "write records it as EXTERNAL_OBSERVED"))
```

- [ ] **Step 4: Run, Step 5: Commit** — `git commit -m "zurvan: knowledge status reports unrecorded drift"`

---

## Task 4 — Falsify, gate, record

- [ ] **Step 1: Falsify.** A scratchpad plugin making `_absorb_external_drift` a no-op must turn `test_a_hand_edit_becomes_its_own_revision_before_the_managed_write` and `test_the_cli_records_a_hand_edit_end_to_end` red; one dropping the `external_observations` INSERT must turn `test_a_manual_observation_is_recorded` red.

```python
def pytest_configure(config):
    from scripts import knowledge_revision as kr
    kr._absorb_external_drift = lambda base: None
```

- [ ] **Step 2: Gate.** `bash scripts/check.sh` (EXIT=0, pytest 0 failed), `public_repo_guard`, `git diff --check`, live `wiki/*.md` hashed before and after.
- [ ] **Step 3: Record.** Design §5.2 note; AGENTS/CHANGELOG `Raouf:` entry closing 2026-09-24 item 3 with the unstable-store bound; CLAUDE.md ledger section line.

```text
closes  2026-09-24 item 3 (hand edit invisible) — except while the store is still changing
does not close  Z0 gate 12 as specified (capture-time detection is step 5/6)
```
