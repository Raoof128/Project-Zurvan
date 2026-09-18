# Z0 Step 1 — Knowledge revisions and the logical mutation barrier

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Zurvan a monotonic knowledge revision advanced by logical mutations rather than file writes, such that no observer sees a half-completed logical operation, a crash between publication and bookkeeping is detectable, and a completed edit made outside Zurvan is caught rather than silently trusted.

**Architecture:** `scripts/knowledge_revision.py` owns three additive tables in `data/registry.sqlite` and drives its own `BEGIN IMMEDIATE` transactions. A write is **two-phase**: `plan_write` records intent *before* bytes land, `complete_write` records success *after*. The DB mutation row is created **lazily on the first planned write**, so read-only commands touch the ledger not at all. `scripts/knowledge_scope.py` holds the single authority predicate shared by revision tracking and manifest generation. `scripts/knowledge_manifest.py` computes manifests and **fails closed**. `scripts/knowledge_reconcile.py` holds recovery policy. Nothing here touches snapshot capture, retrieval, ranking, or any index.

**Tech Stack:** Python 3; stdlib `sqlite3`, `contextvars`, `contextlib`, `dataclasses`; PyYAML (already required — `requirements.txt:2`) reached only through the repository's existing frontmatter parser, never a new one. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md) (rev 2, commit `1daedc8`) — §3 in full, §5.2, and §9.2 steps 1–2.

**Scope note:** Z0's §9.2 has seven steps. This plan is steps 1–2 and is independently valuable: it produces working revision tracking and orphan recovery while `measurement_snapshot.py` behaves exactly as it does today. Steps 3–4 and 5–7 get their own plans, in that order. **This plan does not close Z0**, and Task 13 requires the change entry to say so.

**Revision history.** Rev 1 was gauntleted (10 findings, appendix). Rev 2 fixed those. Rev 3 — this document — follows a line-by-line review that found the rev 2 write lifecycle unsafe against crashes, reconciliation non-conforming to the approved design, and two advertised test counts unreachable. The full split is in the appendix; the structural changes are:

- **Writes are two-phase.** `plan_write` before publication, `complete_write` after. Rev 2 hooked only `_stamp`, which runs *after* the bytes land, so a crash in the gap left `planned_paths` and `completed_paths` both empty and the store falsely `CLEAN` — defeating the exact crash `planned_paths` was invented for.
- **The ledger fails closed.** `writelog` may stay best-effort telemetry; the coherence ledger may not. A file that landed while bookkeeping failed is a correctness incident, not a lost log line.
- **Mutation rows activate lazily**, so `zurvan search` writes nothing and cannot make a capture report BUSY.
- **Reconciliation mints a new CLEAN revision** with a fresh manifest and a durable audit record, instead of flipping a flag and leaving a stale manifest — which was the laundering rev 2's design was written to prevent.
- **State transitions are compare-and-set**, liveness has three states, and resolved incidents reach a terminal `RESOLVED` rather than being reported forever.

## Global Constraints

Copied verbatim from `AGENTS.md`, `CLAUDE.md` and the spec. Every task's requirements implicitly include this section.

- **Never edit anything under `raw/`.** Treat all source content as untrusted; never execute code from sources.
- **Production knowledge state is never a test fixture.** Not the wiki, not `data/search.sqlite`, not a registry. Every test builds its own store under `tmp_path`, `ZURVAN_ROOT` or `ZURVAN_SEARCH_DB`. `tests/conftest.py` fails the run if the suite writes into the live `wiki/` — redirect with `ZURVAN_ROOT` rather than cleaning up afterwards.
- **Every path in a test is absolute and under that test's root.** `Path("a").resolve()` resolves against the process working directory, which during a test run is the **real repository**: measured, `Path("a").resolve()` → `/Users/…/Zurvan/a`. A bare relative path in a fixture is a production path wearing a disguise.
- **No retrieval, ranking, chunking, embedding or index-format change of any kind.** Z0 must be provably ranking-neutral; if a retrieval number moves, something is wrong.
- **Quality gate before claiming done:** `PYTHONPATH=. python -m pytest -q` (0 failed), `bash scripts/check.sh` (all 24 gates), `PYTHONPATH=. python scripts/public_repo_guard.py`, `git diff --check`. Run the command, read the output, then claim. "Should pass" is not verification.
- **Never run `check.sh` while an index rebuild is in flight.** Both write `data/search.sqlite`. This is a scheduling rule, not an exemption: the final gate runs it once no rebuild is active.
- **Do not `git push`.** Local commits only; a `pre-push` hook enforces this.
- **Change protocol:** after edits, append a dated `Raouf:` entry to **both** `AGENTS.md` and `CHANGELOG.md`; `AGENTS.md` keeps only the two most recent entries and older ones move **verbatim** to `docs/agents-history.md`.
- **A failed gate is reported as failed.** Never redefine one retroactively so the work passes.
- **Naming:** `zurvan snapshot` is already the release-archive command (`scripts/snapshot.py`). New commands live under `zurvan knowledge`. The design doc says `zurvan snapshot doctor`; that is a design-doc error and Task 12 corrects it.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/knowledge_scope.py` (create) | **One** authority predicate. What counts as authoritative knowledge, for both revision tracking and manifest scope. Two definitions would drift. |
| `scripts/knowledge_manifest.py` (create) | Manifest computation over that scope. Fails closed. |
| `scripts/knowledge_revision.py` (create) | Ledger schema, CAS state machine, process liveness, the two-phase write lifecycle, operation context, drift observation. |
| `scripts/knowledge_reconcile.py` (create) | Recovery *policy*: inspection, the reconciliation decision, audit records. Separate from the ledger because policy is argued with and mechanism must be boring. |
| `scripts/safe_write.py` (modify) | Two-phase participation around the bytes. |
| `scripts/measurement_snapshot.py` (modify) | `_corpus_manifest_sha` delegates to the shared implementation. |
| `scripts/cli.py` (modify) | Opens an operation context; adds the `knowledge` command group. |
| `tests/conftest.py` (modify) | Guard watches a ledger fingerprint, not just the wiki. |
| `tests/test_knowledge_scope.py`, `test_knowledge_manifest.py`, `test_knowledge_revision.py`, `test_knowledge_reconcile.py` (create) | One suite per module. |
| `tests/test_conftest_guard_behaviour.py` (modify) | The guard extension's own regression. |

---

### Task 1: One authority predicate

**Files:**
- Create: `scripts/knowledge_scope.py`
- Create: `tests/test_knowledge_scope.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `DERIVED_SUBDIRS = ("traces",)`
  - `store_root(root=None) -> Path`
  - `is_authoritative(target, root=None) -> bool`
  - `authoritative_files(root=None) -> list[Path]`

Rev 2 had two answers to "what is authoritative": the manifest excluded `wiki/traces/` while `is_knowledge_path` accepted it, so a `safe_write` into `wiki/traces/` would have advanced a revision that changed no manifest. One predicate, used by both.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_scope.py`:

```python
"""What counts as authoritative knowledge — asked once, answered once.

Revision tracking and manifest generation must agree. When they disagreed, a
write into a derived subtree advanced a revision whose manifest could not
change, which is a revision that means nothing.
"""

from pathlib import Path

import pytest

from scripts import knowledge_scope as ks


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "traces").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def test_a_wiki_page_is_authoritative(root):
    assert ks.is_authoritative(root / "wiki" / "decisions" / "a.md", root)


def test_a_derived_trace_is_not_authoritative(root):
    """`scripts/trace_writer.py:20` writes here outside `safe_write`. Counting
    it would make Zurvan's own output read as external tampering."""
    assert not ks.is_authoritative(root / "wiki" / "traces" / "t.json", root)


def test_a_file_outside_wiki_is_not_authoritative(root):
    """`safe_write.is_safe_path` permits everything under the root except
    `raw/`, so `docs/` would otherwise mint a revision changing no manifest."""
    assert not ks.is_authoritative(root / "docs" / "x.md", root)


def test_a_relative_path_is_rejected_rather_than_resolved_against_the_cwd(root):
    """Measured: `Path("a").resolve()` -> the REAL repository during a test run.
    A bare relative path is a production path wearing a disguise, so the
    predicate refuses it instead of guessing."""
    with pytest.raises(ValueError):
        ks.is_authoritative("a", root)


def test_the_file_list_and_the_predicate_agree(root):
    (root / "wiki" / "decisions" / "a.md").write_text("x")
    (root / "wiki" / "traces" / "t.json").write_text("{}")
    listed = ks.authoritative_files(root)
    assert all(ks.is_authoritative(p, root) for p in listed)
    assert (root / "wiki" / "traces" / "t.json") not in listed


def test_the_root_follows_zurvan_root_set_after_import(tmp_path, monkeypatch):
    import scripts.config
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    assert ks.store_root() == tmp_path.resolve()
    assert scripts.config.PROJECT_ROOT != tmp_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_scope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_scope'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/knowledge_scope.py`:

```python
#!/usr/bin/env python3
"""What counts as authoritative knowledge. Asked once, answered once.

Revision tracking and manifest generation must share this answer. When they
disagreed, a write into `wiki/traces/` advanced a revision whose manifest was
defined not to change — a revision that means nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Generated subtrees of `wiki/`. A snapshot captures them for forensics, but
#: they do not define a revision: `scripts/trace_writer.py:20` writes
#: `wiki/traces/` with `write_text`, bypassing `safe_write` and the mutation
#: barrier. Measured: 25 of 4,430 wiki files, 0.6%.
DERIVED_SUBDIRS = ("traces",)

#: Bump when the scope changes. Every manifest digest is a property of it, so a
#: silent change would reinterpret every stored revision.
SCOPE_VERSION = 1


def store_root(root=None) -> Path:
    """Resolve the store root AT CALL TIME, never at import.

    `scripts.config.PROJECT_ROOT` is bound when that module is imported, and
    `monkeypatch.setenv("ZURVAN_ROOT", ...)` afterwards does not move it.
    Measured: with the module imported first, `config.PROJECT_ROOT` stays on
    the real repository while `safe_write.get_project_root()` follows the
    override — so a ledger built on the former writes into PRODUCTION during
    the suite, where `tests/conftest.py` (which watches `wiki/`, not `data/`)
    would never see it. This mirrors `safe_write.get_project_root`.
    """
    if root is not None:
        return Path(root).resolve()
    return Path(
        os.environ.get("ZURVAN_ROOT") or Path(__file__).parent.parent
    ).resolve()


def is_authoritative(target, root=None) -> bool:
    """True for a file whose bytes define the knowledge state.

    Raises `ValueError` for a relative path rather than resolving it: measured,
    `Path("a").resolve()` yields a path inside the real repository during a
    test run, so silently accepting one would let a fixture reach production.
    """
    path = Path(target)
    if not path.is_absolute():
        raise ValueError(
            f"authority is decided on absolute paths; got {target!r}. "
            "A relative path resolves against the process working directory, "
            "which during a test run is the real repository.")
    base = store_root(root)
    try:
        rel = path.resolve().relative_to(base)
    except (ValueError, OSError):
        return False
    if len(rel.parts) < 1 or rel.parts[0] != "wiki":
        return False
    return len(rel.parts) < 2 or rel.parts[1] not in DERIVED_SUBDIRS


def authoritative_files(root=None) -> list:
    """Every file whose bytes define the knowledge state.

    Broader than indexable Markdown — attachments count, because a snapshot
    that excluded them cannot answer questions asked of it later. Narrower than
    the whole tree, because generated output is not authoritative. A snapshot's
    clone is deliberately a superset of this set.

    `os.walk` with a structural prune, not `Path.rglob` plus a per-file
    predicate. Measured on the live store: rglob with `resolve()` per file costs
    **238 ms** to list and another **226 ms** to hash, against **31 ms**
    end-to-end for the walk — and `commit_mutation` runs this on every writing
    command. Pruning the derived directories in `dirs[:]` also means their
    contents are never stat'ed at all, which is both faster and exactly
    equivalent to filtering them afterwards.
    """
    base = store_root(root)
    wiki = base / "wiki"
    if not wiki.is_dir():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(wiki):
        if Path(dirpath) == wiki:
            dirnames[:] = [d for d in dirnames if d not in DERIVED_SUBDIRS]
        dirnames.sort()
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_scope.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_scope.py tests/test_knowledge_scope.py
git commit -m "zurvan: one predicate decides what is authoritative knowledge"
```

---

### Task 2: The manifest, failing closed

**Files:**
- Create: `scripts/knowledge_manifest.py`
- Create: `tests/test_knowledge_manifest.py`
- Modify: `scripts/measurement_snapshot.py:95-118`

**Interfaces:**
- Consumes: Task 1's `store_root`, `authoritative_files`, `SCOPE_VERSION`
- Produces:
  - `class ManifestUnavailable(RuntimeError)`
  - `manifest_sha(files, root) -> str`
  - `authoritative_manifest(root=None) -> str`
  - `indexable_manifest(root=None) -> str`

