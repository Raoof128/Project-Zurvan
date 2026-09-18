# Z0 — Coherent snapshots and the derived-store lifecycle

**Date:** 2026-09-18
**Phase:** Z0 of the Zurvan 2.0 programme
([canonical spec](2026-09-18-zurvan-2-canonical-upgrade-spec.md),
[amendments](2026-09-18-zurvan-2-amendments.md))
**Supersedes in scope:** [2026-09-17-measurement-snapshot-design.md](2026-09-17-measurement-snapshot-design.md),
which remains accurate for what it built and is the starting point, not a rival.
**Status:** Design. Not implemented. Nothing in this document may be reported as
built until it is measured.

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

So `capture()` returns an object that *looks* like a snapshot and is, for the
hybrid retriever, genuinely sufficient — that retriever reads only the index.
It is not sufficient for anything Z1 onwards needs, because every later phase
asks questions of more than the index.

Worse, the gap is invisible. A snapshot with a frozen index and a moving wiki
answers questions exactly like a coherent one. That is the same failure shape as
the 2026-09-16 index that held 8,000 of 185,303 chunks: structurally valid,
silently wrong, and indistinguishable from correct at the call site.

The canonical spec names the missing property (A39):

> Freezing wiki bytes, claims, graph, and search independently does not prove
> they describe the same logical knowledge state.

**Z0 closes when a snapshot can prove one coherent knowledge state, and refuses
to exist when it cannot.**

---

## 2. The three mechanisms, in dependency order

Everything below rests on three ideas. They are separable and each is testable
on its own.

1. **A knowledge revision** — a monotonic integer naming a logical state of
   writable knowledge, advanced by *logical mutations*, never by file writes.
2. **An immutable-bytes abstraction** — a way to hold a store's bytes such that
   no live edit can reach them, with a preferred fast path and a portable
   fallback.
3. **`built_from_revision`** — a truthful stamp on every derived store, which is
   only obtainable if the rebuild that produced it read a snapshot.

Miss any one and the other two cannot prove anything.

---

## 3. Knowledge revisions and the logical mutation barrier

### 3.1 Why a write counter is wrong

The obvious design advances the revision inside `safe_write`. It is wrong, and
the failure is ordinary rather than exotic. `zurvan decision add` performs two
writes:

```
revision 100
  write wiki/decisions/foo.md      → revision 101
                                        ← a snapshot taken here is byte-perfect
  append wiki/log.md               → revision 102
```

Revision 101 is internally consistent at the filesystem level and represents a
half-completed logical operation: a decision page that the log does not know
about. That is a Franken-state produced by the mechanism meant to prevent them.

**A revision is advanced by a logical mutation, not by a write.**

### 3.2 The barrier

```
begin_mutation(project, writer)      → mutation M17, base_revision 1847
    safe_write(A)
    safe_write(B)
    safe_write(C)
end_mutation(M17)                    → knowledge_revision 1847 → 1848
```

The lock is held for the mutation boundary only — the commit point — never for
the duration of an evaluation (§8.1). Locking is **cross-process**: the store is
shared and other projects write into it. The CHANGELOG records an Instagram
session writing `wiki/log.md` at 15:21:04 during a gate run; that writer must
participate, which it does automatically once the CLI's write paths are wrapped.

`begin_mutation`/`end_mutation` wrap at the CLI command level, not inside
`safe_write`. `safe_write.write_file_safely` and `append_file_safely`
(`scripts/safe_write.py:62,81`) become *participants*: they record their target
into the active mutation's `touched_paths` via the existing `_stamp` →
`writelog.record` path (`scripts/writelog.py:97`), which already captures
session token, pid and ppid.

**A write with no active mutation is not an error.** It is recorded as an
implicit single-write mutation that opens and commits immediately. Anything
stricter would break every existing caller on day one, and the property that
matters — no snapshot observes a partial logical operation — is preserved either
way.

### 3.3 Revisions are CLEAN or SUSPECT

Liveness and trustworthiness are different facts and must not share a flag.

```
ACTIVE mutation
   ├── owner alive          → snapshot returns BUSY
   └── owner provably dead  → ORPHANED
                                  ↓
                            revision R = SUSPECT
                                  ↓
                            snapshot of R forbidden
```

The store does **not** become unusable. Writes continue and the revision
advances. But:

> Advancing past a SUSPECT revision does not make the successor trustworthy.

If R held a half-written decision page and the next mutation only appends a
session log, R+1 still holds the half-written page. So `SUSPECT` **propagates**
until an explicit repair boundary clears it. `CLEAN`/`SUSPECT` is a property of
revisions, not of mutations.

### 3.4 Recovery: `zurvan knowledge reconcile`

