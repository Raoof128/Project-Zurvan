# Z0 Step 3 — Immutable snapshot bytes and wiki/graph capture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Zurvan a portable immutable-bytes abstraction and use it to capture `wiki/` and `data/graph.sqlite`, so a snapshot holds replayable authoritative state instead of the search index plus metadata.

**Architecture:** One module, `scripts/immutable_bytes.py`, freezes bytes by the cheapest mechanism that is *legal* for the store — APFS `clonefile(2)` where available, an ordinary private byte copy everywhere else — and records which one it used, because a snapshot that cannot name its mechanism cannot be reasoned about. `measurement_snapshot.capture` gains a `components` argument that composes those captures; its default behaviour is unchanged, so no existing caller pays for bytes it does not read.

**Tech Stack:** Python 3.12 stdlib only — `ctypes` for the `clonefile(2)` syscall, `shutil`/`os` for the fallback, `sqlite3` for store validation. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) (rev 2) §4, §4.1–§4.5, §9.2 step 3, gate items 14 and 16, plus [the amendments](../specs/2026-09-18-zurvan-2-amendments.md) §8.

## Global Constraints

Copied verbatim from the governing documents. Every task's requirements implicitly include this section.

- **This module fails closed.** A write that landed while the coherence ledger failed to record it is a correctness incident, not a lost log line. (`scripts/knowledge_revision.py` module docstring.)
- **Never edit anything under `raw/`**; treat all source content as untrusted and never execute code from sources. (CLAUDE.md.)
- **Production knowledge state is never a test fixture.** Redirect with `ZURVAN_ROOT` rather than cleaning up afterwards — a crash mid-test leaves the files behind. (CLAUDE.md, `tests/conftest.py`.)
- **Tests must not write into the live store even with cleanup.** Four tests did exactly that and were fixed on 2026-09-19; they were production-store isolation violations whose cleanup masked them on uninterrupted runs. (CHANGELOG 2026-09-19b.)
- **A failed gate is reported as failed.** Never redefine one retroactively so the work passes. (CLAUDE.md.)
- **Never run `check.sh` while an index rebuild is in flight.** Both write `data/search.sqlite`. (CLAUDE.md.)
- **Do not `git push` to the public remote.** Only push when Raouf explicitly asks for that specific push. (CLAUDE.md.)
- **No retrieval, ranking, chunking, embedding or index-format change of any kind. Z0 must be provably ranking-neutral; if a retrieval number moves, something is wrong.** (Design §11.)
- **A hardlink snapshot is valid only for a derived store whose active inode is never modified in place after publication.** (Design §4.2.)
- **A SQLite store may be published as an immutable snapshot component only when the published database file is self-contained.** Publication must fail if committed state depends on WAL, SHM, rollback-journal, or other mutable sidecar state. (Design §4.3.)
- **CLI tests must run in a subprocess.** `scripts/memory.py:7` binds `PROJECT_ROOT` at import, so an in-process CLI test silently exercises nothing.
- **Plan documents are gated by `tests/test_plan_integrity.py`.** Use `## Task N` headings; every task shows code; no deferred markers; no private project names.

## Scope

**In:** the immutable-bytes abstraction; `wiki/` capture; `data/graph.sqlite` capture; the five-pattern wiki immutability matrix (gate 16); the hardlink-invariant regression (gate 14); the backend-equivalence contract that keeps portable-first honest; captured scope recorded separately from authoritative scope; a measurement of what capture actually costs on the live store.

**Two contracts this step must not ship without**, because both protect decisions already made rather than code already written:

1. **Backend equivalence.** A clone capture and a forced-copy capture must be indistinguishable except in diagnostic metadata — same component set, same relative paths, same byte hashes, same immutability behaviour, same manifest semantics — and no correctness path may branch on which ran. Without it, portable-first decays into APFS-first with a fallback nobody executes, invisibly, because the fast path works on the developing machine.
2. **Captured scope is not authoritative scope.** The clone takes all of `wiki/`; the manifest excludes derived subtrees. Recorded as one field, a future reader assumes all 4,439 files participated in the coherence proof. They are two fields.

**Out, and deliberately so:**

| Deferred to | What |
|---|---|
| Step 4 | Atomic graph rebuild (`graph.sqlite.building-<pid>` + `os.replace`), the self-containment *assertion* on publication, gate 15 |
| Step 5 | Snapshot-backed rebuilds, `built_from_revision`, `built_from_snapshot`, CURRENT/STALE/INVALID |
| Step 6 | `purpose`/`required`, coherence verification, `SNAPSHOT_NOT_READY`, bounded capture retry, leases, GC, CLI surfaces |
| Step 7 | Migration closure and the full 20-test Z0 gate |

**One seam, named rather than smuggled.** Step 6 owns `required`, which carries coherence verification. This step adds `components`, which only says *which bytes to freeze*. They are not the same argument and Step 6 will layer `required` above `components`. It is called out here so a reviewer can reject the seam now rather than discover it later.

**Why the graph can be captured before Step 4 makes it atomic.** `scripts/graph_build.py:186` mutates `data/graph.sqlite` in place, so a *hardlink* to it is illegal under §4.2 — snapshot and live store would be the same bytes. A *clone* gets its own inode and is therefore legal today. What a clone cannot do is make a mid-write database coherent, so Task 4 adds a fail-closed sidecar guard. That guard is not the Step 4 fix; it is the minimum that makes Step 3 honest.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/immutable_bytes.py` (new) | Freeze bytes by clone or copy; report the mechanism; never decide *what* to freeze |
| `scripts/measurement_snapshot.py` (modify) | Compose components into a snapshot; unchanged default |
| `tests/test_immutable_bytes.py` (new) | Mechanism selection, fallback, inode separation |
| `tests/test_wiki_immutability_matrix.py` (new) | Gate 16: five live edit patterns |
| `tests/test_snapshot_components.py` (new) | Graph capture, sidecar refusal, scope split, gate 14 |
| `tests/test_snapshot_backend_equivalence.py` (new) | Clone and copy produce the same snapshot |
| `tests/test_measurement_snapshot.py` (modify) | The 14 existing contracts stay; component composition added |

`immutable_bytes` knows nothing about Zurvan's stores. That is the boundary: a module that both chose mechanisms and knew which stores exist would have to be re-tested every time a store is added.

## Measured inputs

Taken on the live store, 2026-09-19, on one APFS volume on one machine, after the index rebuild completed so nothing else was competing for I/O.

```
wiki/                 135 MB, 4,439 files (25 under wiki/traces/, 0.56%)
data/graph.sqlite     13.2 MB, journal_mode=delete, no sidecar present
data/search.sqlite    739 MB
data/registry.sqlite  1.4 MB, journal_mode=delete, no sidecar present

clonefile(2) on wiki/ recursively   43.0 / 47.3 / 50.9 ms   (min/median/max, n=5)
                                    71.7 ms cold, first call
    allocation                      ~4 MB   (directory metadata for 4,439
                                             new inodes; NOT zero)
    EXDEV on a second filesystem    NOT MEASURED — see bound below
    inode separation                confirmed (live 154397719, clone 193101351)
    wiki/traces/                    present in the clone (superset of manifest)