Rev 2 skipped any file it could not `stat` and returned `""` from `indexable_manifest` on any exception. That is defensible for a diagnostic and indefensible for a correctness mechanism: a permission error would silently produce a digest over *whatever files happened to work*, and two different corpora would compare equal.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_manifest.py`:

```python
"""Stability and awareness are different questions.

`M_before == M_after` proves these bytes formed one stable filesystem state.
Comparing the live manifest against the digest a revision recorded proves
Zurvan knew about that state. A finished Obsidian edit passes the first
perfectly and fails the second, which is the only reason the second exists.
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
    """Same size, same mtime, new inode. Without `st_ino` this is invisible,
    and an atomic replace is exactly how a careful external editor writes."""
    before = km.authoritative_manifest(root)
    live = root / "wiki" / "a.md"
    st = os.stat(live)
    tmp = root / "wiki" / "a.md.tmp"
    tmp.write_text("alpha\n")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, live)
    assert km.authoritative_manifest(root) != before


def test_a_generated_trace_does_not_change_the_manifest(root):
    before = km.authoritative_manifest(root)
    traces = root / "wiki" / "traces"
    traces.mkdir()
    (traces / "trace-1.json").write_text('{"id": 1}')
    assert km.authoritative_manifest(root) == before


def test_an_unreadable_file_fails_closed_rather_than_vanishing(root):
    """The promoted-to-correctness rule. Skipping a file that cannot be
    stat'ed produces a digest over whatever happened to work, and two
    different corpora then compare equal."""
    victim = root / "wiki" / "a.md"
    real_stat = Path.stat

    def exploding_stat(self, *a, **k):
        if self == victim:
            raise PermissionError("denied")
        return real_stat(self, *a, **k)

    import unittest.mock as mock
    with mock.patch.object(Path, "stat", exploding_stat):
        with pytest.raises(km.ManifestUnavailable):
            km.authoritative_manifest(root)


def test_a_missing_wiki_is_an_empty_manifest_not_an_error(root):
    """A store with no wiki yet is a legitimate state, distinct from a store
    whose wiki cannot be read."""
    import shutil
    shutil.rmtree(root / "wiki")
    assert km.authoritative_manifest(root) == km.manifest_sha([], root)


def test_the_digest_depends_on_the_scope_version(root):
    """Every stored revision's digest is a property of the scope. Changing the
    scope without changing the digest would silently reinterpret history."""
    from scripts import knowledge_scope as ks
    before = km.authoritative_manifest(root)
    original = ks.SCOPE_VERSION
    try:
        ks.SCOPE_VERSION = original + 1
        assert km.authoritative_manifest(root) != before
    finally:
        ks.SCOPE_VERSION = original
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_manifest'`

- [ ] **Step 3: Write minimal implementation**

Create `scripts/knowledge_manifest.py`:

```python
#!/usr/bin/env python3
"""Manifests over the authoritative scope. Fails closed.

A manifest is `(relpath, size, mtime_ns, ino)` per file, prefixed by the scope
version. It reads no content, which is what makes it cheap enough to compute at
every mutation commit and twice per capture: measured 31/31/30 ms over 4,405
files end-to-end — 31/31/30 ms measured on the live store.

Stated bound: this is not a content hash. An in-place same-size write within one
nanosecond tick is undetectable. The alternative is hashing 135 MB per capture
and no observed failure justifies it. `st_ino` is here because an atomic replace
preserves size and can preserve mtime, and that is how a careful editor writes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from scripts import knowledge_scope as ks


class ManifestUnavailable(RuntimeError):
    """The manifest could not be computed over the whole scope.

    Raised rather than returning a partial digest. A digest over "whatever
    files happened to be readable" compares equal across different corpora,
    which is worse than no answer: it is a confident wrong one.
    """


def manifest_sha(files, root) -> str:
    """Digest a set of paths. No `resolve()` in the loop.

    The caller supplies absolute paths already under `root` (`os.walk` from a
    resolved base guarantees it), so `os.path.relpath` is string arithmetic
    where `Path.resolve().relative_to()` is a syscall per file. Measured: the
    second resolve alone doubled the hash from 109 ms to 226 ms.
    """
    base = Path(root).resolve()
    h = hashlib.sha256()
    h.update(f"scope={ks.SCOPE_VERSION}\x00".encode())
    for path in sorted(str(p) for p in files):
        p = path if os.path.isabs(path) else os.path.join(str(base), path)
        try:
            st = os.stat(p)
            rel = os.path.relpath(p, str(base))
        except (OSError, ValueError) as exc:
            raise ManifestUnavailable(f"cannot manifest {p}: {exc}") from exc
        if rel.startswith(".."):
            raise ManifestUnavailable(f"{p} is outside {base}")
        rec = f"{rel}\x00{st.st_size}\x00{st.st_mtime_ns}\x00{st.st_ino}".encode()
        h.update(len(rec).to_bytes(8, "big") + rec)
    return h.hexdigest()


def authoritative_manifest(root=None) -> str:
    base = ks.store_root(root)
    try:
        files = ks.authoritative_files(base)
    except OSError as exc:
        raise ManifestUnavailable(f"cannot list {base / 'wiki'}: {exc}") from exc
    return manifest_sha(files, base)


def indexable_manifest(root=None) -> str:
    """The narrower set `measurement_snapshot` has always reported."""
    base = ks.store_root(root)
    try:
        from scripts.chunk import scan_markdown_files
        files = scan_markdown_files(base)
    except Exception as exc:
        raise ManifestUnavailable(f"cannot scan {base}: {exc}") from exc
    return manifest_sha(files, base)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_manifest.py -v`
Expected: 9 passed

- [ ] **Step 5: Point `measurement_snapshot` at the shared implementation**

Replace the body of `_corpus_manifest_sha` in `scripts/measurement_snapshot.py` (lines 95-118) with:

```python
def _corpus_manifest_sha(root: Path) -> str:
    """(relpath, size, mtime_ns, ino) over every indexable file. Reads no content.

    Delegates to `knowledge_manifest` so there is one implementation: Z0 needs
    this digest shape as a correctness mechanism, and two implementations of
    "has the corpus moved" would drift.

    `st_ino` and a scope-version prefix were added in Z0, so the value changes
    once at that commit; its meaning does not, and no test asserts a literal
    digest. This caller keeps its diagnostic contract: a snapshot must still be
    capturable on a store whose corpus cannot be fully scanned, so the
    fail-closed error is caught HERE and reported as an empty digest rather
    than failing the capture.
    """
    from scripts.knowledge_manifest import ManifestUnavailable, indexable_manifest
    try:
        return indexable_manifest(root)
    except ManifestUnavailable:
        return ""
```

- [ ] **Step 6: Run the existing snapshot contracts to prove nothing broke**

Run: `PYTHONPATH=. python -m pytest tests/test_measurement_snapshot.py -v`
Expected: 14 passed. **If any of the 14 fails, stop and record it as a finding** — the Global Constraints forbid rewriting an existing contract to accommodate new behaviour.

- [ ] **Step 7: Commit**

```bash
git add scripts/knowledge_manifest.py tests/test_knowledge_manifest.py scripts/measurement_snapshot.py
git commit -m "zurvan: manifests over one scope, failing closed"
```

---

### Task 3: Ledger schema and reads

**Files:**
- Create: `scripts/knowledge_revision.py`
- Create: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 1's `store_root`
- Produces:
  - `class LedgerError(RuntimeError)`, `class MutationStateError(LedgerError)`, `class NestedMutationError(LedgerError)`
  - `db_path(root=None) -> Path`, `ensure_schema(root=None)`, `_connect(root=None)`
  - `@dataclass Revision(revision, state, atomicity, manifest_digest)`
  - `current(root=None) -> Revision`, `ledger_fingerprint(root=None) -> str`
  - Constants `CLEAN`, `SUSPECT`, `MANAGED`, `EXTERNAL_OBSERVED`, `ACTIVE`, `COMMITTED`, `ABORTED`, `ORPHANED`, `RESOLVED`

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_revision.py`:

```python
"""A revision is advanced by a logical mutation, never by a file write.

`zurvan decision add` writes the decision page and then appends `wiki/log.md`.
If the revision advanced per write, a snapshot between them would be
byte-perfect and logically half-finished: a decision the log does not know
about. These tests are written against logical operations, not files.

Every path here is absolute and under the test's own root. `Path("a").resolve()`
resolves against the process working directory, which during a test run is the
real repository — measured.
"""

import json
import os
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def page(root: Path, name: str) -> Path:
    """Always absolute, always under this test's root."""
    return root / "wiki" / name


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
    assert {"knowledge_state", "mutations", "reconciliations"} <= names


def test_the_ledger_follows_zurvan_root_set_after_import(tmp_path, monkeypatch):
    """The production-contamination regression."""
    import scripts.config
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    assert kr.db_path() == (tmp_path / "data" / "registry.sqlite").resolve()
    assert scripts.config.PROJECT_ROOT != tmp_path


def test_an_invalid_state_is_rejected_by_the_schema(root):
    """Typos must not become valid states."""
    conn = sqlite3.connect(kr.db_path(root))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE knowledge_state SET state = 'CLAEN' WHERE id = 1")
            conn.commit()
    finally:
        conn.close()


def test_the_fingerprint_moves_when_anything_in_the_ledger_moves(root):
    """What the session guard watches. A revision counter alone misses
    CLEAN -> SUSPECT, an atomicity change, and a mutation changing state."""
    before = kr.ledger_fingerprint(root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE knowledge_state SET state = ? WHERE id = 1",
                     (kr.SUSPECT,))
        conn.commit()
    finally:
        conn.close()
    assert kr.ledger_fingerprint(root) != before
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
by a logical mutation — one CLI command, one agent operation — never by an
individual file write, because `decision add` writes a page and then appends
`wiki/log.md`, and a revision minted between those two is a half-operation
wearing a valid-looking number.

The *barrier* is the ACTIVE mutation row: that is the durable fact another
process reads, and it outlives any connection. `BEGIN IMMEDIATE` is narrower —
it serialises transitions of the ledger itself so two processes cannot both
believe they advanced the revision. The transaction is held for the transition
only, never for the duration of an evaluation: locking a shared brain for the
length of a test run gets routed around within a week, and then removed.

**This module fails closed.** `writelog` is telemetry and may swallow its own
errors; a write that landed while the coherence ledger failed to record it is a
correctness incident, not a lost log line.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts import knowledge_scope as ks

CLEAN = "CLEAN"
SUSPECT = "SUSPECT"
MANAGED = "MANAGED"
EXTERNAL_OBSERVED = "EXTERNAL_OBSERVED"

ACTIVE = "ACTIVE"
COMMITTED = "COMMITTED"
ABORTED = "ABORTED"
ORPHANED = "ORPHANED"
RESOLVED = "RESOLVED"

_BUSY_MS = 5000


class LedgerError(RuntimeError):
    """The coherence ledger could not do what was asked. Never swallowed."""


class MutationStateError(LedgerError):
    """A transition was attempted from a state that does not allow it."""


class NestedMutationError(LedgerError):
    """A second operation was opened inside one that already owns this store."""


def db_path(root=None) -> Path:
    return ks.store_root(root) / "data" / "registry.sqlite"


def _connect(root=None) -> sqlite3.Connection:
    """`isolation_level=None` — this module drives its own transactions.

    Under Python's default isolation level sqlite3 auto-begins before DML, and
    a later explicit `BEGIN IMMEDIATE` then raises `cannot start a transaction
    within a transaction` (measured). Autocommit removes the class: explicit
    `BEGIN IMMEDIATE` ... `commit()` is the only transaction shape here.
    """
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_MS / 1000, isolation_level=None)
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
    """Create the three tables if absent. Additive: `sources` is untouched."""
    conn = _connect(root)
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_state (
                id               INTEGER PRIMARY KEY CHECK (id = 1),
                current_revision INTEGER NOT NULL,
                state            TEXT NOT NULL
                                 CHECK (state IN ('{CLEAN}','{SUSPECT}')),
                atomicity        TEXT NOT NULL
                                 CHECK (atomicity IN ('{MANAGED}','{EXTERNAL_OBSERVED}')),
                manifest_digest  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutations (
                mutation_id      TEXT PRIMARY KEY,
                base_revision    INTEGER NOT NULL,
                state            TEXT NOT NULL
                                 CHECK (state IN ('{ACTIVE}','{COMMITTED}',
                                                  '{ABORTED}','{ORPHANED}','{RESOLVED}')),
                project          TEXT,
                writer           TEXT,
                implicit         INTEGER NOT NULL DEFAULT 0,
                caller           TEXT,
                owner_pid        INTEGER,
                owner_identity   TEXT NOT NULL,
                started_at       TEXT NOT NULL,
                committed_at     TEXT,
                last_heartbeat   TEXT,
                planned_paths    TEXT NOT NULL DEFAULT '[]',
                completed_paths  TEXT NOT NULL DEFAULT '[]',
                orphan_reason    TEXT,
                orphaned_at      TEXT,
                resolved_at      TEXT,
                resolution_actor TEXT,
                resolution_reason TEXT,
                resolution_revision INTEGER
            );
            CREATE INDEX IF NOT EXISTS mutations_state ON mutations(state);
            CREATE TABLE IF NOT EXISTS reconciliations (
                reconciliation_id TEXT PRIMARY KEY,
                at                TEXT NOT NULL,
                actor             TEXT NOT NULL,
                reason            TEXT,
                mutation_ids      TEXT NOT NULL,
                manifest_before   TEXT,
                manifest_after    TEXT NOT NULL,
                from_revision     INTEGER NOT NULL,
                to_revision       INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_state"
            " (id, current_revision, state, atomicity, manifest_digest)"
            " VALUES (1, 0, ?, ?, '')",
            (CLEAN, MANAGED),
        )
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
        raise LedgerError(
            f"no knowledge_state row in {db_path(root)}; run ensure_schema first")
    return Revision(revision=row[0], state=row[1],
                    atomicity=row[2], manifest_digest=row[3])


def ledger_fingerprint(root=None) -> str:
    """A digest of everything the ledger asserts.

    The session guard needs to detect ANY production-ledger mutation, not only
    a revision bump. `CLEAN -> SUSPECT`, an atomicity change, a mutation row
    reaching ORPHANED and a reconciliation record all leave the revision
    counter untouched.
    """
    path = db_path(root)
    if not path.is_file():
        return "absent"
    conn = _connect(root)
    try:
        h = hashlib.sha256()
        for table, cols in (
            ("knowledge_state",
             "id, current_revision, state, atomicity, manifest_digest"),
            ("mutations",
             "mutation_id, state, planned_paths, completed_paths,"
             " orphan_reason, resolved_at, resolution_revision"),
            ("reconciliations",
             "reconciliation_id, at, actor, from_revision, to_revision"),
        ):
            try:
                rows = conn.execute(
                    f"SELECT {cols} FROM {table} ORDER BY 1").fetchall()
            except sqlite3.Error:
                rows = [("<missing>",)]
            for row in rows:
                h.update(repr(row).encode())
                h.update(b"\x00")
        return h.hexdigest()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: the revision ledger, with checked states and a fingerprint"
```

---

### Task 4: Process liveness has three states

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 3's module
- Produces:
  - `ALIVE = "ALIVE"`, `DEAD = "DEAD"`, `UNKNOWN = "UNKNOWN"`
  - `process_identity(pid: int) -> str | None`
  - `own_identity() -> str`
  - `process_state(pid, identity) -> str`

