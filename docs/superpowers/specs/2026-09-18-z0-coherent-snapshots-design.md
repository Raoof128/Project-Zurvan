# Z0 — Coherent snapshots and the derived-store lifecycle

**Date:** 2026-09-18 (rev 2, after design review)
**Phase:** Z0 of the Zurvan 2.0 programme
([canonical spec](2026-09-18-zurvan-2-canonical-upgrade-spec.md),
[amendments](2026-09-18-zurvan-2-amendments.md))
**Supersedes in scope:** [2026-09-17-measurement-snapshot-design.md](2026-09-17-measurement-snapshot-design.md),
which remains accurate for what it built and is the starting point, not a rival.
**Status:** Design. Not implemented. Nothing here may be reported as built until
it is measured.

**Rev 2 changed four things that were wrong, not unclear.** Rev 1 claimed
implicit mutations were safe (they are not, §3.2); made rebuild and capture
mutually dependent (§2.2); detected only *racing* unmanaged edits and not
*completed* ones (§5.2); and made reconciliation require a rebuild that would
refuse to run (§3.5). It also described the existing manifest as computing an
inode field it does not compute. The review that found these is in the
2026-09-18f change entry.

---

## 1. What is actually wrong today

A measurement snapshot exists and pins exactly one file.

```
snapshot S            live store
  search.sqlite  →    frozen (hardlink, proven)
  wiki/          →    MOVES
  graph.sqlite   →    MOVES
  claims         →    MOVES
```

`capture()` returns something that *looks* like a snapshot and is, for the
hybrid retriever, genuinely sufficient — that retriever reads only the index. It
is not sufficient for anything Z1 onwards needs, because every later phase asks
questions of more than the index.

The gap is invisible. A snapshot with a frozen index and a moving wiki answers
questions exactly like a coherent one. That is the shape of the 2026-09-16 index
holding 8,000 of 185,303 chunks: structurally valid, silently wrong,
indistinguishable from correct at the call site.

Canonical A39 names the missing property:

> Freezing wiki bytes, claims, graph, and search independently does not prove
> they describe the same logical knowledge state.

**Z0 closes when a snapshot can prove one coherent knowledge state, and refuses
to exist when it cannot.**

---

## 2. Two snapshots, because there are two jobs

This is the spine of the design and the fix for two circular dependencies.

### 2.1 The two purposes

```
AUTHORITATIVE snapshot          MEASUREMENT snapshot
  immutable authoritative         immutable authoritative inputs
  inputs at revision R          + derived stores proven built_from R
        │                                    │
        ▼                                    ▼
  rebuild_search_index()            eval, retrieval, any measurement
  graph_build()
```

An **authoritative snapshot** pins what the knowledge *is*: `wiki/`, the
registry state a build needs, the revision, and later the claim ledger. It makes
**no requirement of derived stores**, because its entire job is to be the input
that produces them.

A **measurement snapshot** pins the authoritative inputs *and* requires that
every capability the caller declares is present at the same revision.

```python
capture(purpose="rebuild_search", required={"wiki"})
capture(purpose="measurement",    required={"wiki", "search", "graph"})
```

`required` is explicit at the call site. One function whose definition of
"valid" silently changes with its caller is how this class of bug is built.

### 2.2 The deadlock this removes

Rev 1 had capture verify derived stamps unconditionally, and had rebuild begin
by capturing. So:

```
wiki    revision 1850
search  built_from 1849      ← stale
graph   built_from 1850

capture()  → refuses: search is stale
rebuild()  → needs capture()
           → DEADLOCK: search can never reach 1850
```

With the split, `rebuild_search(required={"wiki"})` succeeds against the stale
store, produces a search index stamped 1850, and the measurement snapshot then
finds both derived stores at 1850. The stale index is an input to nothing and a
blocker of nothing.

---

## 3. Knowledge revisions

### 3.1 Why a write counter is wrong

The obvious design advances the revision inside `safe_write`. The failure is
ordinary. `zurvan decision add` performs two writes:

```
revision 100
  write wiki/decisions/foo.md      → revision 101
                                        ← a snapshot here is byte-perfect
  append wiki/log.md               → revision 102
```

Revision 101 is consistent at the filesystem level and represents a
half-completed logical operation: a decision page the log does not know about. A
Franken-state produced by the mechanism meant to prevent them.

**A revision is advanced by a logical mutation, not by a write.**

### 3.2 The barrier, and the honest limit of implicit mutations