Not `rollback`. Rollback semantics cannot be proven here — a dead process proves
the mutation will not finish, and proves nothing about whether the bytes it
already wrote are complete. Automatic rollback would be fiction and automatic
timeout-commit would be a lie of the same size.

`reconcile` must:

1. detect orphaned mutations;
2. list touched paths (available: `writelog.entries`, `scripts/writelog.py:120`);
3. compare current bytes against any pre-mutation hashes available;
4. validate Markdown/frontmatter on touched files;
5. verify the double manifest is stable;
6. rebuild derived stores if required;
7. verify every derived store reports the same new revision;
8. only then mark the resulting revision `CLEAN`.

**No silent deletion, ever.** `reconcile` may refuse and report; it may not
resolve by discarding.

`zurvan snapshot doctor` reports the state in a form a human can act on:

```
SUSPECT revision 1848
Orphaned mutation:
  project: instagram
  owner:   pid 48211 / start-token 1f3c…
  touched: wiki/decisions/instagram-….md
           wiki/log.md
Snapshots blocked until reconciliation. Live writes remain available.
```

### 3.5 Schema

`data/registry.sqlite` currently holds one table (`sources`), so this is
additive:

```sql
CREATE TABLE knowledge_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    current_revision INTEGER NOT NULL,
    state            TEXT NOT NULL      -- CLEAN | SUSPECT
);

CREATE TABLE mutations (
    mutation_id      TEXT PRIMARY KEY,
    base_revision    INTEGER NOT NULL,
    state            TEXT NOT NULL,     -- ACTIVE | COMMITTED | ORPHANED
    project          TEXT,
    writer           TEXT,
    owner_pid        INTEGER,
    owner_identity   TEXT NOT NULL,     -- pid + process start time; PIDs are reused
    started_at       TEXT NOT NULL,
    committed_at     TEXT,
    last_heartbeat   TEXT,
    touched_paths    TEXT,              -- JSON array
    orphan_reason    TEXT
);
```

`owner_identity` pairs the pid with its process start time. A pid alone is
recycled and would let an unrelated new process vouch for a dead mutation.

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

No bespoke CAS is built in Z0 (amendment 3). Its only job is deduplication,
which APFS already does; a CAS would add a second GC surface for no measured
benefit.

### 4.1 Per store

| Store | Mechanism | Legal because | Measured cost |
|---|---|---|---|
| `wiki/` (135 MB, 4,425 files) | clonefile, else copy | COW clone gets its own inode | 0.55–0.59 s, **0 blocks** |
| `data/search.sqlite` (738 MB) | hardlink | published inode is never modified in place | 0.26 ms, 0 blocks |
| `data/graph.sqlite` (13 MB) | clonefile, else copy | see §4.3 | < 10 ms, 0 blocks |
| manifest (stat walk) | — | — | 15–17 ms warm, 116 ms cold |

Bound: measured on one APFS volume on one machine. `cp -c` fails across volumes
and does not exist off APFS, which is why the fallback is a first-class path and
not an afterthought. Snapshots live under `data/snapshots/`, on the same volume
as the stores, so the fast path applies by construction.

### 4.2 The hardlink invariant, made explicit

> A hardlink snapshot is valid **only** for a derived store whose active inode
> is never modified in place after publication.

`search.sqlite` qualifies today only because `rebuild_search_index` builds to
`data/search.sqlite.building-<pid>` and promotes with one `os.replace()`, giving
a new inode. If anyone later opens the canonical path for writing, every
existing snapshot silently stops being one. **Z0 owes a regression that attempts
exactly that in-place production mutation and fails loudly.**

### 4.3 The graph must become atomic

`scripts/graph_build.py:186` is a bare `sqlite3.connect(db_path)` against
`scripts/graph_schema.py:8`'s canonical path. The inode is mutated in place, so
**a hardlink to it is illegal under A35** — the snapshot and the live store would
be the same bytes.

Z0 could work around this by cloning under the capture barrier. It should not.
The graph gets the same shape as the search index:

```
build   data/graph.sqlite.building-<pid>
validate
stamp   schema_version, graph_generation_id, built_from_revision,
        built_from_snapshot, built_at        ← inside the DB, not the filename
os.replace()
```

Stamps live **inside** the database. A filename is not evidence.

### 4.4 The wiki regression must exceed "one edit pattern"

Proving inode separation proves too little. The regression clones a wiki file
and then, against the **live** copy:

- modifies it in place;
- renames it;
- deletes it;
- replaces it atomically (`write tmp` + `os.replace`);
- truncates it to zero.

After each, the snapshot bytes must be unchanged. That closes the "COW looked
fine under one edit pattern" hole.

