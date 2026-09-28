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

## 8. Mutation attribution: four named facts, not one overloaded field

**Date:** 2026-09-19. **Amends:** Z0 step 1 (`mutations` schema).
**Forced by:** two defects found in the LIVE production ledger, not in review.

### 8.1 What was wrong

Z0 step 1 shipped a single `project` column on `mutations`, filled by the CLI
wrapper from `project_registry.get_current_project()`. That value is the
registry's `current`: one file, shared by every concurrent session on the
machine. It is therefore neither the subject of the write nor an identity of
the caller, and it cannot name a session at all — two sessions in two repos
read the same value.

Observed at revision 8:

```
mfc9e4ccdd1d0  COMMITTED  otherproj  cli     <- written from inside the Zurvan repo
```

while the decision page that same mutation wrote stamped `project: zurvan`.
The ledger and the page disagreed about one write. `memory.resolve_project`
already existed precisely to stop that class of error — its docstring records
the 2026-09-15 incident in which four decisions written from inside the Zurvan
repo were stamped with an unrelated project because another session had left
that workspace selected — and the ledger reintroduced it one layer down.

### 8.2 The model

One column cannot hold two facts, so the column is replaced by four, each
named for what its value can actually support:

| field | source | meaning |
|---|---|---|
| `store_project` | `memory.resolve_project(explicit)`, declared by the write path | the SUBJECT stamped on the page; the publication control `public_repo_guard` reads. Whose knowledge moved. |
| `execution_project` | the registered project containing the working directory | WHERE execution happened. For an MCP server this is its own workspace, **not** the requester's. |
| `selected_project` | the registry's `current` | diagnostic only. Never consulted for ownership, orphan attribution or routing. |
| `caller_project` | nothing, yet | reserved for a caller that supplies a trustworthy project identity of its own. Always NULL until then. |

`caller_project` is deliberately empty rather than absent: naming the
cwd-derived value "caller" would plant the next attribution bug the first time
a request crosses a process boundary.

`store_project` is declared from the write path — `memory.add_decision`
resolves the subject once and both stamps the page and declares it — so the
page and the ledger take the same value from the same call and cannot
disagree. A second, different subject inside one operation raises: one
mutation, one subject; if a command ever writes two projects' pages the
operation boundary is drawn in the wrong place.

`"unknown"` is normalised to NULL. `resolve_project` answers `"unknown"`
because it is a publication control and fails closed; a ledger column has a
real representation for absence and does not need a second spelling.

`execution_project` and `selected_project` are captured **best effort** — the
one place in `knowledge_revision` that does not fail closed. They are
diagnostics, and a coherence ledger that refused a write because the project
registry was unreadable would trade a real guarantee for a label.

### 8.3 Migration of the live ledger

Recorded values ARE selections, so they move to `selected_project` verbatim.
The other three stay NULL on historical rows: nothing in the old row records
those facts, and a backfill would be a guess wearing the authoritative field's
name.

### 8.4 The MCP boundary (the more urgent defect)

The same inspection found four implicit mutations for two decisions:

```
m99e68f36fdf3  COMMITTED  implicit  scripts.memory:add_decision
m625b81a185fa  COMMITTED  implicit  scripts.memory:append_to_log
me499f76dea52  COMMITTED  implicit  scripts.memory:add_decision
mb9fa67565fd7  COMMITTED  implicit  scripts.memory:append_to_log
```

`scripts/mcp_tools.py` called `memory` directly, so the page and the
`wiki/log.md` append each activated their own implicit mutation and a revision
was minted **between** them — naming a state in which a decision page exists
that the log does not mention. That is the half-operation §3 of the Z0 design
forbids, and it was live, not hypothetical.

An MCP tool is now a top-level operation boundary, parallel to a CLI
invocation. The boundary belongs at the tool and **not** inside
`memory.add_decision`: making a library function its own operation would give
every caller an independent boundary and move the split one level down.

```
CLI command / MCP tool / agent operation   <- operation boundary
                  |
            memory functions
                  |
      safe_write two-phase participant
```

A memory function called with no boundary above it still becomes an implicit
mutation, recorded as such for the migration inventory. **Superseded by §9
(2026-09-28): it is now refused.**

### 8.5 Not fixed here, deliberately

`zurvan decision add --project X` cannot reach `add_decision(project=X)`:
`cli.py:42` scans `sys.argv` for `--project` anywhere and pops it as the global
workspace switch before argparse runs, then re-execs the CLI in that project's
directory. The subcommand's documented subject flag is shadowed. Measured
2026-09-19; the flag's help now says so, and the fix is a separate change
because it alters CLI argument semantics. **Fixed 2026-09-28 (§9.3).**

---

## 9. Amendment 7 — implicit mode is closed to production

### 9.1 The evidence

On 2026-09-23 an agent working in another repository (Antigravity, pid 22012)
recorded two decisions and a note. `PYTHONPATH=. python3 scripts/cli.py` failed
under an interpreter without PyYAML; `uv run` worked, but `decision add
--project` could not carry the subject it needed (§8.5), so it imported the
library:

```
uv run python -c "from scripts.memory import add_decision, add_note; ..."
```