```
begin_mutation(project, writer)      → mutation M17, base_revision 1847
    safe_write(A)
    safe_write(B)
    safe_write(C)
end_mutation(M17)                    → knowledge_revision 1847 → 1848
```

The lock is held for the mutation boundary only, never for the duration of an
evaluation (§8.1). Locking is **cross-process**: the store is shared, and the
CHANGELOG records an Instagram session writing `wiki/log.md` at 15:21:04 during
a gate run.

`begin_mutation`/`end_mutation` wrap at the **top-level CLI dispatcher**, so
every existing command inherits a mutation context without rewriting individual
`safe_write` call sites. `safe_write.write_file_safely` and
`append_file_safely` (`scripts/safe_write.py:62,81`) become participants,
recording targets through the existing `_stamp` → `writelog.record` path
(`scripts/writelog.py:97`), which already carries session token, pid and ppid.

**Rev 1 was wrong here, and the correction matters.** It said a write with no
active mutation is "not an error… the property that matters is preserved either
way". It is not preserved. An unwrapped caller doing two writes produces:

```
safe_write(A)   → implicit mutation commits, revision 101
                     ← SNAPSHOT HERE: no ACTIVE mutation, capture succeeds
safe_write(B)   → implicit mutation commits, revision 102
```

That is precisely the half-operation §3.1 exists to eliminate, reintroduced
through the compatibility door.

**An inherited mutation id is honoured only in the store that issued it.**
The barrier is announced to subprocesses through the environment, and
`zurvan --project X` re-invokes the CLI against a *different* store. An id that
names no row in the registry being written is treated as no active mutation, so
the write opens an implicit one and is recorded. Trusting the inherited value
would make cross-project writes silently untracked — failing open, in the one
place this design cannot afford it.

**Implicit mutation is a migration mechanism, not the Z0 contract:**

```
implementation   safe_write outside a mutation → implicit mutation
                                               + warning
                                               + caller recorded
        ↓
inventory        enumerate real production callers from those records
        ↓
wrap             every multi-write operation gets an explicit mutation
        ↓
Z0 closure       no production logical operation relies on implicit mode
```

Implicit support may survive for genuinely standalone single writes, marked as
such in metadata. **Z0 may not claim logical-operation atomicity for an unknown
caller performing multiple implicit writes**, and the closure gate (§10.1)
tests that the inventory is empty rather than assuming it.

**Amended 2026-09-28 — the migration door is closed.** An agent in another
repository imported `scripts.memory` directly and produced six revisions for
three logical writes (revisions 73-78, 2026-09-23). A door any importer can
walk through is not a migration mechanism. `plan_write` now refuses an
authoritative write with no operation (`KnowledgeOperationRequired`), before
the bytes and before the row; implicit mode requires
`allow_implicit_for_migration(reason)` and records the reason. The pipeline
above is unchanged in intent — its first stage is now opt-in. Full record:
`2026-09-18-zurvan-2-amendments.md` §9.

### 3.3 Revisions are CLEAN or SUSPECT

Liveness and trustworthiness are different facts and must not share a flag.

```
ACTIVE mutation
   ├── owner alive          → capture returns BUSY
   └── owner provably dead  → ORPHANED
                                  ↓
                            revision R = SUSPECT
                                  ↓
                            snapshot of R forbidden
```

The store does not become unusable. Writes continue and the revision advances.
But:

> Advancing past a SUSPECT revision does not make the successor trustworthy.

If R held a half-written decision page and the next mutation only appends a
session log, R+1 still holds the half-written page. `SUSPECT` **propagates**
until an explicit repair boundary clears it. `CLEAN`/`SUSPECT` is a property of
revisions, not of mutations.

### 3.4 What CLEAN means, precisely

```
CLEAN   = the authoritative knowledge state is accepted as coherent
```

It does **not** mean every derived index is current. That is separately modelled
by `CURRENT` / `STALE` / `INVALID` (§6.2). Conflating the two is what produced
the reconciliation deadlock in rev 1.

### 3.5 Recovery: `zurvan knowledge reconcile`

Not `rollback`. Rollback semantics cannot be proven: a dead process proves the
mutation will not finish and proves nothing about whether the bytes it already
wrote are complete. Automatic rollback would be fiction; automatic
timeout-commit a lie of the same size.