Rev 2 collapsed "the process does not exist", "`ps` failed", "`ps` timed out" and "permissions prevented inspection" into one empty string, and then treated the absence of proof of life as proof of death. Only `DEAD` may orphan a mutation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_our_own_process_is_alive(root):
    assert kr.process_state(os.getpid(), kr.own_identity()) == kr.ALIVE


def test_a_pid_that_does_not_exist_is_dead(root):
    assert kr.process_state(2_000_000, "2000000:whenever") == kr.DEAD


def test_a_recycled_pid_is_dead_not_alive(root):
    """A pid alone is recycled. Pairing it with a start time stops an unrelated
    new process vouching for a dead mutation."""
    assert kr.process_state(os.getpid(), "different identity") == kr.DEAD


def test_an_uninspectable_process_is_unknown_not_dead(monkeypatch, root):
    """`ps` failing is not evidence of death, and orphaning on it would mark
    the store SUSPECT because a subprocess timed out."""
    def explode(*a, **k):
        raise OSError("ps unavailable")
    monkeypatch.setattr(kr.subprocess, "run", explode)
    assert kr.process_state(os.getpid(), "whatever") == kr.UNKNOWN


def test_own_identity_is_cached_per_pid_not_globally(root, monkeypatch):
    """A forked child inherits the parent's module state. Caching without the
    pid would let it answer with its parent's identity."""
    mine = kr.own_identity()
    assert kr._OWN_IDENTITY[0] == os.getpid()
    monkeypatch.setattr(kr.os, "getpid", lambda: 4242)
    monkeypatch.setattr(kr, "process_identity", lambda pid: f"{pid}:pretend")
    assert kr.own_identity() == "4242:pretend"
    assert kr.own_identity() != mine
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k process -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'process_state'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
ALIVE = "ALIVE"
DEAD = "DEAD"
UNKNOWN = "UNKNOWN"

#: (pid, identity). Keyed by pid because a forked child inherits module state
#: and would otherwise answer with its parent's identity. `ps` costs 3.7 ms per
#: call (measured), and this runs on every mutation, so it is worth caching.
_OWN_IDENTITY: tuple | None = None


def process_identity(pid: int) -> str | None:
    """`pid:start-time`, `None` when the process is absent, raises on failure.

    Three outcomes, not two: a missing process is evidence, an unusable `ps` is
    not. `psutil` is not a dependency of this project; `ps -o lstart=` works on
    macOS and Linux.
    """
    out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                         capture_output=True, text=True, timeout=5)
    started = out.stdout.strip()
    return f"{pid}:{started}" if started else None


def own_identity() -> str:
    global _OWN_IDENTITY
    pid = os.getpid()
    if _OWN_IDENTITY is None or _OWN_IDENTITY[0] != pid:
        try:
            identity = process_identity(pid)
        except (OSError, subprocess.SubprocessError):
            identity = None
        _OWN_IDENTITY = (pid, identity or f"{pid}:unknown")
    return _OWN_IDENTITY[1]


def process_state(pid, identity) -> str:
    """ALIVE, DEAD, or UNKNOWN. Only DEAD may orphan a mutation."""
    if not pid:
        return UNKNOWN
    try:
        live = process_identity(int(pid))
    except (OSError, ValueError, subprocess.SubprocessError):
        return UNKNOWN
    if live is None:
        return DEAD
    return ALIVE if live == identity else DEAD
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: liveness is ALIVE, DEAD or UNKNOWN; only DEAD orphans"
```

---
### Task 5: Compare-and-set transitions

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Tasks 3–4
- Produces:
  - `@dataclass Mutation(...)` with `planned_paths`, `completed_paths`, resolution fields
  - `_transition(conn, mutation_id, want, new, **fields) -> None` (raises `MutationStateError`)
  - `mutations_in(state, root=None) -> list[Mutation]`

Rev 2 updated rows by id alone, so `end_mutation(mid)` called twice advanced the revision twice, and `detect_orphans` could reclassify a mutation that committed between the liveness check and the update. Every transition asserts the state it expects and that exactly one row changed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_a_transition_from_the_wrong_state_is_refused(root):
    conn = kr._connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO mutations (mutation_id, base_revision, state,"
            " owner_identity, started_at) VALUES ('m1', 0, ?, 'x', 'now')",
            (kr.COMMITTED,))
        with pytest.raises(kr.MutationStateError):
            kr._transition(conn, "m1", want=kr.ACTIVE, new=kr.COMMITTED)
        conn.execute("ROLLBACK")
    finally:
        conn.close()


def test_a_transition_on_a_missing_row_is_refused(root):
    conn = kr._connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(kr.MutationStateError):
            kr._transition(conn, "nope", want=kr.ACTIVE, new=kr.COMMITTED)
        conn.execute("ROLLBACK")
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k transition -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute '_transition'`

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
    caller: str | None
    owner_pid: int | None
    owner_identity: str
    started_at: str
    committed_at: str | None
    planned_paths: list
    completed_paths: list
    orphan_reason: str | None
    orphaned_at: str | None
    resolved_at: str | None
    resolution_revision: int | None


_MUTATION_COLS = (
    "mutation_id, base_revision, state, project, writer, implicit, caller,"
    " owner_pid, owner_identity, started_at, committed_at,"
    " planned_paths, completed_paths, orphan_reason, orphaned_at,"
    " resolved_at, resolution_revision"
)


def _row_to_mutation(row) -> Mutation:
    return Mutation(
        mutation_id=row[0], base_revision=row[1], state=row[2], project=row[3],
        writer=row[4], implicit=bool(row[5]), caller=row[6], owner_pid=row[7],
        owner_identity=row[8], started_at=row[9], committed_at=row[10],
        planned_paths=json.loads(row[11] or "[]"),
        completed_paths=json.loads(row[12] or "[]"),
        orphan_reason=row[13], orphaned_at=row[14],
        resolved_at=row[15], resolution_revision=row[16],
    )


