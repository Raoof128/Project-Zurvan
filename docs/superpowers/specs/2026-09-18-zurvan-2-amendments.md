# Zurvan 2.0 — amendments and state reconciliation

**Date:** 2026-09-18
**Status:** Living. The canonical spec
([2026-09-18-zurvan-2-canonical-upgrade-spec.md](2026-09-18-zurvan-2-canonical-upgrade-spec.md))
is kept **verbatim** as committed, preserving its two-gauntlet lineage. Every
change to it since lands here, dated, with the evidence that forced it.

The canonical spec's §34 says a phase that needs to violate an acceptance
invariant "is an architecture change and must amend this spec explicitly before
implementation". This file is that mechanism. It amends *mechanism*, never
*invariant*.

---

## 1. Reconciliation: what is actually built, as of 2026-09-18

The canonical spec §1 lists the existing system and §26 lists phases Z0–Z12.
Neither states which phases are done. This is that statement.

| Phase | Claimed state | Evidence |
|---|---|---|
| **Z0** — coherent snapshots | **PARTIAL, ~1 of 7 components** | `scripts/measurement_snapshot.py` (commit `a77de4e`) pins `data/search.sqlite` by `os.link` plus a chunk-id-set hash. 14 contracts green; `check.sh` gate 6 scores a snapshot. |
| Z1–Z12 | **NOT STARTED** | — |

**Z0 is not closed and must not be reported as closed.** Its acceptance gate is
§30.16 — *"a snapshot proves cross-store revision coherence, not merely per-file
immutability"*. What exists proves per-file immutability for exactly one file.
`grep -rn 'knowledge_revision\|built_from_revision\|generations/' scripts/ tests/`
returns nothing: there is no revision, no capture barrier, no wiki byte replay,
no graph capture, no claim-ledger hook, no lease/GC, no capture-race retry.

Two properties of the shipped piece *are* correct and are carried forward:

- The hardlink is legal under A35 only because `rebuild_search_index` builds to
  a temp path and promotes with one `os.replace()`, so the published inode is
  never modified in place. That argument is store-specific. It does **not**
  transfer to `wiki/` or to `data/graph.sqlite`.
- `_corpus_manifest_sha` (`measurement_snapshot.py:95`) computes a
  `(relpath, size, mtime_ns)` manifest — **no inode field** — and is documented
  there as diagnostic. Z0 promotes it to a correctness mechanism **and extends
  it with `st_ino`**, which the same `stat` call already returns, so the
  measured cost is unchanged. The extension is new behaviour, not existing
  behaviour. **Corrected 2026-09-18 (rev 3 gauntlet):** the 15–17 ms figure was
  an `os.walk` measurement; an implementation using `Path.rglob` with a
  `resolve()` per file costs 464 ms, 15× worse. The mechanism is only as cheap
  as the walk that feeds it, so the implementation is specified, not left open.

---

## 2. Measured programme inputs

Facts produced by measurement, recorded here so they steer the programme rather
than decay in a session log.

### 2.1 The dense lane is 83% of query time

```
vector scan   0.425 s
query total   0.512 s
              ≈ 83%, full scan over 184,516 vectors
```

Measured 2026-09-18 (CHANGELOG `2026-09-18c`, follow-up 2). The canonical spec
has no phase for this: §11.2 only preserves the known-good dense representation
and defers quantisation. Amendment 2 gives it one.

### 2.2 APFS clone makes replayable bytes nearly free

Measured 2026-09-18 on the live store, `/dev/disk3s5` (APFS):

| Operation | Time (3 runs) | Real disk |
|---|---|---|
| `cp -c -R wiki` — 135 MB, 4,425 files | 0.59 / 0.56 / 0.55 s | **0** |
| `cp -c data/graph.sqlite` — 13 MB | 0.00 s | **0** |
| `os.link data/search.sqlite` — 738 MB (existing) | 0.26 ms | **0** |
| authoritative manifest, `os.walk` over 4,405 wiki files | 31/31/30 ms | — |