**Rev 1 ordered this wrongly** — it had reconcile rebuild derived stores before
marking the revision CLEAN, while §6.1 has rebuild refuse a SUSPECT snapshot. A
SUSPECT revision could therefore never produce the snapshot needed to rebuild
the stores required to make it CLEAN. Correct order:

```
SUSPECT authoritative state
        ↓
inspect orphan; inspect and repair affected authoritative files
        ↓
explicit reconciliation decision
        ↓
commit NEW CLEAN authoritative revision R'
        ↓
capture AUTHORITATIVE snapshot S(R')          ← now permitted: R' is CLEAN
        ↓
rebuild search / graph
        ↓
derived stores become CURRENT
```

So `reconcile` does:

1. detect orphaned mutations;
2. list `planned_paths` and `completed_paths` (§3.6);
3. compare current bytes against any pre-mutation hashes available;
4. validate Markdown/frontmatter on touched files;
5. verify the manifest is stable;
6. **stop for human or authorised-agent review when semantic completeness cannot
   be established**;
7. on acceptance, commit a new CLEAN revision.

Rebuilding is *downstream* of reconciliation, not part of it.

**Step 6 is not optional.** Markdown parsing proves syntax, not that a two-file
operation finished. A decision page that parses cleanly while `log.md` never
received its line is syntactically perfect and semantically half-done.
`reconcile` may refuse and report; it may never resolve by deleting.

`zurvan knowledge doctor` reports it in a form a human can act on:

```
SUSPECT revision 1848
Orphaned mutation:
  project: instagram
  owner:   pid 48211 / start-token 1f3c…
  planned:   wiki/decisions/instagram-….md, wiki/log.md
  completed: wiki/decisions/instagram-….md
  → 1 planned path never completed; semantic completeness UNPROVEN
Snapshots blocked until reconciliation. Live writes remain available.
```

### 3.6 Schema

`data/registry.sqlite` holds one table (`sources`), so this is additive:

```sql
CREATE TABLE knowledge_state (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),
    current_revision      INTEGER NOT NULL,
    state                 TEXT NOT NULL,   -- CLEAN | SUSPECT
    atomicity             TEXT NOT NULL,   -- MANAGED | EXTERNAL_OBSERVED
    manifest_digest       TEXT NOT NULL    -- authoritative manifest AT this revision
);

CREATE TABLE mutations (
    mutation_id      TEXT PRIMARY KEY,
    base_revision    INTEGER NOT NULL,
    state            TEXT NOT NULL,     -- ACTIVE | COMMITTED | ORPHANED
    project          TEXT,
    writer           TEXT,
    implicit         INTEGER NOT NULL DEFAULT 0,
    owner_pid        INTEGER,
    owner_identity   TEXT NOT NULL,     -- pid + process start time; PIDs are reused
    started_at       TEXT NOT NULL,
    committed_at     TEXT,
    last_heartbeat   TEXT,
    planned_paths    TEXT,              -- JSON, written BEFORE the writes
    completed_paths  TEXT,              -- JSON, appended after each publication
    orphan_reason    TEXT
);
```

Three fields carry review findings:

- `owner_identity` pairs pid with process start time. A pid alone is recycled,
  and an unrelated new process would otherwise vouch for a dead mutation.
- **`planned_paths` is recorded before the first write**, `completed_paths`
  after each successful publication. If the process dies between writing a file
  and stamping it, a single `touched_paths` list loses exactly the path
  reconciliation needs to investigate. The difference between the two lists is
  the investigation.
- `implicit` marks the migration path of §3.2 so the inventory is a query, not
  an audit.

---

## 4. Immutable bytes: a portable abstraction

The correctness contract is an **immutable, replayable, coherent snapshot**. The
mechanism is an implementation detail the caller must not be able to observe.

```
Snapshot abstraction
  ├── APFS clonefile / COW        ← preferred fast path
  ├── immutable CAS object        ← reused only where one already exists
  └── ordinary private byte copy  ← correctness fallback, always available
```

No bespoke CAS is built in Z0 (amendment 3): its only job is deduplication,
which APFS already does, and it would add a second GC surface for no measured
benefit.

### 4.1 Per store

| Store | Mechanism | Legal because | Measured cost |
|---|---|---|---|
| `wiki/` (135 MB, 4,439 files) | clonefile, else copy | COW clone gets its own inode | **43–72 ms**, ~4 MB metadata |
| `data/search.sqlite` (739 MB) | hardlink | published inode never modified in place | 0.26 ms, no new blocks |
| `data/graph.sqlite` (13.2 MB) | clonefile, else copy | see §4.4 | **0.33 ms**, no new blocks |
| authoritative manifest (`os.walk`) | — | — | 31 ms over 4,405 files |