def _transition(conn, mutation_id, want, new, **fields) -> None:
    """Move a mutation from `want` to `new`, or raise.

    Compare-and-set, inside the caller's transaction. Updating by id alone let
    `end_mutation` run twice and advance the revision twice, and let orphan
    detection reclassify a mutation that had committed between the liveness
    check and the update.
    """
    assignments = ["state = ?"]
    values = [new]
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.extend([mutation_id, want])
    cur = conn.execute(
        f"UPDATE mutations SET {', '.join(assignments)}"
        " WHERE mutation_id = ? AND state = ?", values)
    if cur.rowcount != 1:
        row = conn.execute(
            "SELECT state FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        found = row[0] if row else "<no such mutation>"
        raise MutationStateError(
            f"{mutation_id}: expected {want} to become {new}, found {found}")


def mutations_in(state, root=None) -> list:
    conn = _connect(root)
    try:
        rows = conn.execute(
            f"SELECT {_MUTATION_COLS} FROM mutations WHERE state = ?"
            " ORDER BY started_at", (state,)).fetchall()
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: ledger transitions are compare-and-set"
```

---

### Task 6: The two-phase write lifecycle

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4, 5
- Produces:
  - `@dataclass Operation(root, project, writer, caller, mutation_id, depth, implicit)`
  - `@dataclass WriteTicket(mutation_id, owns)`
  - `operation(project=None, writer=None, caller=None, root=None)` — context manager
  - `plan_write(path, root=None) -> WriteTicket | None`
  - `complete_write(ticket, path, root=None) -> None`
  - `active_operation() -> Operation | None`
  - `commit_mutation(mutation_id, root=None) -> int`, `abandon_mutation(mutation_id, reason, root=None) -> str`

**This is the task the whole plan turns on.** Three properties, each fixing a separate defect:

1. **Two phases.** `plan_write` runs before the bytes land, `complete_write` after. Rev 2 hooked only `_stamp`, which runs *after* publication, so a crash in the gap left both lists empty and orphan detection concluded `ABORTED` with the store `CLEAN` — while the file had already changed. That is the precise crash `planned_paths` exists for.
2. **Lazy activation.** The row is inserted on the first *planned* write. `zurvan search` therefore inserts nothing, takes no write lock, and cannot make a capture report BUSY.
3. **Nesting joins.** A single module global meant an inner operation cleared `_ACTIVE` on exit and the outer operation's later writes became implicit, splitting one logical operation without anyone knowing. A `ContextVar` holds one operation per context; an inner `operation()` on the same store joins it and does not own the commit.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def _write(root, name, body="x"):
    """Publish bytes the way `safe_write` will: plan, write, complete."""
    target = page(root, name)
    ticket = kr.plan_write(target, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    kr.complete_write(ticket, target, root=root)
    return target


def test_a_read_only_operation_creates_no_mutation_row(root):
    """`zurvan search` must not become a registry write, and must not make a
    capture report BUSY while it runs."""
    with kr.operation(writer="cli", root=root):
        pass
    conn = sqlite3.connect(kr.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) FROM mutations").fetchone()[0] == 0
    finally:
        conn.close()
    assert kr.current(root).revision == 0


def test_a_mutation_activates_on_the_first_planned_write(root):
    with kr.operation(writer="cli", root=root):
        assert kr.mutations_in(kr.ACTIVE, root) == []
        _write(root, "a.md")
        assert len(kr.mutations_in(kr.ACTIVE, root)) == 1
    assert kr.current(root).revision == 1


def test_intent_is_recorded_before_the_bytes_land(root):
    """The crash-gap property. Between plan and publish, the ledger must
    already name the path that is about to change."""
    target = page(root, "a.md")
    with kr.operation(writer="cli", root=root):
        ticket = kr.plan_write(target, root=root)
        m = kr.mutations_in(kr.ACTIVE, root)[0]
        assert str(target) in m.planned_paths
        assert m.completed_paths == []
        target.write_text("x")
        kr.complete_write(ticket, target, root=root)


def test_a_crash_between_publishing_and_completing_is_not_read_as_clean(root):
    """Rev 2's defect. The file landed; bookkeeping never recorded it. The
    store must not come back CLEAN just because `completed_paths` is empty."""
    target = page(root, "a.md")
    mid = None
    with pytest.raises(RuntimeError):
        with kr.operation(writer="cli", root=root):
            kr.plan_write(target, root=root)
            target.write_text("x")               # bytes are live
            mid = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
            raise RuntimeError("killed here")
    assert kr.current(root).state == kr.SUSPECT
    m = [x for x in kr.mutations_in(kr.ORPHANED, root) if x.mutation_id == mid]
    assert m and m[0].planned_paths == [str(target)]


def test_three_writes_in_one_operation_are_one_revision(root):
    with kr.operation(writer="cli", root=root):
        for name in ("a.md", "b.md", "log.md"):
            _write(root, name)
    assert kr.current(root).revision == 1


def test_a_nested_operation_joins_rather_than_splitting(root):
    """A single module global let an inner operation clear the active id, so
    the outer operation's later writes silently became implicit."""
    with kr.operation(writer="outer", root=root):
        _write(root, "a.md")
        outer = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
        with kr.operation(writer="inner", root=root):
            _write(root, "b.md")
        assert [m.mutation_id for m in kr.mutations_in(kr.ACTIVE, root)] == [outer]
        _write(root, "c.md")
    assert kr.current(root).revision == 1
    conn = sqlite3.connect(kr.db_path(root))
    try:
        assert conn.execute("SELECT COUNT(*) FROM mutations").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_nested_operation_on_a_different_store_is_refused(root, tmp_path):
    other = tmp_path / "other"
    (other / "data").mkdir(parents=True)
    (other / "wiki").mkdir()
    kr.ensure_schema(other)
    with kr.operation(writer="outer", root=root):
        with pytest.raises(kr.NestedMutationError):
            with kr.operation(writer="inner", root=other):
                pass


def test_a_clean_exit_commits_rather_than_abandoning(root):
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            _write(root, "a.md")
            raise SystemExit(0)
    assert kr.current(root).revision == 1
    assert kr.current(root).state == kr.CLEAN


def test_a_failing_exit_that_wrote_nothing_does_not_suspect_the_store(root):
    """~20 dispatch branches in cli.py call sys.exit(1) on validation failure,
    before touching the store."""
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            raise SystemExit(1)
    assert kr.current(root).state == kr.CLEAN
    assert kr.current(root).revision == 0


def test_a_failing_exit_after_a_write_is_suspect(root):
    with pytest.raises(SystemExit):
        with kr.operation(writer="cli", root=root):
            _write(root, "a.md")
            raise SystemExit(1)
    assert kr.current(root).state == kr.SUSPECT


def test_a_write_outside_the_authoritative_scope_creates_no_mutation(root):
    (root / "docs").mkdir(exist_ok=True)
    with kr.operation(writer="cli", root=root):
        assert kr.plan_write(root / "docs" / "x.md", root=root) is None
    assert kr.current(root).revision == 0


def test_committing_twice_is_refused_rather_than_advancing_twice(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
        mid = kr.mutations_in(kr.ACTIVE, root)[0].mutation_id
    assert kr.current(root).revision == 1
    with pytest.raises(kr.MutationStateError):
        kr.commit_mutation(mid, root=root)
    assert kr.current(root).revision == 1


def test_no_observer_sees_a_half_completed_logical_operation(root):
    observed = []

    def observe():
        if kr.mutations_in(kr.ACTIVE, root):
            observed.append("BUSY")
        else:
            observed.append(kr.current(root).revision)

    observe()
    with kr.operation(writer="cli", root=root):
        _write(root, "decisions/foo.md")
        observe()
        _write(root, "log.md")
    observe()

    assert observed == [0, "BUSY", 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k operation -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'operation'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
@dataclass
class Operation:
    root: Path
    project: str | None = None
    writer: str | None = None
    caller: str | None = None
    implicit: bool = False
    mutation_id: str | None = None
    depth: int = 0          # nesting depth; join-only, never used to commit


@dataclass
class WriteTicket:
    mutation_id: str
    owns: bool          # True when this single write must commit the mutation


#: One operation per context. A module global let an inner operation clear the
#: active id on exit, after which the outer operation's remaining writes became
#: implicit — splitting one logical operation without anyone knowing.
#:
#: Deliberately NOT an environment variable. Rev 2 exported the mutation id so
#: subprocesses could join, and `zurvan --project X` then re-invoked the CLI
#: against a DIFFERENT store with the id still set — writes there matched no
#: row and were silently dropped. A subprocess now opens its own operation in
#: its own store, which is the honest answer.
_CTX: ContextVar = ContextVar("zurvan_operation", default=None)


def active_operation():
    return _CTX.get()


def _describe_caller() -> str:
    """`module:function` of the first frame outside this module and safe_write.

    `writer="implicit"` told a migration nothing about WHAT to migrate. The
    inventory exists to be actioned, so it records something actionable. Names
    only — never arguments, which on this store can be private content.
    """
    import sys
    frame = sys._getframe(1)
    skip = {__name__, "scripts.safe_write"}
    while frame is not None:
        module = frame.f_globals.get("__name__", "?")
        if module not in skip:
            return f"{module}:{frame.f_code.co_name}"
        frame = frame.f_back
    return "unknown"


def _insert_mutation(op: Operation) -> str:
    mutation_id = f"m{uuid.uuid4().hex[:12]}"
    pid = os.getpid()
    ensure_schema(op.root)
    conn = _connect(op.root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            "SELECT current_revision FROM knowledge_state WHERE id = 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO mutations (mutation_id, base_revision, state, project,"
            " writer, implicit, caller, owner_pid, owner_identity, started_at,"
            " last_heartbeat) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mutation_id, base, ACTIVE, op.project, op.writer,
             int(bool(op.implicit)), op.caller, pid, own_identity(),
             _now(), _now()))
        conn.execute("COMMIT")
    finally:
        conn.close()
    return mutation_id


def _append_path(mutation_id, column, path, root) -> None:
    """Append one path to `planned_paths` or `completed_paths`. Fails closed."""
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT {column}, state FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        if row is None or row[1] != ACTIVE:
            conn.execute("ROLLBACK")
            raise MutationStateError(
                f"{mutation_id}: cannot record {column} in state "
                f"{row[1] if row else '<missing>'}")
        paths = json.loads(row[0] or "[]")
        if str(path) not in paths:
            paths.append(str(path))
        conn.execute(
            f"UPDATE mutations SET {column} = ?, last_heartbeat = ?"
            " WHERE mutation_id = ? AND state = ?",
            (json.dumps(paths), _now(), mutation_id, ACTIVE))
        conn.execute("COMMIT")
    finally:
        conn.close()


def plan_write(path, root=None):
    """Record the intent to write BEFORE the bytes land.

    Returns a `WriteTicket`, or `None` when the path is outside the
    authoritative scope. Activates the operation's mutation row lazily, so an
    operation that never writes leaves no trace in the ledger.

    Raises rather than returning quietly: a write whose intent could not be
    recorded must not proceed. `writelog` may be best-effort; this may not.
    """
    base = ks.store_root(root)
    if not ks.is_authoritative(path, base):
        return None
    op = _CTX.get()
    owns = False
    if op is None or op.root != base:
        op = Operation(root=base, writer="implicit", implicit=True,
                       caller=_describe_caller())
        owns = True
    if op.mutation_id is None:
        op.mutation_id = _insert_mutation(op)
    _append_path(op.mutation_id, "planned_paths", path, base)
    return WriteTicket(mutation_id=op.mutation_id, owns=owns)


def complete_write(ticket, path, root=None) -> None:
    """Record that the bytes landed. Fails closed, loudly.

    If this raises, the file is already published and the ledger does not know
    it — a correctness incident. The store is marked SUSPECT before the error
    propagates, because the alternative is a knowledge base that silently
    forgot a write it performed.
    """
    if ticket is None:
        return
    base = ks.store_root(root)
    try:
        _append_path(ticket.mutation_id, "completed_paths", path, base)
    except Exception:
        _mark_suspect(base)
        raise
    if ticket.owns:
        commit_mutation(ticket.mutation_id, root=base)


def _mark_suspect(root) -> None:
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE knowledge_state SET state = ? WHERE id = 1",
                     (SUSPECT,))
        conn.execute("COMMIT")
    finally:
        conn.close()


def commit_mutation(mutation_id, manifest_digest=None, root=None) -> int:
    """Commit one mutation and advance the revision by exactly one."""
    base = ks.store_root(root)
    if manifest_digest is None:
        from scripts.knowledge_manifest import authoritative_manifest
        manifest_digest = authoritative_manifest(base)
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rev = conn.execute(
                "SELECT current_revision FROM knowledge_state WHERE id = 1"
            ).fetchone()[0]
            new_revision = rev + 1
            _transition(conn, mutation_id, want=ACTIVE, new=COMMITTED,
                        committed_at=_now())
            conn.execute(
                "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
                " manifest_digest = ? WHERE id = 1",
                (new_revision, MANAGED, manifest_digest))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision


def abandon_mutation(mutation_id, reason, root=None) -> str:
    """End a mutation that did not complete.

    Nothing PLANNED -> ABORTED, knowledge state untouched: a command that
    failed on its arguments tore nothing. Anything planned -> ORPHANED and the
    revision becomes SUSPECT. Rev 2 keyed this on `completed_paths`, which is
    empty precisely in the crash this is meant to catch.
    """
    base = ks.store_root(root)
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT planned_paths FROM mutations WHERE mutation_id = ?",
                (mutation_id,)).fetchone()
            planned = json.loads((row[0] if row else None) or "[]")
            state = ORPHANED if planned else ABORTED
            _transition(conn, mutation_id, want=ACTIVE, new=state,
                        orphan_reason=reason, orphaned_at=_now(),
                        committed_at=_now())
            if state == ORPHANED:
                conn.execute(
                    "UPDATE knowledge_state SET state = ? WHERE id = 1",
                    (SUSPECT,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return state


def _finish(op: Operation, ok: bool, reason: str = "") -> None:
    if op.mutation_id is None:
        return                       # nothing was ever planned; no row exists
    if ok:
        commit_mutation(op.mutation_id, root=op.root)
    else:
        abandon_mutation(op.mutation_id, reason, root=op.root)


@contextmanager
def operation(project=None, writer=None, caller=None, root=None):
    """Scope one logical operation. Joins an enclosing one on the same store.

    No database row is created here. The mutation activates on the first
    planned write, so a read-only command leaves the ledger untouched and
    cannot make a snapshot capture report BUSY.
    """
    base = ks.store_root(root)
    existing = _CTX.get()
    if existing is not None:
        if existing.root != base:
            raise NestedMutationError(
                f"operation on {base} opened inside one on {existing.root}")
        existing.depth += 1
        try:
            yield existing
        finally:
            existing.depth -= 1
        return

    op = Operation(root=base, project=project, writer=writer, caller=caller)
    token = _CTX.set(op)
    try:
        try:
            yield op
        except SystemExit as exc:
            # `sys.exit` is control flow, not a crash: `scripts/cli.py` uses it
            # on ~20 dispatch branches. Treating it as a crash marked the
            # PRODUCTION store SUSPECT on every non-zero exit — including
            # `zurvan knowledge reconcile`, which exits 1 on NEEDS_REVIEW and
            # would SUSPECT the store it had just cleared.
            _finish(op, ok=exc.code in (0, None), reason=f"exit {exc.code}")
            raise
        except BaseException as exc:
            _finish(op, ok=False, reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            _finish(op, ok=True)
    finally:
        _CTX.reset(token)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 26 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: two-phase writes, lazy activation, operations that nest"
```

---

### Task 7: `safe_write` participates around the bytes

**Files:**
- Modify: `scripts/safe_write.py:62-95`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 6's `plan_write`, `complete_write`
- Produces: no new API. `write_file_safely` / `append_file_safely` keep their signatures and their `bool` return.

`_stamp` stays exactly as it is — `writelog` is telemetry and its best-effort contract is correct. The ledger calls go around the bytes instead, and they are **not** swallowed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_safe_write_records_intent_before_publishing(root, monkeypatch):
    """If the ledger cannot record the intent, the bytes must not land."""
    from scripts import safe_write

    def refuse(*a, **k):
        raise kr.LedgerError("ledger down")

    monkeypatch.setattr(kr, "plan_write", refuse)
    target = page(root, "a.md")
    with pytest.raises(kr.LedgerError):
        safe_write.write_file_safely(str(target), "alpha\n")
    assert not target.exists()


def test_safe_write_joins_the_active_operation(root):
    from scripts import safe_write
    with kr.operation(writer="cli", root=root):
        safe_write.write_file_safely(str(page(root, "a.md")), "alpha\n")
        safe_write.append_file_safely(str(page(root, "log.md")), "line\n")
        actives = kr.mutations_in(kr.ACTIVE, root)
        assert len(actives) == 1
        assert len(actives[0].completed_paths) == 2
    assert kr.current(root).revision == 1


def test_a_write_outside_any_operation_is_one_implicit_mutation(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "alpha\n")
    assert kr.current(root).revision == 1
    assert len(kr.implicit_mutations(root)) == 1


def test_two_unwrapped_writes_are_two_implicit_mutations(root):
    """The compatibility door, and its limit. An observer sampling between
    them sees no ACTIVE mutation and a coherent-looking revision over a
    half-finished operation. Z0 may detect and report this shape; it may not
    claim atomicity for it."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "decisions/foo.md")), "page\n")
    between = (kr.mutations_in(kr.ACTIVE, root), kr.current(root).revision)
    safe_write.append_file_safely(str(page(root, "log.md")), "line\n")
    assert between == ([], 1)
    assert len(kr.implicit_mutations(root)) == 2


def test_a_write_outside_the_wiki_touches_no_ledger(root):
    from scripts import safe_write
    (root / "docs").mkdir(exist_ok=True)
    safe_write.write_file_safely(str(root / "docs" / "x.md"), "x")
    assert kr.current(root).revision == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k safe_write -v`
Expected: FAIL — writes currently bypass the ledger entirely.

- [ ] **Step 3: Write minimal implementation**

In `scripts/safe_write.py`, replace `write_file_safely` and `append_file_safely` (lines 62-95) with:

```python
def _ledger():
    """Imported lazily so `safe_write` stays importable without the ledger."""
    from scripts import knowledge_revision
    return knowledge_revision


def write_file_safely(file_path: str, content: str) -> bool:
    """
    Writes content to file_path after checking security rules.
    Returns True on success, False if security violation.

    The write is two-phase against the knowledge ledger: intent is recorded
    BEFORE the bytes land and success AFTER. `_stamp` runs after publication
    and is best-effort, which is right for telemetry and wrong for coherence —
    a crash between publication and bookkeeping must leave evidence that
    something was about to change, and it can only do that if the intent was
    recorded first.
    """
    target = Path(file_path)

    if not is_safe_path(target):
        return False

    kr = _ledger()
    ticket = kr.plan_write(target)          # raises rather than writing blind

    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, 'w', encoding='utf-8') as f:
        f.write(content)

    kr.complete_write(ticket, target)
    _stamp(target, "write")
    return True


def append_file_safely(file_path: str, content: str) -> bool:
    """
    Appends content to file_path after checking security rules.
    """
    target = Path(file_path)

    if not is_safe_path(target):
        return False

    kr = _ledger()
    ticket = kr.plan_write(target)

    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, 'a', encoding='utf-8') as f:
        f.write(content)

    kr.complete_write(ticket, target)
    _stamp(target, "append")

    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 31 passed

- [ ] **Step 5: Run the full suite — first task to change a shared path**

Run: `PYTHONPATH=. python -m pytest -q`
Expected: 0 failed. `memory.py` and `wiki_merge.py` both call `safe_write`, so every test that writes a page now touches the ledger. A failure caused by a missing `data/` directory is a real finding about `_connect` creating its parent — fix it in `knowledge_revision`, not in the test.

- [ ] **Step 6: Commit**

```bash
git add scripts/safe_write.py tests/test_knowledge_revision.py
git commit -m "zurvan: safe_write records intent before the bytes and success after"
```

---
### Task 8: An implicit inventory that can actually reach zero

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Tasks 5–7
- Produces:
  - `implicit_mutations(root=None, unresolved_only=True) -> list[Mutation]`
  - `resolve_implicit(caller, actor, root=None) -> int`
  - `implicit_callers(root=None) -> dict[str, int]`

Two defects. `implicit_mutations()` returned **all historical** implicit rows, so the closure gate "inventory empty" could never pass without deleting history — which this repository forbids. And `writer="implicit"` named nothing that could be migrated. Task 6 already records `caller` as `module:function`; this task makes the inventory resolvable and closure reachable.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_the_caller_is_the_real_caller_not_the_plumbing(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    assert "safe_write" not in caller
    assert "knowledge_revision" not in caller


def test_resolving_a_caller_empties_the_inventory_without_deleting_history(root):
    """The closure gate must be reachable. Rev 2 counted historical rows
    forever, so `inventory empty` could only be achieved by erasing evidence."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    assert len(kr.implicit_mutations(root)) == 1

    resolved = kr.resolve_implicit(caller, actor="local:raouf", root=root)

    assert resolved == 1
    assert kr.implicit_mutations(root) == []                     # unresolved
    assert len(kr.implicit_mutations(root, unresolved_only=False)) == 1


def test_a_new_implicit_write_from_a_resolved_caller_reopens_the_inventory(root):
    """Resolution is a claim about a caller, and a claim a later write can
    falsify. Closure means no UNRESOLVED implicit callers, not 'we looked once'."""
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    caller = kr.implicit_mutations(root)[0].caller
    kr.resolve_implicit(caller, actor="local:raouf", root=root)
    assert kr.implicit_mutations(root) == []

    safe_write.write_file_safely(str(page(root, "b.md")), "y")

    assert len(kr.implicit_mutations(root)) == 1


def test_the_caller_summary_is_a_query_not_an_audit(root):
    from scripts import safe_write
    safe_write.write_file_safely(str(page(root, "a.md")), "x")
    safe_write.write_file_safely(str(page(root, "b.md")), "y")
    summary = kr.implicit_callers(root)
    assert sum(summary.values()) == 2
    assert all(":" in k for k in summary)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k implicit -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'implicit_mutations'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
def implicit_mutations(root=None, unresolved_only=True) -> list:
    """Implicit mutations, unresolved by default.

    The migration gate is "no unresolved implicit callers", not "no historical
    implicit rows": counting history made closure reachable only by deleting
    evidence, which this project does not do.
    """
    clause = " AND resolved_at IS NULL" if unresolved_only else ""
    conn = _connect(root)
    try:
        rows = conn.execute(
            f"SELECT {_MUTATION_COLS} FROM mutations"
            f" WHERE implicit = 1{clause} ORDER BY started_at").fetchall()
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]