`du` reports 417 MB for three wiki clones; `df` free space **rose 5.5 MB**
across the test. Clones carry distinct inodes with `nlink` 1, so an in-place
edit to a live wiki file provably cannot reach the clone.

**Bound:** every figure is from one APFS volume on one machine. `cp -c` fails
across volumes and does not exist on non-APFS filesystems. This is why
Amendment 3 is written portable-first rather than as an APFS design.

### 2.3 `data/graph.sqlite` is written in place

`scripts/graph_build.py:186` is a bare `sqlite3.connect(db_path)` against the
canonical path — no build-and-promote. A hardlink to it is therefore **illegal
under A35**: same inode, mutable in place. Amendment 3 makes the graph rebuild
atomic rather than working around this.

---

## 3. Amendment 1 — Z1V, vector retrieval acceleration

**Date:** 2026-09-18. **Amends:** §26 phase list, §11.2.

A phase is inserted after Z1:

```
Z0    coherent immutable snapshots
Z1    observability + sealed eval programme
Z1V   vector retrieval acceleration / ANN investigation
Z2    claim ledger + temporal model
...
```

**Z1V — vector retrieval acceleration.** Compares the exact-cosine baseline
against ANN candidate(s) on: recall/ranking parity, Recall@1/@3, MRR, nDCG,
p50/p95 latency, index build cost, disk, RSS, incremental-update behaviour, and
deterministic rebuilds.

**Gate:** Z1V is subject to §9.5 unchanged. Approximation does not win on
latency alone — if it moves ranks, the spec requires knowing exactly what those
milliseconds cost. Z1V may not start before Z0 and Z1 close, because its entire
claim is a before/after retrieval comparison and §3.8 forbids promotion without
frozen evaluation.

**Rationale:** §2.1. Deliberately placed *after* the measurement foundation:
touching retrieval before snapshots are coherent reproduces the exact failure
Zurvan 2.0 exists to remove — better numbers measured on uncertain state.

---

## 4. Amendment 2 — Z0 is the derived-store lifecycle, not a file freezer

**Date:** 2026-09-18. **Amends:** §26 Z0 scope.

Z0 is renamed **"Coherent snapshot and derived-store lifecycle"** and its build
list becomes:

- knowledge revisions, with `CLEAN` / `SUSPECT` state and
  `MANAGED` / `EXTERNAL_OBSERVED` atomicity;
- a **logical mutation barrier** (transactions, not a per-write counter), with
  implicit mutation as a migration mechanism only;
- **two capture purposes** — an *authoritative* snapshot that pins inputs and
  requires nothing of derived stores, and a *measurement* snapshot that requires
  the capabilities its caller declares;
- double manifest verification, **plus a revision↔manifest binding** so a
  completed unmanaged edit is detected rather than only a racing one;
- COW snapshot capture behind a portable abstraction;
- orphan detection and `knowledge reconcile`, which reaches `CLEAN` without
  rebuilding;
- snapshot leases and GC;
- **snapshot-backed search rebuild**;
- **snapshot-backed graph rebuild**;
- truthful `built_from_revision`;
- `COHERENT+CURRENT` / `COHERENT+STALE` / `INVALID` health states.

**Rationale:** the last four are additions, and they exist because
`built_from_revision` is otherwise unimplementable honestly. A rebuild that
reads the live wiki while the corpus moves `R → R+3` indexed no single
revision; stamping either number is false. Only a rebuild that reads a snapshot
can stamp a revision it can prove. Without them, §30.16's verification step has
nothing truthful to compare against and Z0 would close on a partial claim for a
second time.

**Rev 2 of the Z0 design added the two-purpose capture split.** Design review
found that a single capture contract deadlocks: capture verified derived stamps
unconditionally while rebuild began by capturing, so a stale search index could
never be rebuilt. The same conflation deadlocked reconciliation, because
reaching `CLEAN` required a rebuild that refuses `SUSPECT` input. Separating
authoritative from measurement capture, and defining `CLEAN` as a property of
authoritative state rather than of derived currency, dissolves both.

