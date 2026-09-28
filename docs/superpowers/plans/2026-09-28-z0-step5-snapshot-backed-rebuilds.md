# Z0 Step 5 — Rebuilds read a snapshot, stamp the revision they read, and report their health

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A search or graph rebuild reads a frozen snapshot of its inputs taken at knowledge revision R, stamps `built_from_revision = R` inside the store it publishes, refuses a SUSPECT store, and `zurvan knowledge status` reports each derived store as CURRENT, STALE or INVALID.

**Architecture:** A new module, `scripts/build_snapshot.py`, captures the build inputs (`wiki/`, `docs/`, the root Markdown files) through `measurement_snapshot.capture` and proves the captured bytes ARE revision R with the design's double manifest: the live manifest before and after the clone must both equal the ledger's digest at R, with the revision unchanged in between. Drift is minted first (`trigger="capture"`), which is Z0 §10.2 item 12 as written. Both rebuilders take `snapshot=`; their `__main__` entry points use it. `scripts/derived_health.py` compares the stamps with the ledger.

**Tech Stack:** Python 3.12 stdlib, `sqlite3`. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) §5.1, §6.1–6.3, §9.2 step 5, §10.1 item 5, §10.2 items 9, 11, 12, 17, 18.

## Global Constraints

- **No implicit live reads once the build starts.** (Design §6.1.)
- **A rebuild refuses a SUSPECT snapshot.** (Design §6.1, gate item 18.)
- **Promotion must not require `built_from_revision == current_revision`.** Promote as COHERENT+STALE. (Design §6.3.)
- **Stale is a normal state, not corruption.** (Design §6.2.)
- **Stamps live inside the database. A filename is not evidence.** (Design §4.4.)
- **No retrieval, ranking, chunking, embedding or index-format change of any kind. Z0 must be provably ranking-neutral.** (Design §11.) Adding a metadata table the retriever never reads is recorded below as a decision, not assumed to be neutral: Task 6 proves it.
- **Production knowledge state is never a test fixture.** (AGENTS.md.) Every registry path now follows `ZURVAN_ROOT` (2026-09-28c); a subprocess test is still checked against the live `sources` table.
- **Never run `check.sh` while an index rebuild is in flight.** (CLAUDE.md.)
- **A failed gate is reported as failed.** (CLAUDE.md.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.**

## Amendments to the design, made here and argued

1. **The snapshot captures build inputs, not only `wiki/`.** §6.1 writes `capture(required={"wiki"})`. Measured 2026-09-28: the search index reads 86 files under `docs/` and 3 root files (1,985 chunks, ~1%); the graph reads 90 `docs/` nodes and 3 root files. A wiki-only snapshot would silently drop them — a ranking change. The snapshot therefore captures `wiki/`, `docs/`, and the union of the two root-file lists (`README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md`). `built_from_revision` describes the wiki (the ledger's scope); `docs/` and root files are git-tracked and outside the ledger — stated as a bound, not hidden.
2. **Coherence is proven on the live side.** The manifest carries `st_ino`, and a clone has new inodes, so a snapshot's manifest never equals the live one. The proof is §5.1's double manifest: live manifest before == live manifest after == ledger digest at R, revision unchanged, no mutation active.
3. **Build snapshots are released after the build.** Retention and leases are step 6. The stamp records the snapshot id and the ledger digest it proved; the bytes are not kept. `built_from_snapshot` therefore names a proof, not a replayable directory — stated.
4. **A store with no ledger baseline builds from live and is stamped nothing** (health INVALID, reason "no truthful source snapshot"), rather than refusing. A fresh clone of the repository has no ledger; refusing would make `zurvan index search` fail there.

## Measured inputs (2026-09-28, before execution)

```text
build inputs, live             wiki/ + docs/ + 4 root files; scan_markdown_files on a clone
                               of them == the live list (4,683 == 4,683)
capture cost, live             manifest 169 ms (cold) + clone 144 ms + manifest 58 ms,
                               stable across the clone
index_identity                 NOT called by build capture (it is the 6.7 s cost, and a
                               build does not need to describe an index)
```

## Review Focus

1. **A write landing during the clone.** Expected: the snapshot is discarded and capture retries; after `attempts` it raises `BuildSnapshotNotReady`, and nothing is built. (Task 2, `test_a_write_during_capture_is_retried_then_refused`.)
2. **A crashed writer's ACTIVE row at capture time.** Expected: orphan detection runs; a dead owner does not block capture forever. (Task 2, `test_a_dead_writer_does_not_block_capture`.)
3. **The first rebuild on a fresh clone with no ledger.** Expected: builds from live, stamps nothing, health INVALID with that reason. (Task 5, `test_a_store_with_no_ledger_is_invalid_not_refused`.)
4. **A rebuild while the corpus advances.** Expected: stamps the revision it read; health STALE. (Task 3, `test_gate_17_a_rebuild_stamps_the_revision_it_read`.)
5. **An old store built before this step.** Expected: INVALID ("no stamp"), never CURRENT. (Task 5, `test_an_unstamped_store_is_invalid`.)

---

## Task 0 — Preflight baseline

Nothing is edited. Runs alone.

- [ ] **Step 1: Freeze a corpus with its own ledger, and build both stores with the CURRENT code**

```bash
M=~/.zurvan/measurements/z0-step5; rm -rf $M; mkdir -p $M/frozen/data
PYTHONPATH=. python - <<'EOF'
import os
from pathlib import Path
from scripts.immutable_bytes import clone_tree, clone_file
F = Path(os.path.expanduser("~/.zurvan/measurements/z0-step5/frozen"))
for d in ("wiki", "docs"):
    clone_tree(Path(d), F / d)
for f in ("README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md"):
    clone_file(Path(f), F / f)
EOF
ZURVAN_ROOT=$M/frozen PYTHONPATH=. python -c "from scripts import knowledge_revision as kr; kr.ensure_schema(); print(kr.bootstrap(actor='z0-step5-baseline'))"
cp ~/.zurvan/measurements/z0-step4/digest.py $M/digest.py
PYTHONPATH=. python - <<'EOF'
import os
M = os.path.expanduser("~/.zurvan/measurements/z0-step5")
import scripts.graph_build as gb
gb.PROJECT_ROOT = M + "/frozen"
gb.build_graph(M + "/before-graph.sqlite")
EOF
ZURVAN_EMBED_PROVIDER=mock ZURVAN_EMBED_MODEL=mock_model PYTHONPATH=. python -c "
import os; from scripts.rebuild_search_index import rebuild_search_index
M=os.path.expanduser('~/.zurvan/measurements/z0-step5')
rebuild_search_index(M+'/before-search.sqlite', root=M+'/frozen')"
PYTHONPATH=. python $M/digest.py graph  $M/before-graph.sqlite  | tee $M/before-graph.digest
PYTHONPATH=. python $M/digest.py search $M/before-search.sqlite | tee $M/before-search.digest
```

- [ ] **Step 2: Regression floor**

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider 2>&1 | grep -E '[0-9]+ passed' | tee $M/pytest-floor.txt
```

Expected: `1243 passed` (1238 + 5 plan-integrity cases for this plan). If different, explain before editing.

---

## Task 1 — `docs` and `roots` capture components

**Files:** Modify `scripts/measurement_snapshot.py`; Test `tests/test_snapshot_components.py`

**Interfaces:**
- Produces: `capture_docs(root, into) -> Component`, `capture_roots(root, into) -> Component`, `BUILD_ROOT_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md")`, `BUILD_INPUTS = ("wiki", "docs", "roots")`, both registered in `_COMPONENTS`.

- [ ] **Step 1: Failing tests** (append to `tests/test_snapshot_components.py`)

```python
# ── build inputs: docs and root files (Z0 step 5) ───────────────────────────

def test_build_inputs_capture_exactly_what_the_indexer_reads(store, into):
    """Amendment 1: the index reads docs/ and root files too. A snapshot of
    the build inputs must give scan_markdown_files the same list as live."""
    from scripts.chunk import scan_markdown_files
    (store / "docs").mkdir()
    (store / "docs" / "guide.md").write_text("# Guide\n")
    (store / "docs" / "evaluation").mkdir()
    (store / "docs" / "evaluation" / "x.md").write_text("# excluded by the indexer\n")
    (store / "README.md").write_text("# Readme\n")
    (store / "CHANGELOG.md").write_text("# Changes\n")
    (store / "wiki" / "p.md").write_text("# Page\n")
    snap = ms.capture(store, label="t", components=ms.BUILD_INPUTS)
    try:
        assert sorted(scan_markdown_files(snap.path)) == sorted(scan_markdown_files(store))
        assert (snap.path / "CHANGELOG.md").is_file()      # the graph reads it
    finally:
        snap.release()


def test_a_missing_docs_tree_is_captured_as_absent_not_refused(store, into):
    snap = ms.capture(store, label="t", components=("docs", "roots"))
    try:
        assert not (snap.path / "docs").exists()
        assert "docs" in snap.identity["components"]
    finally:
        snap.release()
```

- [ ] **Step 2: Run** — `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_snapshot_components.py -k "build_inputs or missing_docs"`. Expected: `AttributeError: module 'scripts.measurement_snapshot' has no attribute 'BUILD_INPUTS'`.

- [ ] **Step 3: Implement** (after `capture_graph`)

```python
BUILD_ROOT_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md")


def capture_docs(root, into) -> Component:
    """`docs/` is read by both rebuilders (amendment 1). Git-tracked, outside
    the ledger: captured for input identity, not for revision coherence. An
    absent tree is captured as absent - a fresh store may have none."""
    from scripts.immutable_bytes import ImmutableBytesError, clone_tree
    source = Path(root) / "docs"
    destination = Path(into) / "docs"
    if not source.is_dir():
        return Component(name="docs", path=destination, backend="absent",
                         logical_bytes=0, captured_scope=FULL_TREE,
                         authoritative_scope=WHOLE_COMPONENT)
    try:
        mechanism = clone_tree(source, destination)
    except ImmutableBytesError as exc:
        raise SnapshotComponentError(f"docs capture failed: {exc}") from exc
    total = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    return Component(name="docs", path=destination, backend=mechanism,
                     logical_bytes=total, captured_scope=FULL_TREE,
                     authoritative_scope=WHOLE_COMPONENT)


def capture_roots(root, into) -> Component:
    """The root Markdown files the rebuilders read: the union of search's
    ROOT_INDEX_FILES and the graph's target_files."""
    from scripts.immutable_bytes import ImmutableBytesError, clone_file
    total, mechanism = 0, "absent"
    for name in BUILD_ROOT_FILES:
        source = Path(root) / name
        if source.is_file():
            try:
                mechanism = clone_file(source, Path(into) / name)
            except ImmutableBytesError as exc:
                raise SnapshotComponentError(f"{name} capture failed: {exc}") from exc
            total += source.stat().st_size
    return Component(name="roots", path=Path(into), backend=mechanism,
                     logical_bytes=total, captured_scope=SINGLE_FILE,
                     authoritative_scope=WHOLE_COMPONENT)


BUILD_INPUTS = ("wiki", "docs", "roots")
```

and in `_COMPONENTS`: `"docs": capture_docs, "roots": capture_roots,`.

- [ ] **Step 4: Run** — both pass; the whole file stays green.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: snapshots can capture every build input"`

---

## Task 2 — `capture_build_inputs`: a snapshot proven to be revision R

**Files:** Create `scripts/build_snapshot.py`; Test `tests/test_build_snapshot.py`

**Interfaces:**
- Consumes: `capture(root=None, label="measurement", deep=False, components=None) -> Snapshot`, `BUILD_INPUTS`, `observe_external_drift(root=None, trigger="manual")`, `DriftUnobservable`, `detect_orphans(root=None) -> list`
- Produces: `class BuildSnapshotRefused(RuntimeError)`, `class BuildSnapshotNotReady(RuntimeError)`, `class NoBaseline(BuildSnapshotRefused)`, `class BuildSnapshot` (fields `snapshot`, `revision`, `atomicity`, `manifest_digest`; property `path`; `stamp() -> dict`; `release() -> None`; context manager), `capture_build_inputs(root=None, label="rebuild", attempts=3) -> BuildSnapshot`, `inputs_for_rebuild(root=None)` (context manager yielding a `BuildSnapshot` or `None` when the store has no baseline).

- [ ] **Step 1: Failing tests**

```python
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
```

- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: No module named 'scripts.build_snapshot'` at collection.

- [ ] **Step 3: Implement** `scripts/build_snapshot.py`

```python
"""Capture a rebuild's inputs as a snapshot PROVEN to be knowledge revision R.

Design §6.1: a rebuild takes minutes; one that reads the live corpus while it
advances indexed no single revision. The proof is §5.1's double manifest -
live manifest before == live manifest after == the ledger's digest at R,
revision unchanged, no mutation active. A clone has new inodes, so the
snapshot's own manifest can never equal the live one (amendment 2).

The revision describes `wiki/` (the ledger's scope). `docs/` and the root
files are captured so the build reads no live byte, but they are git-tracked
and outside the ledger - a stated bound (amendment 1).
"""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks


class BuildSnapshotRefused(RuntimeError):
    """The store cannot give a truthful snapshot: SUSPECT (design §6.1)."""


class NoBaseline(BuildSnapshotRefused):
    """No ledger, or no recorded manifest: nothing for a revision to mean."""


class BuildSnapshotNotReady(RuntimeError):
    """The store kept changing; no snapshot was kept (gate item 11)."""


def _live_manifest(base) -> str:
    from scripts.knowledge_manifest import authoritative_manifest
    return authoritative_manifest(base)


def _refuse_if_suspect(state) -> None:
    if state.state != kr.CLEAN:
        raise BuildSnapshotRefused(
            "store is SUSPECT; reconcile before rebuilding - a derived store "
            "built from torn knowledge looks exactly like a correct one")


def _unchanged(base, m_before, before) -> bool:
    """The second half of §5.1's double manifest: nothing moved during the
    clone. A separate function so falsification can remove exactly this."""
    after = kr.current(base)
    return (_live_manifest(base) == m_before and after.revision == before.revision
            and after.state == kr.CLEAN and not kr.mutations_in(kr.ACTIVE, base))


@dataclass
class BuildSnapshot:
    snapshot: object
    revision: int
    atomicity: str
    manifest_digest: str

    @property
    def path(self):
        return self.snapshot.path

    def stamp(self) -> dict:
        """What a derived store records about its source (design §4.4)."""
        return {
            "built_from_revision": str(self.revision),
            "built_from_snapshot": self.snapshot.snapshot_id,
            "built_from_atomicity": self.atomicity,
            "built_from_manifest": self.manifest_digest,
        }

    def release(self) -> None:
        self.snapshot.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False


def capture_build_inputs(root=None, label="rebuild", attempts=3) -> BuildSnapshot:
    from scripts import measurement_snapshot as ms
    base = ks.store_root(root)
    for attempt in range(attempts):
        try:
            state = kr.current(base)
        except kr.LedgerError as exc:
            raise NoBaseline(f"no ledger: {exc}") from exc
        if not state.manifest_digest:
            raise NoBaseline("no recorded manifest; run `zurvan knowledge bootstrap`")
        _refuse_if_suspect(state)
        kr.detect_orphans(base)
        if kr.mutations_in(kr.ACTIVE, base):
            time.sleep(0.1 * (attempt + 1))
            continue
        try:
            kr.observe_external_drift(base, trigger="capture")
        except kr.DriftUnobservable:
            time.sleep(0.1 * (attempt + 1))
            continue
        before = kr.current(base)
        if before.state != kr.CLEAN:
            continue
        m_before = _live_manifest(base)
        if m_before != before.manifest_digest:
            continue
        snap = ms.capture(base, label=label, components=ms.BUILD_INPUTS)
        if _unchanged(base, m_before, before):
            bs = BuildSnapshot(snap, before.revision, before.atomicity,
                               before.manifest_digest)
            (snap.path / "build.json").write_text(json.dumps(bs.stamp(), indent=2))
            return bs
        snap.release()
    raise BuildSnapshotNotReady(
        f"the store kept changing across {attempts} attempts; nothing was built")


@contextmanager
def inputs_for_rebuild(root=None):
    """A proven snapshot, or None on a store with no baseline (amendment 4).
    SUSPECT and NotReady propagate: a rebuild must not proceed from them."""
    try:
        bs = capture_build_inputs(root)
    except NoBaseline:
        yield None
        return
    try:
        yield bs
    finally:
        bs.release()
```

- [ ] **Step 4: Run** — `tests/test_build_snapshot.py` all pass; `tests/test_snapshot_components.py tests/test_external_drift.py` stay green.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: a build snapshot is proven to be revision R, or not taken"`

---

## Task 3 — The search index is rebuilt from a snapshot and stamped

**Files:** Modify `scripts/rebuild_search_index.py` (`rebuild_search_index`, `__main__`); Test `tests/test_build_snapshot.py`

**Interfaces:**
- Consumes: `capture_build_inputs(root=None, label="rebuild", attempts=3) -> BuildSnapshot`, `inputs_for_rebuild(root=None)`, `BuildSnapshot.stamp() -> dict`
- Produces: `rebuild_search_index(db_path=None, root=None, snapshot=None)`; `INDEX_META = "index_meta"`; `read_index_meta(db_path) -> dict`

- [ ] **Step 1: Failing tests** (append)

```python
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
```

- [ ] **Step 2: Run** — Expected: `ImportError: cannot import name 'read_index_meta'`.

- [ ] **Step 3: Implement.** In `scripts/rebuild_search_index.py`:

```python
INDEX_META = "index_meta"


def read_index_meta(db_path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            return dict(conn.execute(f"SELECT key, value FROM {INDEX_META}"))
        except sqlite3.OperationalError:
            return {}                      # built before Z0 step 5: unstamped
    finally:
        conn.close()


def _stamp_index(build_path, fields: dict) -> None:
    """Write the source stamps into the BUILD file, before publication. The
    retriever never reads this table (proved in Task 6)."""
    from datetime import datetime, timezone
    conn = sqlite3.connect(build_path)
    try:
        conn.execute(f"CREATE TABLE {INDEX_META} (key TEXT PRIMARY KEY,"
                     " value TEXT NOT NULL)")
        rows = dict(fields)
        rows["built_at"] = datetime.now(timezone.utc).isoformat()
        conn.executemany(f"INSERT INTO {INDEX_META} (key, value) VALUES (?, ?)",
                         sorted(rows.items()))
        conn.commit()
    finally:
        conn.close()
```

`rebuild_search_index` gains `snapshot=None`: at the top, `if snapshot is not None and root is not None: raise ValueError("pass snapshot OR root, not both")`, then `if snapshot is not None: root = snapshot.path`. After `check_index_health` passes and before `_publication.publish(...)`:

```python
        _stamp_index(build_path, snapshot.stamp() if snapshot is not None else {})
```

`__main__` becomes:

```python
if __name__ == "__main__":
    _install_sigterm_cleanup()
    from scripts.build_snapshot import inputs_for_rebuild
    with inputs_for_rebuild() as bs:
        if bs is None:
            print("  no ledger baseline: building from the live tree, unstamped")
        rebuild_search_index(snapshot=bs)
```

- [ ] **Step 4: Run** — the two new tests, then `tests/test_index_integrity.py tests/test_hybrid_search.py tests/test_measurement_snapshot.py` stay green.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: the search index is rebuilt from a proven snapshot and stamped"`

---

## Task 4 — The graph is rebuilt from a snapshot and stamped

**Files:** Modify `scripts/graph_build.py` (`get_file_content`, `_collect_graph`, `_write_graph`, `build_graph`, `__main__`); Modify `tests/test_graph_publication.py` (three fakes); Test `tests/test_build_snapshot.py`

**Interfaces:**
- Produces: `get_file_content(path: str, root=None) -> str`, `_collect_graph(root=None)`, `_write_graph(build_path, nodes_dict, edges_list, stamp=None) -> None`, `build_graph(db_path: str = DB_PATH, snapshot=None)`

- [ ] **Step 1: Failing test** (append)

```python
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
```

- [ ] **Step 2: Run** — Expected: `TypeError: build_graph() got an unexpected keyword argument 'snapshot'`.

- [ ] **Step 3: Implement.** `get_file_content(path: str, root=None) -> str` reads from `root or PROJECT_ROOT`; `_collect_graph(root=None)` uses `base = str(root) if root is not None else PROJECT_ROOT` everywhere it used `PROJECT_ROOT` and passes `root=base` to `get_file_content`; `_write_graph` gains `stamp=None` and, when given, adds its items to the `graph_meta` rows; `build_graph(db_path: str = DB_PATH, snapshot=None)` calls `_collect_graph(snapshot.path if snapshot is not None else None)` and `_write_graph(..., stamp=snapshot.stamp() if snapshot is not None else None)`. `__main__`:

```python
if __name__ == "__main__":
    from scripts.build_snapshot import inputs_for_rebuild
    with inputs_for_rebuild() as bs:
        build_graph(snapshot=bs)
```

`tests/test_graph_publication.py::test_the_graph_is_stamped_inside_the_database` asserts the exact key set; an unstamped build keeps exactly that set, a stamped build adds the four `built_from_*` keys.

Three step-4 test fakes must follow the new signatures (found by the plan's gauntlet — without this they fail with `TypeError`, not the behaviour they test):

```python
    # test_a_failed_build_leaves_the_published_graph_untouched
    def disk_full(build_path, nodes_dict, edges_list, stamp=None):
    # test_a_sigkilled_build... and test_a_build_still_collecting... (child source)
        "def slow_collect(root=None):\n"
```

- [ ] **Step 4: Run** — new test plus `tests/test_graph_publication.py tests/test_graph_build.py`.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: the graph is rebuilt from a proven snapshot and stamped"`

---

## Task 5 — CURRENT, STALE, INVALID

**Files:** Create `scripts/derived_health.py`; Modify `scripts/cli.py` (`knowledge status`); Test `tests/test_derived_health.py`

**Interfaces:**
- Produces: `CURRENT = "COHERENT+CURRENT"`, `STALE = "COHERENT+STALE"`, `INVALID = "INVALID"`, `store_health(kind, root=None) -> tuple` returning `(state, built_from, current_revision, reason)` for `kind` in `("search", "graph")`.

- [ ] **Step 1: Failing tests**

```python
"""Design §6.2: stale is a normal state, not corruption. Conflating them is
what makes operators reach for a rebuild they do not need."""

import pytest

from scripts import derived_health as dh
from scripts import knowledge_revision as kr


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _build_graph(store):
    import scripts.graph_build as gb
    from scripts.build_snapshot import capture_build_inputs
    with capture_build_inputs(store) as bs:
        gb.build_graph(str(store / "data" / "graph.sqlite"), snapshot=bs)


def _write(store, name):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=store):
        write_file_safely(str(store / "wiki" / name), "x\n")


def test_a_fresh_build_is_current(store):
    _build_graph(store)
    assert dh.store_health("graph", store)[0] == dh.CURRENT


def test_a_write_after_the_build_makes_it_stale_not_invalid(store):
    _build_graph(store)
    _write(store, "b.md")
    state, built, now, _ = dh.store_health("graph", store)
    assert state == dh.STALE and int(built) == now - 1


def test_an_unstamped_store_is_invalid(store):
    import scripts.graph_build as gb
    gb.build_graph(str(store / "data" / "graph.sqlite"))       # live, unstamped
    state, _, _, reason = dh.store_health("graph", store)
    assert state == dh.INVALID and "no truthful source snapshot" in reason


def test_a_missing_store_is_invalid(store):
    state, _, _, reason = dh.store_health("search", store)
    assert state == dh.INVALID and "missing" in reason


def test_a_stamp_from_the_future_is_invalid(store):
    """Metadata mismatch (design §6.2): a store claiming a revision the ledger
    has not reached."""
    _build_graph(store)
    import sqlite3, os
    db = store / "data" / "graph.sqlite"
    os.chmod(db, 0o644)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE graph_meta SET value = '999' WHERE key = 'built_from_revision'")
    conn.commit()
    conn.close()
    assert dh.store_health("graph", store)[0] == dh.INVALID


def test_a_store_with_no_ledger_is_invalid_not_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    import scripts.graph_build as gb
    from scripts.build_snapshot import inputs_for_rebuild
    with inputs_for_rebuild(tmp_path) as bs:
        assert bs is None
        gb.build_graph(str(tmp_path / "data" / "graph.sqlite"), snapshot=bs)
    assert dh.store_health("graph", tmp_path)[0] == dh.INVALID


def test_gate_5_reconcile_then_rebuild_is_current(store):
    """Gate item 5, second half: reconcile reaches CLEAN without a rebuild,
    and a rebuild afterwards makes the derived store CURRENT."""
    _build_graph(store)
    _write(store, "b.md")
    assert dh.store_health("graph", store)[0] == dh.STALE
    _build_graph(store)
    assert dh.store_health("graph", store)[0] == dh.CURRENT
```

- [ ] **Step 2: Run** — Expected: `ModuleNotFoundError: No module named 'scripts.derived_health'`.

- [ ] **Step 3: Implement** `scripts/derived_health.py`

```python
"""CURRENT, STALE or INVALID for each derived store (design §6.2).

    COHERENT+CURRENT    built_from_revision == knowledge revision
    COHERENT+STALE      built_from_revision <  knowledge revision
    INVALID             no truthful source snapshot, or metadata mismatch

Stale is normal. Only INVALID means "do not trust this store's provenance".
"""

from pathlib import Path

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks

CURRENT = "COHERENT+CURRENT"
STALE = "COHERENT+STALE"
INVALID = "INVALID"


def _meta(kind, base) -> tuple:
    if kind == "graph":
        from scripts.graph_schema import read_graph_meta
        path = Path(base) / "data" / "graph.sqlite"
        return path, (read_graph_meta(path) if path.is_file() else None)
    if kind == "search":
        from scripts.rebuild_search_index import read_index_meta
        path = Path(base) / "data" / "search.sqlite"
        return path, (read_index_meta(path) if path.is_file() else None)
    raise ValueError(f"unknown derived store {kind!r}")


def store_health(kind, root=None) -> tuple:
    base = ks.store_root(root)
    path, meta = _meta(kind, base)
    if meta is None:
        return INVALID, None, None, f"{path.name} is missing"
    built = meta.get("built_from_revision")
    try:
        now = kr.current(base).revision
    except kr.LedgerError:
        return INVALID, built, None, "no ledger to compare against"
    if built is None:
        return INVALID, None, now, "no truthful source snapshot (unstamped build)"
    if int(built) > now:
        return INVALID, built, now, "stamp names a revision the ledger has not reached"
    if int(built) == now:
        return CURRENT, built, now, ""
    return STALE, built, now, f"{now - int(built)} revision(s) behind"
```

In `knowledge status`, after the `drift:` block:

```python
            from scripts import derived_health as dh
            for kind in ("search", "graph"):
                state, built, _, reason = dh.store_health(kind)
                line = f"{state}" + (f" (built from {built})" if built else "")
                print(f"{kind + ':':11} {line}" + (f" — {reason}" if reason else ""))
```

- [ ] **Step 4: Run** — `tests/test_derived_health.py`, `tests/test_external_drift.py` (its status tests) green.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: derived stores report CURRENT, STALE or INVALID"`

---

## Task 6 — Equivalence, falsification, gate, records

- [ ] **Step 1: Ranking neutrality on the frozen corpus.** Build both stores from the frozen corpus through the NEW snapshot path and compare with Task 0.

```bash
M=~/.zurvan/measurements/z0-step5
ZURVAN_ROOT=$M/frozen PYTHONPATH=. python - <<'EOF'
import os
M = os.path.expanduser("~/.zurvan/measurements/z0-step5")
import scripts.graph_build as gb
from scripts.build_snapshot import capture_build_inputs
with capture_build_inputs() as bs:
    gb.build_graph(M + "/after-graph.sqlite", snapshot=bs)
EOF
ZURVAN_ROOT=$M/frozen ZURVAN_EMBED_PROVIDER=mock ZURVAN_EMBED_MODEL=mock_model PYTHONPATH=. python -c "
import os; from scripts.rebuild_search_index import rebuild_search_index
from scripts.build_snapshot import capture_build_inputs
M=os.path.expanduser('~/.zurvan/measurements/z0-step5')
with capture_build_inputs() as bs:
    rebuild_search_index(M+'/after-search.sqlite', snapshot=bs)"
PYTHONPATH=. python $M/digest.py graph  $M/after-graph.sqlite  > $M/after-graph.digest
PYTHONPATH=. python $M/digest.py search $M/after-search.sqlite > $M/after-search.digest
diff $M/before-graph.digest  $M/after-graph.digest  && echo "graph: IDENTICAL"
diff $M/before-search.digest $M/after-search.digest && echo "search: IDENTICAL"
```

Required: both IDENTICAL. The new `index_meta` table is outside what `digest.py` reads; that the retriever never reads it is shown by `grep -n index_meta scripts/hybrid_search.py scripts/context_export.py` returning nothing.

- [ ] **Step 2: Falsify.** Scratchpad plugins, each removing exactly one mechanism; each must turn the named test red:

```python
# falsify_no_second_manifest.py — skip the after-clone check
def pytest_configure(config):
    import scripts.build_snapshot as mod
    mod._unchanged = lambda base, m_before, before: True
# falsify_suspect_allowed.py — do not refuse a SUSPECT store
def pytest_configure(config):
    import scripts.build_snapshot as mod
    mod._refuse_if_suspect = lambda state: None
# falsify_live_read.py — the indexer ignores the snapshot and reads live
def pytest_configure(config):
    import scripts.rebuild_search_index as rsi
    real = rsi.iter_all_markdown_chunks
    rsi.iter_all_markdown_chunks = lambda root=None: real(None)
```

Required red: `falsify_no_second_manifest` → `test_a_write_during_capture_is_retried_then_refused`; `falsify_suspect_allowed` → `test_gate_18_a_suspect_store_is_refused`; `falsify_live_read` → `test_gate_17_a_rebuild_stamps_the_revision_it_read` (`wiki/after.md` appears in the index). The first draft of these plugins replaced the exception class the test imports, and removed the manifest entirely — both would have left their target test green. Any red that is a harness error is inconclusive and redone.

Dry-run 2026-09-28 (throwaway worktree, Tasks 1-2 exactly as written): 28 passed; `falsify_no_second_manifest` → DID NOT RAISE `BuildSnapshotNotReady`; `falsify_suspect_allowed` → the capture is STILL refused, one layer down, by `observe_external_drift`'s own SUSPECT guard (`LedgerError`), so the test goes red on the error type. SUSPECT is defended twice; record it that way, not as a single mechanism.

- [ ] **Step 3: Gate** — `check.sh` EXIT=0 with the live wiki hashed before and after; republish both live stores (`scripts/zurvan index search`, `scripts/zurvan graph rebuild`) and read `zurvan knowledge status`: both `COHERENT+CURRENT`.
- [ ] **Step 4: Records** — design §10.2/§10.3 status (items 11, 12, 17, 18 and 5 addressed; 9 partial — measurement capture coherence is step 6), amendments 1–4 into the amendments spec, CLAUDE.md, AGENTS/CHANGELOG `Raouf:` entry.

```text
Z0 after step 5: items 5, 11, 12, 14, 15, 16, 17, 18 addressed; 9 partial; OPEN
```
