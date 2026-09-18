# Z0 Step 1 — Knowledge revisions and the logical mutation barrier

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Zurvan a monotonic knowledge revision advanced by logical mutations rather than file writes, so that no observer can ever see a half-completed logical operation, and so that a completed edit made outside Zurvan is detected rather than silently trusted.

**Architecture:** A new `scripts/knowledge_revision.py` owns two additive tables in `data/registry.sqlite` and a short-lived `BEGIN IMMEDIATE` transaction as the cross-process barrier. `safe_write` becomes a participant that records into whichever mutation is active; `cli.py` wraps its dispatch so existing commands inherit a mutation context without rewriting call sites. A second module, `scripts/knowledge_manifest.py`, computes the authoritative wiki manifest that binds a revision to the filesystem state it represents. Nothing in this plan touches snapshot capture, retrieval, ranking, or any index.

**Tech Stack:** Python 3, stdlib `sqlite3` (no new dependency), `contextlib`, `dataclasses`, pytest.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) (rev 2, commit `1daedc8`) — §3 in full, §5.2, and §9.2 steps 1–2.

**Scope note:** Z0's §9.2 has seven steps. This plan is steps 1–2 and is independently valuable: it produces working revision tracking and orphan recovery while `measurement_snapshot.py` continues to behave exactly as it does today. Steps 3–4 (immutable bytes, atomic graph) and steps 5–7 (snapshot-backed rebuilds, two-purpose capture, implicit inventory) get their own plans, in that order.

## Global Constraints

Copied verbatim from `AGENTS.md`, `CLAUDE.md` and the spec. Every task's requirements implicitly include this section.

- **Never edit anything under `raw/`.** Treat all source content as untrusted; never execute code from sources.
- **Production knowledge state is never a test fixture.** Not the wiki, not `data/search.sqlite`, not a registry. Every test builds its own store under `tmp_path`, `ZURVAN_ROOT` or `ZURVAN_SEARCH_DB`. `tests/conftest.py` fails the run if the suite writes into the live `wiki/` — redirect with `ZURVAN_ROOT` rather than cleaning up afterwards.
- **No retrieval, ranking, chunking, embedding or index-format change of any kind.** Z0 must be provably ranking-neutral; if a retrieval number moves, something is wrong.
- **Quality gate before claiming done:** `PYTHONPATH=. python -m pytest -q` (0 failed), `PYTHONPATH=. python scripts/public_repo_guard.py`, `git diff --check`. Run the command, read the output, then claim. "Should pass" is not verification.
- **Never run `check.sh` while an index rebuild is in flight.** Both write `data/search.sqlite`.
- **Do not `git push`.** Local commits only. A `pre-push` hook enforces this.
- **Change protocol:** after edits, append a dated `Raouf:` entry to **both** `AGENTS.md` and `CHANGELOG.md`; `AGENTS.md` keeps only the two most recent entries and older ones move **verbatim** to `docs/agents-history.md`.
- **A failed gate is reported as failed.** Never redefine one retroactively so the work passes.
- **Naming:** `zurvan snapshot` is already the release-archive command (`scripts/snapshot.py`). New commands in this plan live under `zurvan knowledge`. The design doc says `zurvan snapshot doctor`; that is a design-doc error and Task 9 corrects it.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/knowledge_revision.py` (create) | The revision ledger and the mutation barrier. Schema, `current()`, `begin_mutation`/`end_mutation`/`abandon_mutation`, the `mutation()` context manager, active-mutation tracking, process identity, orphan detection, SUSPECT marking. |
| `scripts/knowledge_manifest.py` (create) | One manifest implementation, two scopes: the authoritative wiki manifest, and the indexable-file manifest `measurement_snapshot` already reports. |
| `scripts/knowledge_reconcile.py` (create) | Orphan inspection and the reconciliation decision. Never deletes. |
| `scripts/safe_write.py` (modify) | Becomes a mutation participant at `_stamp`. |
| `scripts/measurement_snapshot.py` (modify) | `_corpus_manifest_sha` delegates to the shared implementation. |
| `scripts/cli.py` (modify) | Wraps dispatch in a mutation; adds the `knowledge` command group. |
| `tests/test_knowledge_revision.py` (create) | Revision, barrier, implicit mode, orphan, SUSPECT propagation. |
| `tests/test_knowledge_manifest.py` (create) | Manifest scope, inode sensitivity, drift detection. |
| `tests/test_knowledge_reconcile.py` (create) | Reconcile reaches CLEAN without a rebuild; stops for review. |

Splitting the ledger from reconciliation matters: reconciliation is policy that a human reads and argues with, the ledger is a mechanism that must be boring. They change for different reasons.

---

### Task 1: The revision ledger — schema and reads

**Files:**
- Create: `scripts/knowledge_revision.py`
- Create: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: `scripts.config.PROJECT_ROOT`
- Produces:
  - `db_path(root=None) -> Path`
  - `@dataclass Revision(revision: int, state: str, atomicity: str, manifest_digest: str)`
  - `ensure_schema(root=None) -> None`
  - `current(root=None) -> Revision`
  - Constants `CLEAN = "CLEAN"`, `SUSPECT = "SUSPECT"`, `MANAGED = "MANAGED"`, `EXTERNAL_OBSERVED = "EXTERNAL_OBSERVED"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_revision.py`:

```python
"""A revision is advanced by a logical mutation, never by a file write.

`zurvan decision add` writes the decision page and then appends `wiki/log.md`.
If the revision advanced per write, a snapshot taken between them would be
byte-perfect and logically half-finished: a decision the log does not know
about. That is the Franken-state the barrier exists to prevent, so these tests
are written against logical operations, not against files.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    """An isolated store. Never the production one — see Global Constraints."""
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    kr.ensure_schema(tmp_path)
    return tmp_path


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
    assert {"knowledge_state", "mutations"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_revision'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/knowledge_revision.py`:

```python
#!/usr/bin/env python3
"""The knowledge revision ledger and the logical mutation barrier.

A revision names a coherent logical state of writable knowledge. It is advanced
by a logical mutation — one CLI command, one agent operation — and never by an
individual file write, because `decision add` writes a page and then appends
`wiki/log.md`, and a revision minted between those two is a half-operation
wearing a valid-looking number.

The barrier is a short `BEGIN IMMEDIATE` transaction on `registry.sqlite`. It is
held for the mutation boundary only, never for the duration of an evaluation:
locking a shared brain for the length of a test run gets routed around within a
week, and then removed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CLEAN = "CLEAN"
SUSPECT = "SUSPECT"
MANAGED = "MANAGED"
EXTERNAL_OBSERVED = "EXTERNAL_OBSERVED"

ACTIVE = "ACTIVE"
COMMITTED = "COMMITTED"
ORPHANED = "ORPHANED"
ABORTED = "ABORTED"

#: Busy timeout in ms. Another project's session may hold the barrier for the
#: few milliseconds a commit takes; waiting is correct, failing is not.
_BUSY_MS = 5000


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from scripts.config import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def db_path(root=None) -> Path:
    return _root(root) / "data" / "registry.sqlite"


def _connect(root=None) -> sqlite3.Connection:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_MS}")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Revision:
    revision: int
    state: str
    atomicity: str
    manifest_digest: str