**Corrected 2026-09-19 by Z0 step 3's measurement.** The wiki row previously
read `0.55–0.59 s, 0 blocks`. Both were wrong:

- **Timing.** One recursive `clonefile(2)` syscall on the directory measures
  43–72 ms (median 47.3, n=5) — roughly ten times faster. The old figure is
  consistent with per-file `cp -c`, which forks once per file.
- **"Zero disk" is retired.** A directory clone allocates about 4 MB of
  metadata for 4,439 new inodes. The claim that survives measurement is:
  **no additional file-data blocks were observed on APFS copy-on-write
  capture; filesystem metadata allocation is non-zero.** Use that wording;
  a claim of "zero" invites a reviewer to find the 4 MB and distrust the rest.

The portable fallback, measured on the same tree: `shutil.copytree` takes
1,779 ms and really allocates 147 MB — 25–37x the clone. It is an honest
portability path, not a decoration, and a test asserts the two backends produce
indistinguishable snapshots.

Bound: measured on one APFS volume on one machine. `cp -c` fails across volumes
and does not exist off APFS, which is why the fallback is a first-class path.
EXDEV itself was **NOT MEASURED** — this machine has no second volume:

```text
clone failure -> fallback path            TESTED (fault injection)
real EXDEV on a second filesystem         NOT MEASURED
```
Snapshots live under `data/snapshots/`, on the same volume as the stores, so the
fast path applies by construction.

**The clone is a superset of the manifest.** Everything under `wiki/` is
captured, because a snapshot that excluded something cannot answer questions
asked of it later and the clone costs zero blocks either way. But **derived
subtrees do not define a revision**: `wiki/traces/` is generated output that
`scripts/trace_writer.py:20` writes with `write_text`, bypassing `safe_write`
and therefore the mutation barrier. Including it in the authoritative manifest
would make Zurvan's own tracing register as external tampering — the mechanism
of §5.2 firing on the thing it exists to detect. Traces are captured for
forensics and excluded from the manifest. Measured: 25 of 4,430 files, 0.6%.

### 4.2 The hardlink invariant, made explicit

> A hardlink snapshot is valid **only** for a derived store whose active inode
> is never modified in place after publication.

`search.sqlite` qualifies today only because `rebuild_search_index` builds to
`data/search.sqlite.building-<pid>` and promotes with one `os.replace()`. If
anyone later opens the canonical path for writing, every existing snapshot
silently stops being one. **Z0 owes a regression that attempts exactly that
in-place production mutation and fails loudly.**

### 4.3 Published stores must be self-contained SQLite

> **A SQLite store may be published as an immutable snapshot component only when
> the published database file is self-contained. Publication must fail if
> committed state depends on WAL, SHM, rollback-journal, or other mutable
> sidecar state.**

A hardlink pins one file. If committed state lived in a `-wal` sidecar, the
pinned file would not be the whole database.

**Measured: this is not a live defect.** All three stores report
`journal_mode=delete` and no `-wal`, `-shm` or `-journal` sidecar exists on
disk. So this enters as an **assertion that locks in a property which currently
holds**, not as a fix:

- publication validates, checkpoints if applicable, and closes before promoting;
- publication asserts the published file needs no `-wal`, `-shm` or journal
  sidecar;
- a test asserts `journal_mode` on each published store, so a future
  `PRAGMA journal_mode=WAL` fails visibly instead of silently invalidating every
  snapshot ever taken.

It earns its place most on the graph, which is written in place today and can
carry a `-journal` sidecar mid-write.

### 4.4 The graph must become atomic

`scripts/graph_build.py:186` is a bare `sqlite3.connect(db_path)` against
`scripts/graph_schema.py:8`'s canonical path. The inode is mutated in place, so
**a hardlink to it is illegal under A35** — snapshot and live store would be the
same bytes.

Z0 could work around this by cloning under the barrier. It should not. The graph
gets the search index's shape:

```
build   data/graph.sqlite.building-<pid>
validate
stamp   schema_version, graph_generation_id, built_from_revision,
        built_from_snapshot, built_at      ← inside the DB, not the filename
os.replace()
```

Stamps live **inside** the database. A filename is not evidence.