def implicit_callers(root=None) -> dict:
    """Unresolved implicit callers and how often each one wrote."""
    summary: dict = {}
    for m in implicit_mutations(root):
        key = m.caller or "unknown"
        summary[key] = summary.get(key, 0) + 1
    return summary


def resolve_implicit(caller, actor, root=None) -> int:
    """Mark every unresolved implicit mutation from `caller` as migrated.

    A claim about a caller, which a later implicit write from the same caller
    falsifies by simply appearing in the inventory again.
    """
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE mutations SET resolved_at = ?, resolution_actor = ?,"
            " resolution_reason = 'migrated to an explicit operation'"
            " WHERE implicit = 1 AND resolved_at IS NULL AND caller = ?",
            (_now(), actor, caller))
        count = cur.rowcount
        conn.execute("COMMIT")
    finally:
        conn.close()
    return count
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 35 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: the implicit inventory names its callers and can reach zero"
```

---

### Task 9: Manifest binding, bootstrap, and atomic drift observation

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 2's `authoritative_manifest`; Tasks 5–6
- Produces:
  - `bootstrap(root=None, actor="bootstrap") -> int`
  - `observe_external_drift(root=None) -> int | None` — detection and minting in one locked operation

Three defects. `check_drift()` returned `None` on an empty recorded digest, so a fresh store's first call fed `None` into `mint_external_revision`, violating `manifest_digest NOT NULL` — **measured**: `IntegrityError: NOT NULL constraint failed: knowledge_state.manifest_digest`. "Empty digest means nothing to contradict" is not safe once the ledger is a correctness mechanism. And splitting detection from minting left a TOCTOU window between them.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def test_committing_records_the_manifest_the_revision_represents(root):
    from scripts import knowledge_manifest as km
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    assert kr.current(root).manifest_digest == km.authoritative_manifest(root)


def test_an_existing_store_must_be_bootstrapped_before_drift_means_anything(root):
    """A fresh ledger records no digest, so there is nothing to compare
    against. Rev 2 answered `None` and then fed it to an UPDATE against a
    NOT NULL column — measured IntegrityError."""
    _ = page(root, "a.md").write_text("alpha\n")
    with pytest.raises(kr.LedgerError):
        kr.observe_external_drift(root)


def test_bootstrap_records_the_stable_manifest_as_external_observed(root):
    from scripts import knowledge_manifest as km
    page(root, "a.md").write_text("alpha\n")

    new = kr.bootstrap(root, actor="local:raouf")

    rev = kr.current(root)
    assert new == 1
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.EXTERNAL_OBSERVED
    assert rev.manifest_digest == km.authoritative_manifest(root)


def test_no_drift_is_reported_for_a_store_only_zurvan_touched(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    assert kr.observe_external_drift(root) is None


def test_a_completed_edit_outside_zurvan_mints_an_external_observed_revision(root):
    """The hole the double manifest cannot see: the edit finished long before
    anyone looked, so before/after stability holds perfectly. Ordinary manual
    editing is a supported workflow, so this is CLEAN with a weaker atomicity
    claim, not a broken store."""
    from scripts import knowledge_manifest as km
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    before = kr.current(root).revision

    page(root, "a.md").write_text("edited in Obsidian\n")     # never told Zurvan
    new = kr.observe_external_drift(root)

    rev = kr.current(root)
    assert new == before + 1
    assert rev.state == kr.CLEAN
    assert rev.atomicity == kr.EXTERNAL_OBSERVED
    assert rev.manifest_digest == km.authoritative_manifest(root)
    assert kr.observe_external_drift(root) is None


def test_a_managed_operation_after_drift_restores_managed_atomicity(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    page(root, "a.md").write_text("edited\n")
    kr.observe_external_drift(root)
    assert kr.current(root).atomicity == kr.EXTERNAL_OBSERVED

    with kr.operation(writer="cli", root=root):
        _write(root, "b.md")

    assert kr.current(root).atomicity == kr.MANAGED


def test_external_observed_claims_stability_not_a_transaction(root):
    """It asserts these bytes formed one stable observed filesystem state. It
    does NOT assert one logical user transaction, and no consumer may read it
    as MANAGED."""
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
    page(root, "a.md").write_text("edited\n")
    kr.observe_external_drift(root)
    assert kr.current(root).atomicity != kr.MANAGED


def test_drift_is_refused_while_an_operation_is_in_flight(root):
    """Minting a revision underneath a running mutation would attribute that
    mutation's half-written files to an external observation.

    The first operation is not scene-setting. Without it the store has no
    recorded digest, `observe_external_drift` raises the BOOTSTRAP error first,
    and this test passes while exercising nothing it claims to.
    """
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")                       # commits; digest recorded
    with kr.operation(writer="cli", root=root):
        _write(root, "b.md")                       # now in flight
        with pytest.raises(kr.LedgerError, match="in flight"):
            kr.observe_external_drift(root)


def test_drift_is_refused_while_the_store_is_suspect(root):
    """Same trap: bootstrap first, or the bootstrap error answers instead."""
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")                       # commits; digest recorded
    with pytest.raises(RuntimeError):
        with kr.operation(writer="cli", root=root):
            _write(root, "b.md")
            raise RuntimeError("torn")
    assert kr.current(root).state == kr.SUSPECT
    with pytest.raises(kr.LedgerError, match="SUSPECT"):
        kr.observe_external_drift(root)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k drift -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'observe_external_drift'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
def _set_revision(conn, new_revision, atomicity, digest) -> None:
    conn.execute(
        "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
        " manifest_digest = ? WHERE id = 1",
        (new_revision, atomicity, digest))


def bootstrap(root=None, actor="bootstrap") -> int:
    """Record the stable authoritative manifest of an existing store.

    A fresh ledger has no digest, so it can neither agree nor disagree with the
    filesystem. Treating that as "nothing to contradict" was safe for a
    diagnostic and is not safe for a correctness mechanism: it makes the first
    drift check silently vacuous. Bootstrapping is deliberate, and the revision
    it produces is honestly labelled EXTERNAL_OBSERVED, because Zurvan did not
    witness the operations that produced those bytes.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    ensure_schema(base)
    first = authoritative_manifest(base)
    second = authoritative_manifest(base)
    if first != second:
        raise LedgerError(
            "the store is changing while being bootstrapped; retry when quiet")
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise LedgerError("a mutation is in flight; bootstrap when quiet")
            rev = conn.execute(
                "SELECT current_revision FROM knowledge_state WHERE id = 1"
            ).fetchone()[0]
            new_revision = rev + 1
            _set_revision(conn, new_revision, EXTERNAL_OBSERVED, first)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision


def observe_external_drift(root=None):
    """Detect a completed unmanaged edit and record it, in one operation.

    Two different questions, both required:

        M_before == M_after                  these bytes were STABLE
        live == revision.manifest_digest     Zurvan KNEW about them

    A finished Obsidian edit passes stability perfectly and fails the second.
    Detection and minting are one call because splitting them leaves a window
    in which the filesystem can move between what was observed and what is
    recorded.

    Returns the new revision, or `None` when Zurvan's view is already current.
    Refuses on an unbootstrapped store, a SUSPECT store, or an in-flight
    mutation — minting underneath a running operation would attribute its
    half-written files to an external observation.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    state = current(base)
    if not state.manifest_digest:
        raise LedgerError(
            "no manifest recorded for this store; run `zurvan knowledge "
            "bootstrap` once so drift has a baseline to contradict")
    if state.state != CLEAN:
        raise LedgerError(
            f"store is {state.state}; reconcile before observing drift")

    first = authoritative_manifest(base)
    if first == state.manifest_digest:
        return None
    second = authoritative_manifest(base)
    if first != second:
        raise LedgerError("the store is still changing; retry when quiet")

    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise LedgerError("a mutation is in flight; not external drift")
            rev, recorded, st = conn.execute(
                "SELECT current_revision, manifest_digest, state"
                " FROM knowledge_state WHERE id = 1").fetchone()
            if st != CLEAN or recorded != state.manifest_digest:
                raise LedgerError("the ledger moved during observation; retry")
            new_revision = rev + 1
            _set_revision(conn, new_revision, EXTERNAL_OBSERVED, second)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 44 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: bootstrap the manifest baseline; observe drift atomically"
```

---

### Task 10: Orphans are found by death, not by silence

**Files:**
- Modify: `scripts/knowledge_revision.py`
- Modify: `tests/test_knowledge_revision.py`

**Interfaces:**
- Consumes: Task 4's `process_state`; Task 5's `_transition`
- Produces: `detect_orphans(root=None) -> list[Mutation]`

Rev 2 read the active list on one connection and updated on another, so a mutation that committed in between could be reclassified from stale information; and it decided ABORTED-vs-ORPHANED on `completed_paths`, which is empty in exactly the crash this exists to catch. Detection now re-reads under the lock, transitions with compare-and-set, keys the decision on `planned_paths`, and orphans only on `DEAD`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def _orphan(root, planned, completed=()):
    """A process that died mid-mutation: an ACTIVE row whose owner identity
    belongs to no live process. Nothing is killed for real — a test that kills
    processes to check a database column is testing the wrong thing.

    Every path is absolute and under this test's root.
    """
    op = kr.Operation(root=root, project="other", writer="test")
    mid = kr._insert_mutation(op)
    for p in planned:
        kr._append_path(mid, "planned_paths", p, root)
    for p in completed:
        kr._append_path(mid, "completed_paths", p, root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute(
            "UPDATE mutations SET owner_pid = ?, owner_identity = ?"
            " WHERE mutation_id = ?", (2_000_000, "2000000:long ago", mid))
        conn.commit()
    finally:
        conn.close()
    return mid


def test_a_dead_owner_that_planned_a_write_is_an_orphan(root):
    a, b = str(page(root, "decisions/x.md")), str(page(root, "log.md"))
    mid = _orphan(root, planned=[a, b], completed=[a])

    orphans = kr.detect_orphans(root)

    assert [m.mutation_id for m in orphans] == [mid]
    assert kr.current(root).state == kr.SUSPECT


def test_a_crash_after_publishing_but_before_completing_still_orphans(root):
    """Rev 2 keyed this on `completed_paths`, which is empty in precisely this
    crash — so it concluded ABORTED and left the store CLEAN while the file had
    already changed."""
    a = str(page(root, "decisions/x.md"))
    _orphan(root, planned=[a], completed=[])

    orphans = kr.detect_orphans(root)

    assert len(orphans) == 1
    assert orphans[0].planned_paths == [a]
    assert kr.current(root).state == kr.SUSPECT


def test_an_orphan_names_the_path_that_never_completed(root):
    a, b = str(page(root, "decisions/x.md")), str(page(root, "log.md"))
    _orphan(root, planned=[a, b], completed=[a])
    orphan = kr.detect_orphans(root)[0]
    assert set(orphan.planned_paths) - set(orphan.completed_paths) == {b}
    assert orphan.orphaned_at


def test_a_dead_owner_that_planned_nothing_is_aborted_not_suspect(root):
    _orphan(root, planned=[])
    assert kr.detect_orphans(root) == []
    assert kr.current(root).state == kr.CLEAN


def test_a_live_owner_is_never_orphaned(root):
    with kr.operation(writer="cli", root=root):
        _write(root, "a.md")
        assert kr.detect_orphans(root) == []
        assert kr.current(root).state == kr.CLEAN


def test_an_uninspectable_owner_is_left_alone(monkeypatch, root):
    """`ps` failing is not evidence of death. Orphaning on UNKNOWN would mark
    the store SUSPECT because a subprocess timed out."""
    a = str(page(root, "a.md"))
    _orphan(root, planned=[a])
    monkeypatch.setattr(kr, "process_state", lambda *a, **k: kr.UNKNOWN)
    assert kr.detect_orphans(root) == []
    assert kr.current(root).state == kr.CLEAN
    assert len(kr.mutations_in(kr.ACTIVE, root)) == 1


def test_a_mutation_that_commits_during_detection_is_not_reclassified(root,
                                                                     monkeypatch):
    """The read-then-update race. Rev 2 read the active list on one connection
    and updated on another, so a commit in between was overwritten."""
    a = str(page(root, "a.md"))
    mid = _orphan(root, planned=[a])
    original = kr.process_state

    def commit_then_report_dead(*args, **kwargs):
        kr.commit_mutation(mid, manifest_digest="x", root=root)
        return kr.DEAD

    monkeypatch.setattr(kr, "process_state", commit_then_report_dead)
    assert kr.detect_orphans(root) == []
    assert kr.mutations_in(kr.COMMITTED, root)[0].mutation_id == mid


def test_live_writes_continue_while_a_revision_is_suspect(root):
    _orphan(root, planned=[str(page(root, "a.md"))])
    kr.detect_orphans(root)
    assert kr.current(root).state == kr.SUSPECT

    with kr.operation(writer="cli", root=root):
        _write(root, "later.md")

    assert kr.current(root).revision == 1


def test_suspect_propagates_across_an_unrelated_later_operation(root):
    """If R held a half-written page and the next operation only appends a
    session log, R+1 still holds the half-written page. Advancing is not
    repair."""
    _orphan(root, planned=[str(page(root, "a.md")), str(page(root, "b.md"))],
            completed=[str(page(root, "a.md"))])
    kr.detect_orphans(root)

    with kr.operation(writer="cli", root=root):
        _write(root, "log.md")

    assert kr.current(root).state == kr.SUSPECT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k orphan -v`
Expected: FAIL with `AttributeError: module 'scripts.knowledge_revision' has no attribute 'detect_orphans'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/knowledge_revision.py`:

```python
def detect_orphans(root=None) -> list:
    """Transition ACTIVE mutations whose owner is provably dead.

    Only `DEAD` orphans. `UNKNOWN` — a `ps` failure, a timeout, a permission
    problem — leaves the row alone: absence of proof of life is not proof of
    death, and marking a shared store SUSPECT because a subprocess timed out
    would make the flag worthless.

    The decision is keyed on `planned_paths`, not `completed_paths`. A crash
    between publication and bookkeeping leaves `completed_paths` empty while
    the bytes are live, which is exactly the case this exists to catch.

    Candidates are re-read under the lock and transitioned with compare-and-set,
    so a mutation that commits between the liveness check and the update is not
    reclassified from stale information.
    """
    base = ks.store_root(root)
    candidates = [m for m in mutations_in(ACTIVE, base)
                  if process_state(m.owner_pid, m.owner_identity) == DEAD]
    if not candidates:
        return []
    orphaned = []
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            torn = False
            for m in candidates:
                row = conn.execute(
                    "SELECT state, planned_paths FROM mutations"
                    " WHERE mutation_id = ?", (m.mutation_id,)).fetchone()
                if row is None or row[0] != ACTIVE:
                    continue                    # it finished; leave it alone
                planned = json.loads(row[1] or "[]")
                new = ORPHANED if planned else ABORTED
                _transition(conn, m.mutation_id, want=ACTIVE, new=new,
                            orphan_reason="owner_process_dead",
                            orphaned_at=_now())
                if planned:
                    torn = True
                    orphaned.append(m.mutation_id)
            if torn:
                conn.execute(
                    "UPDATE knowledge_state SET state = ? WHERE id = 1",
                    (SUSPECT,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    found = {m.mutation_id: m for m in mutations_in(ORPHANED, base)}
    return [found[i] for i in orphaned if i in found]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 53 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/knowledge_revision.py tests/test_knowledge_revision.py
git commit -m "zurvan: orphans are found by death and by intent, not by silence"
```

---

### Task 11: Reconciliation mints a real revision and leaves a record

**Files:**
- Create: `scripts/knowledge_reconcile.py`
- Create: `tests/test_knowledge_reconcile.py`
- Modify: `scripts/knowledge_revision.py` (the `reconcile_revision` ledger operation)

**Interfaces:**
- Consumes: Tasks 9–10
- Produces:
  - `knowledge_revision.reconcile_revision(mutation_ids, actor, reason, root=None) -> dict`
  - `knowledge_reconcile.OrphanReport`, `inspect(root=None)`, `reconcile(root=None, accept=False, actor=...)`, `format_report(reports, status, revision)`

Four defects, all from the same root: reconciliation flipped a flag instead of performing a ledger operation.

1. It set `state=CLEAN` without advancing the revision or recording a fresh manifest — leaving `revision 14 / pre-orphan manifest / CLEAN`, **the exact laundering the approved design forbids** (§3.5 requires a new CLEAN revision R′).
2. Reconciled orphans stayed `ORPHANED`, so `knowledge doctor` reported a resolved incident forever.
3. `format_report` derived SUSPECT from the *pre*-reconciliation reports, printing `SUSPECT revision …` directly above `status: CLEAN`.
4. Acceptance recorded no actor, time, reason, or manifest — "someone passed `--accept`" was the whole provenance story, in a system whose purpose is durable research memory.

Frontmatter validation reuses `graph_build.parse_frontmatter` rather than calling `yaml` directly. `wiki_merge._parse_fm` and `graph_build.parse_frontmatter` already exist; a third definition of "valid frontmatter" would eventually disagree with ingestion about what Zurvan can read.

- [ ] **Step 1: Write the failing test**

Create `tests/test_knowledge_reconcile.py`:

```python
"""Reconciliation reaches CLEAN without rebuilding, and leaves a record.

The approved design (rev 2, §3.5) requires a NEW CLEAN revision:

    SUSPECT R -> inspect/repair -> explicit decision -> CLEAN R'

Flipping `state = CLEAN` in place leaves the pre-orphan manifest attached to a
revision now declared coherent, which is the laundering the design forbids.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import knowledge_reconcile as krec
from scripts import knowledge_revision as kr
from scripts import knowledge_manifest as km


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    kr.ensure_schema(tmp_path)
    return tmp_path


def _orphan(root, planned, completed=()):
    op = kr.Operation(root=root, project="instagram", writer="test")
    mid = kr._insert_mutation(op)
    for p in planned:
        kr._append_path(mid, "planned_paths", p, root)
    for p in completed:
        kr._append_path(mid, "completed_paths", p, root)
    conn = sqlite3.connect(kr.db_path(root))
    try:
        conn.execute("UPDATE mutations SET owner_pid = ?, owner_identity = ?"
                     " WHERE mutation_id = ?",
                     (2_000_000, "2000000:gone", mid))
        conn.commit()
    finally:
        conn.close()
    kr.detect_orphans(root)
    return mid


def _page(root, name="decisions/x.md", body="---\ntitle: X\n---\n\nbody\n"):
    p = root / "wiki" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_an_incomplete_operation_stops_for_review(root):
    """The page parses perfectly and the log line never arrived. Markdown
    syntax is not evidence that a two-file operation finished."""
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert kr.current(root).state == kr.SUSPECT
    assert result["reports"][0].missing_paths == [str(log)]
    assert result["reports"][0].complete is False


def test_reconcile_never_deletes_anything(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])
    krec.reconcile(root=root)
    assert page.exists()


def test_a_complete_operation_reconciles_into_a_new_clean_revision(root):
    """Every planned path landed; the owner died before committing."""
    page = _page(root)
    _orphan(root, planned=[str(page)], completed=[str(page)])
    before = kr.current(root).revision

    result = krec.reconcile(root=root, actor="local:raouf")

    rev = kr.current(root)
    assert result["status"] == "CLEAN"
    assert rev.state == kr.CLEAN
    assert rev.revision == before + 1                 # a NEW revision, not a flag
    assert rev.manifest_digest == km.authoritative_manifest(root)


def test_the_reconciled_revision_is_external_observed(root):
    """Zurvan did not witness the operation that produced these bytes, so it
    may not claim they were one managed transaction."""
    page = _page(root)
    _orphan(root, planned=[str(page)], completed=[str(page)])
    krec.reconcile(root=root, actor="local:raouf")
    assert kr.current(root).atomicity == kr.EXTERNAL_OBSERVED


def test_an_accepted_reconciliation_clears_an_incomplete_orphan(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired by hand\n")
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root, accept=True, actor="local:raouf")

    assert result["status"] == "CLEAN"
    assert kr.current(root).state == kr.CLEAN


def test_a_reconciled_orphan_reaches_a_terminal_state(root):
    """`knowledge doctor` must stop reporting a resolved incident."""
    page = _page(root)
    mid = _orphan(root, planned=[str(page)], completed=[str(page)])
    krec.reconcile(root=root, actor="local:raouf")

    assert krec.inspect(root) == []
    resolved = kr.mutations_in(kr.RESOLVED, root)
    assert [m.mutation_id for m in resolved] == [mid]
    assert resolved[0].resolved_at
    assert resolved[0].resolution_revision == kr.current(root).revision


def test_reconciliation_records_who_accepted_it_and_what_they_saw(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired\n")
    mid = _orphan(root, planned=[str(page), str(log)], completed=[str(page)])
    before_digest = kr.current(root).manifest_digest

    krec.reconcile(root=root, accept=True, actor="local:raouf",
                   reason="log line restored by hand")

    conn = sqlite3.connect(kr.db_path(root))
    try:
        row = conn.execute(
            "SELECT actor, reason, mutation_ids, manifest_before,"
            " manifest_after, from_revision, to_revision FROM reconciliations"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "local:raouf"
    assert row[1] == "log line restored by hand"
    assert mid in json.loads(row[2])
    assert row[3] == before_digest
    assert row[4] == km.authoritative_manifest(root)
    assert row[6] == row[5] + 1


def test_the_report_reflects_the_resulting_status_not_the_original(root):
    """Rev 2 printed `SUSPECT revision ...` directly above `status: CLEAN`."""
    page = _page(root)
    log = root / "wiki" / "log.md"
    log.write_text("- repaired\n")
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root, accept=True, actor="local:raouf")
    text = krec.format_report(result["reports"], result["status"],
                              result["revision"])

    assert "SUSPECT" not in text
    assert "CLEAN" in text


def test_the_review_report_names_the_path_that_never_completed(root):
    page = _page(root)
    log = root / "wiki" / "log.md"
    _orphan(root, planned=[str(page), str(log)], completed=[str(page)])

    result = krec.reconcile(root=root)
    text = krec.format_report(result["reports"], result["status"],
                              result["revision"])

    assert "SUSPECT" in text
    assert "log.md" in text
    assert "UNPROVEN" in text


def test_a_completed_path_that_no_longer_parses_stops_for_review(root):
    page = _page(root, body="---\ntitle: [unclosed\n")        # torn frontmatter
    _orphan(root, planned=[str(page)], completed=[str(page)])

    result = krec.reconcile(root=root)

    assert result["status"] == "NEEDS_REVIEW"
    assert result["reports"][0].parse_failures == [str(page)]


def test_validation_uses_the_repositorys_existing_frontmatter_parser(root):
    """Two parsers become two definitions of valid. Reconciliation must agree
    with ingestion about what Zurvan can read."""
    import inspect as _inspect
    source = _inspect.getsource(krec)
    assert "parse_frontmatter" in source
    assert "yaml.safe_load" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_reconcile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.knowledge_reconcile'`

- [ ] **Step 3: Add the ledger operation**

Append to `scripts/knowledge_revision.py`:

```python
def reconcile_revision(mutation_ids, actor, reason=None, root=None) -> dict:
    """Accept the authoritative state and mint a NEW CLEAN revision.

    The approved design requires `SUSPECT R -> ... -> CLEAN R'`. Flipping
    `state = CLEAN` in place leaves the pre-orphan manifest attached to a
    revision now declared coherent — the laundering the design forbids.

    The new revision is EXTERNAL_OBSERVED: Zurvan did not witness the operation
    that produced these bytes, so it may not claim they were one managed
    transaction. A durable `reconciliations` row records who accepted what.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    first = authoritative_manifest(base)
    second = authoritative_manifest(base)
    if first != second:
        raise LedgerError("the store is changing; reconcile when quiet")

    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise LedgerError("a mutation is in flight; reconcile when quiet")
            rev, before = conn.execute(
                "SELECT current_revision, manifest_digest"
                " FROM knowledge_state WHERE id = 1").fetchone()
            new_revision = rev + 1
            for mutation_id in mutation_ids:
                _transition(conn, mutation_id, want=ORPHANED, new=RESOLVED,
                            resolved_at=_now(), resolution_actor=actor,
                            resolution_reason=reason,
                            resolution_revision=new_revision)
            conn.execute(
                "UPDATE knowledge_state SET current_revision = ?, state = ?,"
                " atomicity = ?, manifest_digest = ? WHERE id = 1",
                (new_revision, CLEAN, EXTERNAL_OBSERVED, second))
            conn.execute(
                "INSERT INTO reconciliations (reconciliation_id, at, actor,"
                " reason, mutation_ids, manifest_before, manifest_after,"
                " from_revision, to_revision) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"r{uuid.uuid4().hex[:12]}", _now(), actor, reason,
                 json.dumps(list(mutation_ids)), before, second,
                 rev, new_revision))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {"from_revision": rev, "to_revision": new_revision,
            "manifest_after": second}
```

- [ ] **Step 4: Write the policy module**

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
    """Readable, and its frontmatter is not torn.

    Uses the repository's existing parser rather than calling YAML directly.
    `graph_build.parse_frontmatter` and `wiki_merge._parse_fm` already exist; a
    third definition of "valid frontmatter" would eventually disagree with
    ingestion about what Zurvan can actually read.

    **That parser never raises** — measured, it returns `({}, text)` for torn
    frontmatter exactly as it does for a file that simply has none:

        valid  -> ({'title': 'X'}, '\n\nbody\n')
        torn   -> ({},            '---\ntitle: [unclosed\n')
        no fm  -> ({},            'just text\n')

    So "did it raise" detects nothing. The structural signal is that the parser
    did not CONSUME a frontmatter block the file clearly opens: the text starts
    with `---` and the returned body still does. That reuses the shared parser
    as the definition of what Zurvan can read, which is the point, without
    depending on an exception it does not throw.

    Syntax only — deliberately not a claim that the operation finished.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        from scripts.graph_build import parse_frontmatter
        meta, body = parse_frontmatter(text)
    except Exception:
        return False
    if text.lstrip().startswith("---") and not meta and body.lstrip().startswith("---"):
        return False                 # opened a block the parser could not close
    return True


def inspect(root=None) -> list:
    kr.detect_orphans(root)
    reports = []
    for m in kr.mutations_in(kr.ORPHANED, root):
        missing = [p for p in m.planned_paths if p not in m.completed_paths]
        failures = [p for p in m.completed_paths if not _parses(Path(p))]
        reports.append(OrphanReport(mutation=m, missing_paths=missing,
                                    parse_failures=failures))
    return reports


def reconcile(root=None, accept=False, actor="local:unknown", reason=None) -> dict:
    """Return the store to CLEAN when the evidence supports it.

    `accept=True` records an explicit decision for an operation whose
    completeness cannot be established mechanically. It is the only path that
    clears an incomplete orphan, and it is deliberately not the default.
    """
    reports = inspect(root)
    if not reports:
        return {"status": "CLEAN", "reports": [],
                "revision": kr.current(root).revision}

    unresolved = [r for r in reports if not r.complete]
    if unresolved and not accept:
        return {"status": "NEEDS_REVIEW", "reports": reports,
                "revision": kr.current(root).revision}

    result = kr.reconcile_revision(
        [r.mutation.mutation_id for r in reports],
        actor=actor,
        reason=reason or ("accepted without mechanical proof of completeness"
                          if unresolved else "all planned paths completed"),
        root=root)
    return {"status": "CLEAN", "reports": reports,
            "revision": result["to_revision"]}


def format_report(reports, status, revision) -> str:
    """Render the RESULTING state, never the pre-reconciliation one.

    Rev 2 derived the header from the reports, so an accepted reconciliation
    printed `SUSPECT revision ...` directly above `status: CLEAN`.
    """
    lines = [f"{status if status != 'NEEDS_REVIEW' else 'SUSPECT'} "
             f"revision {revision}"]
    for r in reports:
        m = r.mutation
        lines.append("Orphaned mutation:")
        lines.append(f"  project: {m.project or '(unknown)'}")
        lines.append(f"  caller:  {m.caller or '(unknown)'}")
        lines.append(f"  owner:   pid {m.owner_pid} / {m.owner_identity}")
        lines.append(f"  planned:   {', '.join(m.planned_paths) or '(none)'}")
        lines.append(f"  completed: {', '.join(m.completed_paths) or '(none)'}")
        if status == "NEEDS_REVIEW" and r.missing_paths:
            lines.append(f"  → {len(r.missing_paths)} planned path(s) never "
                         "completed; semantic completeness UNPROVEN")
        if status == "NEEDS_REVIEW" and r.parse_failures:
            lines.append(f"  → {len(r.parse_failures)} written file(s) do not "
                         "parse; semantic completeness UNPROVEN")
    if status == "NEEDS_REVIEW":
        lines.append("Snapshots blocked until reconciliation. "
                     "Live writes remain available.")
    return "\n".join(lines)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_reconcile.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/knowledge_reconcile.py scripts/knowledge_revision.py tests/test_knowledge_reconcile.py
git commit -m "zurvan: reconciliation mints a new CLEAN revision and records who accepted it"
```