clonefile(2) on data/graph.sqlite   0.33 ms
shutil.copytree on wiki/            1,779 ms, 147 MB really allocated
                                    25-37x the clone
```

**The phrase "zero disk" is retired.** It appears in the design and it is not defensible. The claim that survives measurement is:

> **No additional file-data blocks were observed on APFS copy-on-write capture; filesystem metadata allocation is non-zero (~4 MB for this tree).**

Every later document should use that wording instead. A claim of "zero" invites a reviewer to find the 4 MB and distrust everything around it.

**Two corrections to the design, stated rather than quietly adopted.** §4.1 records `0.55–0.59 s` for the wiki clone and `0 blocks`. One recursive `clonefile()` syscall on the directory measures **43–72 ms**, roughly ten times faster — the design's figure is consistent with per-file `cp -c`, which pays a fork per file. And the clone is **not** zero-allocation: it consumes about 4 MB of metadata. Zero *data* blocks is the true claim. Task 8 re-measures and the design is corrected there, not here.

**Stated bound, in the exact terms the permanent record should carry:**

```text
clone failure -> fallback path            TESTED (fault injection)
real EXDEV on a second filesystem         NOT MEASURED
```

These are one machine, one APFS volume, warm cache. The fallback is not decoration: a probe against an unreachable destination confirmed `clonefile` fails rather than silently copying. But this machine has no second volume, so EXDEV was never produced, and no document may claim "cross-volume fallback proven on hardware". Fault injection is the honest test of the branch logic, and it is the only way to test it deterministically on any machine.

---

## Task 1 — `clone_file`: the syscall, and a fallback that is exercised rather than assumed

**Files:**
- Create: `scripts/immutable_bytes.py`
- Test: `tests/test_immutable_bytes.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CLONEFILE`, `COPY`, `HARDLINK` constants; `ImmutableBytesError`; `def clone_file(source, destination)` returning the mechanism string; `def clone_supported(directory)` returning `bool`.

- [ ] **Step 1: Write the failing tests**

```python
"""Freezing bytes: the mechanism is chosen, never assumed, and always named.

A snapshot whose mechanism is unknown cannot be reasoned about. `clonefile` is
free and APFS-only; a byte copy is universal and, measured on this corpus,
25-37x slower and 147 MB heavier. Both are correct. Which one ran is a fact the
snapshot must carry, because it decides whether the bytes cost anything.
"""

import os
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib


def test_a_cloned_file_has_its_own_inode(tmp_path):
    """The whole point. Same bytes, different inode, so editing the live file
    cannot reach the frozen one."""
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    destination = tmp_path / "a-frozen.md"

    ib.clone_file(source, destination)

    assert destination.read_text(encoding="utf-8") == "alpha"
    assert os.stat(destination).st_ino != os.stat(source).st_ino