### 4.5 The wiki regression must exceed "one edit pattern"

Proving inode separation proves too little. The regression clones a wiki file
and then, against the **live** copy: modifies it in place; renames it; deletes
it; replaces it atomically; truncates it to zero. After each, snapshot bytes
unchanged. That closes the "COW looked fine under one edit pattern" hole.

---

## 5. Unmanaged writers

The barrier covers writes that go through Zurvan. It cannot cover Obsidian, an
editor, or `cp`. There are **two distinct failures here**, and rev 1 only
addressed the first.

### 5.1 The racing edit — double manifest

An edit landing *while* capture walks the tree yields bytes from different
moments. Detection: compute the manifest before and after the capture; any
difference discards the capture and retries. Cost 31 ms (§4.1).

`_corpus_manifest_sha` (`scripts/measurement_snapshot.py:95`) computes
`(relpath, size, mtime_ns)` — **it does not compute an inode field**; rev 1 said
it did. Z0 promotes this function from diagnostic to correctness mechanism
**and extends it with `st_ino`**, which the same `stat` call already returns, so
the measured cost is unchanged.

The authoritative manifest covers `wiki/` **excluding derived subtrees**
(§4.1). Measured end-to-end on the live store: **31/31/30 ms** over 4,405
files using `os.walk` with a structural prune. The implementation matters more
than the mechanism here — `Path.rglob` with a `resolve()` per file costs 238 ms
to list and 226 ms to hash, 15× worse, and this runs on every writing command.

### 5.2 The completed edit — revision/manifest binding

The double manifest catches nothing here:

```
revision 100, search built_from 100, graph built_from 100

Obsidian edits foo.md and finishes          ← Zurvan never saw it
revision still 100

capture: M_before == M_after   (stable — the edit is long over)
         search says 100, graph says 100
         → declares coherence, falsely
```

The derived stores were built from the *old* `foo.md`. Nothing in rev 1 could
detect it, because stability is not the same question as awareness.

**Fix: every revision records the authoritative manifest digest it represents**
(`knowledge_state.manifest_digest`, computed at `end_mutation`). Capture
compares the live stable manifest against the revision's recorded digest:

```
live_manifest == revision_manifest   → Zurvan's view is current
live_manifest != revision_manifest   → unmanaged drift discovered
```

Drift does **not** mark the revision SUSPECT. Ordinary Obsidian editing is a
supported workflow and making it miserable would be a design failure. Instead a
new revision is minted:

```
revision 100    atomicity = MANAGED
      ↓         stable external drift discovered
revision 101    atomicity = EXTERNAL_OBSERVED
                manifest  = the new live manifest
                state     = CLEAN
```

Search and graph are then correctly `STALE` at 100, which is true and visible.

The two checks answer different questions and both are required:

| Check | Proves |
|---|---|
| `M_before == M_after` | these bytes formed **one stable filesystem state** |
| `live == revision.manifest_digest` | Zurvan **knew about** that state |

`atomicity` is the honest label on the result. `EXTERNAL_OBSERVED` claims one
stable observed filesystem state; it does **not** claim one logical user
transaction, and no consumer may treat it as one. A measurement snapshot
surfaces the atomicity of the revision it pins, so an evaluation can record what
kind of input it actually had.

**Second bound, on concurrent mutations.** If A and B are in flight together
and A commits first, the manifest A records already contains B's half-written
files. This is benign for drift detection — B's commit overwrites the digest
moments later, and capture refuses while any mutation is ACTIVE — but revision
A's `MANAGED` label is not a faithful record of "the state after A alone".
Stated rather than engineered away: serialising every logical operation across
a shared cross-project store would reintroduce the global lock this design
exists to avoid.

**Bound, stated rather than engineered away:** size + `mtime_ns` + inode is not
a content hash. An in-place same-size write within one nanosecond tick is
undetectable. The alternative is hashing 135 MB per capture and no observed
failure justifies it.

---

## 6. Derived stores: coherent, current and stale are three states

### 6.1 Rebuilds consume authoritative snapshots

A rebuild takes minutes. Starting at R while the wiki reaches R+3, it indexed no
single revision: stamping R is false and stamping R+3 is more false. The only
honest stamp comes from reading a snapshot.

```
capture(purpose="rebuild_search", required={"wiki"})   → S(R)
acquire lease
read ONLY S(R)'s authoritative inputs
build temporary derived store
validate
stamp built_from_revision = R, built_from_snapshot = S
os.replace()
release lease
```