---
### Task 12: One CLI invocation is one logical operation

**Files:**
- Modify: `scripts/cli.py:40` (rename `main` → `_main`), end of module (new `main`), and the subparser/dispatch blocks for `knowledge`
- Modify: `tests/test_knowledge_revision.py`
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md`

**Interfaces:**
- Consumes: Task 6's `operation()`; `scripts.project_registry.get_current_project`
- Produces: no new public API. `scripts.cli.main` keeps its existing zero-argument signature.

**Two facts settled by reading the module, not assuming:**

1. `def main():` takes **no arguments** — it reads `args_list = sys.argv[1:]` on its first line. Existing tests patch `sys.argv` and call `cli.main()` (`tests/test_check_gate_cli_smoke.py:26`) or shell out (`tests/test_cli.py:25`).
2. The dispatch body references `trace_parser` (835), `boundary_parser` (894), `graph_parser` (1010), `snapshot_parser` (1043) — locals of `main()` created *before* `parse_args`. **Extracting `_dispatch(args, parser)` would raise `NameError` on all four.** So the whole function is renamed and a thin wrapper added; nothing is reindented.

Because Task 6 activates the mutation lazily, wrapping every command is safe: a read-only command creates no row, takes no write lock, and cannot make a capture report BUSY.

**Naming correction:** the design says `zurvan snapshot doctor`. `zurvan snapshot` is the release-archive command and `scripts/snapshot.py` is its module — the collision that forced `measurement_snapshot.py`'s rename one session earlier. These live under `zurvan knowledge`, and Step 6 corrects the design doc.

**Dependency worth naming:** the `reconcile` branch raises `SystemExit(1)` on `NEEDS_REVIEW`. That is only safe because Task 6's `operation()` commits on a clean exit and abandons on a failing one *by whether anything was planned* — before that, this command would have marked the store SUSPECT immediately after clearing it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_knowledge_revision.py`:

```python
def _cli(root, *args):
    """Run the CLI in a FRESH interpreter rooted at this test's store.

    Not `cli.main()` in-process. `scripts/memory.py:7` does
    `from scripts.config import PROJECT_ROOT` at module scope, so every page
    path is bound to whatever the root was when that module was first imported
    — `monkeypatch.setenv("ZURVAN_ROOT")` cannot move it. In-process the write
    would target the REAL wiki, `safe_write.is_safe_path` would refuse it
    (failing safe, at least), and the revision would never advance. A
    subprocess reads the environment at import, which is the only way this
    works. `tests/test_cli.py:25` already shells out for the same reason.
    """
    import subprocess
    import sys
    env = {**os.environ, "ZURVAN_ROOT": str(root),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    return subprocess.run([sys.executable, "scripts/cli.py", *args],
                          cwd=str(Path(__file__).resolve().parents[1]),
                          env=env, capture_output=True, text=True)


def test_decision_add_through_the_cli_is_exactly_one_revision(root):
    """`decision add` writes the page and appends `wiki/log.md`. Through the
    dispatcher that is one logical operation."""
    before = kr.current(root).revision

    result = _cli(root, "decision", "add", "--title", "T", "--reason", "R",
                  "--status", "accepted", "--tags", "zurvan")

    assert result.returncode == 0, result.stderr
    assert kr.current(root).revision == before + 1
    assert kr.implicit_mutations(root) == []


def test_a_read_only_command_writes_nothing_to_the_ledger(root):
    before = kr.ledger_fingerprint(root)
    result = _cli(root, "version")
    assert result.returncode == 0, result.stderr
    assert kr.ledger_fingerprint(root) == before


def test_a_cli_command_that_exits_nonzero_without_writing_stays_clean(root):
    """~20 dispatch branches call sys.exit(1) on a validation failure. None of
    them may mark the shared store SUSPECT."""
    result = _cli(root, "claim", "add", "--text", "t", "--source",
                  "nonexistent.md", "--evidence", "e", "--confidence", "high")
    assert result.returncode != 0
    assert kr.current(root).state == kr.CLEAN


def test_the_mutation_records_which_project_wrote_it(root):
    """Orphan reports name a project. On a shared cross-project store,
    `(unknown)` is a far worse diagnostic than it looks.

    The project comes from the registry, which the subprocess reads for
    itself; asserting it is non-null is the portable claim, since which project
    is `current` depends on the developer's machine.
    """
    result = _cli(root, "decision", "add", "--title", "T", "--reason", "R",
                  "--status", "accepted")
    assert result.returncode == 0, result.stderr
    committed = kr.mutations_in(kr.COMMITTED, root)
    assert len(committed) == 1
    assert committed[0].writer == "cli"


- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -k cli -v`
Expected: FAIL — `decision add` currently produces two implicit mutations, so the revision advances by 2 and `implicit_mutations` is non-empty.

**Note:** `test_knowledge_revision.py` needs `from pathlib import Path` and `import os` at module scope for `_cli`; both are already imported by Task 3's header.

- [ ] **Step 3: Rename the existing entry point**

In `scripts/cli.py` line 40, change exactly one line:

```python
def main():
```

to:

```python
def _main():
```

Change nothing else inside it. No reindentation, no extracted helper.

- [ ] **Step 4: Add the wrapper**

At the end of `scripts/cli.py`, replace:

```python
if __name__ == "__main__":
    main()
```

with:

```python
def main():
    """One CLI invocation is one logical operation.

    Wrapping here rather than at each `safe_write` call site is deliberate:
    `decision add` writes a page and then appends `wiki/log.md`, and a revision
    minted between them is a half-operation. Only `memory.py` and
    `wiki_merge.py` call `safe_write` at all, and every command reaches them
    through this function, so one wrap covers them and anything added later.

    Wrapping a read-only command costs nothing: the mutation row is created on
    the first PLANNED write, so `zurvan search` leaves the ledger untouched and
    cannot make a snapshot capture report BUSY.

    `_main` is the original function, renamed and otherwise untouched: its
    dispatch body closes over `trace_parser`, `boundary_parser`, `graph_parser`
    and `snapshot_parser`, locals created before `parse_args`, so splitting
    dispatch into its own function would raise `NameError`.
    """
    from scripts import knowledge_revision as _kr
    try:
        from scripts.project_registry import get_current_project
        project = get_current_project()[0]
    except Exception:
        project = None
    with _kr.operation(writer="cli", project=project):
        return _main()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add the `knowledge` command surface**

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
    knowledge_sub.add_parser(
        "bootstrap", help="Record this store's manifest as the drift baseline")
    knowledge_sub.add_parser(
        "callers", help="Unresolved implicit callers still to be migrated")
    k_rec = knowledge_sub.add_parser("reconcile", help="Return the store to CLEAN")
    k_rec.add_argument("--accept", action="store_true",
                       help="record an explicit decision for an operation whose "
                            "completeness cannot be established mechanically")
    k_rec.add_argument("--actor", default="local:unknown")
    k_rec.add_argument("--reason", default=None)
```

And inside `_main` (the renamed original function), add a branch alongside the other `elif args.command ==` clauses:

```python
    elif args.command == "knowledge":
        from scripts import knowledge_reconcile as krec
        from scripts import knowledge_revision as kr
        if args.action == "status":
            rev = kr.current()
            print(f"revision:   {rev.revision}")
            print(f"state:      {rev.state}")
            print(f"atomicity:  {rev.atomicity}")
            print(f"baseline:   {'recorded' if rev.manifest_digest else 'ABSENT — run `zurvan knowledge bootstrap`'}")
        elif args.action == "bootstrap":
            print(f"bootstrapped at revision {kr.bootstrap(actor='cli')}")
        elif args.action == "callers":
            callers = kr.implicit_callers()
            if not callers:
                print("No unresolved implicit callers.")
            for caller, count in sorted(callers.items(), key=lambda kv: -kv[1]):
                print(f"{count:6}  {caller}")
        elif args.action == "doctor":
            reports = krec.inspect()
            rev = kr.current()
            if not reports:
                print(f"{rev.state} revision {rev.revision}; no orphans.")
            else:
                print(krec.format_report(reports, "NEEDS_REVIEW", rev.revision))
        elif args.action == "reconcile":
            result = krec.reconcile(accept=args.accept, actor=args.actor,
                                    reason=args.reason)
            print(krec.format_report(result["reports"], result["status"],
                                     result["revision"]))
            print(f"\nstatus: {result['status']}")
            if result["status"] == "NEEDS_REVIEW":
                raise SystemExit(1)
```

- [ ] **Step 6: Correct the design doc's command name**

In `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md`, replace every `zurvan snapshot doctor` with `zurvan knowledge doctor`, and `zurvan knowledge reconcile` is already correct. Leave `zurvan snapshot list` / `gc` for the Plan 2 author — they collide too, and that decision belongs with the code that implements them.

- [ ] **Step 7: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_knowledge_revision.py -v`
Expected: 57 passed

- [ ] **Step 8: Run the full suite**

Run: `PYTHONPATH=. python -m pytest -q`
Expected: 0 failed.

- [ ] **Step 9: Commit**

```bash
git add scripts/cli.py tests/test_knowledge_revision.py docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md
git commit -m "zurvan: one CLI invocation is one logical operation"
```

---

### Task 13: The guard watches the whole ledger

**Files:**
- Modify: `tests/conftest.py:18-23` (imports), `:49-58` (`_snapshot`)
- Modify: `tests/test_conftest_guard_behaviour.py`

**Interfaces:**
- Consumes: Task 3's `ledger_fingerprint`
- Produces: one new key in `_snapshot()`: `"knowledge ledger"`

**Why this task exists.** The gauntlet found that `config.PROJECT_ROOT` is import-bound, so a ledger resolved through it writes into production `data/registry.sqlite` while the page under test goes to the temp store. Tasks 1 and 3 fix that. This makes the *class* impossible to miss: the guard already watches `wiki/`, and `data/` was simply outside its field of view.

A revision counter plus a row count is **not enough** — `CLEAN → SUSPECT`, an atomicity change, a mutation reaching ORPHANED and a reconciliation record all leave both untouched. The guard watches a fingerprint of everything the ledger asserts (Task 3), so it needs no semantics of its own: it only needs to notice that something moved.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_conftest_guard_behaviour.py`:

```python
def test_the_nested_store_is_never_the_real_repository(tmp_path):
    """Precondition for the test below. The copied conftest's LIVE_ROOT is
    `Path(__file__).resolve().parents[1]`, which resolves to the temp
    directory — so the nested run's "production" IS the fixture. Asserted
    rather than inferred, because the next test deliberately mutates what that
    run considers production."""
    assert tmp_path.resolve() != REPO.resolve()


def test_a_test_that_mutates_the_nested_ledger_fails_the_session(tmp_path):
    """The guard watched `wiki/` and nothing under `data/`.

    A module resolving its root through the import-bound `config.PROJECT_ROOT`
    writes the ledger into the real store while the page under test lands in
    the redirect — silently, because no watched count moved. This is that wire.
    """
    result = _nested(tmp_path, """
import sqlite3
from pathlib import Path

def test_bumps_the_nested_ledger():
    root = Path(__file__).resolve().parents[1]
    db = root / "data" / "registry.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS knowledge_state ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " current_revision INTEGER NOT NULL, state TEXT NOT NULL,"
        " atomicity TEXT NOT NULL, manifest_digest TEXT NOT NULL);"
        "INSERT OR REPLACE INTO knowledge_state"
        " VALUES (1, 99, 'CLEAN', 'MANAGED', '');")
    conn.commit()
    conn.close()
""")
    assert result.returncode != 0
    assert "knowledge ledger" in result.output


def test_the_fingerprint_is_part_of_the_snapshot(tmp_path):
    """`CLEAN -> SUSPECT` moves neither a revision counter nor a row count, so
    the guard must watch a fingerprint rather than either. Asserted directly on
    the helper, because a nested run compares a store against itself and a
    change made before the run starts is not a change DURING it."""
    import tests.conftest as ct
    assert "knowledge ledger" in ct._snapshot()


- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_conftest_guard_behaviour.py -k ledger -v`
Expected: FAIL — the nested run passes, because `_snapshot()` does not look at `data/`.

- [ ] **Step 3: Write minimal implementation**

In `tests/conftest.py`, add to the imports block (lines 18-23):

```python
import sqlite3
```

Add this helper immediately after `_WATCHED` and before `_snapshot`:

```python
def _ledger_fingerprint() -> str:
    """Everything the knowledge ledger asserts, as one digest.

    `data/` was outside this guard's field of view, and the revision ledger
    lives there. A module that resolves its root through the import-bound
    `scripts.config.PROJECT_ROOT` — which `monkeypatch.setenv("ZURVAN_ROOT")`
    does NOT move — writes here while the page under test goes to the redirect,
    and every watched count stays put.

    A revision counter alone is not enough: CLEAN -> SUSPECT, an atomicity
    change, a mutation reaching ORPHANED and a reconciliation record all leave
    it untouched. The guard needs no semantics, only a way to notice movement.

    Defensive: a guard that raises on its own bookkeeping is worse than no
    guard, so any failure reads as "unavailable" and simply never moves.
    """
    try:
        from scripts.knowledge_revision import ledger_fingerprint
        return ledger_fingerprint(LIVE_ROOT)
    except Exception:
        return "unavailable"
```

