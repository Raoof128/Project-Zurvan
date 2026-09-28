# Z0 Step 4 — Atomic graph publication, and published stores that refuse in-place writes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `data/graph.sqlite` the way `data/search.sqlite` is published — built beside the canonical path, validated, made durable and read-only, then swapped in with one `os.replace()` — and make an in-place write to either published store fail loudly, which closes gates 14 and 15.

**Architecture:** One new module, `scripts/sqlite_publish.py`, owns the publication protocol for a derived SQLite store: sibling build path, abandoned-build sweep, self-containment and integrity validation, `fsync` of the file, `chmod 0444`, `os.replace`, `fsync` of the directory. `rebuild_search_index` and `graph_build` both call it, so there is one protocol with one set of tests rather than two copies that drift. The graph gains a `graph_meta` table stamped inside the database.

**Tech Stack:** Python 3.12 stdlib only — `sqlite3`, `os`, `fcntl` (for `F_FULLFSYNC` where it exists), `stat`, `uuid`. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) §4.2, §4.3, §4.4, §9.2 step 4, gate items 14 and 15, plus [the amendments](../specs/2026-09-18-zurvan-2-amendments.md) §9.

## Global Constraints

Copied verbatim from the governing documents. Every task's requirements implicitly include this section.

- **A hardlink snapshot is valid only for a derived store whose active inode is never modified in place after publication.** (Design §4.2.)
- **A SQLite store may be published as an immutable snapshot component only when the published database file is self-contained. Publication must fail if committed state depends on WAL, SHM, rollback-journal, or other mutable sidecar state.** (Design §4.3.)
- **Stamps live inside the database. A filename is not evidence.** (Design §4.4.)
- **No retrieval, ranking, chunking, embedding or index-format change of any kind. Z0 must be provably ranking-neutral; if a retrieval number moves, something is wrong.** (Design §11.)
- **Production knowledge state is never a test fixture.** A test builds its own store under `tmp_path`, `ZURVAN_ROOT` or `ZURVAN_SEARCH_DB`. (AGENTS.md standing invariants.)
- **Cleanup is not isolation.** Never "simplify" a test back to a live path with cleanup at the end. (CLAUDE.md.)
- **Never run `check.sh` while an index rebuild is in flight.** Both write `data/search.sqlite`. (CLAUDE.md.)
- **A failed gate is reported as failed.** Never redefine one retroactively so the work passes. (CLAUDE.md.)
- **Do not `git push` to the public remote.** Only push when Raouf explicitly asks for that specific push. (CLAUDE.md.)
- **A write with no operation above it is REFUSED.** Building a derived store is not an authoritative write (`data/` is outside `wiki/`), so no operation is needed — but nothing in this step may write under `wiki/`. (CLAUDE.md, 2026-09-28.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.** `## Task N` headings; every task shows code; one signature per function; no deferred markers; no private project names; never quote the retrieval instrument.

## Scope

**In:** the shared publication protocol; the graph built aside and published atomically with stamps inside the database; the search index moved onto the same protocol; published files read-only so an in-place write fails (gate 14); self-containment asserted at publication (gate 15); kill-point tests at every step of the protocol; a falsification pass; migration of the two live stores; a before/after equivalence proof on a frozen corpus.

**Out, and deliberately so:**

| Deferred to | What |
|---|---|
| Step 5 | `built_from_revision`, `built_from_snapshot`, snapshot-backed rebuilds, CURRENT/STALE/INVALID. `graph_meta` has room for them; this step writes only what it can know. |
| Step 6 | `purpose`/`required`, coherence verification, leases, GC |
| Separate change | Hand edits invisible to the ledger between reconciles (2026-09-24 item 3). Must close before Z0 closes. |
| Separate change | Graph readers (`graph_query`, `graph_context`, `graph_export`) open read-write and would create an empty file at a missing canonical path. With a published 0444 file they cannot modify it; converting them to `mode=ro` is a reader change, not a publication change. |

**What "fails loudly" means, bounded.** A published file is mode `0444`. Measured 2026-09-28 on this machine: an ordinary read-write `sqlite3.connect` still reads it, an `INSERT` raises `sqlite3.OperationalError: attempt to write a readonly database`, `open(path, "r+b")` raises `PermissionError` (errno 13), no sidecar is left behind, `os.replace` onto the 0444 path succeeds, and a hardlinked snapshot keeps its bytes. **The bound:** the owner can `chmod` it back and root ignores it. This stops an accidental in-place write — the hazard §4.2 names — and is not a security boundary. Gate 14 is recorded as closed with that bound stated, never without it.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/sqlite_publish.py` (new) | The publication protocol for one derived SQLite store. Knows nothing about which stores exist. |
| `scripts/graph_schema.py` (modify) | Graph schema including `graph_meta`; `read_graph_meta`; `content_digest` |
| `scripts/graph_build.py` (modify) | Collect nodes/edges (unchanged logic), write to the build path, publish |
| `scripts/rebuild_search_index.py` (modify) | Promote through `sqlite_publish.publish`; sweep moved out |
| `scripts/measurement_snapshot.py` (modify) | `capture_search` refuses a writable published index |
| `tests/test_sqlite_publish.py` (new) | Protocol contracts and kill points |
| `tests/test_graph_publication.py` (new) | Graph built aside, stamped, SIGKILL-safe, equivalent |
| `tests/test_snapshot_components.py` (modify) | Gate 14 flips from "reproduced" to "enforced" |

## Measured inputs (gauntlet, 2026-09-28, before execution)

Every premise below was settled by running it, not by reading it. Four of them
changed the plan.

```text
0444 published file            read ok; INSERT -> "attempt to write a readonly database";
                               open(r+b) -> errno 13; no sidecar; os.replace onto it ok;
                               hardlinked snapshot keeps its bytes; BEGIN IMMEDIATE still
                               succeeds (a lock, not a write) and leaves no sidecar
WAL build, closed cleanly      no -wal/-shm left; mode=ro open reports journal_mode=wal
corruption (28 KB test db)     512 bytes at offset 4196 -> integrity_check "ok"  (MISSES)
                               page-2 type byte / truncation / header page count /
                               cell-pointer array -> sqlite3.DatabaseError "malformed"
                               raised BY THE PRAGMA, not returned as rows
graph build determinism        two builds of one frozen corpus: same 4,682 nodes /
                               3,946 edges, DIFFERENT digest. Cause: edge_id is
                               uuid4() per build (graph_build.py:84). Digest without
                               edge_id: identical (7 duplicate edge groups preserved)
integrity_check, live graph    10-13 ms (13 MB)      quick_check, live search 1.3-1.8 s
F_FULLFSYNC after real write   13 MB: 16 ms; 746 MB: 5 ms; directory fsync < 0.1 ms
                               (a lower bound: dirty-page state at flush is uncontrolled;
                               a clone-copy measured first was INVALID - no dirty data)
read-only fallout, simulated   10 existing tests write a published store in place:
                               test_hybrid_search (3, fixture injects chunks) and
                               test_index_integrity (5, deliberate corruption) +
                               test_snapshot_components (2, the hazard tests rewritten here)
in-place writers in scripts/   none: graph readers only read; snapshot.py only tars;
                               db.py / decision_memory.py open other files
```

Changes the gauntlet forced: `assert_publishable` wraps every `sqlite3.Error`;
the corruption test damages the page-type byte; `content_digest` excludes
`edge_id`; the live search index is republished inside Task 2, not Task 5,
because `capture_search` will refuse the current writable one.

## Review Focus

Failure modes the design implies but no happy path exercises, most likely first. Each has its test in the owning task.

1. **A rebuild killed with SIGKILL mid-write** — no `finally` runs. Expected: the canonical graph is byte-identical and still opens; the next build sweeps the orphan build file and its `-journal`. (Task 3, `test_a_sigkilled_build_leaves_the_published_graph_untouched`.)
2. **A reader holding the old graph while a new one is published.** Expected: its open connection keeps reading the old generation; a new connection sees the new one. (Task 1, `test_a_reader_holding_the_old_store_keeps_reading_it`.)
3. **The very first build, with no canonical file yet.** Expected: publication creates it, read-only. (Task 3, `test_the_first_build_publishes_where_nothing_existed`.)
4. **Disk full during the build.** Expected: the canonical store is untouched and the build file is gone. (Task 3, `test_a_failed_build_leaves_the_published_graph_untouched`.)
5. **Two graph builds at once.** Expected: the second refuses and names the live pid, as the search rebuild already does. (Task 3, `test_a_second_concurrent_build_refuses`.)

---

## Task 0 — Preflight: freeze the inputs and take the baseline before anything changes

Nothing is edited in this task. Every later comparison is against what it records, so it runs first and **alone** — on 2026-09-28 a baseline that was still running when edits began had to be discarded.

**Files:** none in the repo. Writes only under `~/.zurvan/measurements/z0-step4/` (private, never tracked).

- [ ] **Step 1: Confirm nothing is rebuilding and nothing is dirty**

```bash
pgrep -f "rebuild_search_index|graph_build|check.sh" && echo "BUSY - stop" || echo clear
git status --short          # only .obsidian/graph.json may appear
ls data | grep -E 'building|journal|-wal|-shm' || echo "no sidecars"
stat -f '%Sp %N' data/graph.sqlite data/search.sqlite
```

- [ ] **Step 2: Freeze a corpus to build both stores from, before and after**

The live wiki moves under other sessions' writes, so equivalence is proven on a frozen clone.

```bash
M=~/.zurvan/measurements/z0-step4; mkdir -p $M/frozen
PYTHONPATH=. python - <<'EOF'
import os
from pathlib import Path
from scripts.immutable_bytes import clone_tree, clone_file
M = Path(os.path.expanduser("~/.zurvan/measurements/z0-step4/frozen"))
for d in ("wiki", "docs"):
    print(d, clone_tree(Path(d), M / d))
for f in ("AGENTS.md", "CHANGELOG.md", "README.md"):
    print(f, clone_file(Path(f), M / f))
EOF
```

- [ ] **Step 3: Write the digest script both halves of the comparison will use**

One script, saved once, so "before" and "after" cannot differ in how they measure. Timestamps are excluded because `extract_node_data` defaults `created_at`/`updated_at` to `datetime.now()`; `edge_id` is excluded because it is `uuid4()` per build — the gauntlet measured two builds of one frozen corpus differing in every edge_id and nothing else.

```bash
cat > ~/.zurvan/measurements/z0-step4/digest.py <<'EOF'
import hashlib, sqlite3, sys
def graph(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = hashlib.sha256()
    for r in c.execute("SELECT node_id, node_type, title, path, slug, content_hash, tags FROM nodes ORDER BY node_id"):
        h.update(repr(r).encode())
    # edge_id is uuid4() per build (graph_build.py:84): excluded, or two builds
    # of the same corpus never match. Duplicates kept; order is by content.
    for r in c.execute("SELECT from_node_id, to_node_id, edge_type, evidence, source_path FROM edges ORDER BY from_node_id, to_node_id, edge_type, evidence, source_path"):
        h.update(repr(r).encode())
    n = c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    e = c.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    return f"{h.hexdigest()} nodes={n} edges={e}"
def search(path):
    from scripts.measurement_snapshot import index_identity
    i = index_identity(path)
    return f"chunk_id_set_sha={i.get('chunk_id_set_sha')} chunks={i.get('chunks')}"
print({"graph": graph, "search": search}[sys.argv[1]](sys.argv[2]))
EOF
```

- [ ] **Step 4: Build both stores from the frozen corpus with the CURRENT code**

```bash
M=~/.zurvan/measurements/z0-step4
PYTHONPATH=. python - <<'EOF'
import os
from pathlib import Path
M = Path(os.path.expanduser("~/.zurvan/measurements/z0-step4"))
import scripts.graph_build as gb
gb.PROJECT_ROOT = str(M / "frozen")
gb.build_graph(str(M / "before-graph.sqlite"))
EOF
ZURVAN_EMBED_PROVIDER=mock ZURVAN_EMBED_MODEL=mock_model PYTHONPATH=. python -c "
import os; from scripts.rebuild_search_index import rebuild_search_index
M=os.path.expanduser('~/.zurvan/measurements/z0-step4')
rebuild_search_index(M+'/before-search.sqlite', root=M+'/frozen')"
PYTHONPATH=. python $M/digest.py graph  $M/before-graph.sqlite  | tee $M/before-graph.digest
PYTHONPATH=. python $M/digest.py search $M/before-search.sqlite | tee $M/before-search.digest
```

- [ ] **Step 5: Record the regression floor and the live retrieval identity**

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider 2>&1 | tail -1 | tee $M/pytest-floor.txt
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6 2>&1 | grep -E 'Top-1|Top-3|MRR|chunk-set' | tee $M/eval-before.txt
```

Expected floor: `1172 passed, 4 skipped` (2026-09-28, commit `62c3110`). If it differs, stop and find out why before editing anything.

---

## Task 1 — `sqlite_publish`: one protocol, validated, durable, read-only, atomic

**Files:**
- Create: `scripts/sqlite_publish.py`
- Test: `tests/test_sqlite_publish.py`

**Interfaces:**
- Produces:
  - `BUILD_SUFFIX = ".building-"`, `SIDECARS = ("-journal", "-wal", "-shm")`, `PUBLISHED_MODE = 0o444`
  - `class PublicationError(RuntimeError)`
  - `build_path_for(canonical) -> str`
  - `sweep_abandoned_builds(canonical) -> set`
  - `discard_build(build_path) -> None`
  - `assert_publishable(build_path, integrity=True) -> None`
  - `publish(build_path, canonical, integrity=True) -> None`
  - `assert_published_immutable(canonical) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""The publication protocol for a derived SQLite store.

build aside -> validate -> fsync file -> chmod 0444 -> os.replace -> fsync dir

A reader must observe the old valid store or the new valid store, never an
intermediate one, and a published file must refuse an in-place write — the
hazard that makes a hardlink snapshot silently stop being one (design §4.2).
"""

import os
import sqlite3
import stat

import pytest

from scripts import sqlite_publish as sp


def _db(path, rows=(1,), journal="delete"):
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA journal_mode={journal}")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()
    return str(path)


def _rows(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT x FROM t ORDER BY x")]
    finally:
        conn.close()


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def live(tmp_path):
    """A canonical store already published once, holding row 1."""
    canonical = str(tmp_path / "store.sqlite")
    build = sp.build_path_for(canonical)
    _db(build, rows=(1,))
    sp.publish(build, canonical)
    return canonical


# ── the happy path, stated as properties ─────────────────────────────────────

def test_publication_replaces_the_canonical_store(live):
    build = sp.build_path_for(live)
    _db(build, rows=(2,))
    sp.publish(build, live)
    assert _rows(live) == [2]
    assert not os.path.exists(build)


def test_a_published_store_is_read_only(live):
    assert stat.S_IMODE(os.stat(live).st_mode) == sp.PUBLISHED_MODE


def test_the_build_path_is_a_sibling_owned_by_this_process(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    assert sp.build_path_for(canonical) == f"{canonical}.building-{os.getpid()}"


def test_a_build_path_in_another_directory_is_refused(tmp_path):
    """`os.replace` is atomic only within one directory; a build elsewhere
    could be a copy, and a copy is not atomic."""
    (tmp_path / "other").mkdir()
    build = _db(tmp_path / "other" / "b.sqlite")
    with pytest.raises(sp.PublicationError):
        sp.publish(build, str(tmp_path / "store.sqlite"))


# ── gate 14: an in-place write to a published store fails loudly ─────────────

def test_an_in_place_sqlite_write_to_a_published_store_fails_loudly(live):
    conn = sqlite3.connect(live)          # default read-write open, as readers do
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t VALUES (99)")
            conn.commit()
    finally:
        conn.close()
    assert _rows(live) == [1]


def test_an_in_place_byte_write_to_a_published_store_fails_loudly(live):
    with pytest.raises(PermissionError):
        open(live, "r+b")


def test_a_refused_write_leaves_no_sidecar(live):
    conn = sqlite3.connect(live)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (99)")
    finally:
        conn.close()
    assert [s for s in sp.SIDECARS if os.path.exists(live + s)] == []


def test_a_writable_published_store_is_reported(tmp_path):
    canonical = _db(tmp_path / "store.sqlite")          # made the old way
    with pytest.raises(sp.PublicationError, match="writable"):
        sp.assert_published_immutable(canonical)


# ── gate 15: publication refuses a store that is not self-contained ──────────

@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_a_build_with_a_sidecar_is_not_published(live, suffix):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,))
    with open(build + suffix, "wb") as f:
        f.write(b"mid-write")
    with pytest.raises(sp.PublicationError, match=suffix.lstrip("-")):
        sp.publish(build, live)
    assert _bytes(live) == before


def test_a_wal_mode_build_is_not_published(live):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,), journal="wal")
    for s in ("-wal", "-shm"):                # checkpointed and closed: no sidecars
        if os.path.exists(build + s):
            os.remove(build + s)
    with pytest.raises(sp.PublicationError, match="wal"):
        sp.publish(build, live)
    assert _bytes(live) == before


def test_a_corrupt_build_is_not_published(live):
    """Page 2's b-tree type byte. Measured 2026-09-28: 512 bytes of 0xff at
    offset 4196 landed in unused space and integrity_check said "ok" - a
    corruption test that corrupts nothing proves nothing."""
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=range(2000))
    with open(build, "r+b") as f:
        f.seek(4096)
        f.write(b"\x00")
    with pytest.raises(sp.PublicationError, match="not a readable database|integrity"):
        sp.publish(build, live, integrity=True)
    assert _bytes(live) == before


# ── kill points: a failure at any step leaves the old store valid ────────────

@pytest.mark.parametrize("point", ["_fsync_file", "_chmod", "_replace"])
def test_a_failure_before_the_swap_leaves_the_old_store(live, monkeypatch, point):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,))

    def boom(*a, **k):
        raise OSError(28, f"injected at {point}")
    monkeypatch.setattr(sp, point, boom)

    with pytest.raises(OSError):
        sp.publish(build, live)
    assert _bytes(live) == before and _rows(live) == [1]


def test_a_failure_after_the_swap_leaves_the_new_store(live, monkeypatch):
    """The directory fsync runs after the rename. If it fails, the rename has
    already happened: the new store is live and complete, and the error still
    propagates, because durability was not confirmed."""
    build = _db(sp.build_path_for(live), rows=(2,))

    def dir_fsync_fails(path):
        raise OSError(5, "injected at _fsync_dir")
    monkeypatch.setattr(sp, "_fsync_dir", dir_fsync_fails)
    with pytest.raises(OSError):
        sp.publish(build, live)
    assert _rows(live) == [2]


def test_the_protocol_runs_in_order(live, monkeypatch):
    calls = []
    for name in ("assert_publishable", "_fsync_file", "_chmod", "_replace", "_fsync_dir"):
        real = getattr(sp, name)
        def spy(*a, _real=real, _name=name, **k):
            calls.append(_name)
            return _real(*a, **k)
        monkeypatch.setattr(sp, name, spy)
    sp.publish(_db(sp.build_path_for(live), rows=(2,)), live)
    assert calls == ["assert_publishable", "_fsync_file", "_chmod", "_replace", "_fsync_dir"]


# ── readers ──────────────────────────────────────────────────────────────────

def test_a_reader_holding_the_old_store_keeps_reading_it(live):
    held = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        assert [r[0] for r in held.execute("SELECT x FROM t")] == [1]
        sp.publish(_db(sp.build_path_for(live), rows=(2,)), live)
        assert [r[0] for r in held.execute("SELECT x FROM t")] == [1]
    finally:
        held.close()
    assert _rows(live) == [2]


# ── abandoned builds ─────────────────────────────────────────────────────────

def test_a_dead_builders_file_and_journal_are_swept(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    dead = f"{canonical}{sp.BUILD_SUFFIX}999999"
    _db(dead)
    open(dead + "-journal", "wb").close()
    assert sp.sweep_abandoned_builds(canonical) == set()
    assert not os.path.exists(dead) and not os.path.exists(dead + "-journal")


def test_a_live_builders_file_is_left_and_named(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    parent = os.getppid()
    mine = f"{canonical}{sp.BUILD_SUFFIX}{parent}"
    _db(mine)
    assert sp.sweep_abandoned_builds(canonical) == {parent}
    assert os.path.exists(mine)


def test_discarding_a_build_removes_its_sidecars_and_tolerates_absence(tmp_path):
    build = _db(tmp_path / f"s.sqlite{sp.BUILD_SUFFIX}{os.getpid()}")
    open(build + "-journal", "wb").close()
    os.chmod(build, 0o444)
    sp.discard_build(build)
    sp.discard_build(build)
    assert not os.path.exists(build) and not os.path.exists(build + "-journal")


def test_fsync_falls_back_where_full_fsync_does_not_exist(tmp_path, monkeypatch):
    """Linux has no F_FULLFSYNC. The fallback is a peer path, so it runs."""
    import types
    calls = []
    monkeypatch.setattr(sp, "fcntl", types.SimpleNamespace())
    monkeypatch.setattr(sp.os, "fsync", lambda fd: calls.append(fd))
    sp._fsync_file(_db(tmp_path / "f.sqlite"))
    assert len(calls) == 1


def test_full_fsync_is_used_where_it_exists(tmp_path, monkeypatch):
    """The effect, not the call order: removing the flush must turn a test red,
    and `test_the_protocol_runs_in_order` cannot see it (its spies record the
    name even when the body is gone - found by the gauntlet's dry run)."""
    import types
    seen = []
    fake = types.SimpleNamespace(F_FULLFSYNC=51,
                                 fcntl=lambda fd, op: seen.append(op))
    monkeypatch.setattr(sp, "fcntl", fake)
    sp._fsync_file(_db(tmp_path / "f.sqlite"))
    assert seen == [51]


def test_the_directory_is_fsynced(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sp.os, "fsync", lambda fd: calls.append(fd))
    sp._fsync_dir(str(tmp_path))
    assert len(calls) == 1
```

- [ ] **Step 2: Run them and watch every one fail for the right reason**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_sqlite_publish.py`
Expected: collection error `ModuleNotFoundError: No module named 'scripts.sqlite_publish'`.

- [ ] **Step 3: Write the module**

```python
"""Publish a derived SQLite store: built aside, validated, made immutable, swapped in.

    build   <canonical>.building-<pid>
    validate            self-contained (no sidecar, not WAL), integrity
    fsync the file      so the bytes are durable before anyone can see them
    chmod 0444          so the published inode refuses an in-place write
    os.replace()        the only mutation of the canonical path
    fsync the directory so the rename itself is durable

A reader observes the old valid store or the new one, never an intermediate.
A hardlink snapshot is legal only because the published inode is never
modified in place (design §4.2); `0444` is what turns that from a convention
into an error. Bounded: the owner can chmod it back and root ignores it, so
this stops an accidental in-place write and is not a security boundary.

Knows nothing about which stores exist. `rebuild_search_index` and
`graph_build` are its callers.
"""

import fcntl
import glob
import os
import sqlite3
import stat

BUILD_SUFFIX = ".building-"
SIDECARS = ("-journal", "-wal", "-shm")
PUBLISHED_MODE = 0o444

# Indirections, so a test can inject a failure at each step without patching
# the `os` module for the whole process.
_chmod = os.chmod
_replace = os.replace


class PublicationError(RuntimeError):
    """A store could not be published. The canonical file is untouched."""


def build_path_for(canonical) -> str:
    return f"{canonical}{BUILD_SUFFIX}{os.getpid()}"


def sweep_abandoned_builds(canonical) -> set:
    """Delete build files left by builds that were killed outright.

    A SIGKILL cannot clean up after itself, so these accumulate. Files owned by
    a LIVE process are left alone and their pids returned: that is a concurrent
    build, and the caller refuses to start beside it. Moved verbatim from
    `rebuild_search_index` on 2026-09-28 so the graph shares it.
    """
    live_builders: set = set()
    for path in glob.glob(f"{canonical}{BUILD_SUFFIX}*"):
        # SQLite's own sidecars are named `<db>-journal` / `-wal` / `-shm`, so
        # the pid is the LEADING integer of the suffix, not the whole of it.
        # Parsing the whole suffix made a live rebuild's journal look ownerless
        # and deletable, which would corrupt that build.
        suffix = path.rsplit(BUILD_SUFFIX, 1)[-1]
        digits = ""
        for ch in suffix:
            if not ch.isdigit():
                break
            digits += ch
        pid = int(digits) if digits else None
        if pid is not None and pid != os.getpid():
            try:
                os.kill(pid, 0)
                live_builders.add(pid)
                continue  # a live build owns this file
            except (OSError, ProcessLookupError):
                pass
        if pid != os.getpid():
            try:
                os.remove(path)
                print(f"  discarded abandoned build file {os.path.basename(path)}")
            except OSError:
                pass
    return live_builders


def discard_build(build_path) -> None:
    """Remove a build file and its SQLite sidecars. Absence is fine; a
    read-only build is removable because unlink needs the directory, not the
    file."""
    for leftover in (build_path, *(f"{build_path}{s}" for s in SIDECARS)):
        try:
            os.remove(leftover)
        except FileNotFoundError:
            pass


def assert_publishable(build_path, integrity=True) -> None:
    """The file must BE the database (design §4.3), and a whole one."""
    for suffix in SIDECARS:
        if os.path.exists(f"{build_path}{suffix}"):
            raise PublicationError(
                f"{os.path.basename(build_path)} has a {suffix.lstrip('-')} "
                "sidecar: its committed state is not all in the file")
    # Measured: real corruption makes the PRAGMA itself raise
    # `DatabaseError: database disk image is malformed` rather than return
    # rows, so every sqlite3 error here is a refusal to publish.
    try:
        conn = sqlite3.connect(f"file:{build_path}?mode=ro", uri=True)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            result = (conn.execute("PRAGMA integrity_check").fetchall()
                      if integrity else [("ok",)])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise PublicationError(
            f"{os.path.basename(build_path)} is not a readable database: {exc}"
        ) from exc
    if mode != "delete":
        raise PublicationError(
            f"{os.path.basename(build_path)} is in {mode} mode; a published "
            "store must be self-contained (journal_mode=delete)")
    if result != [("ok",)]:
        raise PublicationError(
            f"{os.path.basename(build_path)} failed integrity_check: "
            f"{result[:3]}")


def _fsync_file(path) -> None:
    """`F_FULLFSYNC` where it exists: on macOS plain `fsync` does not flush the
    drive's cache."""
    fd = os.open(path, os.O_RDONLY)
    try:
        if hasattr(fcntl, "F_FULLFSYNC"):
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError:
                pass
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(build_path, canonical, integrity=True) -> None:
    """Validate, make durable, make read-only, swap in, make the swap durable.

    Leaves `build_path` for the caller to discard on failure; the caller owns
    the build and its `finally`.
    """
    build_dir = os.path.dirname(os.path.abspath(build_path))
    canonical_dir = os.path.dirname(os.path.abspath(canonical))
    if build_dir != canonical_dir:
        raise PublicationError(
            "a build must be a sibling of the canonical file: os.replace is "
            "atomic only within one directory")
    assert_publishable(build_path, integrity=integrity)
    _fsync_file(build_path)
    _chmod(build_path, PUBLISHED_MODE)
    _replace(build_path, canonical)
    _fsync_dir(canonical_dir)


def assert_published_immutable(canonical) -> None:
    """A published store's inode must refuse an in-place write.

    Called where a hardlink is about to be taken: a writable published file
    means either it predates this protocol or someone chmodded it back, and in
    both cases a hardlink to it could stop being a snapshot.
    """
    mode = stat.S_IMODE(os.stat(canonical).st_mode)
    if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise PublicationError(
            f"{os.path.basename(canonical)} is writable (mode {oct(mode)}); a "
            "published store must be read-only before it can be hardlinked. "
            "Republish it (`zurvan index search` / `zurvan graph rebuild`).")
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_sqlite_publish.py`
Expected: all pass. `test_the_protocol_runs_in_order` spies on `assert_publishable` through the module attribute, so `publish` must call it as a module-level name, which it does.

- [ ] **Step 5: Commit**

```bash
git add scripts/sqlite_publish.py tests/test_sqlite_publish.py
git commit -m "zurvan: one publication protocol for derived SQLite stores"
```

---

## Task 2 — The search index publishes through the protocol, and gate 14 closes

**Files:**
- Modify: `scripts/rebuild_search_index.py` (the `_sweep_abandoned_builds` definition, the promotion `os.replace`, the `finally` cleanup)
- Modify: `scripts/measurement_snapshot.py` (`capture_search`)
- Modify: `tests/test_snapshot_components.py` (the "reproduced, NOT enforced" section)
- Test: `tests/test_snapshot_components.py`, `tests/test_index_integrity.py`

**Interfaces:**
- Consumes: `sweep_abandoned_builds(canonical) -> set`, `discard_build(build_path) -> None`, `publish(build_path, canonical, integrity=True) -> None`, `assert_published_immutable(canonical) -> None`, `PublicationError`
- Produces: `rebuild_search_index._sweep_abandoned_builds` kept as an alias, because `tests/test_index_integrity.py:341,381` call it by that name.

- [ ] **Step 1: Write the failing tests**

Replace the section headed `# ── the hardlink hazard: reproduced, NOT enforced (gate 14 stays OPEN)` in `tests/test_snapshot_components.py` — both `test_an_in_place_write_to_the_published_index_reaches_every_snapshot` and its docstring's "NOT gate 14" — with:

```python
# ── gate 14: the hardlink invariant, enforced ────────────────────────────────

def _publish_index(store):
    """An index published the way `rebuild_search_index` publishes one."""
    from scripts import sqlite_publish as sp
    index = store / "data" / "search.sqlite"
    build = sp.build_path_for(str(index))
    conn = sqlite3.connect(build)
    conn.execute("CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO chunks VALUES (1)")
    conn.commit()
    conn.close()
    sp.publish(build, str(index))
    return index


def test_gate_14_an_in_place_write_to_the_published_index_fails_loudly(
        store, into, monkeypatch):
    """Gate 14. Until 2026-09-28 this test proved the opposite: the write
    succeeded and reached every hardlinked snapshot. The published inode is
    now read-only, so the offending write raises instead, and the snapshot
    keeps the bytes it froze."""
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    index = _publish_index(store)
    component = ms.capture_search(store, into)

    conn = sqlite3.connect(index)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO chunks VALUES (2)")
            conn.commit()
    finally:
        conn.close()

    conn = sqlite3.connect(f"file:{component.path}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    finally:
        conn.close()


def test_capture_refuses_to_hardlink_a_writable_published_index(
        store, into, monkeypatch):
    """An index published before 2026-09-28, or chmodded back, is writable. A
    hardlink to it is the hazard itself, so capture refuses rather than
    freezing something that can change underneath it."""
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    _make_index(store)                         # the old, writable way
    with pytest.raises(ms.SnapshotComponentError, match="writable"):
        ms.capture_search(store, into)
```

Keep `_make_index` (other tests in the file use it), and update `test_the_index_is_captured_by_hardlink_which_is_why_the_hazard_exists` and `test_capture_refuses_an_index_whose_committed_state_is_not_in_the_file` to build with `_publish_index(store)` instead of `_make_index(store)`. For the journal test, the published file is read-only but the directory is not, so writing the `-journal` beside it still works.

Append to `tests/test_index_integrity.py`:

```python
def test_a_rebuilt_index_is_published_read_only(tmp_path, mock_env):
    """The real rebuild path, not the protocol in isolation."""
    import stat
    from scripts import sqlite_publish as sp
    db = tmp_path / "search.sqlite"
    rebuild_search_index(str(db), root=_corpus(tmp_path))
    assert stat.S_IMODE(db.stat().st_mode) == sp.PUBLISHED_MODE
    assert [s for s in sp.SIDECARS if (tmp_path / f"search.sqlite{s}").exists()] == []
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_snapshot_components.py tests/test_index_integrity.py -k "gate_14 or writable or read_only"`
Expected, exactly two failures:
- `test_capture_refuses_to_hardlink_a_writable_published_index` — `DID NOT RAISE`, because `capture_search` does not yet check the mode.
- `test_a_rebuilt_index_is_published_read_only` — mode `0o644`, because the rebuild still promotes with a bare `os.replace`.

`test_gate_14_an_in_place_write_to_the_published_index_fails_loudly` already passes here: `_publish_index` goes through Task 1's protocol directly. That is not a vacuous test — Task 4's `falsify_no_chmod` turns it red — but it is recorded so nobody mistakes it for a Task 2 result.

- [ ] **Step 3: Route the rebuild through the protocol**

In `scripts/rebuild_search_index.py`, delete the body of `_sweep_abandoned_builds` and the `_BUILD_SUFFIX` constant, and replace them with:

```python
from scripts import sqlite_publish as _publication

_BUILD_SUFFIX = _publication.BUILD_SUFFIX
# Kept under its old name: tests/test_index_integrity.py calls it directly.
_sweep_abandoned_builds = _publication.sweep_abandoned_builds
```

Replace the promotion and its `finally`:

```python
        # The only mutation of the canonical path in this function, and it is
        # atomic: readers see the old index or the new one. Integrity was
        # checked by `check_index_health(deep=True)` above, and a full
        # `integrity_check` over 739 MB would double the cost of publishing.
        _publication.publish(build_path, db_path, integrity=False)
    finally:
        reusable.close()
        # The build file AND its sqlite sidecars: a killed rebuild that leaves a
        # `-journal` behind makes the next sweep's job ambiguous.
        _publication.discard_build(build_path)
```

The existing `if os.path.exists(build_path): os.remove(build_path)` before the build becomes `_publication.discard_build(build_path)`.

In `scripts/measurement_snapshot.py`, `capture_search`, immediately after `assert_self_contained(source)`:

```python
    from scripts.sqlite_publish import PublicationError, assert_published_immutable
    try:
        assert_published_immutable(source)
    except PublicationError as exc:
        raise SnapshotComponentError(str(exc)) from exc
```

- [ ] **Step 4: Run the affected files, then find every test that wrote a published index in place**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_snapshot_components.py tests/test_index_integrity.py tests/test_measurement_snapshot.py tests/test_snapshot_backend_equivalence.py`

The gauntlet simulated this step and found exactly these in-place writers. Fix each by what it means; never weaken the protocol to make a test pass.

| Test | What it does | Fix |
|---|---|---|
| `test_hybrid_search.py` — `test_search_hybrid`, `test_fts_porter_stemming_matches_inflected_terms`, `test_vectorised_scoring_matches_the_reference_cosine_fusion` | `_insert_chunk` injects rows into a rebuilt index: a published store used as a scratch fixture | the index-building helper ends with the unlock below, once |
| `test_index_integrity.py` — `test_a_partial_index_is_not_healthy`, `test_chunks_must_equal_embeddings`, `test_the_wrong_model_fails_health`, `test_a_mixed_provider_index_fails_health`, `test_a_wrong_width_vector_fails_deep_health` | deliberately corrupt a published index to exercise health checks | the unlock below, immediately before the corruption |

```python
def _unlock_published_for_fixture(db) -> None:
    """Deliberately undo publication's read-only bit. These tests simulate the
    forbidden in-place write (or use a published store as scratch); saying so
    here keeps it from looking like an accident."""
    os.chmod(db, 0o644)
```

Define it once in each of the two files (they share no helper module today).

- [ ] **Step 5: Republish the live index now, not in Task 5**

From this commit `capture_search` refuses a writable index, and the live one is writable until it is republished. `check.sh` would self-heal (its gate 5 rebuilds before gate 6 captures), but any other session measuring in between would be refused.

```bash
pgrep -f "rebuild_search_index|check.sh" && echo "BUSY - stop" || echo clear
ZURVAN_EMBED_PROVIDER=sentence_transformers ZURVAN_EMBED_MODEL=all-MiniLM-L6-v2 scripts/zurvan index search
stat -f '%Sp %N' data/search.sqlite          # -r--r--r--
```

- [ ] **Step 6: Commit**

```bash
git add scripts/rebuild_search_index.py scripts/measurement_snapshot.py tests/test_snapshot_components.py tests/test_index_integrity.py tests/test_hybrid_search.py
git commit -m "zurvan: gate 14 - a published index refuses an in-place write"
```

---

## Task 3 — The graph is built aside, stamped inside, and published atomically

**Files:**
- Modify: `scripts/graph_schema.py`
- Modify: `scripts/graph_build.py` (`build_graph`, lines 135-208)
- Modify: `scripts/measurement_snapshot.py` (`capture_graph` docstring only)
- Test: `tests/test_graph_publication.py` (new), `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `build_path_for`, `sweep_abandoned_builds`, `discard_build`, `publish` from Task 1
- Produces:
  - `graph_schema.GRAPH_SCHEMA_VERSION = 1`
  - `graph_schema.setup_graph_schema(db_path: str = DB_PATH) -> None` (unchanged signature; now also creates `graph_meta`)
  - `graph_schema.read_graph_meta(db_path) -> dict`
  - `graph_schema.content_digest(db_path) -> str`
  - `graph_build.build_graph(db_path: str = DB_PATH)` (unchanged signature)

- [ ] **Step 1: Write the failing tests**

```python
"""The graph is published, not edited.

`graph_build.py:186` used to open the canonical `data/graph.sqlite` and DELETE
then INSERT into it — a reader mid-build saw an empty or half-filled graph, and
a hardlink to it was illegal (design §4.4). It is now built at
`graph.sqlite.building-<pid>`, stamped inside, and published by
`sqlite_publish.publish`.
"""

import os
import signal
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.graph_build as gb
from scripts import graph_schema as gs
from scripts import sqlite_publish as sp

REPO = Path(__file__).resolve().parents[1]


def _tree(root: Path) -> Path:
    (root / "wiki" / "decisions").mkdir(parents=True)
    (root / "wiki" / "a.md").write_text("---\ntitle: A\n---\nsee [[b]]\n")
    (root / "wiki" / "b.md").write_text("---\ntitle: B\n---\nplain\n")
    (root / "wiki" / "decisions" / "d.md").write_text(
        "---\ntitle: D\ntype: decision\n---\nabout [[a]]\n")
    return root


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = _tree(tmp_path / "repo")
    monkeypatch.setattr(gb, "PROJECT_ROOT", str(root))
    (tmp_path / "data").mkdir()
    return root, str(tmp_path / "data" / "graph.sqlite")


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_the_first_build_publishes_where_nothing_existed(tree):
    _, db = tree
    gb.build_graph(db)
    assert stat.S_IMODE(os.stat(db).st_mode) == sp.PUBLISHED_MODE
    assert gs.read_graph_meta(db)["node_count"] == "3"


def test_the_graph_is_stamped_inside_the_database(tree):
    _, db = tree
    gb.build_graph(db)
    meta = gs.read_graph_meta(db)
    assert meta["schema_version"] == str(gs.GRAPH_SCHEMA_VERSION)
    assert len(meta["graph_generation_id"]) == 32
    assert meta["built_at"].endswith("+00:00")
    assert set(meta) == {"schema_version", "graph_generation_id", "built_at",
                         "node_count", "edge_count"}


def test_each_publication_is_a_new_generation(tree):
    _, db = tree
    gb.build_graph(db)
    first = gs.read_graph_meta(db)["graph_generation_id"]
    gb.build_graph(db)
    assert gs.read_graph_meta(db)["graph_generation_id"] != first


def test_the_same_corpus_gives_the_same_content(tree):
    """Rebuilding is not a ranking change: the node and edge content is a
    function of the corpus. Timestamps are excluded because
    `extract_node_data` defaults them to now()."""
    _, db = tree
    gb.build_graph(db)
    first = gs.content_digest(db)
    gb.build_graph(db)
    assert gs.content_digest(db) == first


def test_edge_ids_are_random_which_is_why_the_digest_ignores_them(tree):
    """Recorded, not endorsed: `graph_build.py:84` mints uuid4() edge ids, so a
    rebuild renames every edge. If this ever fails, edge ids became
    deterministic - then put edge_id back into content_digest."""
    _, db = tree
    def ids():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[0] for r in conn.execute("SELECT edge_id FROM edges")}
        finally:
            conn.close()
    gb.build_graph(db)
    first = ids()
    gb.build_graph(db)
    assert first and first.isdisjoint(ids())


def test_the_published_graph_is_self_contained(tree):
    _, db = tree
    gb.build_graph(db)
    assert [s for s in sp.SIDECARS if os.path.exists(db + s)] == []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_a_failed_build_leaves_the_published_graph_untouched(tree, monkeypatch):
    root, db = tree
    gb.build_graph(db)
    before = _bytes(db)

    def disk_full(*a, **k):
        raise OSError(28, "No space left on device (injected)")
    monkeypatch.setattr(gb, "_write_graph", disk_full)
    (root / "wiki" / "c.md").write_text("---\ntitle: C\n---\nnew\n")

    with pytest.raises(OSError):
        gb.build_graph(db)
    assert _bytes(db) == before
    assert not os.path.exists(sp.build_path_for(db))


def test_a_second_concurrent_build_refuses(tree):
    _, db = tree
    other = f"{db}{sp.BUILD_SUFFIX}{os.getppid()}"
    open(other, "wb").close()
    try:
        with pytest.raises(RuntimeError, match=str(os.getppid())):
            gb.build_graph(db)
    finally:
        os.remove(other)


def test_a_sigkilled_build_leaves_the_published_graph_untouched(tree, tmp_path):
    """No `finally` runs under SIGKILL. The child builds, then kills itself at
    the last moment before the swap.

    Bound: the sweep decides "dead" with `os.kill(pid, 0)`, so if the OS
    reuses the child's pid before the next build, that build refuses as if a
    builder were live. Inherited from the search sweep; the ledger's
    pid+start-time identity would fix it and is not in scope.
    """
    root, db = tree
    gb.build_graph(db)
    before = _bytes(db)

    child = (
        "import os, signal, sys\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "import scripts.graph_build as gb\n"
        "from scripts import sqlite_publish as sp\n"
        f"gb.PROJECT_ROOT = {str(root)!r}\n"
        "sp._replace = lambda *a: os.kill(os.getpid(), signal.SIGKILL)\n"
        f"gb.build_graph({db!r})\n"
    )
    result = subprocess.run([sys.executable, "-c", child], capture_output=True)
    assert result.returncode == -signal.SIGKILL

    assert _bytes(db) == before
    orphans = [p for p in os.listdir(os.path.dirname(db)) if sp.BUILD_SUFFIX in p]
    assert orphans, "the child should have left its build file behind"

    gb.build_graph(db)                          # the next build sweeps it
    assert [p for p in os.listdir(os.path.dirname(db)) if sp.BUILD_SUFFIX in p] == []


def test_an_in_place_write_to_the_published_graph_fails_loudly(tree):
    _, db = tree
    gb.build_graph(db)
    conn = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM nodes")
            conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_graph_publication.py`
Expected: `AttributeError: module 'scripts.graph_schema' has no attribute 'read_graph_meta'` and friends; `test_an_in_place_write...` fails with `DID NOT RAISE`.

- [ ] **Step 3: Add the schema pieces**

In `scripts/graph_schema.py`, add after `DB_PATH`:

```python
GRAPH_SCHEMA_VERSION = 1
```

Inside `setup_graph_schema`, before `conn.commit()`:

```python
    # Stamps live INSIDE the database (design §4.4): a filename is not
    # evidence. Step 5 adds built_from_revision / built_from_snapshot; this
    # step writes only what it can know.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graph_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
```

And two readers at module level:

```python
def read_graph_meta(db_path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT key, value FROM graph_meta"))
    finally:
        conn.close()


def content_digest(db_path) -> str:
    """What the graph SAYS, without when it was built or what it was called.

    Excluded: timestamps (`extract_node_data` defaults them to now()) and
    `edge_id`, which is `uuid4()` per build (`graph_build.py:84`). Measured
    2026-09-28: two builds of one frozen corpus differ in every edge_id and in
    nothing else. Edges are ordered by content, duplicates kept (7 groups on
    the live corpus), so a lost or doubled edge still changes the digest.
    """
    import hashlib
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        h = hashlib.sha256()
        for row in conn.execute(
                "SELECT node_id, node_type, title, path, slug, content_hash, tags"
                " FROM nodes ORDER BY node_id"):
            h.update(repr(row).encode())
        for row in conn.execute(
                "SELECT from_node_id, to_node_id, edge_type, evidence, source_path"
                " FROM edges ORDER BY from_node_id, to_node_id, edge_type,"
                " evidence, source_path"):
            h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        conn.close()
```

`content_digest` hashes exactly what Task 0's `digest.py graph` hashes, in the same order, so the two agree on the same database.

- [ ] **Step 4: Split `build_graph` into collect, write, publish**

In `scripts/graph_build.py`, the two passes (lines 138-182, from `target_dirs = ...` through the edge loop) move unchanged into:

```python
def _collect_graph():
    """(nodes_dict, edges_list) for the corpus under PROJECT_ROOT. The logic is
    the pre-2026-09-28 body of build_graph, moved, not changed."""
    target_dirs = ['wiki', 'docs']
    # ... the existing pass 1 and pass 2 code, verbatim ...
    return nodes_dict, edges_list
```

The write goes to the build path, which is always a fresh file, so the old `DELETE FROM` lines go:

```python
def _write_graph(build_path, nodes_dict, edges_list) -> None:
    import uuid
    from datetime import datetime, timezone
    from scripts.graph_schema import GRAPH_SCHEMA_VERSION
    setup_graph_schema(build_path)
    conn = sqlite3.connect(build_path)
    try:
        cursor = conn.cursor()
        for n in nodes_dict.values():
            cursor.execute("""
                INSERT INTO nodes (node_id, node_type, title, path, slug, content_hash, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (n['node_id'], n['node_type'], n['title'], n['path'], n['slug'], n['content_hash'], n['tags'], n['created_at'], n['updated_at']))
        for e in edges_list:
            cursor.execute("""
                INSERT INTO edges (edge_id, from_node_id, to_node_id, edge_type, evidence, source_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (e['edge_id'], e['from_node_id'], e['to_node_id'], e['edge_type'], e['evidence'], e['source_path'], e['created_at']))
        cursor.executemany("INSERT INTO graph_meta (key, value) VALUES (?, ?)", [
            ("schema_version", str(GRAPH_SCHEMA_VERSION)),
            ("graph_generation_id", uuid.uuid4().hex),
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("node_count", str(len(nodes_dict))),
            ("edge_count", str(len(edges_list))),
        ])
        conn.commit()
    finally:
        conn.close()


def build_graph(db_path: str = DB_PATH):
    """Build the graph beside the canonical file and publish it atomically.

    Until 2026-09-28 this DELETEd and re-INSERTed into the canonical file, so
    a reader mid-build saw a partial graph and a hardlink to it was illegal.
    """
    from scripts import sqlite_publish as sp
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    live = sp.sweep_abandoned_builds(db_path)
    if live:
        raise RuntimeError(
            f"another graph build is already running (pid {sorted(live)}) for "
            f"{os.path.basename(db_path)}. Wait for it, or kill it and remove "
            "its .building-<pid> file.")
    build_path = sp.build_path_for(db_path)
    sp.discard_build(build_path)
    try:
        nodes_dict, edges_list = _collect_graph()
        _write_graph(build_path, nodes_dict, edges_list)
        sp.publish(build_path, db_path, integrity=True)
    finally:
        sp.discard_build(build_path)   # absent after a successful replace

    print(f"Graph built successfully.")
    print(f"Nodes: {len(nodes_dict)}")
    print(f"Edges: {len(edges_list)}")
```

`build_graph` no longer calls `setup_graph_schema(db_path)` on the canonical path: that call opened the published file read-write.

In `scripts/measurement_snapshot.py`, `capture_graph`'s docstring, replace the sentence beginning "`scripts/graph_build.py:186` mutates the canonical inode in place" through "is what makes capturing it legal at all" with:

```python
    """Freeze the graph by CLONE, under a read lock.

    Since 2026-09-28 the graph is published atomically and read-only (Z0 step
    4), so a hardlink would now be legal too. The clone stays: it costs 0.33 ms
    on the 13 MB graph, and a capture path that already works is not changed
    inside the step that made the alternative possible.
    """
```

- [ ] **Step 5: Run the graph tests, including the existing ones**

Run: `PYTHONPATH=. python -m pytest -q -p no:cacheprovider tests/test_graph_publication.py tests/test_graph_build.py tests/test_snapshot_components.py`
Expected: all pass. `test_build_graph_excludes_wiki_traces` still passes: it passes a `db_path` under `tmp_path`, so its build file is a sibling there.

- [ ] **Step 6: Commit**

```bash
git add scripts/graph_schema.py scripts/graph_build.py scripts/measurement_snapshot.py tests/test_graph_publication.py
git commit -m "zurvan: the graph is built aside, stamped inside, and published atomically"
```

---

## Task 4 — Falsification: every mechanism, removed, turns its tests red

A test that passed on first run has proved nothing. Each mechanism is removed in turn with a pytest plugin in the session scratchpad — never an edit to the repo — and the named tests must go red. A falsification whose failure is a harness explosion (`TypeError`, `ImportError`) is **inconclusive** and must be redone with a real behavioural divergence (2026-09-19c).

**Files:** scratchpad only.

- [ ] **Step 1: Write the four falsification plugins**

```python
# falsify_no_chmod.py — published files stay writable
def pytest_configure(config):
    from scripts import sqlite_publish as sp
    sp._chmod = lambda path, mode: None

# falsify_no_selfcontained.py — sidecars and WAL are accepted
def pytest_configure(config):
    from scripts import sqlite_publish as sp
    sp.assert_publishable = lambda build_path, integrity=True: None

# falsify_swap_first.py — the canonical file is replaced before validation
def pytest_configure(config):
    from scripts import sqlite_publish as sp
    real = sp.assert_publishable
    def publish(build_path, canonical, integrity=True):
        sp._replace(build_path, canonical)
        real(canonical, integrity=integrity)
    sp.publish = publish

# falsify_no_fsync.py — durability steps skipped
def pytest_configure(config):
    from scripts import sqlite_publish as sp
    sp._fsync_file = lambda path: None
    sp._fsync_dir = lambda path: None
```

- [ ] **Step 2: Run each against the three new test files and record what went red**

```bash
S=<scratchpad>
for p in falsify_no_chmod falsify_no_selfcontained falsify_swap_first falsify_no_fsync; do
  echo "== $p"
  PYTHONPATH=.:$S python -m pytest -q -p no:cacheprovider -p $p \
    tests/test_sqlite_publish.py tests/test_graph_publication.py tests/test_snapshot_components.py \
    2>&1 | grep -E '^FAILED|passed|failed' | sed 's/ - .*//'
done
```

Required:

| Plugin | Must turn red |
|---|---|
| `falsify_no_chmod` | every `..._fails_loudly` test, `test_a_published_store_is_read_only`, `test_the_first_build_publishes_where_nothing_existed` |
| `falsify_no_selfcontained` | the three sidecar cases, `test_a_wal_mode_build_is_not_published`, `test_a_corrupt_build_is_not_published` |
| `falsify_swap_first` | the sidecar, WAL and corrupt cases (the canonical bytes change before the refusal) |
| `falsify_no_fsync` | `test_fsync_falls_back_where_full_fsync_does_not_exist`, `test_full_fsync_is_used_where_it_exists`, `test_the_directory_is_fsynced` — and NOT `test_the_protocol_runs_in_order`, which checks order, not effect |

What these prove is that the OS primitive is called, never that bytes survived a power cut; durability itself is not observable here. Record it that way. (The gauntlet's dry run found the first draft of this table claiming the order test would catch a missing fsync. It does not.)

- [ ] **Step 3: Record the results table in the task's commit message and move on**

```bash
git commit --allow-empty -m "zurvan: step 4 falsification - <the table from Step 2>"
```

---

## Task 5 — Migrate the live stores and prove equivalence on the frozen corpus

**Files:** none in the repo. Reads Task 0's measurements.

- [ ] **Step 1: Build both stores from the frozen corpus with the NEW code and compare**

```bash
M=~/.zurvan/measurements/z0-step4
PYTHONPATH=. python - <<'EOF'
import os
from pathlib import Path
M = Path(os.path.expanduser("~/.zurvan/measurements/z0-step4"))
import scripts.graph_build as gb
gb.PROJECT_ROOT = str(M / "frozen")
gb.build_graph(str(M / "after-graph.sqlite"))
EOF
ZURVAN_EMBED_PROVIDER=mock ZURVAN_EMBED_MODEL=mock_model PYTHONPATH=. python -c "
import os; from scripts.rebuild_search_index import rebuild_search_index
M=os.path.expanduser('~/.zurvan/measurements/z0-step4')
rebuild_search_index(M+'/after-search.sqlite', root=M+'/frozen')"
PYTHONPATH=. python $M/digest.py graph  $M/after-graph.sqlite  > $M/after-graph.digest
PYTHONPATH=. python $M/digest.py search $M/after-search.sqlite > $M/after-search.digest
diff $M/before-graph.digest  $M/after-graph.digest  && echo "graph: IDENTICAL"
diff $M/before-search.digest $M/after-search.digest && echo "search: IDENTICAL"
PYTHONPATH=. python -c "from scripts.graph_schema import content_digest; print(content_digest('$M/after-graph.sqlite'))"
```

Required: both `IDENTICAL`, and `content_digest` equal to the hash in `after-graph.digest`. Any difference is a ranking-relevant change and stops the step (design §11).

- [ ] **Step 2: Republish the live graph through the new path (the index was republished in Task 2)**

```bash
pgrep -f "rebuild_search_index|graph_build|check.sh" && echo "BUSY - stop" || echo clear
scripts/zurvan graph rebuild
stat -f '%Sp %N' data/graph.sqlite data/search.sqlite      # both -r--r--r--
PYTHONPATH=. python -c "from scripts.graph_schema import read_graph_meta; print(read_graph_meta('data/graph.sqlite'))"
ls data | grep -E 'building|journal|-wal|-shm' || echo "no sidecars"
```

- [ ] **Step 3: Measure what publication costs on the live stores**

```bash
PYTHONPATH=. python - <<'EOF'
import os, time, shutil, tempfile
from scripts import sqlite_publish as sp
for name in ("graph.sqlite", "search.sqlite"):
    src = os.path.join("data", name)
    d = tempfile.mkdtemp(dir="data")
    canonical = os.path.join(d, name)
    times = []
    for _ in range(3):
        build = sp.build_path_for(canonical)
        shutil.copyfile(src, build)
        t = time.perf_counter()
        sp.publish(build, canonical, integrity=(name == "graph.sqlite"))
        times.append((time.perf_counter() - t) * 1000)
    print(name, [f"{x:.0f} ms" for x in times])
    shutil.rmtree(d)
EOF
```

Report the numbers whatever they are. The graph pays `integrity_check`; the search index does not, because `check_index_health(deep=True)` already validated it.

- [ ] **Step 4: Run the eval on the republished index and report it as what it is**

```bash
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6 2>&1 | grep -E 'Top-1|Top-3|MRR|chunk-set'
```

If the chunk-set equals Task 0's, the scores must be byte-identical. If the corpus moved in between (other sessions write), the scores are **not comparable** and are reported as such; the equivalence claim rests on Step 1, which is.

```text
equivalence (frozen corpus, old vs new code)   Step 1 — the claim
live eval after republish                     Step 4 — context only
```

---

## Task 6 — Gate, record, and write back

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md` (§4.2, §4.4, §10.3 items 14-15)
- Modify: `CLAUDE.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`

- [ ] **Step 1: Full gate, with an independent check of the live wiki**

```bash
pgrep -f "rebuild_search_index|graph_build" && echo "BUSY - stop" || echo clear
ls wiki/*.md | xargs shasum > <scratchpad>/wiki_before.sha
bash scripts/check.sh; echo "EXIT=$?"
ls wiki/*.md | xargs shasum > <scratchpad>/wiki_after.sha
diff <scratchpad>/wiki_before.sha <scratchpad>/wiki_after.sha && echo "wiki byte-identical"
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

If the wiki changed, attribute it from the ledger and the write log before saying anything (2026-09-28: it was another project's `question add`, correctly classified as external).

- [ ] **Step 2: Record closure in the design, with the bound**

Under §10.3 in the design, after item 15:

```markdown
**Status 2026-09-28 (Z0 step 4).** Items 14 and 15 CLOSED.

- 14: published stores are mode 0444; an in-place SQLite write raises
  `attempt to write a readonly database` and a byte write raises
  `PermissionError`. Bound: the owner can chmod it back and root ignores it.
  This stops an accidental in-place write; it is not a security boundary.
  `capture_search` refuses to hardlink a writable index.
- 15: `sqlite_publish.assert_publishable` refuses any sidecar, any
  journal_mode other than `delete`, and (graph) any `integrity_check` result
  other than `ok`, before the swap.

Still NOT BUILT: `built_from_revision`, `built_from_snapshot`,
snapshot-backed rebuilds (step 5); coherence verification (step 6). Z0 OPEN.
```

- [ ] **Step 3: Change-protocol entries and write-back**

Dated `Raouf:` entry in `AGENTS.md` and `CHANGELOG.md` (scope / summary / files / verification / follow-ups), moving the 2026-09-24 entry verbatim to `docs/agents-history.md`. Then, **after** every measurement above:

```bash
scripts/zurvan decision add --project zurvan --title "Zurvan: derived SQLite stores are published read-only through one protocol" \
  --reason "Z0 step 4. build aside, validate, fsync, chmod 0444, os.replace, fsync dir. Closes gates 14 and 15; 0444 stops accidental in-place writes and is not a security boundary." \
  --status accepted --tags zurvan z0
scripts/zurvan index search && scripts/zurvan graph rebuild
```

---

## What this step does NOT close

```text
gate 14 CLOSED    bound: chmod-reversible by the owner, ignored by root
gate 15 CLOSED
gate 16 CLOSED    (step 3)
built_from_revision / built_from_snapshot       NOT BUILT (step 5)
snapshot-backed rebuilds, CURRENT/STALE          NOT BUILT (step 5)
coherence verification, leases, GC               NOT BUILT (step 6)
hand edits invisible between reconciles          OPEN — separate change, before Z0 closes
graph readers open read-write                    OPEN — reader change, not publication
edge_id is uuid4() per build                     OPEN — a rebuild renames every edge; any
                                                 consumer that caches edge ids is wrong
                                                 across generations. Content-addressed ids
                                                 are a format change: step 5 or Z1, filed
sweep liveness by bare pid (reuse race)          OPEN — inherited; pid+start-time identity
Z0 OPEN
```