**No implicit live reads once the build starts.** Both paths take the same
contract: `rebuild_search_index(snapshot=S)`, `graph_build(snapshot=S)`.

A rebuild **refuses a SUSPECT snapshot**. Otherwise reconciliation is bypassable:
rebuild a derived store from torn knowledge and it becomes internally
consistent, which looks exactly like correct. Per §3.5, reconciliation reaches
CLEAN *without* rebuilding, so this refusal no longer blocks recovery.

### 6.2 Three health states

```
COHERENT + CURRENT    built_from_revision == knowledge_revision
COHERENT + STALE      built_from_revision <  knowledge_revision
INVALID               no truthful source snapshot, or metadata mismatch
```

```
Knowledge revision: 1854
Search built from:  1851      → STALE but coherent
Graph built from:   1854      → CURRENT and coherent
```

Stale is a normal state, not corruption. Conflating them is what makes operators
reach for a rebuild they do not need.

### 6.3 Promotion does not require CURRENT

Promotion requires validation — SQLite integrity, row counts, provider/model/
dimension (the exact-provider invariant, unchanged), source manifest,
self-containment (§4.3), `built_from_snapshot`, `built_from_revision` — then
atomically replaces.

It must **not** require `built_from_revision == current_revision`. A busy store
advances while the rebuild runs, so that requirement means a sufficiently active
Zurvan never finishes a rebuild. Promote as `COHERENT+STALE` and recommend a
follow-up.

---

## 7. Capture

### 7.1 Algorithm

```
 1. wait until no logical mutation is ACTIVE            (else BUSY)
 2. read revision R; assert state == CLEAN              (else SNAPSHOT_SUSPECT)
 3. compute live manifest M_before
 4. if M_before != revision.manifest_digest:
        mint revision R+1, atomicity = EXTERNAL_OBSERVED, CLEAN   (§5.2)
        restart from step 2
 5. capture authoritative bytes (wiki, registry inputs)
 6. for each capability in `required` ∩ derived:
        capture immutable inode; verify built_from_revision == R
 7. capture registered optional components
 8. compute M_after; assert M_after == M_before                   (§5.1)
 9. re-read revision; assert still R and still CLEAN
10. assert no mutation became ACTIVE
11. write the immutable snapshot manifest, recording `purpose`,
    `required`, per-component status, and the revision's `atomicity`
12. acquire lease
13. expose snapshot
```

Step 6 runs **only over declared capabilities**. An authoritative capture
(`required={"wiki"}`) never inspects a derived store, which is what removes the
§2.2 deadlock.

Any failure discards the partial capture and retries or fails closed. Retry is
bounded — an unbounded retry against a continuously-written store is a hang, and
a hang is worse than a refusal.

### 7.2 A snapshot is allowed to fail, and that is the feature

```
wiki    = revision 1850
search  built_from = 1849
graph   built_from = 1850

capture(purpose="measurement", required={"wiki","search","graph"})
  → SNAPSHOT_NOT_READY: search index is one knowledge revision behind
```

The same state captures fine for `purpose="rebuild_search"`. Refusal is scoped
to what the caller actually claimed.

### 7.3 The claim ledger is declared absent

There is no `claims.sqlite`; the structured ledger is Z2. Claims are Markdown
under `wiki/claims/`, already covered by the authoritative capture. Z0 does not
invent a ledger to satisfy a diagram (§10.5, §34.10):

```yaml
components:
  wiki:    { status: VERIFIED }
  search:  { status: VERIFIED }
  graph:   { status: VERIFIED }
  claims:
    status: UNSUPPORTED_BY_DESIGN
    reason: "structured claim ledger introduced in Z2"
```

**With a guard: if `claims.sqlite` exists and no snapshot adapter is registered,
capture fails closed.** That stops Z2 arriving later and being silently
invisible to Z0's coherence proof. When Z2 ships it registers an adapter and
plugs into this protocol unchanged.

---

## 8. Leases and garbage collection

Extends `sweep_abandoned` (`scripts/measurement_snapshot.py:235`), which already
checks process liveness.

```yaml
snapshot_id:
knowledge_revision:
purpose:
created_at:
last_heartbeat:
owner_pid:
process_start_identity:     # pid alone is recycled
state:
referenced_artifacts:
```

GC reclaims only when the lease is expired **and** the owner no longer matches a
live process identity. Crash cleanup must not delete a snapshot because a
process vanished moments ago (§8.5). Snapshot metadata may outlive the heavy
artifacts it referenced.