---

## 5. Unmanaged writers, and why the double manifest is not optional

The barrier covers writes that go through Zurvan. It cannot cover Obsidian, a
text editor, `cp`, or any tool that does not participate. Such an edit changes
**neither** the revision nor the mutation flag, so re-reading both — the obvious
check — detects nothing, and the clone silently captures a torn state while the
manifest swears it is coherent.

This is canonical §8.1's requirement and it is cheap: 15–17 ms warm for a
`(relpath, size, mtime_ns, inode)` walk over 4,425 files. `_corpus_manifest_sha`
(`scripts/measurement_snapshot.py:95`) already computes this shape and is
documented there as *diagnostic*. Z0 promotes it to a **correctness mechanism**:
computed before and after the capture, and any difference discards the capture
and retries.

Stated bound: `mtime_ns` + size + inode is not a content hash. A write that
preserves all three is undetectable. On APFS `mtime_ns` has nanosecond
resolution and inodes change on atomic replace, so the residual risk is an
in-place same-size write within one nanosecond tick. Recorded rather than
engineered away; a content hash over 135 MB per capture is the alternative and
is not warranted by any observed failure.

---

## 6. Derived stores: coherent, current, and stale are three states

### 6.1 Rebuilds consume snapshots

A rebuild takes minutes. If it starts at R and the wiki reaches R+3 while it
runs, it indexed no single revision: stamping R is false and stamping R+3 is
more false. The only honest stamp comes from reading a snapshot.

```
capture S(R)
acquire lease
read ONLY S(R)/wiki and S(R)'s registered inputs
build temporary derived store
validate
stamp built_from_revision = R, built_from_snapshot = S
os.replace()
release lease
```

**No implicit live reads once the build starts.** Both paths take the same
contract: `rebuild_search_index(snapshot=S)`, `graph_build(snapshot=S)`.

A rebuild **refuses a SUSPECT snapshot**. Otherwise reconciliation is bypassable:
rebuild a derived store from torn knowledge and it becomes internally consistent,
which looks exactly like correct.

### 6.2 Three health states, not two

```
COHERENT + CURRENT    built_from_revision == knowledge_revision
COHERENT + STALE      built_from_revision <  knowledge_revision
INVALID               no truthful source snapshot, or metadata mismatch
```

So:

```
Knowledge revision: 1854
Search built from:  1851      → STALE but coherent
Graph built from:   1854      → CURRENT and coherent
```

Stale is a normal state, not corruption. Conflating the two is what makes
operators reach for a rebuild they do not need.

### 6.3 Promotion does not require CURRENT

Promotion requires validation — SQLite integrity, row counts, provider/model/
dimension (the exact-provider invariant, unchanged), source manifest,
`built_from_snapshot`, `built_from_revision` — and then atomically replaces.

It must **not** require `built_from_revision == current_revision`. A busy store
advances while the rebuild runs, so that requirement means a sufficiently active
Zurvan can never finish a rebuild. Promote as `COHERENT+STALE` and recommend a
follow-up rebuild.

---

## 7. Snapshot capture

### 7.1 Algorithm

```
 1. wait until no logical mutation is ACTIVE        (else BUSY)
 2. read knowledge revision R; assert state == CLEAN (else SNAPSHOT_SUSPECT)
 3. compute wiki manifest M_before
 4. capture wiki immutable bytes
 5. capture search immutable inode
 6. capture graph immutable inode
 7. verify search.built_from_revision == R
 8. verify graph.built_from_revision  == R
 9. capture all registered optional components
10. compute wiki manifest M_after; assert M_after == M_before
11. re-read revision; assert still R and still CLEAN
12. assert no mutation became ACTIVE
13. write the immutable snapshot manifest
14. acquire lease
15. expose snapshot
```

Any failure discards the partial capture and either retries or fails closed.
Retry is bounded; an unbounded retry against a continuously-written store is a
hang, and a hang is a worse failure than a refusal.

### 7.2 A snapshot is allowed to fail, and that is the feature

```
wiki    = revision 1850
search  built_from = 1849
graph   built_from = 1850
```

This does not quietly snapshot. It returns:

```
SNAPSHOT_NOT_READY: search index is one knowledge revision behind
```

For evaluation this is exactly the desired behaviour. The whole point of Z0 is
that a measurement refuses to run on a state it cannot describe.

### 7.3 The claim ledger is declared absent

There is no `claims.sqlite`; the structured ledger is Z2. Claims are Markdown
under `wiki/claims/` and are already covered by the wiki capture. Z0 does not
invent a ledger to satisfy a diagram (§10.5, §34.10):