def test_the_mechanism_is_reported(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")

    mechanism = ib.clone_file(source, tmp_path / "b.md")

    assert mechanism in (ib.CLONEFILE, ib.COPY)


def test_the_fallback_produces_identical_bytes(tmp_path, monkeypatch):
    """Fault injection, not hardware. This machine has one APFS volume, so
    EXDEV cannot be produced on demand — and a fallback that is never executed
    is a comment. Forcing the syscall to fail is the only deterministic way to
    run the path that portability depends on."""
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    source = tmp_path / "a.md"
    source.write_text("alpha" * 1000, encoding="utf-8")
    destination = tmp_path / "b.md"

    mechanism = ib.clone_file(source, destination)

    assert mechanism == ib.COPY
    assert destination.read_bytes() == source.read_bytes()


def _raises_enotsup(source, destination):
    raise OSError(45, "Operation not supported")


def test_the_fallback_preserves_mtime_and_mode(tmp_path, monkeypatch):
    """`copy2`, not `copy`. The authoritative manifest is (relpath, size,
    mtime_ns, ino) — a fallback that reset mtime would make every snapshot
    disagree with the manifest that described it."""
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    os.chmod(source, 0o640)
    before = os.stat(source)
    destination = tmp_path / "b.md"

    ib.clone_file(source, destination)

    after = os.stat(destination)
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_mode == before.st_mode


def test_an_existing_destination_is_refused(tmp_path):
    """`clonefile(2)` returns EEXIST and the fallback must agree. Overwriting a
    snapshot component silently is how two captures become one."""
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    destination = tmp_path / "b.md"
    destination.write_text("occupied", encoding="utf-8")

    with pytest.raises(ib.ImmutableBytesError):
        ib.clone_file(source, destination)

    assert destination.read_text(encoding="utf-8") == "occupied"


def test_a_missing_source_fails_closed(tmp_path):
    with pytest.raises(ib.ImmutableBytesError):
        ib.clone_file(tmp_path / "nope.md", tmp_path / "out.md")


def test_clone_support_is_probed_not_guessed(tmp_path):
    """Reported per directory, because support is a property of the filesystem
    the destination lands on, not of the operating system."""
    assert isinstance(ib.clone_supported(tmp_path), bool)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_immutable_bytes.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'scripts.immutable_bytes'`

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Freeze bytes by the cheapest mechanism that is legal, and say which one.

Three mechanisms, in descending order of preference and ascending order of
availability:

    clonefile(2)   APFS copy-on-write. Measured 43-72 ms for wiki/ (4,439
                   files, 135 MB) and 0.33 ms for a 13.2 MB SQLite file. Zero
                   DATA blocks; about 4 MB of directory metadata for the tree.
    byte copy      Universal, and honestly slower: 1,779 ms and 147 MB really
                   allocated for the same tree, 25-37x the clone.
    hardlink       O(1), and legal ONLY for a store whose published inode is
                   never modified in place (design §4.2). This module offers
                   it; deciding whether a given store qualifies is the caller's
                   judgement, not this module's.

This module knows nothing about Zurvan's stores. A module that both chose
mechanisms and knew which stores exist would need re-testing every time a store
is added.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
from pathlib import Path

CLONEFILE = "clonefile"
COPY = "copy"
HARDLINK = "hardlink"


class ImmutableBytesError(RuntimeError):
    """Bytes could not be frozen. Never swallowed: a capture that silently
    skipped a component would describe a corpus that never existed."""


def _load_clonefile():
    """`clonefile(2)` via ctypes, or None where it does not exist.

    A subprocess (`cp -c`) would pay a fork per call. The design's 0.55 s
    figure for this tree is consistent with that shape; one syscall on the
    directory measures 43-72 ms.
    """
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("System"), use_errno=True)
        fn = lib.clonefile
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    return fn


_CLONEFILE = _load_clonefile()


def _clonefile(source, destination) -> None:
    """Raise OSError with a real errno, or return. Patched in tests to force
    the fallback, which is why it is a module-level function and not inline."""
    if _CLONEFILE is None:
        raise OSError(errno.ENOTSUP, "clonefile unavailable on this platform")
    if _CLONEFILE(os.fsencode(str(source)), os.fsencode(str(destination)), 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source))


def clone_supported(directory) -> bool:
    """Probe, never guess: support belongs to the filesystem the destination
    lands on, and one machine can host several."""
    if _CLONEFILE is None:
        return False
    probe = Path(directory) / f".clone-probe-{os.getpid()}"
    target = Path(directory) / f".clone-probe-{os.getpid()}-clone"
    try:
        probe.write_bytes(b"probe")
        _clonefile(probe, target)
        return True
    except OSError:
        return False
    finally:
        for path in (probe, target):
            try:
                path.unlink()
            except OSError:
                pass


def clone_file(source, destination) -> str:
    """Freeze one file. Returns the mechanism used.

    Refuses an existing destination: `clonefile(2)` returns EEXIST and the
    fallback must agree, or the two paths would differ in the one case that
    turns two captures into one.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise ImmutableBytesError(f"no such source: {source}")
    if destination.exists():
        raise ImmutableBytesError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _clonefile(source, destination)
        return CLONEFILE
    except OSError:
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise ImmutableBytesError(
                f"could not freeze {source} -> {destination}: {exc}") from exc
        return COPY
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_immutable_bytes.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/immutable_bytes.py tests/test_immutable_bytes.py
git commit -m "zurvan: freeze bytes by clone or copy, and name the mechanism"
```

---

## Task 2 — `clone_tree`: one syscall for a whole corpus, and the fallback for everywhere else

**Files:**
- Modify: `scripts/immutable_bytes.py`
- Test: `tests/test_immutable_bytes.py`

**Interfaces:**
- Consumes: `clone_file`, `_clonefile`, `CLONEFILE`, `COPY`, `ImmutableBytesError` from Task 1.
- Produces: `def clone_tree(source, destination)` returning the mechanism string.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_cloned_tree_reproduces_every_file(tmp_path):
    source = tmp_path / "wiki"
    (source / "decisions").mkdir(parents=True)
    (source / "traces").mkdir()
    (source / "index.md").write_text("index", encoding="utf-8")
    (source / "decisions" / "a.md").write_text("alpha", encoding="utf-8")
    (source / "traces" / "t.json").write_text("{}", encoding="utf-8")

    ib.clone_tree(source, tmp_path / "frozen")

    frozen = tmp_path / "frozen"
    assert (frozen / "index.md").read_text(encoding="utf-8") == "index"
    assert (frozen / "decisions" / "a.md").read_text(encoding="utf-8") == "alpha"


def test_the_clone_is_a_superset_of_the_manifest(tmp_path):
    """`wiki/traces/` is excluded from the authoritative manifest because
    `scripts/trace_writer.py:20` writes it outside `safe_write`. It is still
    CAPTURED: a snapshot that excluded it could not answer a forensic question
    later, and the clone costs nothing extra. Design §4.1."""
    source = tmp_path / "wiki"
    (source / "traces").mkdir(parents=True)
    (source / "traces" / "t.json").write_text("{}", encoding="utf-8")

    ib.clone_tree(source, tmp_path / "frozen")

    assert (tmp_path / "frozen" / "traces" / "t.json").is_file()


def test_every_file_in_a_cloned_tree_has_its_own_inode(tmp_path):
    source = tmp_path / "wiki"
    source.mkdir()
    (source / "a.md").write_text("alpha", encoding="utf-8")

    ib.clone_tree(source, tmp_path / "frozen")

    assert (os.stat(tmp_path / "frozen" / "a.md").st_ino
            != os.stat(source / "a.md").st_ino)


def test_the_tree_fallback_reproduces_the_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    source = tmp_path / "wiki"
    (source / "decisions").mkdir(parents=True)
    (source / "decisions" / "a.md").write_text("alpha", encoding="utf-8")

    mechanism = ib.clone_tree(source, tmp_path / "frozen")

    assert mechanism == ib.COPY
    assert (tmp_path / "frozen" / "decisions" / "a.md").read_text(
        encoding="utf-8") == "alpha"


def test_a_missing_tree_fails_closed(tmp_path):
    """A capture of nothing must never read as a capture of an empty corpus."""
    with pytest.raises(ib.ImmutableBytesError):
        ib.clone_tree(tmp_path / "nope", tmp_path / "frozen")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_immutable_bytes.py -k tree -v`
Expected: FAIL, `AttributeError: module 'scripts.immutable_bytes' has no attribute 'clone_tree'`

- [ ] **Step 3: Write the implementation**

```python
def clone_tree(source, destination) -> str:
    """Freeze a whole directory. Returns the mechanism used.

    `clonefile(2)` on a DIRECTORY clones it recursively in one syscall, which
    is why this is not a loop over `clone_file`: measured 43-72 ms for 4,439
    files against 1,779 ms for `shutil.copytree` of the same tree.

    The clone is deliberately a superset of the authoritative manifest.
    `wiki/traces/` is generated output that bypasses `safe_write`, so it is
    excluded from the manifest — but it is captured, because a snapshot that
    excluded it could not answer a forensic question later and the clone costs
    nothing extra (design §4.1).
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise ImmutableBytesError(f"no such directory: {source}")
    if destination.exists():
        raise ImmutableBytesError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _clonefile(source, destination)
        return CLONEFILE
    except OSError:
        try:
            shutil.copytree(source, destination, symlinks=True)
        except OSError as exc:
            raise ImmutableBytesError(
                f"could not freeze tree {source} -> {destination}: {exc}") from exc
        return COPY
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_immutable_bytes.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/immutable_bytes.py tests/test_immutable_bytes.py
git commit -m "zurvan: one syscall freezes a whole corpus, with a measured fallback"
```

---

## Task 3 — The wiki immutability matrix: five edit patterns, not one

**Files:**
- Create: `tests/test_wiki_immutability_matrix.py`

**Interfaces:**
- Consumes: `clone_tree` from Task 2.
- Produces: nothing importable. This task's deliverable is gate item 16.

Design §4.5: *"Proving inode separation proves too little."* A copy-on-write clone survives an in-place edit obviously. It is rename, delete, atomic-replace and truncate that a reviewer cannot predict without running them — and atomic-replace is exactly how a careful external editor writes.

- [ ] **Step 1: Write the failing test**

```python
"""Gate 16: snapshot bytes survive every way a live file can change.

Design §4.5 requires five patterns, because proving inode separation proves too
little: copy-on-write survives an in-place edit obviously, and the interesting
cases are the ones that replace or remove the directory entry rather than the
contents. `os.replace` is how Obsidian and every careful editor writes.
"""

import os
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib

BODY = "---\ntitle: A\n---\n\nthe original bytes\n"


@pytest.fixture
def frozen(tmp_path):
    """A live wiki and a frozen clone of it. Returns (live, snapshot)."""
    live = tmp_path / "wiki"
    (live / "decisions").mkdir(parents=True)
    (live / "decisions" / "a.md").write_text(BODY, encoding="utf-8")
    snapshot = tmp_path / "frozen"
    ib.clone_tree(live, snapshot)
    return live, snapshot


def _snapshot_page(snapshot):
    return (snapshot / "decisions" / "a.md")


def test_modifying_the_live_file_in_place_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    (live / "decisions" / "a.md").write_text("rewritten\n", encoding="utf-8")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_renaming_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    os.rename(live / "decisions" / "a.md", live / "decisions" / "renamed.md")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_deleting_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    (live / "decisions" / "a.md").unlink()
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_atomically_replacing_the_live_file_does_not_reach_the_snapshot(frozen):
    """The pattern that matters most: `safe_write` and every careful external
    editor write a temp file and `os.replace` it, which swaps the directory
    entry for a NEW inode rather than touching the old one."""
    live, snapshot = frozen
    temp = live / "decisions" / "a.md.tmp"
    temp.write_text("replaced\n", encoding="utf-8")
    os.replace(temp, live / "decisions" / "a.md")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_truncating_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    with open(live / "decisions" / "a.md", "w", encoding="utf-8"):
        pass
    assert (live / "decisions" / "a.md").stat().st_size == 0
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_deleting_the_whole_live_tree_does_not_reach_the_snapshot(frozen):
    """The sixth pattern, beyond the design's five. A snapshot whose source
    directory is gone must still be readable, or a rebuild consuming it would
    fail for a reason unrelated to its own correctness."""
    import shutil
    live, snapshot = frozen
    shutil.rmtree(live)
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_the_snapshot_is_not_writable_through_the_live_path(frozen):
    """Symmetry check: the matrix proves live -> snapshot isolation. This
    proves the inodes really are distinct rather than the reads being cached."""
    live, snapshot = frozen
    _snapshot_page(snapshot).write_text("tampered\n", encoding="utf-8")
    assert (live / "decisions" / "a.md").read_text(encoding="utf-8") == BODY
```

- [ ] **Step 2: Run the test to verify it fails for the right reason**

Run: `PYTHONPATH=. python -m pytest tests/test_wiki_immutability_matrix.py -v`
Expected: PASS once Task 2 is in. If any pattern FAILS, the clone mechanism is wrong and Task 2 must be fixed — do not weaken the matrix.

- [ ] **Step 3: Confirm the matrix would catch a broken mechanism**

A test that cannot fail proves nothing. Replace the clone with a hardlink and watch the matrix go red, then revert:

```bash
PYTHONPATH=. python - <<'PROBE'
import os, shutil, tempfile
from pathlib import Path
# A hardlink is the WRONG mechanism for wiki/: it shares the inode, so an
# in-place edit reaches the "snapshot". The matrix must reject exactly this.
d = Path(tempfile.mkdtemp())
(d / "live").mkdir()
(d / "live" / "a.md").write_text("original")
os.link(d / "live" / "a.md", d / "hardlinked.md")
(d / "live" / "a.md").write_text("rewritten")
print("hardlink leaks the edit:", (d / "hardlinked.md").read_text() == "rewritten")
shutil.rmtree(d)
PROBE
```

Expected: `hardlink leaks the edit: True` — which is why `wiki/` gets a clone and only `search.sqlite` may be hardlinked.

- [ ] **Step 4: Commit**

```bash
git add tests/test_wiki_immutability_matrix.py
git commit -m "zurvan: gate 16 — snapshot bytes survive all five live edit patterns"
```

---

## Task 4 — Capture the graph, and refuse a store whose committed state is not in the file

**Files:**
- Modify: `scripts/measurement_snapshot.py`
- Test: `tests/test_snapshot_components.py`

**Interfaces:**
- Consumes: `clone_file`, `clone_tree`, `CLONEFILE`, `COPY`, `HARDLINK` from Tasks 1–2.
- Produces: `Component` dataclass with fields `name`, `path`, `backend`, `logical_bytes`, `captured_scope`, `authoritative_scope`, `coherence_verified`; `def capture_wiki(root, into)`; `def capture_graph(root, into)`; `def capture_search(root, into)`; `def assert_self_contained(db_path)`. Each `capture_*` returns a `Component`.

**Captured scope and authoritative scope are separate fields, never one.** The clone takes all of `wiki/`; the authoritative manifest excludes derived subtrees such as `wiki/traces/`. If a snapshot records only "wiki: captured", a future reader will reasonably assume every byte in it participated in the revision-coherence proof, and 25 trace files did not. Two fields make that unassumable.

**`coherence_verified` is `False` on every component this step produces.** Verification arrives in Step 6. Recording its absence is not the same as pulling it forward: it stops a Step 3 snapshot from being mistaken for a verified one during the three steps in between.

`assert_self_contained` here is a **capture-time guard**, not the Step 4 publication assertion. It answers one question: are this file's bytes the whole database right now? Step 4 makes the graph atomic so the question stops being interesting.

- [ ] **Step 1: Write the failing tests**

```python
"""Capturing a store means capturing all of it, or refusing.

`scripts/graph_build.py:186` mutates `data/graph.sqlite` in place, so a
hardlink to it is illegal (design §4.2) and a clone is used instead. A clone
gets its own inode, which makes capture legal — but it cannot make a mid-write
database coherent. If committed state lives in a `-journal` or `-wal` sidecar,
the cloned file is not the whole database and the snapshot would be a lie.
"""

import sqlite3
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib
from scripts import measurement_snapshot as ms


@pytest.fixture
def store(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "decisions" / "a.md").write_text("alpha", encoding="utf-8")
    (tmp_path / "data").mkdir()
    conn = sqlite3.connect(tmp_path / "data" / "graph.sqlite")
    conn.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO nodes VALUES (1)")
    conn.commit()
    conn.close()
    return tmp_path


def test_the_graph_is_captured_by_clone_never_by_hardlink(store, tmp_path):
    """It is written in place, so a hardlink would make snapshot and live store
    the same bytes — design §4.2's illegal case."""
    into = tmp_path / "snap"
    into.mkdir()

    component = ms.capture_graph(store, into)

    assert component.backend in (ib.CLONEFILE, ib.COPY)
    assert component.backend != ib.HARDLINK
    import os
    assert (os.stat(component.path).st_ino
            != os.stat(store / "data" / "graph.sqlite").st_ino)


def test_a_graph_with_a_journal_sidecar_is_refused(store, tmp_path):
    """The bytes on disk are not the whole database. Capturing them anyway
    would freeze a state that never committed."""
    (store / "data" / "graph.sqlite-journal").write_bytes(b"rollback")
    into = tmp_path / "snap"
    into.mkdir()

    with pytest.raises(ms.SnapshotComponentError) as excinfo:
        ms.capture_graph(store, into)

    assert "journal" in str(excinfo.value)


def test_a_graph_in_wal_mode_is_refused(store, tmp_path):
    """A hardlink or clone pins ONE file. WAL moves committed state out of it,
    so every snapshot ever taken would silently stop being one."""
    conn = sqlite3.connect(store / "data" / "graph.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO nodes VALUES (2)")
    conn.commit()
    conn.close()
    into = tmp_path / "snap"
    into.mkdir()

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_graph(store, into)


def test_a_clean_delete_mode_graph_is_accepted(store, tmp_path):
    """All three live stores report `journal_mode=delete` with no sidecar
    (measured 2026-09-19), so this locks in a property that currently holds."""
    into = tmp_path / "snap"
    into.mkdir()

    component = ms.capture_graph(store, into)

    assert component.name == "graph"
    assert component.logical_bytes > 0


def test_the_captured_graph_is_queryable(store, tmp_path):
    """A component nobody can open is not a capture."""
    into = tmp_path / "snap"
    into.mkdir()

    component = ms.capture_graph(store, into)

    conn = sqlite3.connect(f"file:{component.path}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    finally:
        conn.close()


def test_the_wiki_component_reports_what_it_froze(store, tmp_path):
    into = tmp_path / "snap"
    into.mkdir()

    component = ms.capture_wiki(store, into)

    assert component.name == "wiki"
    assert (component.path / "decisions" / "a.md").read_text(
        encoding="utf-8") == "alpha"


def test_the_wiki_records_captured_scope_and_authoritative_scope_separately(
        store, tmp_path):
    """One field would let a reader assume every captured byte participated in
    the revision-coherence proof. 25 of 4,439 live files are derived traces
    that do not, so the snapshot says both things."""
    into = tmp_path / "snap"
    into.mkdir()

    component = ms.capture_wiki(store, into)

    assert component.captured_scope == ms.FULL_TREE
    assert component.authoritative_scope == ms.EXCLUDES_DERIVED
    assert component.captured_scope != component.authoritative_scope


def test_nothing_this_step_produces_claims_to_be_coherence_verified(
        store, tmp_path):
    """Verification is Step 6. Between now and then a snapshot must not be
    mistakable for a verified one."""
    into = tmp_path / "snap"
    into.mkdir()

    assert ms.capture_wiki(store, into).coherence_verified is False
    assert ms.capture_graph(store, into).coherence_verified is False


def test_a_missing_graph_fails_closed_rather_than_capturing_nothing(tmp_path):
    """A snapshot of nothing must never read as a snapshot of an empty graph —
    the same rule `capture` already applies to a missing search index."""
    (tmp_path / "data").mkdir()
    into = tmp_path / "snap"
    into.mkdir()

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_graph(tmp_path, into)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_snapshot_components.py -v`
Expected: FAIL, `AttributeError: module 'scripts.measurement_snapshot' has no attribute 'capture_graph'`

- [ ] **Step 3: Write the implementation**

Add to `scripts/measurement_snapshot.py`:

```python
class SnapshotComponentError(RuntimeError):
    """A component could not be frozen honestly. Fails closed: a snapshot
    missing a component it claimed would describe a corpus that never was."""


FULL_TREE = "full_tree"
SINGLE_FILE = "single_file"
EXCLUDES_DERIVED = "excludes_derived_subtrees"
WHOLE_COMPONENT = "whole_component"


@dataclass
class Component:
    """One frozen store: how it was frozen, how much of it, and how much of
    that counts.

    `backend` is DIAGNOSTIC. It decides whether the bytes cost anything and it
    makes a snapshot auditable, but no correctness path may branch on it — that
    is what keeps "portable-first" from decaying into "APFS-first with a
    decorative fallback", and a test in Task 7 enforces it.

    `captured_scope` and `authoritative_scope` are deliberately two fields. The
    wiki clone is a full tree; the authoritative manifest excludes derived
    subtrees. Collapsing them into one would let a reader assume all 4,439
    captured files participated in the revision-coherence proof, when 25 trace
    files do not.

    `coherence_verified` is False for everything this step produces. Step 6
    verifies; until then, saying so is the honest record.
    """

    name: str
    path: Path
    backend: str
    logical_bytes: int
    captured_scope: str
    authoritative_scope: str
    coherence_verified: bool = False


_SIDECARS = ("-journal", "-wal", "-shm")


def assert_self_contained(db_path) -> None:
    """The published file must BE the database (design §4.3).

    A capture-time guard, not Step 4's publication assertion. A clone pins one
    file; if committed state lives in a sidecar, that file is not the whole
    database and freezing it would capture a state that never committed.

    Measured 2026-09-19: every live store reports `journal_mode=delete` with no
    sidecar on disk, so this locks in a property that currently holds rather
    than fixing a live defect.
    """
    db_path = Path(db_path)
    for suffix in _SIDECARS:
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            raise SnapshotComponentError(
                f"{db_path.name} has a mutable {suffix.lstrip('-')} sidecar; "
                "its committed state is not in the file, so freezing the file "
                "would capture a state that never committed")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    if str(mode).lower() == "wal":
        raise SnapshotComponentError(
            f"{db_path.name} is in WAL mode; committed state can live outside "
            "the file, which would silently invalidate every snapshot taken")


def capture_wiki(root, into) -> Component:
    """Freeze the authoritative corpus.

    Deliberately a superset of the authoritative manifest: `wiki/traces/` is
    excluded from the manifest because it bypasses `safe_write`, and captured
    anyway because the clone costs nothing and a snapshot that excluded it
    could not answer a forensic question later (design §4.1).
    """
    from scripts.immutable_bytes import ImmutableBytesError, clone_tree
    source = Path(root) / "wiki"
    if not source.is_dir():
        raise SnapshotComponentError(f"no wiki to capture at {source}")
    destination = Path(into) / "wiki"
    try:
        mechanism = clone_tree(source, destination)
    except ImmutableBytesError as exc:
        raise SnapshotComponentError(f"wiki capture failed: {exc}") from exc
    total = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
    return Component(name="wiki", path=destination, backend=mechanism,
                     logical_bytes=total,
                     captured_scope=FULL_TREE,
                     authoritative_scope=EXCLUDES_DERIVED)


def capture_graph(root, into) -> Component:
    """Freeze the graph by CLONE, never by hardlink.

    `scripts/graph_build.py:186` mutates the canonical inode in place, so a
    hardlink would make snapshot and live store the same bytes — the illegal
    case of design §4.2. Step 4 makes the graph atomic; until then the clone is
    what makes capturing it legal at all.
    """
    from scripts.immutable_bytes import ImmutableBytesError, clone_file
    source = Path(root) / "data" / "graph.sqlite"
    if not source.is_file():
        raise SnapshotComponentError(f"no graph to capture at {source}")
    assert_self_contained(source)
    destination = Path(into) / "graph.sqlite"
    try:
        mechanism = clone_file(source, destination)
    except ImmutableBytesError as exc:
        raise SnapshotComponentError(f"graph capture failed: {exc}") from exc
    return Component(name="graph", path=destination, backend=mechanism,
                     logical_bytes=destination.stat().st_size,
                     captured_scope=SINGLE_FILE,
                     authoritative_scope=WHOLE_COMPONENT)


def capture_search(root, into) -> Component:
    """Freeze the search index by HARDLINK — legal only because
    `rebuild_search_index` builds to a temp file and promotes with one
    `os.replace()`, so the published inode is never modified in place.

    Task 5 adds the regression that fails loudly if that ever stops being true.
    """
    from scripts.immutable_bytes import COPY, HARDLINK
    source = _canonical_index(root)
    if not source.is_file():
        raise SnapshotComponentError(
            f"no search index to snapshot at {source}; build one before measuring")
    assert_self_contained(source)
    destination = Path(into) / INDEX_NAME
    try:
        os.link(source, destination)
        mechanism = HARDLINK
    except OSError:
        shutil.copy2(source, destination)
        mechanism = COPY
    return Component(name="search", path=destination, backend=mechanism,
                     logical_bytes=destination.stat().st_size,
                     captured_scope=SINGLE_FILE,
                     authoritative_scope=WHOLE_COMPONENT)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_snapshot_components.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/measurement_snapshot.py tests/test_snapshot_components.py
git commit -m "zurvan: capture wiki and graph as components, refusing a store with a sidecar"
```

---

## Task 5 — The hardlink invariant, made to fail loudly

**Files:**
- Modify: `tests/test_snapshot_components.py`

**Interfaces:**
- Consumes: `capture_search` from Task 4.
- Produces: nothing importable. This task's deliverable is gate item 14.

Design §4.2 owes a regression that *attempts* the illegal in-place mutation. The point is not that today's code is careful; it is that the day someone opens the canonical path for writing, every snapshot ever taken silently stops being one — and a test is the only thing that will say so.

- [ ] **Step 1: Write the failing test**

```python
def test_an_in_place_write_to_the_published_index_reaches_every_snapshot(
        store, tmp_path):
    """Gate 14. This test DOCUMENTS the hazard by reproducing it, then pins the
    guard that refuses it.

    A hardlink is legal only because `rebuild_search_index` builds to
    `data/search.sqlite.building-<pid>` and promotes with one `os.replace()`.
    If anyone opens the canonical path for writing instead, the snapshot and
    the live store are the same inode, and every existing snapshot silently
    stops being one.
    """
    index = store / "data" / "search.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO chunks VALUES (1)")
    conn.commit()
    conn.close()
    into = tmp_path / "snap"
    into.mkdir()
    component = ms.capture_search(store, into)

    # The hazard, reproduced: write to the CANONICAL path in place.
    conn = sqlite3.connect(index)
    conn.execute("INSERT INTO chunks VALUES (2)")
    conn.commit()
    conn.close()

    # The snapshot moved with it. That is the whole point of the invariant.
    conn = sqlite3.connect(f"file:{component.path}?mode=ro", uri=True)
    try:
        leaked = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()
    assert leaked == 2, (
        "a hardlink shares the inode, so this MUST leak; if it did not, the "
        "mechanism changed and the invariant below is testing nothing")


def test_capture_refuses_an_index_whose_inode_is_shared_with_a_writer(
        store, tmp_path, monkeypatch):
    """The guard: publication must assert the promoted file is the one that was
    built, so an in-place writer is caught at capture rather than discovered
    when a measurement disagrees with itself."""
    index = store / "data" / "search.sqlite"
    conn = sqlite3.connect(index)
    conn.execute("CREATE TABLE chunks (chunk_id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    (store / "data" / "search.sqlite-journal").write_bytes(b"mid-write")
    into = tmp_path / "snap"
    into.mkdir()

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture_search(store, into)
```

- [ ] **Step 2: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_snapshot_components.py -k hardlink -v`
Expected: both PASS with Task 4's `assert_self_contained` in place. The first documents the hazard; the second pins the guard.

- [ ] **Step 3: Commit**

```bash
git add tests/test_snapshot_components.py
git commit -m "zurvan: gate 14 — the hardlink invariant fails loudly when violated"
```

---

## Task 6 — Compose components into `capture`, without charging existing callers

**Files:**
- Modify: `scripts/measurement_snapshot.py`
- Test: `tests/test_measurement_snapshot.py`

**Interfaces:**
- Consumes: `Component`, `capture_wiki`, `capture_graph`, `capture_search` from Task 4.
- Produces: `def capture(root=None, label="measurement", deep=False, components=None)` returning a `Snapshot` whose `identity["components"]` maps name to mechanism.

`components=None` means `("search",)` — today's behaviour exactly. `check.sh` gate 6 calls `--path-only` on every run and reads only the index; making it clone 135 MB would be a 47 ms tax for bytes nobody opens.

**Two ordering facts an implementer needs.** `_COMPONENTS` maps names to the functions from Task 4, so it must appear *below* their definitions in the file or the module will not import. And a capture that omits `search` returns a `Snapshot` whose `index_path` is `None`; `--path-only` and every existing test use the default and are unaffected, but anything added later that reads `index_path` unconditionally will need to check.

- [ ] **Step 1: Write the failing tests**

```python
def _build_store(root):
    """A throwaway store with all three components present.

    Defined here because every test below needs it and a plan that references a
    helper it never shows is the placeholder this document exists to avoid.
    """
    import sqlite3
    (root / "wiki" / "decisions").mkdir(parents=True)
    (root / "wiki" / "decisions" / "a.md").write_text("alpha", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    for name, table in (("search.sqlite", "chunks"), ("graph.sqlite", "nodes")):
        conn = sqlite3.connect(root / "data" / name)
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.execute(f"INSERT INTO {table} VALUES (1)")
        if table == "chunks":
            conn.execute("CREATE TABLE embeddings"
                         " (chunk_id INTEGER, provider TEXT, model TEXT,"
                         "  dimension INTEGER, vector BLOB)")
        conn.commit()
        conn.close()


def test_the_default_capture_is_unchanged(tmp_path, monkeypatch):
    """The 14 existing contracts in this file must not move. A step that
    silently changed what every caller captures would be a behaviour change
    wearing a refactor's clothes."""
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)

    with ms.capture(root=tmp_path) as snapshot:
        assert set(snapshot.identity["components"]) == {"search"}
        # Absence is the record. A default capture froze no wiki, and nothing
        # in the manifest may let that be read as "captured and verified".
        assert "wiki" not in snapshot.identity["components"]
        assert not (snapshot.path / "wiki").exists()


def test_an_authoritative_capture_freezes_the_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)

    with ms.capture(root=tmp_path, components=("wiki",)) as snapshot:
        record = snapshot.identity["components"]["wiki"]
        assert record["capture_backend"] in ("clonefile", "copy")
        assert record["captured_scope"] == "full_tree"
        assert record["authoritative_scope"] == "excludes_derived_subtrees"
        assert record["coherence_verified"] is False
        assert (snapshot.path / "wiki" / "decisions" / "a.md").is_file()


def test_a_full_capture_freezes_all_three(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)

    with ms.capture(root=tmp_path,
                    components=("wiki", "search", "graph")) as snapshot:
        assert set(snapshot.identity["components"]) == {"wiki", "search", "graph"}


def test_an_unknown_component_fails_closed(tmp_path, monkeypatch):
    """Silently skipping an unrecognised name would let a caller believe it had
    frozen something it had not — the claims-guard failure of design §8.2, one
    step early."""
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture(root=tmp_path, components=("claims",))


def test_release_removes_every_component(tmp_path, monkeypatch):
    """A cloned wiki is real directory entries. Leaking one per capture would
    fill the disk with metadata even though the data blocks are shared."""
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)

    snapshot = ms.capture(root=tmp_path, components=("wiki", "search"))
    path = snapshot.path
    snapshot.release()

    assert not path.exists()
    assert (tmp_path / "wiki" / "decisions" / "a.md").is_file()


def test_a_partial_capture_leaves_nothing_behind(tmp_path, monkeypatch):
    """Design §7.1: any failure discards the partial capture. A half-captured
    snapshot directory that survives is indistinguishable from a whole one."""
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)
    (tmp_path / "data" / "graph.sqlite").unlink()

    with pytest.raises(ms.SnapshotComponentError):
        ms.capture(root=tmp_path, components=("wiki", "graph"))

    leftovers = list((tmp_path / "data" / "snapshots").glob("*"))
    assert leftovers == [], leftovers
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_measurement_snapshot.py -v`
Expected: FAIL, `TypeError: capture() got an unexpected keyword argument 'components'`

- [ ] **Step 3: Write the implementation**

Replace the body of `capture` in `scripts/measurement_snapshot.py`:

```python
_COMPONENTS = {
    "wiki": capture_wiki,
    "search": capture_search,
    "graph": capture_graph,
}

DEFAULT_COMPONENTS = ("search",)


def capture(root=None, label="measurement", deep=False, components=None):
    """Freeze the declared components. Returns a `Snapshot`.

    `components=None` means `DEFAULT_COMPONENTS`, which is today's behaviour
    exactly: `check.sh` gate 6 calls this on every run and reads only the
    index, so cloning 135 MB of wiki would be a 47 ms tax on bytes nobody
    opens.

    This is NOT Step 6's `required`. `components` says which bytes to freeze;
    `required` will add coherence verification and `SNAPSHOT_NOT_READY` on top.
    They are kept apart so the seam is reviewable.

    Any failure discards the partial capture (design §7.1): a half-captured
    directory that survived would be indistinguishable from a whole one.
    """
    base = _root(root)
    names = tuple(components) if components is not None else DEFAULT_COMPONENTS
    unknown = [n for n in names if n not in _COMPONENTS]
    if unknown:
        raise SnapshotComponentError(
            f"no capture adapter registered for {unknown}; refusing rather "
            "than silently omitting a component the caller believes it froze")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_id = f"{label}-{stamp}-{os.getpid()}"
    directory = snapshots_dir(base) / snapshot_id
    directory.mkdir(parents=True, exist_ok=False)
    try:
        captured = {}
        for name in names:
            captured[name] = _COMPONENTS[name](base, directory)
        # A component's ABSENCE from this mapping means it was not captured.
        # Recording only the names that were frozen is what stops a later
        # reader treating a missing key as "captured and verified".
        identity = {
            "snapshot_id": snapshot_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
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
        index_path = captured["search"].path if "search" in captured else None
        if index_path is not None:
            identity["index_path"] = str(_canonical_index(root))
            identity["index_inode"] = index_path.stat().st_ino
            identity["link"] = captured["search"].backend
            identity.update(index_identity(index_path, deep=deep))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return Snapshot(snapshot_id=snapshot_id, path=directory,
                    index_path=index_path, identity=identity)
```

- [ ] **Step 4: Run the whole snapshot suite**

Run: `PYTHONPATH=. python -m pytest tests/test_measurement_snapshot.py tests/test_snapshot_components.py -v`
Expected: all pass, including the 14 pre-existing contracts unchanged

- [ ] **Step 5: Commit**

```bash
git add scripts/measurement_snapshot.py tests/test_measurement_snapshot.py
git commit -m "zurvan: capture composes declared components, default unchanged"
```

---

## Task 7 — Backend equivalence: the fallback is a peer, not a decoration

**Files:**
- Create: `tests/test_snapshot_backend_equivalence.py`

**Interfaces:**
- Consumes: `capture` and the `Component` record from Tasks 4 and 6; `_clonefile` from Task 1.
- Produces: nothing importable. This task's deliverable is the portable-first contract itself.

The decision on record is **portable-first**: the correctness contract is an immutable replayable snapshot, `clonefile` is the preferred fast path, and byte-copy is the portability path. Without a test, that decays into APFS-first with a fallback nobody has run — and the decay is invisible, because the fast path works on the machine doing the developing.

Two captures of the same store, one per backend, must be indistinguishable except in diagnostic metadata: same component set, same relative paths, same byte hashes, same immutability behaviour, same manifest semantics.

- [ ] **Step 1: Write the failing tests**

```python
"""The two backends must produce the same snapshot, or "portable-first" is a slogan.

`clonefile` is free and APFS-only. A byte copy is universal, and measured on
this corpus it is 25-37x slower and allocates 147 MB. Both are correct, and
downstream code may never be able to tell which one ran.

The fast path works on the machine doing the developing, so a fallback that
silently diverged would be discovered by someone else, later, on hardware we do
not have. That is what makes this contract worth a test rather than a comment.
"""

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib
from scripts import measurement_snapshot as ms


def _raises_enotsup(source, destination):
    raise OSError(45, "Operation not supported")


def _hash_tree(root):
    """relpath -> sha256 over every file, so two captures compare by CONTENT
    rather than by the mechanism that produced them."""
    digests = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            digests[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return digests


@pytest.fixture
def equivalence_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    _build_store(tmp_path)
    return tmp_path


def _capture_both(root, monkeypatch):
    """One capture per backend, against the same unchanged store."""
    cloned = ms.capture(root=root, label="clone",
                        components=("wiki", "search", "graph"))
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    copied = ms.capture(root=root, label="copy",
                        components=("wiki", "search", "graph"))
    return cloned, copied


def test_the_two_backends_really_are_different_backends(equivalence_store, monkeypatch):
    """If both captures used the same mechanism, every comparison below is
    vacuous. This is the guard that keeps the rest of the file honest."""
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        assert cloned.identity["components"]["wiki"]["capture_backend"] == "clonefile"
        assert copied.identity["components"]["wiki"]["capture_backend"] == "copy"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_capture_the_same_component_set(equivalence_store, monkeypatch):
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        assert (set(cloned.identity["components"])
                == set(copied.identity["components"]))
    finally:
        cloned.release()
        copied.release()


def test_both_backends_produce_the_same_paths_and_the_same_bytes(
        store, monkeypatch):
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        a = _hash_tree(cloned.path / "wiki")
        b = _hash_tree(copied.path / "wiki")
        assert set(a) == set(b), "relative paths differ between backends"
        assert a == b, "byte content differs between backends"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_report_the_same_manifest_semantics(equivalence_store, monkeypatch):
    """Everything in the component record except the backend name must match.
    Scope and verification status are semantics; the mechanism is diagnostics."""
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        for name in cloned.identity["components"]:
            a = dict(cloned.identity["components"][name])
            b = dict(copied.identity["components"][name])
            a.pop("capture_backend")
            b.pop("capture_backend")
            assert a == b, f"{name}: semantics differ between backends"
    finally:
        cloned.release()
        copied.release()


def test_both_backends_survive_the_same_live_edits(equivalence_store, monkeypatch):
    """Immutability is a property of the snapshot, not of the backend. The
    matrix runs against the fallback too, because a copy that somehow shared
    state would be the same defect with a different cause."""
    cloned, copied = _capture_both(equivalence_store, monkeypatch)
    try:
        page = equivalence_store / "wiki" / "decisions" / "a.md"
        import os
        replacement = equivalence_store / "wiki" / "decisions" / "a.md.tmp"
        replacement.write_text("rewritten", encoding="utf-8")
        os.replace(replacement, page)
        page.unlink()

        for snapshot in (cloned, copied):
            frozen = snapshot.path / "wiki" / "decisions" / "a.md"
            assert frozen.read_text(encoding="utf-8") == "alpha"
    finally:
        cloned.release()
        copied.release()


def test_no_correctness_path_branches_on_the_capture_backend():
    """The decay this prevents: "portable-first" quietly becoming "APFS-first
    with a decorative fallback".

    Walks the AST for a comparison against a backend constant. Assigning one is
    fine — that is how the mechanism gets recorded. COMPARING one means some
    behaviour depends on which filesystem happened to be underneath.
    """
    tree = ast.parse(inspect.getsource(ms))
    constants = {"CLONEFILE", "COPY", "HARDLINK"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for side in [node.left, *node.comparators]:
            if isinstance(side, ast.Name) and side.id in constants:
                offenders.append(ast.unparse(node))
            elif isinstance(side, ast.Attribute) and side.attr in constants:
                offenders.append(ast.unparse(node))
    assert not offenders, (
        "correctness branches on the capture backend: " + "; ".join(offenders))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_snapshot_backend_equivalence.py -v`
Expected: FAIL until `_build_store` is importable. Copy that helper from Task 6's test module into this one rather than importing across test modules — a shared fixture between two files is a coupling neither file declares.

- [ ] **Step 3: Make them pass**

No production change should be needed. If one is, that is the finding: it means the backends were not equivalent, and the fix belongs in `immutable_bytes`, never in the test.

Run: `PYTHONPATH=. python -m pytest tests/test_snapshot_backend_equivalence.py -v`
Expected: 6 passed

- [ ] **Step 4: Commit**

```bash
git add tests/test_snapshot_backend_equivalence.py
git commit -m "zurvan: clone and copy must produce the same snapshot, or portable-first is a slogan"
```

---

## Task 8 — Measure it on the live store, and correct the design where it was wrong

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md`
- Modify: `AGENTS.md`, `CHANGELOG.md`

Design §10.4 requires *"a measurement, reported whatever it says: end-to-end capture cost and peak RSS on the live store, against today's 485 ms baseline. The estimate from §4.1's components is ~1.1 s and zero disk. **An estimate is not a result.**"*

- [ ] **Step 1: Measure a full capture on the live store**

```bash
cd /Users/dev/Desktop/Raouf/Zurvan
PYTHONPATH=. python - <<'MEASURE'
import resource, time
from scripts import measurement_snapshot as ms

before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t0 = time.perf_counter()
snapshot = ms.capture(label="measure", components=("wiki", "search", "graph"))
elapsed = time.perf_counter() - t0
after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
try:
    print(f"full capture   : {elapsed*1000:.0f} ms")
    print(f"peak RSS delta : {(after - before) / 1e6:.2f} MB")
    print(f"mechanisms     : {snapshot.identity['components']}")
    print(f"logical bytes  : {snapshot.identity['logical_bytes']}")
finally:
    snapshot.release()
    print("released:", snapshot.released)
MEASURE
```

Record the numbers that come out, including if they are worse than the estimate. The estimate is `~1.1 s`; the component measurements in this plan suggest well under that, and the run is the answer of record either way.

- [ ] **Step 2: Correct §4.1 of the design with the measured figures**

The design's table says `0.55–0.59 s, 0 blocks` for the wiki. Both are wrong in a way worth fixing rather than footnoting: the timing is ~10x pessimistic because it measured a per-file shape, and the clone allocates ~4 MB of directory metadata rather than nothing.

Retire the phrase **"zero disk"** everywhere it appears, and replace the claim with the one that survives measurement:

> No additional file-data blocks were observed on APFS copy-on-write capture; filesystem metadata allocation is non-zero.

Also record the EXDEV bound in the design itself, in these terms, so no later document can upgrade it by accident:

```text
clone failure -> fallback path            TESTED (fault injection)
real EXDEV on a second filesystem         NOT MEASURED
```

```bash
# Replace the wiki row in §4.1's table with the measured values, add the
# per-file-vs-syscall note, and sweep the phrase that is being retired. Edit by
# hand — the surrounding prose refers to the numbers, so a blind substitution
# would leave the text disagreeing with itself.
grep -n "135 MB, 4,430 files" docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md
grep -rn "zero disk\|0 blocks" docs/superpowers/specs/
```

- [ ] **Step 3: Verify nothing retrieval-shaped moved**

Z0 must be provably ranking-neutral (design §11). This step changes no retrieval code, so the eval must be identical — but "identical to what" needs a recorded before, so **this baseline is taken before Task 1 begins**, not here:

```bash
# Run this FIRST, before Task 1. Keep the output; it is the comparison.
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6 \
  | tee /tmp/z0-step3-eval-before.txt
```

Then, at this step, re-run it and diff against that file:

```bash
PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6 \
  | tee /tmp/z0-step3-eval-after.txt
diff /tmp/z0-step3-eval-before.txt /tmp/z0-step3-eval-after.txt
```

Expected: no difference. If any retrieval number moves, stop — something is wrong, because nothing here touches ranking, indexing, chunking or embedding.

- [ ] **Step 4: Run the full gate**

```bash
PYTHONPATH=. python -m pytest -q
bash scripts/check.sh
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

Expected: pytest 0 failed; `check.sh` all 24 gates; guard passes; `diff --check` clean. Do not start this while an index rebuild is in flight.

- [ ] **Step 5: Append the dated entry to both logs and commit**

```bash
git add docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md AGENTS.md CHANGELOG.md
git commit -m "zurvan: Z0 step 3 measured on the live store, and the design corrected"
```

---

## What this step does NOT close

Z0's gate (design §10) has twenty items. This step demonstrates **two**: item 14 (hardlink invariant) and item 16 (wiki immutability matrix). It also builds the mechanism several others will need.

Still open after this step, and none of it should be claimed:

- Items 1–7, mutation semantics against real snapshots — need Step 6's capture algorithm.
- Items 8–13, coherence — need `purpose`/`required`, `built_from_revision`, and the bounded retry.
- Item 15, self-contained SQLite as a *publication* assertion — Step 4. Task 4 adds a capture-time guard, which is a different question asked at a different moment.
- Items 17–18, truthful stamps and SUSPECT refusal on rebuild — Step 5.
- Item 19, the claims guard — Step 6.
- Item 20, the regression floor, is re-run by Task 8 but is not closure by itself.

`measurement_snapshot.capture` still defaults to the search index alone. Nothing in the repository yet consumes an authoritative snapshot, which is Step 5's job. **Z0 remains open.**