**Consequence, recorded because it changes a standing rule:** the CLAUDE.md
prohibition on running `check.sh` during an index rebuild becomes a
**resource-contention** concern rather than a correctness one, since a
snapshot-backed rebuild cannot be contaminated by concurrent writes. The
prohibition stays in force until Z0 ships and the property is demonstrated.

---

## 5. Amendment 3 — snapshot mechanism is an abstraction, not `data/generations/`

**Date:** 2026-09-18. **Amends:** §5 storage layout, §8.4.

The canonical spec names a physical mechanism:

```
data/generations/search/<generation-id>.sqlite
data/generations/graph/<generation-id>.sqlite
```

It is replaced by a portable immutable-snapshot abstraction. The **correctness
contract is an immutable, replayable, coherent snapshot**; the mechanism is an
implementation detail the caller must not be able to observe:

```
Snapshot abstraction
  ├── APFS clonefile / COW          ← preferred fast path
  ├── immutable CAS object          ← reused only if already available
  └── ordinary private byte copy    ← correctness fallback, always available
```

Per store:

| Store | Mechanism | Legal because |
|---|---|---|
| `wiki/` | clonefile, else copy | COW clone has its own inode; §2.2 |
| `data/search.sqlite` | hardlink | published inode never modified in place (atomic rebuild) |
| `data/graph.sqlite` | clonefile, else copy — **and** graph rebuild becomes atomic | §2.3; a hardlink is illegal until the inode is immutable |

**No bespoke CAS is built in Z0.** Its only job is deduplication, which APFS
already performs; a CAS would add a second garbage-collection surface for no
measured benefit. It may be added later if measurement justifies it.

**Invariant being preserved, and the one this makes explicit:**

> A hardlink snapshot is valid **only** for a derived store whose active inode
> is never modified in place after publication.

If anyone later opens `data/search.sqlite` for writing, the snapshot guarantee
dies silently. Z0 owes a regression that attempts exactly that in-place
production mutation and fails loudly.

**What is unchanged:** §8.2's coherence requirement, §8.3's rule that a hash
manifest alone is not a snapshot, A35's hardlink restriction, and §30.16. This
amendment changes how immutable bytes are obtained, not what must be true of
them. `data/generations/` is no longer required as the physical implementation
of snapshot isolation.

---

## 6. Amendment 4 — Z0 declares the claim ledger absent

**Date:** 2026-09-18. **Amends:** §8.2 snapshot contents.

§8.2 requires "a consistent claim-ledger snapshot and revision". **There is no
`claims.sqlite`** — the structured claim ledger arrives in Z2. Claims today are
Markdown under `wiki/claims/` and are therefore already covered by the wiki
capture.

Z0 must not invent a ledger to satisfy a diagram, and must not claim a
capability it lacks (§10.5, §34.10). Snapshot manifests declare it:

```yaml
components:
  wiki:    { supported: true }
  search:  { supported: true }
  graph:   { supported: true }
  claims:  { supported: false, reason: "structured claim ledger introduced in Z2" }
```

**With a guard:** if `claims.sqlite` exists and no snapshot adapter is
registered for it, snapshot creation **fails closed**. This is what stops Z2
arriving later and being silently invisible to Z0's coherence proof.

---

## 7. Amendment log

| # | Date | Amends | Summary |
|---|---|---|---|
| 1 | 2026-09-18 | §26, §11.2 | Z1V phase inserted for the measured 83% vector scan |
| 2 | 2026-09-18 | §26 Z0 | Z0 widened to the derived-store lifecycle; snapshot-backed rebuilds; authoritative/measurement capture split (rev 2) |
| 3 | 2026-09-18 | §5, §8.4 | `data/generations/` replaced by a portable snapshot abstraction |
| 4 | 2026-09-18 | §8.2 | Claim-ledger component declared unsupported, with a fail-closed guard |

No amendment to date changes §3 (non-negotiable invariants), §9 (evaluation
policy), §30 (success definition) or §34 (acceptance gate).