```yaml
components:
  wiki:    { supported: true }
  search:  { supported: true }
  graph:   { supported: true }
  claims:
    supported: false
    reason: "structured claim ledger introduced in Z2"
```

**With a guard: if `claims.sqlite` exists and no snapshot adapter is registered
for it, capture fails closed.** That is what stops Z2 arriving later and being
silently invisible to Z0's coherence proof. When Z2 ships it registers an
adapter and plugs into this protocol unchanged.

---

## 8. Leases and garbage collection

Extends `sweep_abandoned` (`scripts/measurement_snapshot.py:235`), which already
checks process liveness.

```yaml
snapshot_id:
knowledge_revision:
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

CLI: `zurvan snapshot list`, `zurvan snapshot gc`, `zurvan snapshot doctor`,
`zurvan knowledge reconcile`. No GC framework.

---

## 9. Migration

`measurement_snapshot.py` keeps working throughout. Specifically:

- `capture()` gains coherence; its existing return contract is preserved or
  extended, never narrowed.
- `check.sh` gate 6 calls `--path-only` (`scripts/check.sh:123`) and `--sweep`
  (line 36). **Both must stay green at every commit in this phase.**
- The 14 existing snapshot contracts in `tests/test_measurement_snapshot.py`
  must not be rewritten to accommodate new behaviour. If one has to change, that
  is a finding to record, not a chore.
- Before revisions exist, `built_from_revision` is absent on both derived
  stores. The verification steps (7, 8) therefore begin as *capability
  unavailable* rather than *pass* — the snapshot declares the component
  unverified. A missing stamp must never read as agreement.

Order of work, each independently valuable and independently verifiable:

1. revision + mutation barrier + CLEAN/SUSPECT + reconcile (no snapshot change);
2. immutable-bytes abstraction + wiki/graph capture + the edit-pattern matrix;
3. atomic graph rebuild;
4. snapshot-backed rebuilds + `built_from_revision` + health states;
5. coherence verification in capture + leases/GC + CLI.

---

## 10. The gate

Z0 closes when all of these are demonstrated, each by a named test:

1. **Coherence.** A capture whose components disagree on revision fails closed
   with `SNAPSHOT_NOT_READY`, naming the component and the gap.
2. **A/B isolation** (canonical §8.7). Live state A at revision E; start S(E);
   mutate live to B/E+1 during the evaluation; prove every measurement input
   still resolves to E, that B is live, and that no A/B hybrid is observable;
   a second snapshot sees the new revision.
3. **Capture race.** Force a write *during capture* and prove the capture retries
   rather than emitting a Franken-state.
4. **Unmanaged edit.** An edit that bypasses `safe_write` entirely is caught by
   the double manifest.
5. **Partial logical operation.** A snapshot attempted between the two writes of
   `decision add` returns BUSY, never a half-state.
6. **Orphan.** Kill a process mid-mutation; the revision becomes SUSPECT,
   snapshots of it are refused, live writes continue, SUSPECT propagates across
   a subsequent unrelated mutation, and only `reconcile` clears it.
7. **Hardlink invariant.** An attempted in-place write to the published
   `search.sqlite` inode fails loudly.
8. **Wiki immutability matrix.** Modify / rename / delete / atomic-replace /
   truncate the live file; snapshot bytes unchanged in all five.
9. **Truthful stamp.** A rebuild running while the corpus advances stamps the
   revision it read, and the resulting store reports `COHERENT+STALE`.
10. **SUSPECT refusal.** A rebuild refuses a SUSPECT snapshot.
11. **Claims guard.** Create an empty `claims.sqlite` with no adapter; capture
    fails closed.
12. **Regression floor.** Full `pytest` 0 failed; `check.sh` all 24 gates;
    `public_repo_guard`; `git diff --check`.

Plus a measurement, reported whatever it says: end-to-end capture cost and peak
RSS on the live store, against today's 485 ms baseline. The estimate from §4.1's
components is ~1.1 s and zero disk. **An estimate is not a result.**

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

---

## 12. Open questions, to be answered by measurement

1. What does a full coherent capture actually cost on the live store, and does
   peak RSS stay near the current 1.20 MB?
2. Does the wiki manifest need to become a content hash? Only an observed
   missed-edit case justifies the cost.
3. Is the bounded capture retry ever exhausted in practice by ordinary
   cross-project write traffic? If so, capture needs a brief write-pause request
   rather than a longer retry loop.
4. Once rebuilds are snapshot-backed, can the CLAUDE.md prohibition on running
   `check.sh` during a rebuild be downgraded from correctness to resource
   contention — and can that be demonstrated rather than argued?
5. What is the right SUSPECT propagation boundary when two projects interleave
   mutations, one of which orphans?