Three logical writes became **six revisions (73-78)**, each page and its
`wiki/log.md` line committing separately — the half-operation §3.2 of the Z0
design names, in production, nine days after the MCP instance of it was fixed.
`resolve_project("ClientX")` answered `unknown` silently, and the agent then
hand-edited the pages outside the ledger. Forensic rows are preserved at
`~/.zurvan/forensics/2026-09-28-implicit-mutations-pid22012*.json`.

The ledger behaved exactly as designed: it recorded and inventoried the writes.
That was the defect. The 2026-09-19 decision to keep direct library writes
"implicit and inventoried" assumed every writer would eventually be wrapped;
an importable module has callers nobody enumerates.

### 9.2 The rule

- `plan_write` raises `KnowledgeOperationRequired` for an authoritative write
  with no operation on its store — before any byte lands and before any ledger
  row exists. It is the single point every `safe_write` passes, so bypassing
  `memory.py` does not bypass the rule.
- `memory`'s public writers (`add_decision`, `add_note`, `add_claim`,
  `add_question`) refuse up front, naming themselves and the way out.
- Implicit mode survives only inside
  `knowledge_revision.allow_implicit_for_migration(reason)`. There is no
  boolean form; the reason is mandatory and is recorded in a new
  `mutations.implicit_reason` column. Historical rows keep NULL.
- Boundaries stay where §8.4 put them: the CLI invocation and the MCP tool.
  `ingest.py` and `extract.py`, which run as `__main__`, now open their own.

### 9.3 Companion fixes, each causal in 9.1

- `--project` switches workspace only BEFORE the subcommand; after it, it
  belongs to the subcommand (`decision add --project clientx` works).
- An explicit project that is not a valid slug raises `InvalidProjectError`.
  It is neither stamped `unknown` nor lowercased on the caller's behalf: the
  subject is a publication control, and canonicalising a typo would
  manufacture authority. `unknown` remains the answer when nobody named one.
- `cli.py` under an interpreter without its dependencies names `scripts/zurvan`
  instead of printing a traceback.

### 9.4 What this does not fix

A hand edit of a wiki page is still invisible until something compares the
manifest (2026-09-24 report, item 3). The pages from 9.1 were edited that way
on 23, 24 and 26 September and the ledger reported CLEAN throughout. That is
the EXTERNAL_OBSERVED path of Z0 §5, not the implicit path, and is its own
change.


## 10. Amendment 8 — Z0 step 5: what a rebuild snapshot captures, and how coherence is proven

Made 2026-09-28 in `docs/superpowers/plans/2026-09-28-z0-step5-snapshot-backed-rebuilds.md`, each forced by a measurement:

1. **Build inputs, not only `wiki/`.** §6.1's `capture(required={"wiki"})` would drop `docs/` (86 search / 90 graph files) and the root Markdown files — about 1% of chunks, a ranking change. The snapshot captures `wiki/`, `docs/` and `README/AGENTS/CLAUDE/CHANGELOG.md`. `built_from_revision` describes the wiki (the ledger's scope); the rest is git-tracked and outside the ledger — a stated bound.
2. **Coherence proven on the live side.** The manifest carries `st_ino`; a clone has new inodes, so a snapshot's manifest never equals the live one. The proof is §5.1's double manifest: live before == live after == the ledger digest at R, revision unchanged, no mutation active.
3. **Snapshots are released after the build.** Retention and leases are step 6; `built_from_snapshot` names a proof, not a replayable directory.
4. **No ledger baseline builds from live, unstamped** (health INVALID) rather than refusing — a fresh clone has no ledger.

Capture also WAITS for live operations (default 300 s) separately from retrying a race: without it, a rebuild during any ingest failed in 0.64 s blaming the store for changing (measured).

---

## 7. Amendment log

| # | Date | Amends | Summary |
|---|---|---|---|
| 1 | 2026-09-18 | §26, §11.2 | Z1V phase inserted for the measured 83% vector scan |
| 2 | 2026-09-18 | §26 Z0 | Z0 widened to the derived-store lifecycle; snapshot-backed rebuilds; authoritative/measurement capture split (rev 2) |
| 3 | 2026-09-18 | §5, §8.4 | `data/generations/` replaced by a portable snapshot abstraction |
| 4 | 2026-09-18 | §8.2 | Claim-ledger component declared unsupported, with a fail-closed guard |
| 5 | 2026-09-19 | Z0 step 1 `mutations` | `project` split into `store_project` / `execution_project` / `selected_project` / `caller_project` (§8) |
| 6 | 2026-09-19 | Z0 step 1 boundaries | MCP tools made top-level operation boundaries; live half-operation defect (§8.4) |
| 7 | 2026-09-28 | Z0 §3.2, amendment 6 | Authoritative writes outside an operation refused; implicit mode only by reasoned migration opt-in; `--project` and project-slug fixes (§9) |
| 8 | 2026-09-28 | Z0 §6.1 | Rebuild snapshots capture all build inputs; coherence by live double manifest; released after build; no-baseline builds unstamped (§10) |

No amendment to date changes §3 (non-negotiable invariants), §9 (evaluation
policy), §30 (success definition) or §34 (acceptance gate).