CLI: `zurvan snapshot list`, `zurvan snapshot gc`, `zurvan knowledge doctor`,
`zurvan knowledge reconcile`. No GC framework.

---

## 9. Migration

`measurement_snapshot.py` keeps working throughout:

- `capture()` gains a `purpose`/`required` contract; the existing return
  contract is preserved or extended, never narrowed. Legacy callers get a
  default equivalent to today's behaviour.
- `check.sh` gate 6 calls `--path-only` (`scripts/check.sh:123`) and `--sweep`
  (line 36). **Both stay green at every commit in this phase.**
- The 14 contracts in `tests/test_measurement_snapshot.py` are not rewritten to
  accommodate new behaviour. If one must change, that is a finding to record.

### 9.1 UNVERIFIED is not PASS

Before stamping lands, derived stores carry no `built_from_revision`. That state
is explicit, never silent:

```yaml
coherence:
  status: UNVERIFIED
  reason: search_has_no_revision_stamp
```

A missing stamp must never read as agreement. The rule that keeps partial
implementation out of the acceptance gate:

> **No retrieval evaluation may claim a coherent Z0 snapshot while any required
> component is `UNVERIFIED`.**

Legacy `capture()` and gate 6 stay operational throughout. At closure:

```
wiki      VERIFIED
search    VERIFIED
graph     VERIFIED
claims    UNSUPPORTED_BY_DESIGN
```

### 9.2 Order of work

Each step is independently valuable and independently verifiable:

1. revision + mutation barrier + CLEAN/SUSPECT + `planned`/`completed` paths +
   reconcile (no snapshot change);
2. revision↔manifest binding + `EXTERNAL_OBSERVED` minting (§5.2);
3. immutable-bytes abstraction + wiki/graph capture + the edit-pattern matrix;
4. atomic graph rebuild + self-containment assertion;
5. snapshot-backed rebuilds + `built_from_revision` + health states;
6. `purpose`/`required` capture + coherence verification + leases/GC + CLI;
7. implicit-mutation inventory driven to empty (§3.2).

---

## 10. The gate

Z0 closes when all of these are demonstrated, each by a named test.

### 10.1 Mutation semantics

1. **Partial logical operation.** A snapshot attempted between the two writes of
   `decision add` returns BUSY, never a half-state.
2. **Implicit multi-write.** Two unwrapped implicit writes are detected and
   reported; Z0 may not claim atomicity for them.
3. **Inventory empty.** No production multi-write operation relies on implicit
   mode at closure — a query over `mutations.implicit`, not an assertion.
4. **Orphan.** Kill a process mid-mutation: the revision becomes SUSPECT,
   snapshots of it are refused, live writes continue, SUSPECT propagates across
   a subsequent unrelated mutation, and only `reconcile` clears it.
5. **Reconcile reaches CLEAN without a rebuild**, and a rebuild afterwards makes
   derived stores CURRENT.
6. **Reconcile stops for review** when a planned path was never completed, even
   though every written file parses.
7. **Planned vs completed.** Kill between writing a file and stamping it; the
   path is still investigable from `planned_paths`.

### 10.2 Coherence

8. **Coherence refusal.** A measurement capture whose declared components
   disagree on revision fails closed with `SNAPSHOT_NOT_READY`, naming the
   component and the gap.
9. **No deadlock.** With search stale at R−1, an authoritative capture succeeds,
   the rebuild from it succeeds, and the measurement capture then succeeds.
10. **A/B isolation** (canonical §8.7). Live state A at revision E; start S(E);
    mutate live to B/E+1 during the evaluation; every measurement input still
    resolves to E; B is live; no A/B hybrid observable; a second snapshot sees
    the new revision.
11. **Capture race.** Force a write *during* capture; capture retries rather than
    emitting a Franken-state.
12. **Completed unmanaged edit.** Edit a wiki file outside `safe_write`, let it
    finish, then capture: the revision/manifest mismatch is detected and a new
    `EXTERNAL_OBSERVED` revision is minted, leaving derived stores STALE.
13. **Atomicity is not laundered.** A snapshot of an `EXTERNAL_OBSERVED` revision
    reports that atomicity and no consumer treats it as MANAGED.

### 10.3 Store invariants

14. **Hardlink invariant.** An attempted in-place write to the published
    `search.sqlite` inode fails loudly.