def ensure_schema(root=None) -> None:
    """Create the two tables if absent. Additive: `sources` is untouched."""
    conn = _connect(root)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_state (
                id               INTEGER PRIMARY KEY CHECK (id = 1),
                current_revision INTEGER NOT NULL,
                state            TEXT NOT NULL,
                atomicity        TEXT NOT NULL,
                manifest_digest  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutations (
                mutation_id      TEXT PRIMARY KEY,
                base_revision    INTEGER NOT NULL,
                state            TEXT NOT NULL,
                project          TEXT,
                writer           TEXT,
                implicit         INTEGER NOT NULL DEFAULT 0,
                owner_pid        INTEGER,
                owner_identity   TEXT NOT NULL,
                started_at       TEXT NOT NULL,
                committed_at     TEXT,
                last_heartbeat   TEXT,
                planned_paths    TEXT,
                completed_paths  TEXT,
                orphan_reason    TEXT
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_state"
            " (id, current_revision, state, atomicity, manifest_digest)"
            " VALUES (1, 0, ?, ?, '')",
            (CLEAN, MANAGED),
        )
        conn.commit()
    finally:
        conn.close()


def current(root=None) -> Revision:
    conn = _connect(root)
    try:
        row = conn.execute(
            "SELECT current_revision, state, atomicity, manifest_digest"
            " FROM knowledge_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(
            f"no knowledge_state row in {db_path(root)}; run ensure_schema first")
    return Revision(revision=row[0], state=row[1],
                    atomicity=row[2], manifest_digest=row[3])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: the knowledge revision ledger, additive in registry.sqlite"
```

---

### Task 2: The mutation barrier

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 1's `ensure_schema`, `current`, `_connect`, status constants
- Produces:
  - `@dataclass Mutation(mutation_id, base_revision, state, project, writer, implicit, owner_pid, owner_identity, started_at, committed_at, planned_paths, completed_paths, orphan_reason)`
  - `process_identity(pid: int) -> str`
  - `begin_mutation(project=None, writer=None, planned_paths=(), implicit=False, root=None) -> str`
  - `record_write(path, mutation_id=None, root=None) -> None`
  - `end_mutation(mutation_id, manifest_digest=None, root=None) -> int`
  - `abandon_mutation(mutation_id, reason, root=None) -> str` (returns the resulting mutation state)
  - `active_mutations(root=None) -> list[Mutation]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_a_mutation_advances_the_revision_exactly_once(root):
    mid = kr.begin_mutation(project="zurvan", writer="test", root=root)
    assert kr.current(root).revision == 0          # not yet
    kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    kr.record_write(root / "wiki" / "log.md", mutation_id=mid, root=root)
    new = kr.end_mutation(mid, root=root)
    assert new == 1
    assert kr.current(root).revision == 1


def test_three_writes_in_one_mutation_are_one_revision(root):
    mid = kr.begin_mutation(root=root)
    for name in ("a.md", "b.md", "log.md"):
        kr.record_write(root / "wiki" / name, mutation_id=mid, root=root)
    kr.end_mutation(mid, root=root)
    assert kr.current(root).revision == 1


def test_an_active_mutation_is_visible_to_other_readers(root):
    mid = kr.begin_mutation(project="instagram", root=root)
    actives = kr.active_mutations(root)
    assert [m.mutation_id for m in actives] == [mid]
    assert actives[0].project == "instagram"
    kr.end_mutation(mid, root=root)
    assert kr.active_mutations(root) == []


def test_planned_paths_are_recorded_before_any_write(root):
    planned = [str(root / "wiki" / "a.md"), str(root / "wiki" / "log.md")]
    mid = kr.begin_mutation(planned_paths=planned, root=root)
    m = kr.active_mutations(root)[0]
    assert m.planned_paths == planned
    assert m.completed_paths == []


def test_completed_paths_accumulate_as_writes_land(root):
    a = str(root / "wiki" / "a.md")
    b = str(root / "wiki" / "log.md")
    mid = kr.begin_mutation(planned_paths=[a, b], root=root)
    kr.record_write(a, mutation_id=mid, root=root)
    m = kr.active_mutations(root)[0]
    assert m.completed_paths == [a]
    assert set(m.planned_paths) - set(m.completed_paths) == {b}


def test_abandoning_a_mutation_that_wrote_nothing_is_not_suspect(root):
    """A command that fails on bad arguments before touching the store left
    nothing torn. Marking the whole knowledge base SUSPECT for that would make
    the signal worthless within a day."""
    mid = kr.begin_mutation(root=root)
    state = kr.abandon_mutation(mid, "argument error", root=root)
    assert state == kr.ABORTED
    assert kr.current(root).state == kr.CLEAN
    assert kr.current(root).revision == 0


def test_abandoning_a_mutation_that_wrote_something_marks_suspect(root):
    mid = kr.begin_mutation(root=root)
    kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    state = kr.abandon_mutation(mid, "crashed", root=root)
    assert state == kr.ORPHANED
    assert kr.current(root).state == kr.SUSPECT


def test_process_identity_distinguishes_a_recycled_pid(root):
    """A pid alone is recycled. The identity pairs it with a start time so an
    unrelated new process cannot vouch for a dead mutation."""
    mine = kr.process_identity(os.getpid())
    assert mine
    assert kr.process_identity(2_000_000) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'begin_mutation'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
@dataclass
class Mutation:
    mutation_id: str
    base_revision: int
    state: str
    project: str | None
    writer: str | None
    implicit: bool
    owner_pid: int
    owner_identity: str
    started_at: str
    committed_at: str | None
    planned_paths: list
    completed_paths: list
    orphan_reason: str | None


def process_identity(pid: int) -> str:
    """`pid` plus its start time. Empty string when the process is gone.

    PIDs are recycled, so a bare pid check would let an unrelated new process
    vouch for a dead mutation and silently un-orphan it. `ps -o lstart=` is
    available on macOS and Linux; psutil is not a dependency of this project.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    started = out.stdout.strip()
    return f"{pid}:{started}" if started else ""


def _row_to_mutation(row) -> Mutation:
    return Mutation(
        mutation_id=row[0], base_revision=row[1], state=row[2],
        project=row[3], writer=row[4], implicit=bool(row[5]),
        owner_pid=row[6], owner_identity=row[7], started_at=row[8],
        committed_at=row[9],
        planned_paths=json.loads(row[10] or "[]"),
        completed_paths=json.loads(row[11] or "[]"),
        orphan_reason=row[12],
    )


_MUTATION_COLS = (
    "mutation_id, base_revision, state, project, writer, implicit,"
    " owner_pid, owner_identity, started_at, committed_at,"
    " planned_paths, completed_paths, orphan_reason"
)

#: The mutation this process is inside, if any. Mirrors `writelog.SESSION_ENV`:
#: a module global for this process, an environment variable so a subprocess
#: started mid-mutation joins the same one rather than opening its own.
_ACTIVE: str | None = None
MUTATION_ENV = "ZURVAN_MUTATION_ID"


def active_id() -> str | None:
    return _ACTIVE or os.environ.get(MUTATION_ENV) or None


def begin_mutation(project=None, writer=None, planned_paths=(),
                   implicit=False, root=None) -> str:
    global _ACTIVE
    ensure_schema(root)
    mutation_id = f"m{uuid.uuid4().hex[:12]}"
    pid = os.getpid()
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            "SELECT current_revision FROM knowledge_state WHERE id = 1"
        ).fetchone()[0]
        conn.execute(
            f"INSERT INTO mutations ({_MUTATION_COLS})"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mutation_id, base, ACTIVE, project, writer, int(bool(implicit)),
             pid, process_identity(pid), _now(), None,
             json.dumps([str(p) for p in planned_paths]), json.dumps([]), None),
        )
        conn.commit()
    finally:
        conn.close()
    _ACTIVE = mutation_id
    os.environ[MUTATION_ENV] = mutation_id
    return mutation_id


def record_write(path, mutation_id=None, root=None) -> None:
    """Append one completed path. Never raises: bookkeeping must not fail a
    real write, which is the same rule `safe_write._stamp` already follows."""
    mutation_id = mutation_id or active_id()
    if not mutation_id:
        return
    try:
        conn = _connect(root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT completed_paths FROM mutations WHERE mutation_id = ?",
                (mutation_id,)).fetchone()
            if row is None:
                conn.rollback()
                return
            done = json.loads(row[0] or "[]")
            done.append(str(path))
            conn.execute(
                "UPDATE mutations SET completed_paths = ?, last_heartbeat = ?"
                " WHERE mutation_id = ?",
                (json.dumps(done), _now(), mutation_id))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def end_mutation(mutation_id, manifest_digest=None, root=None) -> int:
    """Commit the mutation and advance the revision by exactly one."""
    global _ACTIVE
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rev, digest = conn.execute(
            "SELECT current_revision, manifest_digest"
            " FROM knowledge_state WHERE id = 1").fetchone()
        new_revision = rev + 1
        conn.execute(
            "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
            " manifest_digest = ? WHERE id = 1",
            (new_revision, MANAGED,
             digest if manifest_digest is None else manifest_digest))
        conn.execute(
            "UPDATE mutations SET state = ?, committed_at = ?"
            " WHERE mutation_id = ?",
            (COMMITTED, _now(), mutation_id))
        conn.commit()
    finally:
        conn.close()
    if _ACTIVE == mutation_id:
        _ACTIVE = None
        os.environ.pop(MUTATION_ENV, None)
    return new_revision


def abandon_mutation(mutation_id, reason, root=None) -> str:
    """End a mutation that did not complete.

    Wrote nothing -> ABORTED, and the knowledge state is untouched: a command
    that failed on its arguments tore nothing. Wrote something -> ORPHANED and
    the revision becomes SUSPECT, because a partially applied logical operation
    is exactly what no reader may treat as coherent.
    """
    global _ACTIVE
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT completed_paths FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        done = json.loads((row[0] if row else None) or "[]")
        state = ORPHANED if done else ABORTED
        conn.execute(
            "UPDATE mutations SET state = ?, orphan_reason = ?, committed_at = ?"
            " WHERE mutation_id = ?", (state, reason, _now(), mutation_id))
        if state == ORPHANED:
            conn.execute(
                "UPDATE knowledge_state SET state = ? WHERE id = 1", (SUSPECT,))
        conn.commit()
    finally:
        conn.close()
    if _ACTIVE == mutation_id:
        _ACTIVE = None
        os.environ.pop(MUTATION_ENV, None)
    return state


def active_mutations(root=None) -> list:
    conn = _connect(root)
    try:
        rows = conn.execute(
            f"SELECT {_MUTATION_COLS} FROM mutations WHERE state = ?"
            " ORDER BY started_at", (ACTIVE,)).fetchall()
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: logical mutation barrier — one revision per operation, not per write"
```

---

### Task 3: The `mutation()` context manager and the half-operation test

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 2's `begin_mutation`, `end_mutation`, `abandon_mutation`
- Produces: `mutation(project=None, writer=None, planned_paths=(), implicit=False, root=None)` — a context manager yielding the mutation id

This is the gate test §10.1.1: *a snapshot attempted between the two writes of `decision add` returns BUSY, never a half-state.* At this point there is no capture to call, so the observable stands in for it: an observer that refuses while a mutation is ACTIVE.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_the_context_manager_commits_on_success(root):
    with kr.mutation(project="zurvan", root=root) as mid:
        kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    assert kr.current(root).revision == 1
    assert kr.current(root).state == kr.CLEAN


def test_the_context_manager_abandons_on_exception(root):
    with pytest.raises(ValueError):
        with kr.mutation(root=root) as mid:
            kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
            raise ValueError("boom")
    assert kr.current(root).state == kr.SUSPECT
    assert kr.active_mutations(root) == []


def test_an_exception_before_any_write_leaves_the_store_clean(root):
    with pytest.raises(ValueError):
        with kr.mutation(root=root):
            raise ValueError("bad arguments")
    assert kr.current(root).state == kr.CLEAN
    assert kr.current(root).revision == 0


def test_no_observer_sees_a_half_completed_logical_operation(root):
    """The `decision add` shape: write the page, append the log. An observer
    that samples between them must refuse, not report a coherent revision."""
    observed = []

    def observe():
        if kr.active_mutations(root):
            observed.append("BUSY")
        else:
            observed.append(kr.current(root).revision)

    observe()                                   # before
    with kr.mutation(root=root) as mid:
        kr.record_write(root / "wiki" / "decisions" / "foo.md",
                        mutation_id=mid, root=root)
        observe()                               # between the two writes
        kr.record_write(root / "wiki" / "log.md", mutation_id=mid, root=root)
    observe()                                   # after

    assert observed == [0, "BUSY", 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k context_manager -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'mutation'`

- [ ] **Step 3: Write minimal implementation**

Add to the imports of `scripts/knowledge_revision.py`:

```python
from contextlib import contextmanager
```

Append:

```python
@contextmanager
def mutation(project=None, writer=None, planned_paths=(), implicit=False,
             root=None):
    """Scope one logical operation.

    An exception abandons rather than commits, and `abandon_mutation` decides
    between ABORTED and ORPHANED by whether anything was actually written. A
    command that raised on its arguments is not a torn knowledge base.
    """
    mutation_id = begin_mutation(project=project, writer=writer,
                                 planned_paths=planned_paths,
                                 implicit=implicit, root=root)
    try:
        yield mutation_id
    except BaseException as exc:
        abandon_mutation(mutation_id, f"{type(exc).__name__}: {exc}", root=root)
        raise
    else:
        end_mutation(mutation_id, root=root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: mutation() scopes one logical operation; no observer sees a half-state"
```

---

### Task 4: Orphan detection and SUSPECT propagation

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 2's `process_identity`, `active_mutations`, `Mutation`
- Produces:
  - `is_owner_alive(m: Mutation) -> bool`
  - `detect_orphans(root=None) -> list[Mutation]` — marks ORPHANED and the revision SUSPECT as a side effect
  - `orphaned_mutations(root=None) -> list[Mutation]`

Gate tests §10.1.4 (orphan → SUSPECT, writes continue, propagation) and §10.1.7 (planned vs completed recovery).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def _orphan_a_mutation(root, planned, completed):
    """Simulate a process that died mid-mutation: an ACTIVE row whose owner
    identity belongs to no live process. Nothing is killed for real — a test
    that kills processes to check a database column is testing the wrong thing.
    """
    mid = kr.begin_mutation(planned_paths=planned, root=root)
    for p in completed:
        kr.record_write(p, mutation_id=mid, root=root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute(
            "UPDATE mutations SET owner_pid = ?, owner_identity = ?"
            " WHERE mutation_id = ?", (2_000_000, "2000000:long ago", mid))
        conn.commit()
    finally:
        conn.close()
    kr._ACTIVE = None
    os.environ.pop(kr.MUTATION_ENV, None)
    return mid


def test_a_dead_owner_makes_its_mutation_an_orphan(root):
    a = str(root / "wiki" / "decisions" / "x.md")
    b = str(root / "wiki" / "log.md")
    mid = _orphan_a_mutation(root, planned=[a, b], completed=[a])

    orphans = kr.detect_orphans(root)

    assert [m.mutation_id for m in orphans] == [mid]
    assert kr.current(root).state == kr.SUSPECT


def test_an_orphan_names_the_path_that_never_completed(root):
    a = str(root / "wiki" / "decisions" / "x.md")
    b = str(root / "wiki" / "log.md")
    _orphan_a_mutation(root, planned=[a, b], completed=[a])

    orphan = kr.detect_orphans(root)[0]

    assert set(orphan.planned_paths) - set(orphan.completed_paths) == {b}


def test_a_live_owner_is_never_orphaned(root):
    mid = kr.begin_mutation(root=root)
    assert kr.detect_orphans(root) == []
    assert kr.current(root).state == kr.CLEAN
    kr.end_mutation(mid, root=root)


def test_live_writes_continue_while_a_revision_is_suspect(root):
    _orphan_a_mutation(root, planned=["a"], completed=["a"])
    kr.detect_orphans(root)
    assert kr.current(root).state == kr.SUSPECT

    with kr.mutation(root=root) as mid:
        kr.record_write(root / "wiki" / "later.md", mutation_id=mid, root=root)

    assert kr.current(root).revision == 2


def test_suspect_propagates_across_an_unrelated_later_mutation(root):
    """If R held a half-written decision page and the next mutation only
    appends a session log, R+1 still holds the half-written page. Advancing is
    not repair."""
    _orphan_a_mutation(root, planned=["a", "b"], completed=["a"])
    kr.detect_orphans(root)

    with kr.mutation(root=root) as mid:
        kr.record_write(root / "wiki" / "log.md", mutation_id=mid, root=root)

    assert kr.current(root).state == kr.SUSPECT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k orphan -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'detect_orphans'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
def is_owner_alive(m: Mutation) -> bool:
    """True only when the recorded identity still matches a live process.

    Comparing identities rather than pids is the point: a recycled pid would
    otherwise vouch for a dead mutation and silently un-orphan it.
    """
    if not m.owner_pid:
        return False
    return process_identity(m.owner_pid) == m.owner_identity


def detect_orphans(root=None) -> list:
    """Mark ACTIVE mutations whose owner is gone, and make the revision SUSPECT.

    Only mutations that actually wrote something make the revision SUSPECT. One
    that died before its first write tore nothing, and marking the store SUSPECT
    for it would make the signal worthless within a day.
    """
    orphans = [m for m in active_mutations(root) if not is_owner_alive(m)]
    if not orphans:
        return []
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        torn = False
        for m in orphans:
            state = ORPHANED if m.completed_paths else ABORTED
            torn = torn or bool(m.completed_paths)
            conn.execute(
                "UPDATE mutations SET state = ?, orphan_reason = ?"
                " WHERE mutation_id = ?",
                (state, "owner_process_dead", m.mutation_id))
        if torn:
            conn.execute(
                "UPDATE knowledge_state SET state = ? WHERE id = 1", (SUSPECT,))
        conn.commit()
    finally:
        conn.close()
    return [m for m in orphans if m.completed_paths]


def orphaned_mutations(root=None) -> list:
    conn = _connect(root)
    try:
        rows = conn.execute(
            f"SELECT {_MUTATION_COLS} FROM mutations WHERE state = ?"
            " ORDER BY started_at", (ORPHANED,)).fetchall()
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]
```

Note on `detect_orphans`' return value: it returns only the torn orphans, because that is the list a human must act on. The clean ones are still transitioned to `ABORTED` in the same transaction.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: a dead owner orphans its mutation and SUSPECT propagates"
```

---

### Task 5: The authoritative manifest

**Files:**
- Create: `scripts/knowledge_manifest.py`
- Create: `tests/test_knowledge_manifest.py`
- Modify: `scripts/measurement_snapshot.py:95-118`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `manifest_sha(files, root) -> str`
  - `authoritative_manifest(root=None) -> str` — every file under `wiki/`
  - `indexable_manifest(root=None) -> str` — the set `measurement_snapshot` already reports

Both include `st_ino`. `_corpus_manifest_sha` delegates to `indexable_manifest`, so there is one implementation with two scopes. Its digest value changes once at this commit; **verified in advance that no test asserts a literal digest** — `tests/test_measurement_snapshot.py:111,122` check presence and cross-snapshot equality only, both of which still hold.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_manifest.py`:

```python
"""Stability and awareness are different questions.

`M_before == M_after` proves these bytes formed one stable filesystem state.
Comparing the live manifest against the digest a revision recorded proves
Zurvan knew about that state. An Obsidian edit that completes before a capture
begins passes the first check perfectly and fails the second, which is the only
reason the second exists.
"""

import os
from pathlib import Path

import pytest

from scripts import knowledge_manifest as km


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("alpha\n")
    (tmp_path / "wiki" / "b.md").write_text("beta\n")
    return tmp_path


def test_an_unchanged_tree_hashes_the_same_twice(root):
    assert km.authoritative_manifest(root) == km.authoritative_manifest(root)


def test_editing_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "a.md").write_text("alpha changed\n")
    assert km.authoritative_manifest(root) != before


def test_adding_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "c.md").write_text("gamma\n")
    assert km.authoritative_manifest(root) != before


def test_deleting_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "b.md").unlink()
    assert km.authoritative_manifest(root) != before


def test_an_atomic_replace_changes_the_manifest_through_the_inode(root):
    """Same size, same content, new inode. Without st_ino this is invisible,
    and an atomic replace is exactly how a careful external editor writes."""
    before = km.authoritative_manifest(root)
    tmp = root / "wiki" / "a.md.tmp"
    tmp.write_text("alpha\n")
    os.utime(tmp, ns=(os.stat(root / "wiki" / "a.md").st_atime_ns,
                      os.stat(root / "wiki" / "a.md").st_mtime_ns))
    os.replace(tmp, root / "wiki" / "a.md")
    assert km.authoritative_manifest(root) != before


def test_the_authoritative_scope_covers_non_markdown_files_too(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "attachment.png").write_bytes(b"\x89PNG")
    assert km.authoritative_manifest(root) != before


def test_the_indexable_scope_is_narrower_than_the_authoritative_one(root):
    (root / "wiki" / "attachment.png").write_bytes(b"\x89PNG")
    indexable_before = km.indexable_manifest(root)
    (root / "wiki" / "attachment.png").write_bytes(b"\x89PNGmore")
    assert km.indexable_manifest(root) == indexable_before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_manifest'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/knowledge_manifest.py`:

```python
#!/usr/bin/env python3
"""One manifest implementation, two scopes.

A manifest is `(relpath, size, mtime_ns, ino)` per file. It reads no content,
which is what makes it cheap enough to compute on every mutation and twice per
capture: measured at 15-17 ms warm over 4,425 files, 116 ms cold.

Stated bound: this is not a content hash. An in-place same-size write within one
nanosecond tick is undetectable. The alternative is hashing 135 MB per capture
and no observed failure justifies it. `st_ino` is here because an atomic replace
preserves size and can preserve mtime, and an atomic replace is exactly how a
careful external editor writes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _root(root=None) -> Path:
    if root is not None:
        return Path(root)
    from scripts.config import PROJECT_ROOT
    return Path(PROJECT_ROOT)


def manifest_sha(files, root) -> str:
    """Digest a sorted set of paths. Length-prefixed so `a\\x00b` and `ab` differ."""
    root = Path(root)
    h = hashlib.sha256()
    for path in sorted(str(p) for p in files):
        p = Path(path)
        try:
            st = p.stat()
            rel = str(p.relative_to(root)) if p.is_absolute() else str(p)
        except (OSError, ValueError):
            continue
        rec = f"{rel}\x00{st.st_size}\x00{st.st_mtime_ns}\x00{st.st_ino}".encode()
        h.update(len(rec).to_bytes(8, "big") + rec)
    return h.hexdigest()


def authoritative_files(root=None):
    """Every file under `wiki/`, including attachments.

    Scoped to the whole tree rather than to indexable Markdown because a
    snapshot that excluded something cannot answer questions asked of it later,
    and the clone that pairs with this manifest costs zero blocks either way.
    """
    base = _root(root) / "wiki"
    if not base.is_dir():
        return []
    return [p for p in base.rglob("*") if p.is_file()]


def authoritative_manifest(root=None) -> str:
    return manifest_sha(authoritative_files(root), _root(root))


def indexable_manifest(root=None) -> str:
    """The narrower set `measurement_snapshot` has always reported."""
    base = _root(root)
    try:
        from scripts.chunk import scan_markdown_files
        files = scan_markdown_files(base)
    except Exception:
        return ""
    return manifest_sha(files, base)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_manifest.py -v`
Expected: 7 passed

- [ ] **Step 5: Point `measurement_snapshot` at the shared implementation**

Replace the body of `_corpus_manifest_sha` in `scripts/measurement_snapshot.py` (currently lines 95-118) with:

```python
def _corpus_manifest_sha(root: Path) -> str:
    """(relpath, size, mtime_ns, ino) over every indexable file. Reads no content.

    Delegates to `knowledge_manifest` so there is one implementation: Z0 needs
    the same digest shape as a correctness mechanism, and two implementations of
    "has the corpus moved" would drift.

    `st_ino` was added in Z0. The digest value therefore changes once at that
    commit; its meaning does not, and no test asserts a literal digest.
    """
    from scripts.knowledge_manifest import indexable_manifest
    return indexable_manifest(root)
```

- [ ] **Step 6: Run the existing snapshot contracts to prove nothing broke**

Run: `PYTHONPATH=. python -m pytest tests/test_measurement_snapshot.py -v`
Expected: 14 passed. **If any of the 14 fails, stop and record it as a finding** — the Global Constraints forbid rewriting an existing contract to accommodate new behaviour.

- [ ] **Step 7: Commit**

```bash
git add scripts/knowledge_manifest.py tests/test_knowledge_manifest.py scripts/measurement_snapshot.py
git commit -m "zurvan: one manifest implementation, two scopes, now inode-sensitive"
```

---

### Task 6: Binding a revision to its manifest, and EXTERNAL_OBSERVED drift

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 5's `authoritative_manifest`; Task 2's `end_mutation`, `current`
- Produces:
  - `end_mutation(...)` now records the authoritative manifest by default
  - `check_drift(root=None) -> str | None` — returns the live digest when it differs from the revision's, else `None`
  - `mint_external_revision(live_digest, root=None) -> int`

Gate tests §10.2.12 (completed unmanaged edit detected) and §10.2.13 (atomicity not laundered).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_committing_a_mutation_records_the_manifest_it_represents(root):
    from scripts import knowledge_manifest as km
    (root / "wiki" / "a.md").write_text("alpha\n")
    with kr.mutation(root=root) as mid:
        kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    assert kr.current(root).manifest_digest == km.authoritative_manifest(root)


def test_no_drift_is_reported_for_a_store_only_zurvan_touched(root):
    with kr.mutation(root=root) as mid:
        (root / "wiki" / "a.md").write_text("alpha\n")
        kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    assert kr.check_drift(root) is None


def test_a_completed_edit_outside_zurvan_is_detected(root):
    """The hole the double manifest cannot see: the edit finished long before
    anyone looked, so before/after stability holds perfectly."""
    with kr.mutation(root=root) as mid:
        (root / "wiki" / "a.md").write_text("alpha\n")
        kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)

    (root / "wiki" / "a.md").write_text("edited in Obsidian\n")   # never told Zurvan

    assert kr.check_drift(root) is not None


def test_drift_mints_a_clean_external_observed_revision(root):
    """Ordinary manual editing is a supported workflow. Marking it SUSPECT
    would make daily use miserable; the honest move is a weaker atomicity
    claim, not a broken store."""
    with kr.mutation(root=root) as mid:
        (root / "wiki" / "a.md").write_text("alpha\n")
        kr.record_write(root / "wiki" / "a.md", mutation_id=mid, root=root)
    before = kr.current(root).revision

    (root / "wiki" / "a.md").write_text("edited in Obsidian\n")
    live = kr.check_drift(root)
    new = kr.mint_external_revision(live, root=root)

    rev = kr.current(root)
    assert new == before + 1
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.EXTERNAL_OBSERVED
    assert rev.manifest_digest == live
    assert kr.check_drift(root) is None


def test_a_managed_mutation_after_drift_restores_managed_atomicity(root):
    (root / "wiki" / "a.md").write_text("alpha\n")
    kr.mint_external_revision(kr.check_drift(root), root=root)
    assert kr.current(root).atomicity == kr.EXTERNAL_OBSERVED

    with kr.mutation(root=root) as mid:
        (root / "wiki" / "b.md").write_text("beta\n")
        kr.record_write(root / "wiki" / "b.md", mutation_id=mid, root=root)

    assert kr.current(root).atomicity == kr.MANAGED


def test_external_observed_claims_stability_not_a_transaction(root):
    """EXTERNAL_OBSERVED proves these bytes formed one stable observed
    filesystem state. It does NOT prove one logical user transaction, and no
    consumer may read it as MANAGED."""
    (root / "wiki" / "a.md").write_text("alpha\n")
    kr.mint_external_revision(kr.check_drift(root), root=root)
    assert kr.current(root).atomicity != kr.MANAGED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k drift -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'check_drift'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/knowledge_revision.py`, change `end_mutation` so the digest defaults to a freshly computed authoritative manifest. Replace its `manifest_digest` handling:

```python
def end_mutation(mutation_id, manifest_digest=None, root=None) -> int:
    """Commit the mutation and advance the revision by exactly one.

    The manifest is computed here, at the commit boundary, so the revision
    carries the filesystem state it actually represents. That binding is what
    makes a later completed external edit detectable at all.
    """
    global _ACTIVE
    if manifest_digest is None:
        from scripts.knowledge_manifest import authoritative_manifest
        manifest_digest = authoritative_manifest(root)
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rev = conn.execute(
            "SELECT current_revision FROM knowledge_state WHERE id = 1"
        ).fetchone()[0]
        new_revision = rev + 1
        conn.execute(
            "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
            " manifest_digest = ? WHERE id = 1",
            (new_revision, MANAGED, manifest_digest))
        conn.execute(
            "UPDATE mutations SET state = ?, committed_at = ?"
            " WHERE mutation_id = ?",
            (COMMITTED, _now(), mutation_id))
        conn.commit()
    finally:
        conn.close()
    if _ACTIVE == mutation_id:
        _ACTIVE = None
        os.environ.pop(MUTATION_ENV, None)
    return new_revision
```

Append:

```python
def check_drift(root=None) -> str | None:
    """The live authoritative digest when it differs from the revision's.

    Two different questions, both required, and rev 1 of the design only had
    the first:

        M_before == M_after                  these bytes were STABLE
        live == revision.manifest_digest     Zurvan KNEW about them

    A finished Obsidian edit passes stability perfectly and fails this one.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    live = authoritative_manifest(root)
    recorded = current(root).manifest_digest
    if not recorded:
        return None          # nothing recorded yet; nothing to contradict
    return None if live == recorded else live


def mint_external_revision(live_digest, root=None) -> int:
    """Accept observed external drift as a new CLEAN revision.

    Deliberately not SUSPECT. Editing the wiki in Obsidian is a supported
    workflow, and a design that punishes it will be turned off. What changes is
    the strength of the claim, not the health of the store: EXTERNAL_OBSERVED
    asserts one stable observed filesystem state and explicitly not one logical
    user transaction.
    """
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rev = conn.execute(
            "SELECT current_revision FROM knowledge_state WHERE id = 1"
        ).fetchone()[0]
        new_revision = rev + 1
        conn.execute(
            "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
            " manifest_digest = ? WHERE id = 1",
            (new_revision, EXTERNAL_OBSERVED, live_digest))
        conn.commit()
    finally:
        conn.close()
    return new_revision
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: revisions carry their manifest, so a finished external edit is visible"
```

---

### Task 7: `safe_write` participates, and implicit mutations are marked

**Files:**
- Modify: `scripts/safe_write.py:45-60` (`_stamp`)
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 2's `active_id`, `record_write`, `begin_mutation`, `end_mutation`
- Produces: `scripts.knowledge_revision.implicit_write(path, root=None) -> None`

Gate test §10.1.2: two unwrapped implicit writes are detected and reported; Z0 may not claim atomicity for them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_a_write_inside_a_mutation_is_recorded_against_it(root):
    from scripts.safe_write import write_file_safely
    with kr.mutation(root=root) as mid:
        write_file_safely(str(root / "wiki" / "a.md"), "alpha\n")
        m = kr.active_mutations(root)[0]
        assert any(p.endswith("a.md") for p in m.completed_paths)


def test_a_write_outside_a_mutation_opens_an_implicit_one(root):
    from scripts.safe_write import write_file_safely
    write_file_safely(str(root / "wiki" / "a.md"), "alpha\n")
    assert kr.current(root).revision == 1
    assert kr.implicit_mutations(root)


def test_two_unwrapped_writes_are_two_implicit_mutations_not_one_operation(root):
    """This is the defect the compatibility door reintroduces. An unwrapped
    caller doing two writes commits after each, so an observer sampling between
    them sees no ACTIVE mutation and a coherent-looking revision, over a
    half-finished operation. Z0 may not claim atomicity for this shape; it may
    only detect and report it."""
    from scripts.safe_write import write_file_safely
    write_file_safely(str(root / "wiki" / "decisions" / "foo.md"), "page\n")
    between = (kr.active_mutations(root), kr.current(root).revision)
    write_file_safely(str(root / "wiki" / "log.md"), "line\n")

    assert between == ([], 1)                       # nothing ACTIVE, looks fine
    assert len(kr.implicit_mutations(root)) == 2    # and it was two operations


def test_the_implicit_inventory_is_a_query_not_an_audit(root):
    """§10.1.3 closes Z0 on this being empty for production callers."""
    from scripts.safe_write import write_file_safely
    write_file_safely(str(root / "wiki" / "a.md"), "alpha\n")
    with kr.mutation(root=root) as mid:
        write_file_safely(str(root / "wiki" / "b.md"), "beta\n")
    assert len(kr.implicit_mutations(root)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k implicit -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'implicit_mutations'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
#: Warn once per process, not once per write. A decision page plus a log append
#: would otherwise print the same warning twice for one user action.
_WARNED = False


def implicit_write(path, root=None) -> None:
    """Record a write that arrived outside any mutation.

    Migration mechanism, not the Z0 contract. An unwrapped caller performing
    several writes gets several single-write revisions, and no reader may treat
    that sequence as one logical operation. The warning and the `implicit` flag
    exist so the inventory of such callers is a query rather than an audit.
    """
    global _WARNED
    if not _WARNED:
        _WARNED = True
        import sys
        print(f"zurvan: write outside a mutation ({path}); "
              "wrap the operation with knowledge_revision.mutation()",
              file=sys.stderr)
    mutation_id = begin_mutation(writer="implicit", planned_paths=[str(path)],
                                 implicit=True, root=root)
    record_write(path, mutation_id=mutation_id, root=root)
    end_mutation(mutation_id, root=root)


def implicit_mutations(root=None) -> list:
    conn = _connect(root)
    try:
        rows = conn.execute(
            f"SELECT {_MUTATION_COLS} FROM mutations WHERE implicit = 1"
            " ORDER BY started_at").fetchall()
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]
```

Then replace `_stamp` in `scripts/safe_write.py` (lines 45-60) with:

```python
def _stamp(target: Path, action: str) -> None:
    """Record WHO wrote this and WHICH logical operation it belongs to.

    The write has ALREADY succeeded by the time this runs, and the whole call is
    swallowed: bookkeeping that loses someone's decision page in order to record
    that a decision page was written is worse than no bookkeeping.
    """
    try:
        from scripts import writelog
        writelog.record(target, action)
    except Exception:
        pass
    try:
        from scripts import knowledge_revision as kr
        if kr.active_id():
            kr.record_write(target)
        else:
            kr.implicit_write(target)
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py tests/test_knowledge_manifest.py -v`
Expected: 30 passed

- [ ] **Step 5: Run the full suite — this is the first task that changes a shared path**

Run: `PYTHONPATH=. python -m pytest -q`
Expected: 0 failed. `safe_write` is called by `memory.py` and `wiki_merge.py`, so any test that writes a page now also touches the ledger. If a test fails because it has no `data/` directory, that is a real finding about `_connect` creating its parent — fix it in `knowledge_revision`, not in the test.

- [ ] **Step 6: Commit**

```bash
git add scripts/safe_write.py scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: safe_write joins the active mutation; implicit writes are flagged, not blessed"
```

---

### Task 8: The CLI dispatcher wraps every command in a mutation

**Files:**
- Modify: `scripts/cli.py:496` (immediately after `args = parser.parse_args(args_list)`)
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 3's `mutation()` context manager
- Produces: no new public API — behaviour only

Wrapping at the dispatcher is what makes this tractable: only `memory.py` (6 sites) and `wiki_merge.py` (3 sites) call `safe_write` at all, and every CLI path reaches them through `main()`. Rewriting nine call sites individually would be more code and would miss any added later.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_decision_add_through_the_cli_is_exactly_one_revision(root, monkeypatch):
    """`decision add` writes the page and appends `wiki/log.md`. Through the
    dispatcher that is one logical operation and must advance the revision
    once, not twice."""
    from scripts.cli import main
    monkeypatch.setenv("ZURVAN_ROOT", str(root))
    kr.ensure_schema(root)
    before = kr.current(root).revision

    main(["decision", "add", "--title", "T", "--reason", "R",
          "--status", "accepted", "--tags", "zurvan"])

    assert kr.current(root).revision == before + 1
    assert kr.implicit_mutations(root) == []


def test_a_read_only_command_does_not_advance_the_revision(root, monkeypatch):
    from scripts.cli import main
    monkeypatch.setenv("ZURVAN_ROOT", str(root))
    kr.ensure_schema(root)
    before = kr.current(root).revision
    main(["version"])
    assert kr.current(root).revision == before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k cli -v`
Expected: FAIL — `decision add` currently produces two implicit mutations, so the revision advances by 2 and `implicit_mutations` is non-empty.

- [ ] **Step 3: Write minimal implementation**

In `scripts/cli.py`, find:

```python
    args = parser.parse_args(args_list)
    
    if args.command == "project":
```

Replace with:

```python
    args = parser.parse_args(args_list)

    # One CLI invocation is one logical operation. Wrapping here rather than at
    # each `safe_write` call site is deliberate: `decision add` writes a page and
    # appends `wiki/log.md`, and a revision minted between them is a
    # half-operation. Every command inherits the context; read-only commands
    # simply never write, and a mutation that wrote nothing commits no revision.
    from scripts import knowledge_revision as _kr
    with _kr.mutation(writer="cli", project=_kr.MUTATION_ENV and None):
        return _dispatch(args, parser)


def _dispatch(args, parser):
    if args.command == "project":
```

Then add, immediately before `end_mutation`'s revision bump in `knowledge_revision.py`, the rule that an empty mutation is not a revision. Replace the opening of `end_mutation`:

```python
def end_mutation(mutation_id, manifest_digest=None, root=None) -> int:
    """Commit the mutation and advance the revision by exactly one.

    A mutation that wrote nothing commits no revision. Read-only CLI commands
    are wrapped by the dispatcher too, and a revision per `zurvan search` would
    make the counter measure traffic rather than knowledge.
    """
    global _ACTIVE
    conn = _connect(root)
    try:
        row = conn.execute(
            "SELECT completed_paths FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        wrote = bool(json.loads((row[0] if row else None) or "[]"))
    finally:
        conn.close()
    if not wrote:
        conn = _connect(root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE mutations SET state = ?, committed_at = ?"
                " WHERE mutation_id = ?", (COMMITTED, _now(), mutation_id))
            conn.commit()
        finally:
            conn.close()
        if _ACTIVE == mutation_id:
            _ACTIVE = None
            os.environ.pop(MUTATION_ENV, None)
        return current(root).revision
    if manifest_digest is None:
        from scripts.knowledge_manifest import authoritative_manifest
        manifest_digest = authoritative_manifest(root)
    # ... unchanged body from Task 6 continues here
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 32 passed

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. python -m pytest -q`
Expected: 0 failed. Every CLI test now runs inside a mutation.

- [ ] **Step 6: Commit**

```bash
git add scripts/cli.py scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: one CLI invocation is one logical operation"
```

---

### Task 9: `zurvan knowledge` — status, doctor, reconcile

**Files:**
- Create: `scripts/knowledge_reconcile.py`
- Create: `tests/test_knowledge_reconcile.py`
- Modify: `scripts/cli.py` (new `knowledge` subparser and dispatch branch)

**Interfaces:**
- Consumes: Task 4's `detect_orphans`, `orphaned_mutations`; Task 6's `check_drift`, `mint_external_revision`; Task 1's `current`
- Produces:
  - `@dataclass OrphanReport(mutation, missing_paths, parse_failures, complete: bool)`
  - `inspect(root=None) -> list[OrphanReport]`
  - `reconcile(root=None, accept=False) -> dict` — `{"status": "CLEAN"|"NEEDS_REVIEW", "reports": [...], "revision": int}`
  - `format_report(reports, revision) -> str`

Gate tests §10.1.5 (reconcile reaches CLEAN without a rebuild) and §10.1.6 (reconcile stops for review).

**Naming correction:** the design says `zurvan snapshot doctor`. `zurvan snapshot` is the release-archive command and `scripts/snapshot.py` is its module — the same collision that forced `measurement_snapshot.py`'s rename. These live under `zurvan knowledge`. Update the design doc's §3.5 in the same commit.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_reconcile.py`:

```python
"""Reconciliation reaches CLEAN without rebuilding anything.

Rev 1 of the design had reconcile rebuild derived stores before marking a
revision CLEAN, while a rebuild refuses a SUSPECT snapshot. A SUSPECT revision
could therefore never produce the snapshot needed to rebuild the stores required
to make it CLEAN. CLEAN means the authoritative state is accepted as coherent;
whether an index is current is a separate question with its own three states.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_reconcile as krec
from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _orphan(root, planned, completed):
    mid = kr.begin_mutation(planned_paths=planned, root=root)
    for p in completed:
        kr.record_write(p, mutation_id=mid, root=root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute(
            "UPDATE mutations SET owner_pid = ?, owner_identity = ?"
            " WHERE mutation_id = ?", (2_000_000, "2000000:gone", mid))
        conn.commit()
    finally:
        conn.close()
    kr._ACTIVE = None
    os.environ.pop(kr.MUTATION_ENV, None)
    kr.detect_orphans(root)
    return mid


def test_an_incomplete_operation_stops_for_review(root):
    """The page parses perfectly and the log line never arrived. Markdown
    syntax is not evidence that a two-file operation finished."""
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: X\n---\n\nbody\n")
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert kr.current(root).state == kr.SUSPECT
    report = result["reports"][0]
    assert report.missing_paths == [str(log)]
    assert report.complete is False


def test_reconcile_never_deletes_anything(root):
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: X\n---\n\nbody\n")
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    krec.reconcile(root=root)

    assert page.exists()


def test_an_accepted_reconciliation_reaches_clean_without_a_rebuild(root):
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: X\n---\n\nbody\n")
    log = root / "wiki" / "log.md"
    log.write_text("- repaired by hand\n")
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root, accept=True)

    assert result["status"] == "CLEAN"
    assert kr.current(root).state == kr.CLEAN


def test_a_complete_operation_reconciles_without_asking(root):
    """Every planned path landed; the owner simply died before committing."""
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: X\n---\n\nbody\n")
    _orphan(root, planned=[str(page)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "CLEAN"
    assert kr.current(root).state == kr.CLEAN


def test_a_completed_path_that_no_longer_parses_stops_for_review(root):
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: [unclosed\n")          # torn frontmatter
    _orphan(root, planned=[str(page)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["reports"][0].parse_failures == [str(page)]


def test_the_report_names_the_path_that_never_completed(root):
    page = root / "wiki" / "decisions" / "x.md"
    page.write_text("---\ntitle: X\n---\n\nbody\n")
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    text = krec.format_report(krec.inspect(root), kr.current(root).revision)

    assert "SUSPECT" in text
    assert "log.md" in text
    assert "UNPROVEN" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_reconcile'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/knowledge_reconcile.py`:

```python
#!/usr/bin/env python3
"""Inspect orphaned mutations and decide whether the store is coherent again.

Not `rollback`. A dead process proves the mutation will not finish and proves
nothing about whether the bytes it already wrote are complete, so automatic
rollback would be fiction and automatic timeout-commit a lie of the same size.

Reconciliation reaches CLEAN WITHOUT rebuilding anything. CLEAN means the
authoritative knowledge state is accepted as coherent; whether a derived index
is current is a separate question answered by CURRENT / STALE / INVALID. Rev 1
of the design conflated them and deadlocked: reaching CLEAN required a rebuild,
and a rebuild refuses SUSPECT input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scripts import knowledge_revision as kr


@dataclass
class OrphanReport:
    mutation: kr.Mutation
    missing_paths: list = field(default_factory=list)
    parse_failures: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing_paths and not self.parse_failures


def _parses(path: Path) -> bool:
    """Frontmatter parses and the file is readable. Syntax only — deliberately
    not a claim that the operation finished."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return True
    parts = text.split("---", 2)
    if len(parts) < 3:
        return False
    try:
        yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return False
    return True


def inspect(root=None) -> list:
    kr.detect_orphans(root)
    reports = []
    for m in kr.orphaned_mutations(root):
        missing = [p for p in m.planned_paths if p not in m.completed_paths]
        failures = [p for p in m.completed_paths
                    if not _parses(Path(p))]
        reports.append(OrphanReport(mutation=m, missing_paths=missing,
                                    parse_failures=failures))
    return reports


def reconcile(root=None, accept=False) -> dict:
    """Return the store to CLEAN when the evidence supports it.

    `accept=True` records an explicit human decision for an operation whose
    completeness cannot be established mechanically. It is the only path that
    clears an incomplete orphan, and it is deliberately not the default.
    """
    reports = inspect(root)
    unresolved = [r for r in reports if not r.complete]
    if unresolved and not accept:
        return {"status": "NEEDS_REVIEW", "reports": reports,
                "revision": kr.current(root).revision}

    conn = kr._connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE knowledge_state SET state = ? WHERE id = 1",
                     (kr.CLEAN,))
        for r in reports:
            conn.execute(
                "UPDATE mutations SET orphan_reason = ? WHERE mutation_id = ?",
                (("reconciled:accepted" if not r.complete else "reconciled"),
                 r.mutation.mutation_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "CLEAN", "reports": reports,
            "revision": kr.current(root).revision}


def format_report(reports, revision) -> str:
    rev = revision
    lines = []
    state = "SUSPECT" if any(not r.complete for r in reports) else "CLEAN"
    lines.append(f"{state} revision {rev}")
    for r in reports:
        m = r.mutation
        lines.append("Orphaned mutation:")
        lines.append(f"  project: {m.project or '(unknown)'}")
        lines.append(f"  owner:   pid {m.owner_pid} / {m.owner_identity}")
        lines.append(f"  planned:   {', '.join(m.planned_paths) or '(none)'}")
        lines.append(f"  completed: {', '.join(m.completed_paths) or '(none)'}")
        if r.missing_paths:
            lines.append(f"  → {len(r.missing_paths)} planned path(s) never "
                         "completed; semantic completeness UNPROVEN")
        if r.parse_failures:
            lines.append(f"  → {len(r.parse_failures)} written file(s) do not "
                         "parse; semantic completeness UNPROVEN")
    if any(not r.complete for r in reports):
        lines.append("Snapshots blocked until reconciliation. "
                     "Live writes remain available.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_reconcile.py -v`
Expected: 6 passed

- [ ] **Step 5: Add the CLI surface**

In `scripts/cli.py`, after the `snapshot` subparser block (around line 494), add:

```python
    # zurvan knowledge — the revision ledger. NOT under `zurvan snapshot`:
    # that command builds release archives and `scripts/snapshot.py` is its
    # module. Colliding on that name once already cost a session.
    knowledge_parser = subparsers.add_parser(
        "knowledge", help="Knowledge revisions and reconciliation")
    knowledge_sub = knowledge_parser.add_subparsers(dest="action")
    knowledge_sub.add_parser("status", help="Current revision, state, atomicity")
    knowledge_sub.add_parser("doctor", help="Report orphaned mutations")
    k_rec = knowledge_sub.add_parser("reconcile", help="Return the store to CLEAN")
    k_rec.add_argument("--accept", action="store_true",
                       help="record an explicit decision for an operation whose "
                            "completeness cannot be established mechanically")
```

And in `_dispatch`, add a branch alongside the other `elif args.command ==` clauses:

```python
    elif args.command == "knowledge":
        from scripts import knowledge_reconcile as krec
        from scripts import knowledge_revision as kr
        if args.action == "status":
            rev = kr.current()
            drift = kr.check_drift()
            print(f"revision:   {rev.revision}")
            print(f"state:      {rev.state}")
            print(f"atomicity:  {rev.atomicity}")
            print(f"drift:      {'DETECTED' if drift else 'none'}")
        elif args.action == "doctor":
            reports = krec.inspect()
            if not reports:
                print(f"CLEAN revision {kr.current().revision}; no orphans.")
            else:
                print(krec.format_report(reports, kr.current().revision))
        elif args.action == "reconcile":
            result = krec.reconcile(accept=args.accept)
            print(krec.format_report(result["reports"], result["revision"]))
            print(f"\nstatus: {result['status']}")
            if result["status"] == "NEEDS_REVIEW":
                raise SystemExit(1)
```

- [ ] **Step 6: Correct the design doc's command name**

In `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md`, replace both occurrences of `zurvan snapshot doctor` with `zurvan knowledge doctor`, and in §8's CLI line replace `zurvan snapshot doctor` likewise. Leave `zurvan snapshot list` / `zurvan snapshot gc` for the Plan 2 author to resolve — they collide too, and that decision belongs with the code that implements them.

- [ ] **Step 7: Run the full gate**

```bash
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

Expected: 0 failed; guard passed; no whitespace errors. Do **not** run `check.sh` if an index rebuild is in flight.

- [ ] **Step 8: Commit**

```bash
git add scripts/knowledge_reconcile.py tests/test_knowledge_reconcile.py scripts/cli.py docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md
git commit -m "zurvan: knowledge status/doctor/reconcile; CLEAN without a rebuild"
```

---

### Task 10: Change protocol and the honest status entry

**Files:**
- Modify: `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`

- [ ] **Step 1: Write the entry**

Append a dated `Raouf:` entry to **both** `AGENTS.md` and `CHANGELOG.md` covering: scope (files), what the barrier is and why a write counter was wrong, the ABORTED/ORPHANED distinction and why an argument error is not a torn store, the two manifest questions (stability vs awareness) and why both are needed, EXTERNAL_OBSERVED's weaker claim, the implicit-mutation inventory and that **Z0 step 1 does not close Z0**, and verification output actually observed.

Move the older `AGENTS.md` entry **verbatim** to `docs/agents-history.md` so only two remain.

- [ ] **Step 2: State what is still not built**

The entry must say plainly that this plan implements §9.2 steps 1–2 only, and that the Z0 gate (§10) remains **unmet**: no immutable-bytes abstraction, no wiki or graph capture, no atomic graph rebuild, no `built_from_revision`, no two-purpose capture, no leases. `measurement_snapshot.py` still pins only the search index.

- [ ] **Step 3: Verify and commit**

```bash
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
git add AGENTS.md CHANGELOG.md docs/agents-history.md
git commit -m "zurvan: Z0 step 1 — knowledge revisions and the mutation barrier"
```

---

## Self-Review

**Spec coverage.** §3.1 → Task 3's half-operation test. §3.2 barrier → Tasks 2, 8; implicit limit → Task 7. §3.3 CLEAN/SUSPECT → Tasks 2, 4. §3.4 CLEAN's meaning → Task 9. §3.5 reconcile order → Task 9. §3.6 schema → Tasks 1, 2, with `planned_paths`/`completed_paths` in Task 2. §5.1 manifest → Task 5. §5.2 revision↔manifest binding and EXTERNAL_OBSERVED → Task 6.

Gate items covered: §10.1.1 (Task 3), .2 and .3 (Task 7), .4 (Task 4), .5 and .6 (Task 9), .7 (Task 4). §10.2.12 and .13 (Task 6). §10.4 regression floor (Tasks 7, 9, 10).

Gate items **deliberately not covered**, deferred to Plans 2 and 3: §10.2.8–11, §10.3.14–19. Task 10 requires this to be stated in the change entry rather than left implicit.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step carries the code.

**Type consistency.** `Revision(revision, state, atomicity, manifest_digest)` and `Mutation(...)` as defined in Tasks 1–2 are used unchanged in 4, 6, 7, 9. `current()`, `check_drift()`, `detect_orphans()`, `orphaned_mutations()`, `implicit_mutations()`, `active_mutations()` all take `root=None` and are called that way throughout. `reconcile()` returns `{"status", "reports", "revision"}` and `format_report(reports, revision)` consumes that shape in both the test and the CLI branch.

**One known rough edge, flagged rather than hidden.** Task 8 passes `project=_kr.MUTATION_ENV and None`, which always evaluates to `None`. It is a placeholder for the real project attribution the dispatcher should supply from `project_registry.get_current_project()`. The Task 8 implementer should replace it with that call and add a test asserting the project lands on the mutation row — orphan reports name a project, and `(unknown)` in the doctor output on a shared cross-project store is a worse diagnostic than it looks.