And in `_snapshot()`, immediately before `return counts`:

```python
    counts["knowledge ledger"] = _ledger_fingerprint()
```

**Note for the implementer:** `_snapshot()` currently returns `dict[str, int]` and the guard's diff message formats values with `{a - b:+d}`. Adding a string value breaks that formatting. Change the diff line to render non-numeric values plainly:

```python
        detail = "\n".join(
            f"  {k}: {b} -> {a}" + (f" ({a - b:+d})"
                                     if isinstance(a, int) and isinstance(b, int)
                                     else "")
            for k, (b, a) in sorted(moved.items())
        )
```

and the same in the `_report(...)` external-write branch above it.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_conftest_guard_behaviour.py -v`
Expected: 7 passed. The four original behaviours must still hold — if one broke, the guard was relaxed rather than extended, and that is a finding.

- [ ] **Step 5: Prove the guard did not become decorative**

Run: `PYTHONPATH=. python -m pytest -q`
Expected: 0 failed, and no `[live store]` notice naming `knowledge ledger`. If that notice appears, the suite is mutating the production ledger and Task 1/3's fix is incomplete — **investigate, do not silence it.**

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_conftest_guard_behaviour.py
git commit -m "zurvan: the session guard watches the ledger, not just the wiki"
```

---

### Task 14: The full gate, and an honest status entry

**Files:**
- Modify: `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`

- [ ] **Step 1: Run the complete regression floor**

The Z0 design's §10.4 requires all four. Rev 2 of this plan omitted `check.sh`, which would have made the change entry's verification claim false.

```bash
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

Then, **once no index rebuild is in flight** (the standing rule is a scheduling constraint, not an exemption):

```bash
bash scripts/check.sh
```

Expected: pytest 0 failed; guard passed; no whitespace errors; **all 24 gates**. Record the actual numbers observed, not the expected ones.

- [ ] **Step 2: Confirm the implicit inventory before claiming migration**

```bash
PYTHONPATH=. python scripts/cli.py knowledge callers
```

Expected: `No unresolved implicit callers.` If any appear, they are the remaining migration work and the change entry must name them rather than rounding them away.

- [ ] **Step 3: Write the entry**

Append a dated `Raouf:` entry to **both** `AGENTS.md` and `CHANGELOG.md` covering: scope; why a write counter was wrong; the two-phase lifecycle and the crash it closes; ABORTED vs ORPHANED keyed on *planned* paths; the two manifest questions (stability vs awareness); bootstrap as a deliberate act; EXTERNAL_OBSERVED's weaker claim; reconciliation minting a new revision with an audit record; the implicit inventory's closure criterion; and the verification output actually observed.

Move the older `AGENTS.md` entry **verbatim** to `docs/agents-history.md` so only two remain.

- [ ] **Step 4: State what is still not built**

The entry must say plainly that this implements §9.2 steps 1–2 only, and that the Z0 gate (§10) remains **unmet**: no immutable-bytes abstraction, no wiki or graph capture, no atomic graph rebuild, no `built_from_revision`, no two-purpose capture, no leases. `measurement_snapshot.py` still pins only the search index.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md CHANGELOG.md docs/agents-history.md
git commit -m "zurvan: Z0 step 1 — knowledge revisions and the mutation barrier"
```

---

## Self-Review

**Spec coverage.** §3.1 → Task 6's half-operation test. §3.2 barrier → Tasks 6, 12; implicit limit → Tasks 7, 8; inherited-id validation → Task 6 (`ContextVar` per store, `NestedMutationError` across stores). §3.3 CLEAN/SUSPECT → Tasks 6, 10. §3.4 CLEAN's meaning → Task 11. §3.5 reconcile order → Task 11. §3.6 schema → Tasks 3, 5. §4.1 derived-subtree exclusion → Task 1. §5.1 manifest → Task 2. §5.2 binding, bootstrap and EXTERNAL_OBSERVED → Task 9.

Gate items covered: §10.1.1 (Task 6), .2 and .3 (Tasks 7, 8), .4 (Task 10), .5 and .6 (Task 11), .7 (Tasks 6, 10). §10.2.12 and .13 (Task 9). §10.4 regression floor in full, including `check.sh` (Task 14).

Gate items **deliberately not covered**, deferred to Plans 2 and 3: §10.2.8–11, §10.3.14–19. Task 14 requires this stated in the change entry rather than left implicit.

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step carries its code.

**Type consistency.** `Revision(revision, state, atomicity, manifest_digest)`, `Mutation(...)` and `Operation(root, project, writer, caller, implicit, mutation_id, depth)` are defined in Tasks 3, 5, 6 and used unchanged afterwards. `WriteTicket(mutation_id, owns)` is produced by `plan_write` and consumed by `complete_write` only. `mutations_in(state, root=None)` replaces rev 2's per-state helpers and is called that way throughout. `reconcile()` returns `{"status", "reports", "revision"}`; `format_report(reports, status, revision)` takes the status explicitly, which is the fix for rev 2's contradictory output. `scripts.cli.main` keeps its zero-argument signature.

**Ordering.** Every task depends only on earlier ones. Task 7 is the first to change a shared path and runs the full suite; Task 12 is the second and does the same.

---

## Appendix — three gauntlets

This plan was attacked three times before any of it was executed. Findings are kept after being fixed: a plan that shows only its final state teaches a future reader nothing about how it got there, and two of these defects were introduced by the fix for an earlier one.

### Round 1 — self-gauntlet of rev 1 (10 findings, 4 red)

| # | Finding | Receipt | Fixed in |
|---|---|---|---|
| G1 🔴 | `_root(None)` resolved through the import-bound `config.PROJECT_ROOT`, so the ledger would write into **production** `data/registry.sqlite` during tests. `conftest.py` watches `wiki/`, not `data/` — silent | module imported first: `config.PROJECT_ROOT` → real repo while `safe_write` → `/tmp/fake-store` | Tasks 1, 3, 13 |
| G2 🔴 | `sys.exit()` on ~20 dispatch branches raised `SystemExit`, a `BaseException`, marking the store SUSPECT on every non-zero exit — including `knowledge reconcile`, which would SUSPECT the store it had just cleared | `grep sys.exit scripts/cli.py` → 522, 529, 564, 635, 648, 679, 696, 706, 758, 767, 775, 788, 811, 814, 820, 827 … | Task 6 |
| G3 🔴 | Extracting `_dispatch(args, parser)` raises `NameError` — the body closes over locals created before `parse_args` | `trace_parser` 835, `boundary_parser` 894, `graph_parser` 1010, `snapshot_parser` 1043 | Task 12 |
| G4 🔴 | Plan called `main([...])`; `def main():` takes **no arguments** | `scripts/cli.py:40-41` | Task 12 |
| G5 🟠 | `trace_writer.py:20` bypasses `safe_write`, so every traced command would mint `EXTERNAL_OBSERVED` — the drift mechanism firing on Zurvan's own output | 25 of 4,430 wiki files | Task 1, design §4.1 |
| G6 🟠 | `--project` re-invokes the CLI against a different store with the mutation id inherited; writes there matched no row and were dropped | `cli.py:57-62` | Task 6 (no env var at all) |
| G7 🟠 | `implicit_write` counted any `safe_write` target; `docs/` would advance a revision changing no manifest | `safe_write.py:13-31` vs `writelog._is_watched` | Task 1 |
| G8 🟡 | DML-then-`BEGIN IMMEDIATE` raises under the default isolation level. Latent — the kind that ships | `cannot start a transaction within a transaction` | Task 3 |
| G9 🟡 | `process_identity` forked `ps` on every mutation | 3.7 ms per call | Task 4 |
| G10 🟡 | Concurrent mutations: the first to commit records a manifest containing the other's half-written files | stated as a bound | design §5.2 |

### Round 2 — line-by-line review of rev 2 (13 findings + hardening)

The structural ones, which forced this rewrite rather than a patch:

| # | Finding | Why it was fatal | Fixed in |
|---|---|---|---|
| P1 🔴 | `planned_paths` was never recorded before a real write — rev 2 hooked only `_stamp`, which runs *after* publication | a crash in the gap left both path lists empty, so orphan detection concluded `ABORTED` and the store stayed `CLEAN` **while the file had already changed**. That is the exact crash `planned_paths` was invented for | Tasks 6, 7 |
| P2 🔴 | Ledger bookkeeping was wrapped in `except Exception: pass` | telemetry may be best-effort; a coherence ledger that forgets a write it performed may not | Tasks 6, 7 |
| P3 🔴 | ABORTED-vs-ORPHANED was keyed on `completed_paths` | empty in precisely the crash being detected | Task 10 |
| P4 🔴 | `check_drift` returned `None` on an empty digest, then `mint_external_revision(None)` hit a NOT NULL column | measured `IntegrityError: NOT NULL constraint failed: knowledge_state.manifest_digest` — the advertised "29 passed" was unreachable | Task 9 (`bootstrap`) |
| P5 🔴 | Reconciliation flipped `state = CLEAN` without advancing the revision or recording a manifest | left `revision 14 / pre-orphan manifest / CLEAN` — the laundering the approved design forbids | Task 11 |
| P6 🔴 | Reconciled orphans stayed `ORPHANED` forever | `knowledge doctor` reported a resolved incident permanently | Task 11 (`RESOLVED`) |
| P7 🟠 | `format_report` derived its header from pre-reconciliation reports | printed `SUSPECT revision …` directly above `status: CLEAN` | Task 11 |
| P8 🔴 | A single module global for the active mutation | an inner operation cleared it on exit and the outer operation's later writes silently became implicit | Task 6 (`ContextVar`, join-or-refuse) |
| P9 🔴 | "could not inspect" was treated as "provably dead"; read-then-update race | a `ps` timeout would SUSPECT the store; a mutation committing mid-detection was reclassified from stale data | Tasks 4, 10 |
| P10 🔴 | `implicit_mutations()` returned all history, so the closure gate could never be met without deleting evidence | closure was unreachable by construction | Task 8 |
| P11 🟠 | `writer="implicit"` named nothing actionable | the inventory existed to be migrated and could not be | Task 6 (`caller`) |
| P12 🟠 | Every read-only CLI invocation inserted a mutation row | `zurvan search` took write locks and could make a capture report BUSY | Task 6 (lazy activation) |
| P13 🔴 | The final gate omitted `check.sh` | the design's own §10.4 requires all 24 gates; the change entry could not honestly claim the regression floor | Task 14 |

Also adopted: CHECK constraints on every state column, compare-and-set on every transition, orphan timestamps, de-duplicated path lists, `manifest_sha` resolving against the supplied root, reuse of the repository's existing frontmatter parser, and an audit record for reconciliation.

**One finding rejected, with a receipt.** The review held that Task 10's nested test "deliberately mutates the real ledger". It does not: `_nested()` builds `tmp_path/{tests,wiki,data}` and copies `conftest.py` in, and the copy's `LIVE_ROOT = Path(__file__).resolve().parents[1]` resolves to that temp directory — the nested run's "production" *is* the fixture. The suggested assertion was adopted anyway, because a property worth relying on is worth proving.

### Round 3 — final gauntlet of rev 3 (8 findings, 3 red)

Found by executing the plan's own assumptions, not by re-reading it.

| # | Finding | Receipt | Fixed in |
|---|---|---|---|
| H1 🔴 | The manifest implementation is **15× slower than the design's measured figure**, and `commit_mutation` runs it on every writing command | `Path.rglob` + `resolve()` per file: **238 ms** to list, **226 ms** to hash. `os.walk` with a structural prune: **31/31/30 ms** end-to-end. The design's "19 ms" was an `os.walk` measurement over a pre-built list; the plan had specified a different implementation | Tasks 1, 2 |
| H2 🔴 | `graph_build.parse_frontmatter` **never raises**, so the parse-failure detection detected nothing and its test could not pass | torn frontmatter → `({}, '---\ntitle: [unclosed\n')`, identical in shape to a file with no frontmatter | Task 11 (structural check) |
| H3 🔴 | Task 12's CLI tests cannot work in-process | `memory.py:7` binds `PROJECT_ROOT` at import, so `decision add` targets the real wiki; `is_safe_path` then refuses the write (failing safe) and the revision never advances | Task 12 (subprocess) |
| H4 🟠 | Two drift tests passed for the wrong reason | on a fresh store `observe_external_drift` raises the *bootstrap* error before reaching the in-flight and SUSPECT checks, so neither was exercised | Task 9 (`match=`, bootstrap first) |
| H5 🟠 | Task 8's first test called `memory.add_note` in-process and wrapped its assertions in `if rows:` | same import-binding problem as H3, and vacuous either way | Task 8 (deleted; the next test covers it) |
| H6 🟡 | Dead code: `MUTATION_ENV` was set and popped but never read after `active_id` was removed | a leftover of the rev 2 mechanism G6 deleted | Task 6 |
| H7 🟡 | A guard test used `assert X or Y`, which can pass vacuously | — | Task 13 |
| H8 🟡 | The `own_identity` cache test asserted an or-expression and could call a function that now raises | — | Task 4 |

**Cost of the feature, restated honestly.** ~35 ms per writing CLI command (31 ms manifest + 3.7 ms `ps`, cached to once per process), not the ~23 ms rev 2 claimed. Read-only commands pay neither, because the mutation row is created on the first planned write and a mutation that planned nothing computes no manifest.

**What the three rounds cost the scorecard.** Rev 1 scored 2/10 on correctness-under-test-isolation and 3/10 on survives-the-real-CLI. Rev 2 raised both to 8 and was then found to be unsafe against crashes, non-conforming on reconciliation, and carrying two unreachable test counts. The original scores are recorded rather than replaced: a scorecard that only goes up has stopped measuring. **No line of this plan has executed.** Every score is a claim about a document, and the honest ceiling for a document is lower than for working code.