15. **Self-contained SQLite.** Published stores assert `journal_mode` and need no
    `-wal`, `-shm` or journal sidecar.
16. **Wiki immutability matrix.** Modify / rename / delete / atomic-replace /
    truncate the live file; snapshot bytes unchanged in all five.
17. **Truthful stamp.** A rebuild running while the corpus advances stamps the
    revision it read, and the store reports `COHERENT+STALE`.
18. **SUSPECT refusal.** A rebuild refuses a SUSPECT snapshot.
19. **Claims guard.** Create an empty `claims.sqlite` with no adapter; capture
    fails closed.

### 10.4 Regression floor

20. Full `pytest` 0 failed; `check.sh` all 24 gates; `public_repo_guard`;
    `git diff --check`.

Plus a measurement, reported whatever it says: end-to-end capture cost and peak
RSS on the live store, against today's 485 ms baseline. The estimate from §4.1's
components is ~1.1 s and zero disk. **An estimate is not a result.**

**Measured 2026-09-19 (Z0 step 3), and the estimate was wrong by 5x.**

```text
full capture, 3 components   5,904 ms      estimate was ~1.1 s
peak RSS delta                 8.34 MB     §12.1 asked if it stays near 1.20 MB
frozen                       884.1 MB      wiki 131.6 / search 739.2 / graph 13.2
```

The estimate was not wrong about bytes. Freezing them costs ~120 ms, near
enough what §4.1 now records. It was wrong because it counted only the cost of
FREEZING bytes and omitted the cost of DESCRIBING them. `index_identity`
dominates at ~6.7 s:

```text
COUNT(DISTINCT source_path)                     4,014 ms   full scan of chunks
DISTINCT provider, model, dimension FROM embeddings  2,956 ms   SCAN + temp B-tree
chunk_id_set_sha                                  418 ms   matches its docstring
COUNT(*)                                          524 ms
```

This is **pre-existing**: `capture` has called `index_identity` since
2026-09-17, so every snapshot has always paid it and `check.sh` gate 6 pays it
on every run. Step 3 did not introduce it; step 3's measurement is simply the
first time anyone looked. It is **not fixed here**, because the obvious remedy
is an index on `embeddings` and §11 forbids any index-format change in Z0.

Worth carrying to Z1: the embeddings query scans 739 MB to return one row, and
it cannot be replaced by `LIMIT 1`, because the DISTINCT **is** the correctness
check — it exists to catch a second embedding space, where cosine scores stop
being comparable. A real guarantee at a real cost, now measured.

---

## 11. Explicitly not built in Z0

- No content-addressed store (amendment 3).
- No `data/generations/` directory (amendment 3).
- No `claims.sqlite` (Z2) — only the fail-closed hook.
- No retrieval, ranking, chunking, embedding or index-format change of any kind.
  Z0 must be provably ranking-neutral; if a retrieval number moves, something is
  wrong.
- No write lease for ordinary test runs. Still the right tool for an operation
  that genuinely needs a stationary store, still the wrong one for pytest.
- No incremental compilation (Z6). A rebuild remains a rebuild.
- No attempt to make unmanaged editors transactional. `EXTERNAL_OBSERVED` is the
  strongest honest claim available for a tool that does not participate.

---

## 12. Open questions, to be answered by measurement

1. ~~What does a full coherent capture cost on the live store, and does peak RSS
   stay near the current 1.20 MB?~~ **ANSWERED 2026-09-19 (§10.4):** 5,904 ms,
   and peak RSS delta is 8.34 MB — about 7x the 1.20 MB it asked about, still
   trivial in absolute terms. The cost is ~98% identity computation, not bytes.
2. Does the manifest need to become a content hash? Only an observed missed-edit
   case justifies the cost.
3. Is the bounded capture retry ever exhausted by ordinary cross-project write
   traffic? If so, capture needs a brief write-pause request rather than a longer
   retry loop.
4. Once rebuilds are snapshot-backed, can the CLAUDE.md prohibition on running
   `check.sh` during a rebuild be downgraded from correctness to resource
   contention — demonstrated rather than argued?
5. What is the right SUSPECT propagation boundary when two projects interleave
   mutations and one orphans?
6. How often does `EXTERNAL_OBSERVED` actually fire in daily Obsidian use? If it
   is constant, the manifest check is measuring normal life rather than drift,
   and the workflow needs a cheaper way to tell Zurvan an edit happened.
