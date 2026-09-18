# Zurvan 2.0 — Canonical Upgrade Specification

**Date:** 2026-09-18  
**Status:** Final canonical architecture specification after two gauntlet passes; implementation requires phased plans  
**Scope:** Core Zurvan knowledge engine, retrieval, compilation, evaluation, memory, graph, local runtime, and MCP surface  
**Security boundary:** Boundary v1 is frozen as an operationally complete prerequisite. Boundary V5/adversarial-evasion work is explicitly out of scope for this upgrade spec.  
**Design lineage:** Karpathy-style persistent LLM wiki + Zurvan's existing local-first Markdown/SQLite architecture + current 2026 retrieval, graph, MCP, evaluation, and agent-memory patterns.

---

## 0. Executive decision

Zurvan 2.0 adopts a **dual-profile architecture**.

- **FAST** is the default profile and must remain practical on the current Apple Silicon M2 / 8 GB machine.
- **LAB** enables heavier local retrieval and research techniques on stronger hardware.
- Both profiles use the **same durable knowledge semantics, authored/compiled Markdown contracts, provenance rules, claim schema, graph semantics, CLI/MCP contracts, and evaluation policy**.
- Derived retrieval index formats and model backends may differ by profile. Knowledge semantics may not.

The system is not being re-platformed around a fashionable framework. The existing spine remains:

> **immutable evidence + persistent Markdown + embedded SQLite + Git-friendly code + local-first operation**

The upgrade turns that spine into a **knowledge compiler, typed retrieval planner, evidence graph, and measurable memory system**.

---

# 1. Starting point: what already exists and must be preserved

Zurvan is already beyond a basic RAG prototype. The current system includes:

- immutable/untrusted raw source handling;
- Markdown ingestion and an Obsidian-compatible private wiki;
- SQLite FTS5 lexical search;
- semantic embeddings and hybrid retrieval;
- graph-neighbour expansion;
- claims, concepts, entities, decisions, sessions, contradictions, and syntheses;
- local MCP agent memory;
- cross-project federation;
- replayable retrieval/audit traces;
- evidence-pack and report workflows;
- explicit agent preflight/write-back workflows;
- retrieval evaluation with a protected gold instrument;
- strict repo/publication guards;
- Boundary v1 as an egress control;
- atomic search-index rebuilds and exact-provider invariants.

At the most recent healthy production state recorded before this spec:

- search index: **184,389 chunks**;
- embeddings: **184,389**;
- FTS rows: **184,389**;
- sources: **4,446**;
- embedding space: **sentence_transformers / all-MiniLM-L6-v2 / 384**;
- no mock/fallback vectors in the production index;
- Boundary operational FP gate: **0/200**;
- Boundary recall gate: **10/10**;
- Boundary threshold: **T=64 unchanged**;
- production/evaluation Boundary semantics are aligned;
- commit-gate batching is materially faster at larger staged-file counts.

These are **baselines**, not architectural requirements. Future changes must beat or preserve them under frozen evaluation rather than overwriting them first.

---

# 2. Product goal

Zurvan 2.0 should answer five classes of question reliably:

1. **Locate**
   - “Where is the exact file, symbol, hash, decision, error, or quote?”

2. **Explain**
   - “Why did we make this decision?”
   - “What evidence supports it?”
   - “Which approaches failed?”

3. **Reconstruct**
   - “What did we believe on a particular date?”
   - “What changed between two project states?”

4. **Synthesize**
   - “What themes recur across this whole project?”
   - “What are the major unresolved contradictions?”

5. **Investigate**
   - “Follow the evidence across projects, claims, decisions, sources, and time until the answer is supported.”

The user-visible experience should feel like a persistent research partner, not a search box over chunks.

---

# 3. Non-negotiable invariants

## 3.1 Local-first

Core functionality must operate locally using:

- Markdown;
- SQLite;
- local indexes;
- local model weights where model inference is required.

Cloud LLMs may be used as optional compiler/reasoning workers. They must never become the canonical storage layer.

Network use is **opt-in**, never an implicit fallback. A local operation must not silently send private source material to a cloud provider because a local model is unavailable.

## 3.2 Evidence immutability

Raw evidence remains immutable and content-addressable.

- Existing `raw/` immutability rules remain.
- Source content is untrusted and is never executed merely because it was ingested.
- No compiler, compactor, graph job, or retrieval job may rewrite raw evidence.
- Every durable machine claim must be traceable to source evidence or be explicitly marked as an unevidenced user/agent assertion.

## 3.3 Three persistent truth layers

Zurvan does **not** reduce Markdown to a disposable cache.

The durable system has three complementary layers:

1. **Evidence truth**
   - immutable source bytes and evidence spans;

2. **Structured knowledge**
   - claims, entities, events, decisions, relations, temporal state, contradictions;

3. **Markdown knowledge**
   - durable, human-readable, Git-friendly or private-vault knowledge views.

All three are first-class persistent artifacts.

### Markdown has two modes

**Authored Markdown**
- decisions;
- notes;
- open questions;
- session records;
- manually maintained project pages.

These may be written by humans or agents and parsed into structured knowledge where appropriate.

**Compiled Markdown**
- entity views;
- concept pages;
- community summaries;
- contradiction reports;
- generated overviews.

Compiled pages carry explicit metadata and are reproducibly generated from versioned structured state and compiler configuration. Deterministic non-model projections must be byte-stable. Model-generated projections are cached by input/model/compiler identity and, when explicitly regenerated, must preserve grounded evidence/claim semantics even if bytes differ.

The system must never silently overwrite authored Markdown as if it were generated output.

## 3.4 Single writable authority per knowledge artifact

“First-class” does **not** mean two independently writable copies of the same fact.

Every durable artifact declares one authoring authority:

- `evidence` — immutable source/evidence is authoritative; structured/Markdown views are derived;
- `markdown` — authored Markdown is authoritative; structured rows are parsed/indexed views;
- `structured` — a CLI/MCP/API assertion in the claim ledger is authoritative; Markdown is a compiled human view.

Examples:

- a manually authored decision page: `authority=markdown`;
- a claim asserted through `zurvan claim add`: `authority=structured`;
- a source-extracted claim: `authority=evidence`.

The compiler may project between representations, but it must never create an uncontrolled bidirectional sync loop.

Every derived artifact must carry enough metadata to detect authority drift, for example:

```yaml
artifact_id: ...
authority: structured
schema_version: 1
compiler_version: ...
generated_from_revision: ...
generated_from_hash: ...
generated_content_hash: ...
```

If both a derived representation and its authority are edited independently, compilation must stop with a conflict rather than guessing which side wins.

## 3.5 Cyclic knowledge graph, acyclic compilation graph

These are distinct.

### Knowledge graph

May contain cycles.

Allowed relations include:

- `SUPPORTS`
- `CONTRADICTS`
- `QUALIFIES`
- `DUPLICATES`
- `RELATED_TO`
- `SUPERSEDES`
- entity relationships
- project relationships

### Compilation dependency graph

Must be acyclic.

It answers:

> If artifact X changes, what derived artifacts must be recomputed?

The knowledge graph and compilation DAG must not share semantics merely because both are “graphs”.

## 3.6 Frozen measurement inputs

A measurement must never depend on the live shared store remaining stationary.

A retrieval/evaluation run against snapshot **A** must keep measuring **A** while the live knowledge store moves to B, C, or D.

A snapshot is coherent only when every required component belongs to one declared **knowledge revision**. Immutable components from different revisions may not be mixed merely because each is internally valid.

## 3.7 No production mutation from tests

Production knowledge state is never a test fixture.

Every destructive or mutating test uses an isolated root/database/index.

## 3.8 No vibe-based retrieval promotion

A new model, chunker, query planner, fusion policy, graph strategy, or reranker may not replace the production path merely because examples “look better”.

Promotion requires frozen evaluation.

## 3.9 Model interfaces are stable; model names are replaceable

Specific model names in this spec are **benchmark candidates**, not permanent architectural dependencies.

## 3.10 Authorisation is enforced before retrieval

Project/vault/privacy scope is part of the retrieval request, not a post-processing hint.

Every retrieval lane receives the same authorised scope before querying. The planner may narrow scope but may not broaden beyond caller authority. ContextCompiler performs a second defensive scope check, but must never be the first point at which disallowed evidence is removed.

## 3.11 Boundary v1 remains frozen

This spec may consume Boundary APIs and invariants.

It must not silently alter:

- Boundary threshold;
- provenance semantics;
- sink-baseline semantics;
- frozen FP/recall corpora;
- commit-gate policy.

Boundary V5 is a separate design phase.

---

# 4. Compute profiles

## 4.1 Profile semantics

### FAST

Target:
- current M2 / 8 GB machine;
- interactive daily use;
- low startup friction;
- low memory;
- deterministic local behaviour.

### LAB

Target:
- M4 Pro / workstation / server-class local hardware;
- research experiments;
- heavier local models;
- multi-hop/global retrieval;
- multi-vector and multimodal experiments.

### AUTO

Optional convenience profile for interactive use only.

`AUTO` may choose FAST or LAB based on local capability, but:

- **AUTO is forbidden in benchmarks, CI, frozen evaluations, and published measurements.**
- Evaluations must name the exact explicit profile and backend versions.

## 4.2 Initial profile matrix

| Capability | FAST | LAB |
|---|---|---|
| Dense embedding | current MiniLM baseline initially | shadow benchmark winner |
| Dense vector storage | current exact-cosine derived index initially | candidate backend/index |
| Lexical | FTS5 word + trigram | FTS5 + optional sparse neural |
| Reranker | none or small benchmark winner | quantised local reranker |
| Query expansion | rules + lightweight expansion | richer expansion |
| HyDE | disabled by default | experimental only |
| Graph | direct/local expansion | communities + global + DRIFT-style |
| Multi-vector | disabled | experimental |
| Multimodal retrieval | disabled | experimental |
| Runtime | CLI / stdio / lightweight local daemon | warm local service / worker pool |

## 4.3 Resource budgets

FAST has a **target** steady-state RSS budget of approximately **1.2 GB** on the current machine.

This is a target, not a fabricated acceptance claim.

Before it becomes a hard gate:

1. measure current production RSS;
2. record cold and warm states;
3. establish repeatable p50/p95 memory and latency baselines.

A new implementation may not claim compliance until measured.

LAB has no single fixed memory target because hardware differs. Every LAB experiment must record:

- RSS;
- model weight footprint;
- index footprint;
- p50/p95 latency;
- warmup time.

---

# 5. Storage architecture

```text
Zurvan/
├── raw/                         # immutable source material
├── wiki/                        # private durable Markdown knowledge
│   ├── claims/
│   ├── concepts/
│   ├── decisions/
│   ├── entities/
│   ├── communities/
│   ├── failures/
│   ├── sessions/
│   └── ...
├── data/
│   ├── registry.sqlite          # ingestion/source registry + knowledge revision metadata
│   ├── claims.sqlite            # structured claims + temporal state
│   ├── graph.sqlite             # active graph pointer/state
│   ├── search.sqlite            # active search pointer/compat path
│   ├── generations/
│   │   ├── search/              # immutable validated search generations
│   │   └── graph/               # immutable validated graph generations
│   └── traces/                  # private local audit/retrieval traces
├── indexes/
│   ├── active/                  # promoted derived backend artifacts
│   └── candidates/              # shadow experimental artifacts
└── eval/
    ├── manifests/
    ├── dev/                     # visible development queries
    ├── holdout/                 # sealed local-only instrument; never indexed/published
    ├── reserve/                 # sealed replacement pool for retired holdout items
    └── metadata/                # counts/hashes, never a confirmation oracle
```

The repository may continue to keep sensitive wiki/data/evaluation content untracked/private.

The codebase remains Git-friendly without forcing the private knowledge base or protected evaluation instrument into the public repository.

Canonical knowledge schemas are profile-independent. Derived retrieval indexes may use different physical formats behind versioned backend interfaces.

# 6. Claim ledger

## 6.1 Canonical machine-readable claim record

Illustrative schema:

```yaml
schema_version: 1
claim_id: claim_c7f8a9
revision: 3

subject:
  type: concept
  id: concept_boundary_public_predicate

predicate:
  id: implementation_status
  vocabulary_version: 1

object:
  type: literal
  datatype: string
  language: en
  value: rejected

scope:
  project: zurvan
  domain: security

status: superseded

confidence:
  level: high
  basis:
    - empirical_test
    - primary_artifact

valid_time:
  from: 2026-09-17T00:00:00+10:00
  until: null

record_time:
  from: 2026-09-17T15:30:00+10:00
  until: null   # derived from the next revision when one exists

observed_at: 2026-09-17T15:30:00+10:00

assertion:
  method: human_reviewed_agent
  actor_id: local:raouf

provenance:
  - source_id: src_sha256_e3b0c44298fc
    evidence_hash: sha256:...
    locator:
      kind: byte_span
      start: 1420
      end: 1680
  - source_id: src_pdf_sha256_...
    evidence_hash: sha256:...
    locator:
      kind: pdf_region
      page: 7
      bbox: [0.12, 0.18, 0.88, 0.34]

relations:
  superseded_by:
    - claim_d4e1f2
  contradicts: []
  supports:
    - decision_d09
```

Evidence locators are typed. Supported locator families include, as applicable:

- byte span;
- line range;
- Markdown section/heading;
- PDF page/region;
- image region/bounding box;
- media timestamp range;
- DOM selector/URL fragment.

A locator type must be able to re-resolve the evidence against the immutable source identified by `source_id`.

## 6.2 Identity and vocabulary

Stable entity/concept IDs are separate from display names.

Predicates come from a versioned vocabulary. Synonymous natural-language phrases are normalised to a predicate ID rather than creating accidental duplicates such as `removed`, `deprecated`, and `no_longer_supported` for the same semantic relation.

Literal objects retain datatype and, where relevant, language/unit metadata.

## 6.3 No fake probabilities

Zurvan must not generate scientific-looking confidence decimals without calibration.

Default confidence is categorical:

- `low`
- `medium`
- `high`

and must include a basis such as:

- primary source;
- independent confirmation;
- direct measurement;
- agent inference;
- user assertion.

Probabilistic confidence may be added later only with a defined calibration method and validation set.

## 6.4 Bitemporal semantics

Zurvan distinguishes:

- **valid time** — when the claim is/was true in the world;
- **record time** — when Zurvan stored/believed that claim;
- **observed_at** — when the supporting observation was made.

This enables:

- “What was true on 1 September?”
- “What did Zurvan believe on 1 September?”
- “When did Zurvan discover the earlier belief was wrong?”

## 6.5 Append-oriented claim history

Semantic fields of an existing historical revision are not overwritten in place.

A correction or state transition creates a new `claim_id + revision` event. The previous revision remains queryable. `record_time.until` may be derived from the next revision rather than destructively rewriting history.

A materialised `claims_current` view may expose the latest state for fast queries, but it is derived from the revision/event history.

## 6.6 Claim lifecycle

Allowed statuses include:

- `asserted`
- `supported`
- `contested`
- `superseded`
- `retracted`
- `unknown`
- `orphaned`

Claims are not silently overwritten or deleted from history.

Corrections create new state and relation history. Source deletion/tombstoning may make a claim inactive/orphaned in the current view without erasing the historical evidence record.

# 7. Knowledge compiler

Zurvan 2.0 introduces an explicit **KnowledgeCompiler**.

```text
source / authored-Markdown / structured-assertion change
    ↓
authority + revision check
    ↓
structure extraction / normalisation
    ↓
claims / entities / events / decisions
    ↓
relation reconciliation
    ↓
compilation dependency DAG
    ↓
affected compiled Markdown / graph / search artifacts
```

Contradiction analysis may participate once the contradiction subsystem exists, but compilation does not depend on a future phase being present.

## 7.1 Incremental compilation

A changed source must not force a full-system recomputation.

The compiler records dependencies from:

- source → claims;
- authored Markdown → parsed structured views;
- structured assertion → compiled Markdown views;
- claim → entity/concept;
- claim → decision;
- knowledge node → Markdown projection;
- projection → retrieval chunks;
- graph nodes → community summaries.

Only affected descendants are invalidated.

## 7.2 Deletion, rename, and tombstones

The compiler explicitly handles:

- deleted/tombstoned source;
- renamed/moved source;
- source replaced by different bytes at the same path;
- evidence locator no longer resolving;
- claim no longer derivable from active inputs.

Raw evidence remains immutable even when a source is removed from the active corpus.

A same-content rename preserves source identity while updating path aliases. A deleted source is tombstoned in the registry; dependent current views are invalidated/recompiled, while historical provenance remains queryable. Claims that lose all active support are marked according to policy (`orphaned`, `contested`, or `retracted`) rather than silently surviving as current truth.

## 7.3 Reproducibility and idempotence

For deterministic non-model projections, identical versioned inputs must produce byte-identical output.

For model-generated projections, the normal path reuses the cached artifact keyed by:

- input hashes;
- prompt/compiler version;
- model source/revision/hash;
- runtime profile.

A forced regeneration may differ byte-for-byte. It must preserve required evidence/claim coverage and pass the projection's semantic/evidence oracle before replacing the prior artifact.

## 7.4 Failure preservation

Rejected approaches and negative results are first-class memory.

A durable experiment/failure record contains:

```yaml
hypothesis:
method:
result:
verdict:
evidence:
why_failed:
preconditions_to_retry:
```

This prevents future agents from rediscovering the same dead ends.

# 8. Immutable measurement snapshots

This is the first implementation phase because every later retrieval experiment depends on trustworthy measurement.

## 8.1 Knowledge revision

Zurvan introduces a monotonically increasing **knowledge revision** representing a coherent logical state of writable knowledge.

Zurvan-managed writes participate in a short capture barrier/sequence protocol:

1. writer announces mutation intent under the local revision lock;
2. writer commits its authoritative mutation(s);
3. writer increments the stable knowledge revision and releases the lock.

The lock is held only for the mutation/capture boundary, not for the duration of an evaluation.

Manual/unmanaged file edits cannot be assumed to honour the writer lock. Snapshot capture therefore double-checks the source/wiki manifest (metadata + content hash) before accepting the snapshot. If the manifest changes during capture, snapshot creation retries.

## 8.2 Snapshot objective

A measurement snapshot captures one coherent state without globally freezing writers for the measurement duration.

It includes:

- `knowledge_revision`;
- immutable content references for every source/wiki file the measurement may read;
- manifest/content hashes;
- search generation ID and `built_from_revision`;
- graph generation ID and `built_from_revision`;
- a consistent claim-ledger snapshot and revision;
- evaluation instrument version/hash;
- explicit profile/backend manifest.

**Immutable does not imply mutually consistent.** A snapshot is valid only when all required components declare the same coherent knowledge revision (or an explicitly documented compatibility relation). A search index from revision A may not be mixed with a graph from revision B merely because both pass their own integrity checks.

If a required derived generation is stale relative to the requested revision, snapshot creation marks that capability unavailable or fails. It does not silently construct a mixed state.

## 8.3 Replayable file content

A hash manifest alone is not a replayable snapshot. Zurvan's private wiki is untracked and may change after the snapshot begins. Every manifest entry therefore resolves to immutable bytes.

Preferred mechanisms, in order:

1. existing immutable content-addressed blob;
2. filesystem reflink/copy-on-write clone when available;
3. deduplicated content-addressed copy.

A hardlink to a mutable live wiki file is **not** a snapshot: both names reference the same inode and an in-place edit can mutate the supposed snapshot. Hardlinks are allowed only to objects already guaranteed immutable.

The snapshot layer never assumes Git can reconstruct private wiki content.

## 8.4 Immutable generations and mutable ledgers

Purely derived stores use immutable validated generations.

Conceptually:

```text
data/generations/search/<generation-id>.sqlite
data/generations/graph/<generation-id>.sqlite
```

Each generation records at minimum:

- generation ID;
- schema version;
- `built_from_revision`;
- source manifest digest;
- backend/profile manifest digest;
- integrity verdict.

`active` points to the currently promoted generation.

A rebuild:

1. creates a new generation;
2. validates it;
3. atomically promotes the active pointer;
4. retains the previous generation while snapshots still reference it.

The **claim ledger is different**: it contains durable authored/observed state, not merely a rebuildable index. The active ledger evolves transactionally. Snapshot creation captures a consistent read-only ledger image using SQLite backup/snapshot semantics or an equivalent immutable snapshot artifact and records the knowledge revision it represents.

## 8.5 Snapshot leases and garbage collection

Snapshots hold leases on immutable generations/content.

A lease records:

- snapshot ID;
- owner token/process identity where available;
- creation time;
- heartbeat/expiry;
- referenced artifacts.

Crash cleanup must not delete a generation merely because a process disappeared moments ago. Garbage collection requires an expired lease and no active reference. Snapshot metadata/result manifests may outlive heavy copied artifacts according to retention policy.

## 8.6 Snapshot capture protocol

A valid capture follows this pattern:

```text
acquire short capture barrier
read stable revision E
capture/pin authoritative references and claim snapshot
read/verify file manifest
verify required search/graph generations declare E
re-read revision and manifest
if anything changed: discard capture and retry
publish snapshot S(E)
release barrier
```

Writers resume immediately after the capture boundary. The evaluation itself reads only immutable references from S(E).

## 8.7 Snapshot correctness regression

Required experiment:

1. create live state A at revision E;
2. start snapshot S(E);
3. mutate the live store to B/E+1 while evaluation is running;
4. continue evaluation;
5. prove every measurement input still resolves to E;
6. prove B remains live;
7. prove no A/B hybrid is visible;
8. start a second snapshot;
9. prove it sees the new revision;
10. separately force a write *during snapshot capture* and prove capture retries rather than emitting a Franken-state.

# 9. Evaluation system

## 9.1 Preserve the existing 50-query instrument

The current frozen retrieval instrument remains a historical regression suite.

It must not be rewritten into the new dataset.

Existing anti-leak constraints remain:

- instrument text is not indexed;
- reports about the instrument are not indexed;
- references use item IDs/indexes rather than verbatim query text.

## 9.2 Expanded evaluation programme

The new programme begins with **at least 350 queries** and grows when stratum coverage requires it:

- approximately 200 visible development queries;
- at least 150 sealed holdout queries;
- an additional sealed reserve pool for replacing retired/contaminated holdout items.

The total is driven by minimum per-class coverage, not by a cosmetically round number.

### Development set

Visible to developers and agents.

Used for:
- iteration;
- failure analysis;
- tuning;
- planner/reranker development.

### Sealed holdout

Used only for declared promotion candidates.

Holdout protections:

- raw query text is not indexed;
- development agents do not receive raw holdout queries;
- repo-visible manifests contain counts/version hashes, not a confirmation oracle;
- routine reports expose aggregate and predeclared class metrics, not raw query text;
- MCP/trace output follows the same protection;
- every holdout invocation is logged.

A normal candidate receives **development results only**.

A candidate is explicitly frozen and declared a **promotion candidate** before one sealed-holdout run. The candidate may not be tuned from the resulting holdout failures.

If item-level diagnosis is required, the revealed items are retired into a diagnostic set and replaced from the sealed reserve before a later promotion attempt. Repeated operational familiarity is treated as contamination, not ignored.

## 9.3 Stratification and power

Each query has one primary stratum and may have secondary labels.

Primary strata include:

- exact identifiers;
- filenames/paths;
- hashes;
- error strings;
- semantic lookup;
- aliases;
- decision rationale;
- temporal reconstruction;
- contradiction queries;
- provenance/source verification;
- code queries;
- multi-hop questions;
- corpus-wide synthesis;
- cross-project retrieval;
- hard negatives;
- “not found / insufficient evidence” cases.

Critical primary strata should normally have **at least 10 holdout examples** before class-level non-regression is treated as meaningful. If the taxonomy grows, the holdout grows with it.

## 9.4 Metrics

Record at minimum:

- Recall@1;
- Recall@3;
- Recall@10;
- MRR;
- nDCG where graded relevance exists;
- per-query rank changes on development/retired diagnostics;
- class-level metrics;
- p50/p95 latency;
- peak RSS;
- index size;
- model warmup time.

Protected holdout reporting may omit item-level rank details unless items are deliberately retired.

## 9.5 Promotion rule

A frozen promotion candidate may become active only when:

1. it does not regress predeclared safety-/correctness-critical query classes beyond their allowed non-inferiority margin;
2. overall holdout quality is not lower beyond the predeclared margin;
3. at least one predeclared target metric improves beyond measured noise, **or** quality is equivalent and the candidate materially improves a predeclared resource metric;
4. latency/memory remain within the target profile budget.

Before the holdout run, record:

- primary metric;
- critical strata;
- non-inferiority margins;
- improvement target;
- resource budgets.

Measurement uncertainty is estimated with paired bootstrap resampling over holdout queries where appropriate. With small strata, report uncertainty honestly rather than laundering it into a binary win.

If quality is equivalent, the simpler/faster/lower-memory implementation wins.

## 9.6 Shadow evaluation

Every experimental subsystem supports:

```text
ACTIVE
CANDIDATE
```

Examples:

```bash
zurvan eval compare --baseline active --candidate qwen3-shadow --dataset dev
zurvan eval compare --planner rules-v1 --candidate planner-v2 --dataset dev
```

Candidates never overwrite active production artifacts before promotion.

# 10. Query planner

Zurvan 2.0 stops treating all queries as generic hybrid-search requests.

## 10.1 Retrieval request context

Every query carries an immutable request context before planning:

```yaml
request_context:
  caller_id: ...
  capabilities: [READ]
  allowed_projects: [zurvan]
  allowed_vaults: [default]
  allowed_privacy_classes: [private]
  temporal_scope: null
  provenance_scope: null
```

Every lane receives this authorised scope. The planner may narrow it, never broaden it.

## 10.2 Direct resolver before search

Exact structured identifiers bypass retrieval ranking where possible:

- claim ID → claim primary-key lookup;
- decision ID → registry/decision lookup;
- trace ID → trace store;
- exact source hash → registry hash index;
- exact known path → path registry.

FTS/trigram remains useful for partial/unknown identifiers, not as the primary mechanism for an exact key already represented structurally.

## 10.3 Rules-first routing

Deterministic rules handle:

- quoted error strings;
- CLI/code symbols;
- partial paths/filenames;
- partial hashes;
- explicit query operators.

Only unresolved queries reach the learned/lightweight intent layer.

## 10.4 Learned intent routing

A lightweight local classifier may classify unresolved queries into one or more retrieval intents.

It returns:

- intent(s);
- confidence category;
- routes chosen;
- reason/features where available.

If uncertain, it uses multi-route retrieval rather than forcing one intent.

## 10.5 Core intents and capability gates

Core intents:

- `exact_lookup`
- `semantic_lookup`
- `decision_rationale`
- `temporal_state`
- `contradiction`
- `provenance`
- `code_lookup`
- `cross_project`
- `global_synthesis`
- `multi_hop_investigation`

Every intent declares required subsystem capabilities. Until a capability exists, the planner returns an explicit unsupported/degraded route rather than pretending the future subsystem is already available.

Examples:

- `temporal_state` requires claim-ledger v2;
- `contradiction` requires structured claim matching and later contradiction-engine support;
- `global_synthesis` requires community-generation support;
- `multi_hop_investigation` requires the LAB graph/DRIFT capability.

## 10.6 HyDE policy

HyDE is **not** part of the default FAST path.

It is LAB/experimental until it proves value through the promotion workflow.

# 11. Retrieval engines

## 11.1 Lexical lane

Maintain two FTS5 modes:

### Word index
For:
- prose;
- ordinary natural-language queries;
- BM25 ranking;
- stemming where appropriate.

### Trigram index
For:
- identifiers;
- paths;
- partial symbols;
- error fragments;
- domain fragments;
- hashes;
- substring lookup.

SQLite trigram matching does not provide general full-text substring matches shorter than three Unicode characters. Therefore:

- exact structured keys use the direct resolver;
- trigram handles substrings of length ≥3;
- explicit literal/metadata lookup handles legitimate shorter symbols where required.

The planner chooses which lane(s) to invoke.

## 11.2 Dense lane

FAST initially preserves the known-good production embedding model and storage semantics.

Current baseline:

```text
sentence_transformers
all-MiniLM-L6-v2
384 dimensions
```

Do **not** silently change to int8 storage merely because the new architecture supports profiles.

Quantisation is a separate experiment requiring ranking parity and corruption checks.

## 11.3 Reranking

Cross-encoder/local reranking is introduced behind a backend interface.

Candidate policy:

```text
retrieve 40–60
    ↓
fusion
    ↓
rerank top ~20–30
    ↓
ContextCompiler
```

Exact values are benchmark parameters, not architectural constants.

FAST may use:
- no reranker;
- a small measured winner.

LAB may use:
- a larger quantised local reranker.

## 11.4 Sparse neural lane

Sparse encoders are LAB experiments only until a frozen promotion candidate satisfies the §9 quality/resource criteria against the lexical+dense baseline.

## 11.5 Multi-vector lane

ColBERT-style late interaction is LAB-only.

It may be promoted only through the §9 sealed promotion workflow, including predeclared quality/non-inferiority criteria, acceptable index growth, and no dependency or regression imposed on FAST.

## 11.6 Multimodal lane

Visual retrieval is optional LAB functionality.

A PDF/page may have:

- text chunks;
- page/image representation;
- visual embedding;
- figure metadata.

Multimodal indexing must remain derived/rebuildable.

---

# 12. Fusion

Zurvan uses explicit fusion rather than opaque score mixing.

## 12.1 Canonical evidence candidate

All retrieval lanes normalise to one fusion identity before RRF.

Illustrative contract:

```yaml
candidate_id: evc_sha256_...
source_id: src_sha256_...
locator:
  kind: byte_span
  start: 100
  end: 240
project_id: zurvan
privacy_class: private
claim_ids: [claim_17]
page_id: wiki/decisions/example.md

lane_ranks:
  lexical: 2
  dense: 5
  graph: null

lane_scores:
  lexical: -1.24
  dense: 0.842
```

`candidate_id` is derived from a canonical source/evidence identity and normalisation version, not from a lane-specific row ID.

Graph nodes, entities, communities, and temporal records may seed retrieval, but before fusion they must resolve to underlying authorised evidence candidates. A synthetic/community object without underlying evidence may act as a prior, never as final fused evidence.

Overlapping/coextensive candidates are deduplicated or grouped deterministically before context assembly.

## 12.2 Fusion flow

Initial design:

1. lexical candidate list;
2. dense candidate list;
3. graph/temporal candidate list when applicable;
4. normalise all lanes to `EvidenceCandidate`;
5. Reciprocal Rank Fusion (RRF);
6. optional reranker;
7. ContextCompiler.

Fusion configuration and candidate-normalisation version are versioned and traceable.

No fusion-weight or identity-normalisation change may reach production without evaluation.

# 13. ContextCompiler

The ContextCompiler is a first-class subsystem separate from retrieval.

Retriever output is **authorised evidence candidates**.

ContextCompiler output is an **evidence pack for reasoning**.

It is a second defensive scope boundary, not the first. Disallowed projects/vaults/privacy classes must already have been excluded by retrieval lanes.

## 13.1 Responsibilities

Optimise for:

- relevance;
- source diversity;
- claim coverage;
- contradiction coverage when the capability exists;
- freshness;
- temporal appropriateness;
- provenance strength;
- duplicate removal;
- caller/project/privacy scope.

## 13.2 Token budget contract

The ContextCompiler receives the target reasoner contract rather than assuming a universal token count:

```yaml
target_reasoner:
  provider: local|openai|anthropic|...
  model: ...
  tokenizer_revision: ...
  context_limit: ...
  reserved_system_tokens: ...
  reserved_output_tokens: ...
  requested_evidence_budget: ...
```

The compiler calculates the usable evidence budget with the target tokenizer. A literal `6000` token budget is not portable across reasoners.

## 13.3 Output contract

Illustrative:

```yaml
context_pack:
  query_id: ...
  request_context_hash: ...
  tokenizer_revision: ...
  evidence_budget_tokens: ...

  primary_evidence:
    - ...

  supporting_evidence:
    - ...

  contradictions:
    - ...

  background:
    - ...

  unresolved_questions:
    - ...

  excluded:
    - reason: duplicate
    - reason: stale
    - reason: outside_scope
```

The pack is traceable back to the authorised retrieval candidates.

# 14. Retrieval observability

Every retrieval may be run with explainability:

```bash
zurvan search "Why did Boundary reject cat-file -e?" --explain --profile fast
```

A trace may record:

- query hash and, when policy permits, raw query text;
- resolved intent;
- rules fired;
- request-scope hash;
- profile/backend manifest;
- lexical/dense/graph hits and ranks;
- fusion ranks;
- reranker scores;
- ContextCompiler decisions;
- elapsed time by stage;
- authorised source/claim/evidence IDs;
- snapshot/generation IDs.

## 14.1 Trace privacy and retention

Traces are private local artifacts by default and are not indexed into the knowledge corpus unless explicitly transformed into an approved durable record.

Trace modes:

- `summary` — default; minimal sensitive payload;
- `full` — explicit local debugging with raw query/evidence details;
- `protected_eval` — forced for holdout runs; raw holdout text/expected answers are never written to ordinary traces.

The trace store defines:

- restrictive local file permissions;
- retention/rotation policy;
- raw-query/snippet policy;
- redaction rules;
- export policy.

Any trace export to a public/external sink is subject to Boundary and the caller's authorisation. Holdout protection overrides normal explain/debug settings.

# 15. Knowledge graph evolution

Zurvan keeps its own graph architecture.

Microsoft GraphRAG is research inspiration, not a required runtime dependency.

## 15.1 Local retrieval

Entity/claim query:

```text
query
  ↓
authorised entity/claim seeds
  ↓
neighbour expansion
  ↓
underlying evidence candidates
```

## 15.2 Community generations

Community structure is derived state, not a timeless identifier namespace.

Every community build records:

```yaml
community_generation: cg_17
built_from_graph_generation: g_91
algorithm: leiden
algorithm_version: ...
parameters_hash: ...
```

Community IDs are generation-scoped, for example `cg17:c42`.

A graph rebuild may split/merge communities. Old summaries become stale historical artifacts rather than silently changing meaning under the same ID.

## 15.3 Community detection

LAB or background batch process:

- Leiden-style community detection;
- hierarchical community membership;
- generation-scoped community IDs;
- summaries generated from grounded claims/evidence.

## 15.4 Global retrieval

Corpus-level query:

```text
query
  ↓
community summaries as priors
  ↓
relevant communities
  ↓
underlying authorised claims/evidence
  ↓
ContextCompiler
```

Community summaries are priors, not final evidence.

## 15.5 DRIFT-style retrieval

LAB mode may use:

```text
broad query
  ↓
community primer
  ↓
follow-up questions
  ↓
parallel local retrieval
  ↓
evidence reconciliation
```

Generated follow-up questions are retrieval aids, never claims.

# 16. Contradiction engine

Contradiction candidate generation is structured-first, semantic-second.

When a new durable claim appears:

1. generate deterministic candidates using:
   - same/linked subject entity;
   - same or logically related predicate;
   - overlapping scope;
   - overlapping valid time;
2. expand with semantic retrieval for paraphrases/related claims;
3. compare subject/predicate/object/scope/time;
4. classify relation candidates:
   - support;
   - contradiction;
   - qualification;
   - supersession;
   - duplicate;
   - unrelated;
5. attach evidence and confidence basis;
6. require explicit status when uncertain.

A semantic retrieval miss must not be the sole reason a structured contradiction is missed.

Command surface:

```bash
zurvan contradictions list
zurvan contradictions inspect <id>
zurvan contradictions resolve <id>
```

`resolve` appends a resolution event/decision. It never deletes the historical disagreement.

The engine must distinguish temporal supersession from true contradiction.

# 17. Memory consolidation

Zurvan memory is layered.

## Tier 0 — operational events

Examples:
- command/tool events;
- raw session activity;
- temporary execution state.

Tier 0 is **not equivalent to raw source evidence**.

Operational-event retention may be configurable to avoid infinite local growth.

## Tier 1 — episodic memory

- session summaries;
- work completed;
- failed attempts;
- benchmarks;
- pending tasks.

## Tier 2 — semantic memory

- supported claims;
- entities;
- decisions;
- experiments;
- contradictions;
- open questions.

## Tier 3 — canonical synthesis

- project overview;
- concept pages;
- entity pages;
- community pages;
- stable decision/failure histories.

## 17.1 Upward compaction

Compaction may propose:

```text
T0 → T1 → T2 → T3
```

but may not destroy raw evidence provenance.

Operational residue may be pruned under policy only after durable semantic state and required audit material exist.

## 17.2 Candidate write-back

Answers do not automatically mutate canonical knowledge.

A query may emit a **write-back candidate** when it detects durable novelty.

Candidate types:

- claim;
- decision;
- experiment result;
- open question;
- synthesis update.

The candidate must pass:
- novelty check;
- evidence check;
- project scope;
- existing write policy.

Write policy returns one of:

- `PROPOSE` — surface a candidate but make no durable mutation;
- `AUTO_COMMIT` — permitted only for explicitly low-risk artifact classes;
- `REQUIRE_APPROVAL` — human/authorised-agent approval required before mutation.

Examples:

- routine session delta summary may be eligible for `AUTO_COMMIT`;
- new factual claim normally requires evidence and may require approval by policy;
- project decision or destructive supersession defaults to `REQUIRE_APPROVAL`;
- unevidenced synthesis remains `PROPOSE`.

This avoids turning the wiki into conversational sludge while making write authority explicit.

---

# 18. Project state

Add a first-class project-state view.

Example:

```bash
zurvan project state
```

Output includes:

- `as_of` knowledge revision/time;
- active search/graph/claim generation IDs;
- freshness/staleness status per derived subsystem;
- active phase;
- recent decisions;
- unresolved questions;
- unresolved contradictions;
- known failed approaches;
- active experiments;
- search/index health;
- latest successful eval;
- current snapshot/generation IDs;
- stale derived artifacts.

This becomes the preferred agent cold-start interface.

---

# 19. Local daemon and runtime

Zurvan may add:

```bash
zurvan serve --profile fast
```

## 19.1 Transports and safe defaults

Prefer:

- stdio for compatible local agents;
- Unix-domain socket for shared local service;
- optional HTTP bound only to `127.0.0.1` by default.

Unix socket:

- parent directory mode `0700`;
- socket mode `0600` where supported.

Local HTTP:

- generated bearer/capability token stored mode `0600`;
- reject unexpected browser `Origin`/cross-origin requests;
- no unauthenticated mutating endpoints;
- no wildcard CORS;
- remote bind disabled unless explicitly configured.

A remote bind is a separate deployment mode and requires explicit authentication, transport protection appropriate to the environment, and an allowlisted interface. `127.0.0.1` alone is not treated as authorisation.

## 19.2 Capability classes

Daemon/MCP callers receive explicit capabilities such as:

- `READ`;
- `READ_PRIVATE`;
- `FEDERATE`;
- `WRITE_KNOWLEDGE`;
- `EVAL`;
- `REBUILD`;
- `ADMIN`.

A read-only client cannot obtain write/rebuild/eval powers merely because the tools exist.

## 19.3 Benefits

- warm model weights;
- persistent read-only DB handles;
- lower repeated startup latency;
- shared local access across authorised agents;
- cached query-planner/reranker state.

The daemon is **not** a web application.

No browser UI is required by this spec.

# 20. MCP 2026 surface

Target the 2026-07-28 stateless MCP core while preserving compatibility paths as needed.

Authentication/authorisation is enforced by the local runtime; protocol statelessness does not imply application statelessness or trust.

## 20.1 Resources

Illustrative:

```text
zurvan://wiki/{path}
zurvan://claims/{claim_id}
zurvan://entities/{entity_id}
zurvan://decisions/{decision_id}
zurvan://trace/{trace_id}
zurvan://state/{project}
zurvan://snapshot/{snapshot_id}
```

Every resource request is scoped by caller capabilities and allowed projects/vaults.

## 20.2 Tools and minimum capabilities

Illustrative:

```text
zurvan.search             READ
zurvan.context            READ
zurvan.compile            REBUILD or ADMIN
zurvan.claim.assert       WRITE_KNOWLEDGE
zurvan.decision.add       WRITE_KNOWLEDGE
zurvan.question.add       WRITE_KNOWLEDGE
zurvan.memory.compact     WRITE_KNOWLEDGE or ADMIN
zurvan.snapshot.create    EVAL
zurvan.eval.run           EVAL
```

Exact mappings are versioned policy, but permission checks are mandatory.

## 20.3 Explicit state handles

Transport/session state is not hidden in protocol sessions.

Longer application workflows may return explicit opaque handles that the agent passes back. Handles carry no authority beyond the caller's current capability context.

## 20.4 Tasks extension

Long-running work may use the MCP Tasks extension only when the client advertises support.

Protected evaluation instruments are never exposed through MCP as raw query text. If `zurvan.eval.run` targets a holdout dataset, MCP output returns authorised aggregate/class metrics and sealed run IDs only.

Examples:

- large reindex;
- community detection;
- model benchmark;
- bulk compilation.

Fallback behaviour exists for clients without Tasks support, for example a synchronous operation when bounded or an explicit local job handle managed by Zurvan.

# 21. Federation

Cross-project retrieval remains a first-class capability, but federation is deny-by-default.

Each vault/project registry entry records at least:

- stable vault/project ID;
- privacy class;
- owner/authority domain;
- federation policy;
- allowed caller/capability classes where configured.

Every federated request carries:

```yaml
allowed_projects: [...]
allowed_vaults: [...]
allowed_privacy_classes: [...]
capabilities: [READ, FEDERATE]
```

The planner may not autonomously broaden beyond this set.

Every retrieval lane filters scope **before querying**. ContextCompiler applies the same policy again as defence in depth.

Federated results retain:

- source project/vault ID;
- privacy class;
- provenance;
- temporal scope.

No cross-project merge may erase provenance or authorisation context.

Cross-project contradiction detection compares:

- subject;
- scope;
- project;
- valid/record time;
- evidence.

A caller who lacks authority for a vault must not learn that vault's matching result, score, snippet, entity name, or trace detail through federation side channels.

# 22. Profile/backend registry

Introduce a versioned backend registry.

Illustrative:

```yaml
profiles:
  fast:
    lexical: fts5
    trigram: fts5-trigram
    dense: minilm-production
    sparse: null
    reranker: null
    graph: local

  lab:
    lexical: fts5
    trigram: fts5-trigram
    dense: qwen3-shadow
    sparse: splade-shadow
    reranker: qwen3-reranker-shadow
    graph: community-drift
```

Every backend manifest records, where applicable:

```yaml
model:
  source: ...
  revision: ...
  sha256: ...
  tokenizer_revision: ...
  licence: ...

runtime:
  python: ...
  sentence_transformers: ...
  transformers: ...
  torch: ...
  execution_backend: cpu|mps|cuda

index:
  schema_version: ...
  dimensions: ...
  quantisation: ...
  normalisation: ...
```

A profile or model name alone is never enough for a reproducible measurement.

# 23. Model tournament

Do not replace MiniLM first.

Create shadow candidates.

Initial categories:

1. **dense**
   - current MiniLM baseline;
   - multilingual/local candidate(s);

2. **reranker**
   - small FAST candidate;
   - stronger LAB quantised candidate;

3. **sparse**
   - LAB research candidate;

4. **multi-vector**
   - LAB research candidate.

Every ordinary candidate gets:

- frozen snapshot;
- development results;
- latency;
- RSS;
- disk footprint;
- development rank movers;
- failure analysis.

Only a candidate frozen and declared for promotion receives the sealed holdout run described in §9. The tournament must not repeatedly query the holdout while tuning.

# 24. Error handling and fail-closed rules

## 24.1 Retrieval degradation

If any explicitly requested backend/profile component fails:

- do not silently substitute a different model and report the requested profile;
- either:
  - fail explicitly; or
  - degrade only when the caller explicitly allows fallback and the trace records it.

This preserves the exact-provider invariant learned from the production index incident. FAST, LAB, and direct backend requests follow the same rule.

## 24.2 Corrupt derived store

Derived indexes are rebuildable.

If validation fails:
- keep the last healthy generation active;
- quarantine the bad generation;
- never promote partial output.

## 24.3 Missing evidence

A generated claim without adequate evidence must be marked unsupported/unknown rather than fabricated into certainty.

---

# 25. CLI target surface

Illustrative additions:

```bash
# Profiles
zurvan profile list
zurvan profile show
zurvan profile use fast

# Snapshots/eval
zurvan snapshot create
zurvan snapshot inspect <id>
zurvan eval retrieval --profile fast --dataset dev
zurvan eval compare --baseline active --candidate <name>

# Retrieval
zurvan search <query> --profile fast --explain
zurvan context --topic <topic> --profile fast

# Compiler
zurvan compile --scope changed
zurvan compile --explain

# Knowledge state
zurvan claim inspect <id>
zurvan contradictions list
zurvan project state

# Experiments
zurvan experiment list
zurvan experiment inspect <id>

# Runtime
zurvan serve --profile fast
```

Existing commands remain compatible until explicit deprecation.

Breaking CLI/MCP/schema semantics require:

- a versioned contract/schema bump;
- an explicit deprecation warning and replacement path where compatibility is feasible;
- a migration note;
- no silent semantic repurposing of an existing command/tool name.

Deprecated compatibility shims may be removed only in a declared major-version or closure migration, not opportunistically inside an unrelated phase.

---

# 26. Phased implementation

This specification is an architecture programme. Each phase receives its own implementation plan, review, measurement, and commit series.

## Z0 — Immutable measurement snapshots

Build:
- knowledge revision/capture barrier;
- replayable snapshot manifests;
- immutable search/graph generation references;
- claim-ledger snapshot support;
- lease/GC lifecycle;
- concurrent-write and capture-race regressions;
- snapshot-aware evaluation.

Gate:
- evaluation remains deterministic while the live wiki changes;
- a write during capture causes retry, never a cross-store Franken-state.

No retrieval-ranking changes in this phase.

## Z1 — Retrieval observability + expanded evaluation programme

Build:
- trace privacy modes;
- stage timings;
- development/holdout/reserve framework;
- stratum coverage checks;
- promotion-candidate workflow;
- rank-mover reports for development/retired diagnostics.

Gate:
- historical 50-item results remain reproducible;
- instrument leakage guard remains clean;
- sealed holdout cannot leak through trace/MCP/report paths.

## Z2 — Claim ledger foundation + temporal model

Build:
- `claims.sqlite` v2;
- stable IDs/vocabulary;
- append-oriented revisions;
- typed evidence locators;
- bitemporal fields;
- categorical confidence basis;
- migration from existing claim pages/data.

Gate:
- existing authored Markdown remains intact;
- migrated structured claims retain provenance;
- historical/current views reconstruct correctly.

## Z3 — Typed query planner + direct resolver + dual lexical search

Build:
- retrieval request context/scope filter;
- exact direct resolver;
- rules-first router;
- capability-gated intent router;
- FTS5 trigram lane + short-token fallback;
- explicit multi-route retrieval.

Gate:
- exact/code/error development and promotion metrics do not regress;
- out-of-scope vault evidence never enters candidate pools/traces;
- latency recorded by query class.

## Z4 — EvidenceCandidate fusion + ContextCompiler

Build:
- canonical evidence identity;
- lane normalisation/deduplication;
- RRF over evidence candidates;
- candidate-to-context separation;
- tokenizer-aware budget;
- diversity/freshness/provenance selection;
- evidence-pack trace.

Gate:
- context/evidence coverage improves or remains non-inferior under predeclared criteria;
- no citation/evidence loss;
- project/privacy scope remains enforced before and after compilation.

## Z5 — Local reranking + shadow model tournament

Build:
- reranker backend;
- shadow dense indexes;
- backend supply-chain manifests;
- candidate registry;
- sealed promotion workflow.

Gate:
- no production replacement until §9 promotion rule passes.

## Z6 — KnowledgeCompiler + dependency DAG

Build:
- authority metadata/conflict detection;
- dependency tracking;
- deletion/rename/tombstone invalidation;
- compiled Markdown metadata;
- deterministic/reproducible generation rules.

Gate:
- changed source recompiles only affected descendants;
- repeated deterministic compile is byte-stable;
- model-generated projection reuse/regeneration passes its evidence oracle;
- authority conflicts fail closed.

## Z7 — Contradiction engine + failure memory

Build:
- structured-first contradiction candidates;
- semantic expansion;
- supersession reasoning;
- append-only resolution events;
- experiment/failure ledger.

Gate:
- known historical rejected approaches are retrieved as failed, not rediscovered as new proposals;
- temporal supersession is distinguished from contradiction on the frozen contradiction set.

## Z8 — Community graph + global/local retrieval

Build:
- generation-scoped community detection;
- grounded community summaries;
- global retrieval resolving back to evidence candidates.

Gate:
- global-synthesis promotion candidate passes the predeclared §9 promotion criteria;
- no source/provenance traceability loss.

## Z9 — DRIFT-style LAB investigation

Build:
- community primer;
- follow-up question generation;
- parallel local traversal;
- evidence reconciliation.

Gate:
- multi-hop/investigative promotion candidate passes predeclared §9 criteria;
- generated questions never become claims automatically.

## Z10 — Memory consolidation

Build:
- tiered memory;
- explicit write-back policy actions;
- compaction traces;
- retention policy.

Before each compaction experiment, freeze a **compaction oracle manifest** containing the durable IDs/evidence links/decisions/open questions that must survive.

Gate:
- 100-session simulation preserves every required oracle item and required evidence relationship;
- no new canonical claim appears solely because the compactor hallucinated it.

## Z11 — MCP 2026 + local daemon

Build:
- stateless MCP surface;
- explicit handles;
- local capability/auth policy;
- UDS/localhost hardening;
- capability-negotiated Tasks;
- compatibility/deprecation path.

Gate:
- existing MCP clients remain supported or have a documented migration path;
- read-only clients cannot invoke mutating/admin tools;
- no remote exposure by default.

## Z12 — LAB sparse / multi-vector / multimodal experiments

Research only until promotion criteria pass.

No FAST dependency on these features.

# 27. Testing strategy

Every phase uses:

1. unit tests;
2. regression tests;
3. isolated tmp roots;
4. frozen snapshots;
5. before/after retrieval evaluation where ranking can change;
6. full pytest;
7. repo/publication guard;
8. `git diff --check`;
9. index/graph/claim-store integrity checks as applicable.

Security-related paths also retain the existing Boundary red-team gates.

Tests must not mutate the production wiki or active production index.

---

# 28. Observability and scientific record

Every substantive experiment records:

- hypothesis;
- baseline;
- changed variable;
- frozen dataset/snapshot;
- result;
- rank movers;
- regressions;
- latency;
- memory;
- conclusion;
- rejected alternatives.

Failed experiments remain searchable.

Their durable metadata, conclusion, hashes, and evidence pointers are retained. Very large disposable intermediate artifacts may be garbage-collected under policy once the experiment is reproducible from retained inputs/configuration and its scientific record is preserved.

Do not erase a failed result merely because it lost.

---

# 29. Explicit non-goals

Zurvan 2.0 does **not**:

- replace Markdown with a proprietary database;
- replace SQLite merely because vector databases exist;
- require a cloud backend;
- require a web UI;
- make GraphRAG a core dependency;
- auto-promote models based on benchmarks alone without regression review;
- auto-write every chat answer to the wiki;
- mutate raw sources;
- use the live production store as a test fixture;
- use the holdout dataset for iterative tuning;
- expose private project names/paths in public artifacts;
- reopen Boundary v1 semantics;
- implement Boundary V5 in this programme.

---

# 30. Definition of Zurvan 2.0 success

Zurvan 2.0 is successful when all of the following are true:

1. A live shared store can keep accepting writes while retrieval measurements remain deterministic.
2. Exact/code/error queries can bypass unnecessary semantic machinery.
3. Semantic queries route through measured lexical/dense/graph paths.
4. Every ranked result can be explained.
5. Context assembly is traceable and separate from retrieval.
6. Claim history supports temporal and contradiction queries.
7. Source changes incrementally recompile only affected knowledge.
8. Failed approaches are durable, searchable memory.
9. Global/community and investigative retrieval are available without sacrificing evidence traceability.
10. FAST remains practical on the current machine.
11. LAB can scale capability without changing knowledge semantics.
12. Every model/index change is shadow-tested before promotion.
13. Markdown remains useful directly in Obsidian and Git-style workflows.
14. MCP clients can use Zurvan as a durable, local knowledge service.
15. No upgrade weakens the existing evidence, privacy, index-integrity, or Boundary invariants.
16. A snapshot proves cross-store revision coherence, not merely per-file immutability.
17. Federated/private retrieval is authorised before candidate generation.
18. Production and evaluation use the same versioned retrieval semantics for a named profile.

---

# 31. Architecture summary

```text
                 ┌──────────────────────┐
                 │  IMMUTABLE EVIDENCE  │  authority=evidence
                 └──────────┬───────────┘
                            │
                            ▼
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│   AUTHORED MARKDOWN   │→ │  KNOWLEDGE COMPILER  │ ←│ STRUCTURED ASSERTION │
│  authority=markdown   │  │ authority-aware      │  │ authority=structured │
└──────────────────────┘  └──────────┬───────────┘  └──────────────────────┘
                                      │
                          ┌───────────┴───────────┐
                          ▼                       ▼
                 ┌─────────────────┐     ┌─────────────────────┐
                 │ STRUCTURED      │     │ COMPILED MARKDOWN   │
                 │ KNOWLEDGE       │     │ reproducible views  │
                 └────────┬────────┘     └──────────┬──────────┘
                          │                         │
                          └──────────┬──────────────┘
                                     ▼
                            ┌─────────────────┐
                            │ KNOWLEDGE GRAPH │  cyclic semantics
                            └────────┬────────┘
                                     │
                         COMPILATION DEPENDENCY DAG
                              (acyclic invalidation)
                                     │
                 ┌───────────────────┼───────────────────┐
                 ▼                   ▼                   ▼
           FTS5 WORD/TRIGRAM       DENSE             GRAPH/TIME
                 │                   │                   │
                 └──────── authorised retrieval lanes ──┘
                                     │
                            EVIDENCE CANDIDATES
                                     │
                                 RRF FUSION
                                     │
                              OPTIONAL RERANKER
                                     │
                              CONTEXT COMPILER
                                     │
                                EVIDENCE PACK
                                     │
                              REASONING WORKER
                                     │
                               ANSWER + TRACE
                                     │
                              WRITE-BACK POLICY
                            ┌────────┼────────┐
                         PROPOSE  AUTO_COMMIT  APPROVAL
```

Every evaluation/search run is additionally pinned to a coherent **knowledge revision + snapshot + backend manifest**.

Orthogonal compute policy:

```text
FAST
  current machine
  low memory
  low latency
  known-good dense baseline
  FTS5 + dense + local graph

LAB
  stronger hardware
  reranking
  sparse retrieval
  multi-vector
  community/global search
  DRIFT-style investigation
  multimodal experiments
```

Canonical knowledge semantics remain identical across profiles. Physical derived-index formats may differ.

# 32. Research basis

This design uses current external work as inspiration, not as uncritical dependencies.

## Karpathy — persistent LLM wiki

Andrej Karpathy's April 2026 “LLM Wiki” describes a persistent Markdown knowledge base maintained by an LLM so understanding compounds instead of being rebuilt from raw context every session.

Zurvan preserves this principle while adding structured claims, provenance, evaluation, graph retrieval, auditability, and local agent interfaces.

## Microsoft GraphRAG

Current GraphRAG documentation describes:

- entity/relationship/key-claim extraction;
- hierarchical Leiden communities;
- community summaries;
- Basic, Local, Global, and DRIFT retrieval modes.

Zurvan adopts the architectural ideas, not the framework as a mandatory runtime.

## Sentence Transformers v6

Current Sentence Transformers documentation exposes four relevant model families:

- dense SentenceTransformer models;
- CrossEncoder rerankers;
- SparseEncoder models;
- MultiVectorEncoder models for ColBERT-style late interaction and visual-document retrieval.

These become benchmark lanes, not hard-coded dependencies.

## SQLite FTS5

SQLite FTS5 provides:
- word/token search;
- BM25 support;
- the trigram tokenizer for general substring matching.

This supports a dual lexical strategy without abandoning embedded local storage.

## MCP 2026-07-28

The current MCP release moves the protocol core to a stateless request/response design and places long-running Tasks in an extension negotiated by client capability.

Zurvan should adopt the stateless model while keeping explicit application state handles where needed.

## Anthropic long-running-agent practice

Anthropic's 2026 long-running scientific-computing workflow highlights:

- durable progress memory;
- recording failed approaches;
- quantitative test oracles;
- persistent checkpoints across sessions.

Zurvan generalises these ideas into experiment/failure memory, evaluation snapshots, and project state.

## QMD — local hybrid retrieval patterns

The current QMD project demonstrates a fully local Markdown retrieval stack combining:

- SQLite FTS5/BM25;
- local vector search;
- typed query expansion;
- Reciprocal Rank Fusion;
- local reranking;
- a long-lived localhost MCP/HTTP server to avoid repeated model loading.

Zurvan borrows the **patterns**, not QMD's implementation or model choices. In particular, typed lexical/vector/HyDE lanes, RRF, reranking, and warm local serving inform the planner/runtime design while remaining subject to Zurvan's own frozen evaluations.

## OpenAI — compaction, traces, and eval discipline

Current OpenAI developer APIs/documentation provide three useful design patterns:

- explicit compaction for long-running conversation state;
- trace/span inspection for agent/tool behaviour;
- structured eval datasets with repeatable testing criteria.

Zurvan keeps these ideas local: semantic memory compaction is evidence-preserving, retrieval traces are inspectable local artifacts, and promotion decisions are driven by frozen datasets rather than conversational impressions.

---

# 33. References

Anthropic 2026, *Long-running Claude for scientific computing*, Anthropic Research, 23 March 2026, <https://www.anthropic.com/research/long-running-Claude>.

Karpathy, A 2026, *LLM Wiki*, GitHub Gist, 4 April 2026, <https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f>.

Microsoft 2026, *GraphRAG documentation*, Microsoft, <https://microsoft.github.io/graphrag/>.

Model Context Protocol 2026, *The 2026-07-28 Specification*, MCP Project, <https://blog.modelcontextprotocol.io/posts/2026-07-28/>.

Model Context Protocol 2026, *Tasks extension*, MCP Project, <https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks>.

Sentence Transformers 2026, *SentenceTransformers Documentation v6*, <https://sbert.net/>.

SQLite 2026, *SQLite FTS5 Extension*, SQLite, <https://www.sqlite.org/fts5.html>.

OpenAI 2026, *Compact conversation*, OpenAI Developers, <https://developers.openai.com/api/reference/python/resources/beta/subresources/responses/methods/compact>.

OpenAI 2026, *Evals API*, OpenAI Developers, <https://developers.openai.com/api/reference/java/resources/evals/methods/create>.

Tobi 2026, *QMD — Query Markup Documents*, GitHub, <https://github.com/tobi/qmd>.

---

# 34. Final architecture acceptance gate

This specification is considered internally consistent only if all of the following remain true during implementation:

1. one durable artifact has one writable authority;
2. snapshot components share one coherent knowledge revision;
3. evaluation candidates cannot repeatedly consume the sealed holdout while tuning;
4. every retrieval lane enforces caller scope before producing candidates;
5. all lanes normalise to `EvidenceCandidate` before fusion;
6. no backend silently substitutes itself for the requested backend;
7. authored Markdown can remain an authoritative input;
8. generated summaries/questions never become evidence merely by existing;
9. private/federated/MCP access is capability-controlled;
10. every phase is allowed to ship only its declared capabilities, not future-phase semantics.

A phase that needs to violate one of these is an architecture change and must amend this spec explicitly before implementation.

---

# Appendix A — Architecture gauntlet

This appendix records the adversarial review of the unified design. Findings are kept even when resolved so future sessions can see what was challenged.

## A1. “Markdown is only compiled output”

**Severity:** 🔴 Critical design error  
**Original risk:** Turning Markdown into a disposable cache would violate Zurvan's Karpathy/Obsidian/Git-friendly foundation and make the human-readable memory subordinate to opaque SQLite state.  
**Resolution:** Replaced with the three persistent truth layers and explicit authored-vs-compiled Markdown modes.  
**Status:** RESOLVED.

## A2. “Claim DAG”

**Severity:** 🔴 Structural error  
**Original risk:** Claims/contradictions/support relationships naturally form cycles. Forcing the knowledge graph to be a DAG would either lose relations or corrupt semantics.  
**Resolution:** Separated the cyclic knowledge graph from the acyclic compilation dependency DAG.  
**Status:** RESOLVED.

## A3. Numeric LLM confidence such as `0.98`

**Severity:** 🟠 Epistemic quality risk  
**Original risk:** Uncalibrated decimals create fake precision.  
**Resolution:** Categorical confidence plus explicit evidence basis. Probabilities require future calibration.  
**Status:** RESOLVED.

## A4. “Bitemporal” schema with only `observed_at`

**Severity:** 🟠 Data-model ambiguity  
**Original risk:** Could not cleanly distinguish world-valid time from Zurvan's recorded-belief history.  
**Resolution:** Added valid-time and record-time intervals, with `observed_at` retained separately.  
**Status:** RESOLVED.

## A5. Deterministic planner implemented by an SLM

**Severity:** 🟠 Reproducibility risk  
**Original risk:** A learned router is not deterministic merely because it is small.  
**Resolution:** Rules-first routing; learned intent classification only for unresolved queries; multi-route fallback on uncertainty.  
**Status:** RESOLVED.

## A6. HyDE in the default retrieval path

**Severity:** 🟠 Provenance/complexity risk  
**Original risk:** Hypothetical generated text could improve recall but adds an extra generative layer before evidence retrieval.  
**Resolution:** HyDE is LAB/experimental only until proven on holdout.  
**Status:** RESOLVED.

## A7. “Zero hallucination” exact search wording

**Severity:** 🟡 Wording overclaim  
**Original risk:** Exact retrieval does not guarantee a downstream generative answer cannot hallucinate.  
**Resolution:** Exact retrieval is described as retrieval without generative interpretation.  
**Status:** RESOLVED.

## A8. Hard-coded model names as architecture

**Severity:** 🟠 Longevity risk  
**Original risk:** Model names age faster than interfaces and would turn the architecture into a 2026 snapshot.  
**Resolution:** Backend interfaces are stable; model names are benchmark candidates.  
**Status:** RESOLVED.

## A9. MiniLM “int8” assumption

**Severity:** 🔴 Current-state mismatch  
**Original risk:** Current production embeddings are 384-dimensional MiniLM vectors in the existing derived store; silently declaring int8 would mix a storage-format migration into a capability spec.  
**Resolution:** FAST starts from the known-good current production representation. Quantisation is a separate parity experiment.  
**Status:** RESOLVED.

## A10. Single frozen 300-query set used for tuning and judgment

**Severity:** 🔴 Benchmark contamination risk  
**Original risk:** Zurvan would overfit its own evaluation instrument.  
**Resolution:** Replaced the single set with a visible development set plus a sealed holdout/reserve lifecycle. The initial programme is at least 350 queries (about 200 development + at least 150 holdout), while preserving the historical 50-item instrument separately.  
**Status:** RESOLVED.

## A11. Arbitrary `<25 ms` Phase-2 latency gate

**Severity:** 🟠 False engineering precision  
**Original risk:** Full hybrid + graph + reranking cannot honestly inherit an invented latency ceiling before measurement.  
**Resolution:** Stage-level p50/p95 metrics are measured first. Promotion uses explicit FAST/LAB resource budgets and baseline-relative regression checks.  
**Status:** RESOLVED.

## A12. GraphRAG as a dependency

**Severity:** 🟠 Architectural lock-in  
**Original risk:** Zurvan would become a wrapper around another project's storage/query model.  
**Resolution:** Adopt community, Local, Global, and DRIFT ideas while keeping native SQLite/Markdown structures.  
**Status:** RESOLVED.

## A13. MCP Tasks assumed universally available

**Severity:** 🟠 Interoperability risk  
**Original risk:** Tasks are an extension and require client capability.  
**Resolution:** Capability negotiation plus non-Tasks fallback.  
**Status:** RESOLVED.

## A14. Shared store must be frozen during measurements

**Severity:** 🔴 Proven false by production experience  
**Original risk:** Cross-project or same-session writes invalidate long test/eval runs.  
**Resolution:** Immutable measurement snapshots and generation references. Global writer blocking is not the default solution.  
**Status:** RESOLVED IN DESIGN; IMPLEMENTATION IS Z0.

## A15. Full database copy per snapshot

**Severity:** 🟠 Performance/storage risk  
**Original risk:** Copying hundreds of MB for every evaluation would make snapshots impractical.  
**Resolution:** Prefer immutable derived generations referenced by snapshot manifests. Copy-on-write/content-addressing may be used where needed.  
**Status:** RESOLVED IN DESIGN.

## A16. Every chat answer auto-writes to memory

**Severity:** 🔴 Knowledge-quality risk  
**Original risk:** Canonical memory becomes conversational sludge and amplifies model mistakes.  
**Resolution:** Write-back candidates require novelty, evidence, project scope, and existing policy checks.  
**Status:** RESOLVED.

## A17. Compaction deletes raw provenance

**Severity:** 🔴 Auditability risk  
**Original risk:** “Compaction” could accidentally mean destructive forgetting of evidence.  
**Resolution:** Raw evidence is immutable. Only operational residue may be pruned under explicit retention policy after durable semantic/audit state exists.  
**Status:** RESOLVED.

## A18. Community summaries treated as evidence

**Severity:** 🔴 Grounding risk  
**Original risk:** LLM-generated community summaries could become self-citing synthetic truth.  
**Resolution:** Community summaries are retrieval priors. Final ContextCompiler output must resolve to underlying claims/evidence.  
**Status:** RESOLVED.

## A19. Generated DRIFT questions become knowledge

**Severity:** 🟠 Contamination risk  
**Original risk:** Retrieval prompts/follow-up questions could pollute the claim graph.  
**Resolution:** Generated questions are ephemeral retrieval aids unless separately asserted and evidenced.  
**Status:** RESOLVED.

## A20. Candidate model silently falls back

**Severity:** 🔴 Previously observed production failure class  
**Original risk:** A requested model could fail and silently produce a different embedding space.  
**Resolution:** Exact backend/provider invariants remain. Fallback only when explicitly allowed and always traced.  
**Status:** RESOLVED.

## A21. Candidate indexes overwrite production

**Severity:** 🔴 Experiment isolation risk  
**Original risk:** Evaluation could corrupt or replace the active index.  
**Resolution:** ACTIVE/CANDIDATE generations and atomic promotion.  
**Status:** RESOLVED.

## A22. Context packing is treated as part of retrieval

**Severity:** 🟠 Diagnosability risk  
**Original risk:** Retrieval quality and context-selection quality become impossible to distinguish.  
**Resolution:** ContextCompiler is a distinct subsystem with its own trace and tests.  
**Status:** RESOLVED.

## A23. Operational Tier 0 conflated with immutable raw evidence

**Severity:** 🟠 Storage-policy contradiction  
**Original risk:** “Preserve everything forever” would make logs unbounded, while pruning could accidentally destroy evidence.  
**Resolution:** Immutable evidence and operational telemetry are separate classes. Operational retention is configurable; evidence provenance is not discarded.  
**Status:** RESOLVED.

## A24. LAB features accidentally become FAST dependencies

**Severity:** 🔴 Current-machine usability risk  
**Original risk:** Installing/running ColBERT, sparse, vision, or large rerankers could make the M2 workflow unusable.  
**Resolution:** Profile boundaries are explicit; LAB dependencies remain optional. FAST must function without them.  
**Status:** RESOLVED.

## A25. AUTO profile damages reproducibility

**Severity:** 🟠 Measurement reproducibility risk  
**Original risk:** Hardware detection could make the same benchmark silently use different stacks.  
**Resolution:** AUTO forbidden in CI/evaluation. Exact explicit profile/backend manifest required.  
**Status:** RESOLVED.

## A26. New claim ledger destroys existing Markdown or claim pages

**Severity:** 🔴 Migration risk  
**Original risk:** A “clean” new schema could invalidate years of accumulated human-readable memory.  
**Resolution:** Claim-ledger migration is additive, provenance-preserving, and must keep authored Markdown intact.  
**Status:** RESOLVED IN DESIGN; Z2 requires migration gauntlet.

## A27. Instrument/report leakage feeds retrieval

**Severity:** 🔴 Known historical failure mode  
**Original risk:** Evaluation queries or reports become indexed and inflate retrieval metrics.  
**Resolution:** Existing protected-instrument rules remain; the expanded development/holdout/reserve programme inherits exclusion, sealed-holdout, trace, MCP, and report protections.  
**Status:** RESOLVED IN DESIGN.

## A28. Global search hides source identity

**Severity:** 🟠 Provenance risk  
**Original risk:** Cross-community synthesis could return plausible prose with weak traceability.  
**Resolution:** Community/global results must resolve back to claim and evidence IDs before answer context.  
**Status:** RESOLVED.

## A29. Cross-project federation launders privacy/scope

**Severity:** 🔴 Privacy risk  
**Original risk:** Federated retrieval could merge project knowledge without retaining project origin.  
**Resolution:** Federation is deny-by-default: caller capabilities and allowed project/vault/privacy scopes filter every lane before retrieval, and project identity/provenance remain on every authorised result.  
**Status:** RESOLVED.

## A30. The specification is too large for one implementation plan

**Severity:** 🔴 Delivery risk  
**Finding:** True. This is an architecture programme, not a bounded feature.  
**Resolution:** One canonical architecture spec, but Z0–Z12 each receive their own implementation plan, review, measurement and commit series.  
**Status:** RESOLVED.

---

## A31. “Three canonical layers” creates a bidirectional-sync loop

**Severity:** 🔴 Consistency risk  
**Finding:** Calling evidence, structured claims, and Markdown all first-class without declaring write ownership leaves two editable representations of the same knowledge.  
**Resolution:** Added one writable authority per artifact (`evidence`, `markdown`, or `structured`) and fail-on-conflict semantics.  
**Status:** RESOLVED.

## A32. Snapshot manifest stores hashes but not replayable bytes

**Severity:** 🔴 Determinism failure  
**Finding:** A hash of an untracked private wiki file cannot reconstruct state A after the live file becomes B. Git cannot rescue it because the knowledge base is intentionally private/untracked.  
**Resolution:** Snapshot manifests must resolve to immutable bytes; use existing immutable CAS blobs, reflink/COW clones, or deduplicated copies. Hardlinks are allowed only to objects already immutable.  
**Status:** RESOLVED.

## A33. Claim ledger incorrectly modelled as a rebuildable immutable generation

**Severity:** 🟠 Lifecycle mismatch  
**Finding:** Search/graph indexes are derived generations, but the claim ledger contains durable authored/observed state and may evolve transactionally. Treating it like a disposable index confuses source-of-truth semantics.  
**Resolution:** Search/graph use immutable generations; evaluation captures a consistent read-only claim-ledger snapshot via SQLite backup/snapshot semantics.  
**Status:** RESOLVED.

## A34. Holdout leakage through tooling

**Severity:** 🔴 Benchmark integrity risk  
**Finding:** Any sealed holdout would become another training set if raw queries appear in repo reports, MCP output, traces, or routine agent context.  
**Resolution:** Holdout inherits protected-instrument rules; MCP returns authorised aggregate/class metrics and sealed run IDs, raw text stays hidden, and revealed/familiar items are retired/replaced from reserve.  
**Status:** RESOLVED.

## A35. Hardlink used as a snapshot primitive

**Severity:** 🔴 Determinism failure  
**Finding:** A hardlink to a mutable live wiki file references the same inode. An in-place edit can mutate the “snapshot”, violating the entire snapshot invariant.  
**Resolution:** Ordinary live files use immutable CAS blobs, reflink/COW clones, or copies. Hardlinks are permitted only to objects already guaranteed immutable.  
**Status:** RESOLVED.

## A36. Local-first cloud fallback could silently exfiltrate data

**Severity:** 🔴 Privacy/locality risk  
**Finding:** “Cloud LLM optional” is insufficient if local inference failure silently falls back to a network provider.  
**Resolution:** Network inference is explicit opt-in and may not be an implicit fallback for private source processing.  
**Status:** RESOLVED.

## A37. Ingested content loses the existing “untrusted / never execute” rule

**Severity:** 🔴 Security regression  
**Finding:** The new architecture described immutability but initially omitted the existing rule that source content is untrusted and must never be executed.  
**Resolution:** The invariant is carried forward explicitly.  
**Status:** RESOLVED.

## A38. External retrieval/eval ideas missing from the consolidated research basis

**Severity:** 🟡 Completeness gap  
**Finding:** The first consolidation captured GraphRAG, Sentence Transformers, SQLite, MCP, Karpathy, and Anthropic, but omitted QMD's local hybrid/RRF/reranker patterns and OpenAI's compaction/trace/eval patterns discussed during research.  
**Resolution:** Added both as architectural inspiration only, with no dependency or cloud requirement.  
**Status:** RESOLVED.

## A39. Immutable components can still form a cross-store Franken-state

**Severity:** 🔴 Measurement-integrity failure  
**Finding:** Freezing wiki bytes, claims, graph, and search independently does not prove they describe the same logical knowledge state.  
**Resolution:** Added monotonic knowledge revision, `built_from_revision`, coherent snapshot capture, and fail-closed stale-capability rules.  
**Status:** RESOLVED.

## A40. Snapshot capture can race writers

**Severity:** 🔴 Measurement-integrity failure  
**Finding:** Even replayable bytes can come from different moments if capture walks files while writers mutate them.  
**Resolution:** Added short capture barrier for managed writes, double-manifest verification for unmanaged edits, and retry-on-change semantics.  
**Status:** RESOLVED.

## A41. Holdout lifecycle contradicted the model tournament

**Severity:** 🔴 Benchmark contamination risk  
**Finding:** §9 called holdout promotion-only while the tournament gave every candidate holdout results.  
**Resolution:** Ordinary candidates receive development results only; a frozen declared promotion candidate gets one sealed holdout run. Revealed items are retired/replaced.  
**Status:** RESOLVED.

## A42. 100-query holdout was too thin for class-level non-regression

**Severity:** 🔴 Statistical-design risk  
**Finding:** The proposed taxonomy has roughly fifteen primary classes; 100 total items can leave critical classes nearly empty.  
**Resolution:** Initial programme is at least 350 queries with ≥150 sealed holdout and normally ≥10 examples per critical primary stratum; total grows with taxonomy.  
**Status:** RESOLVED.

## A43. RRF lacked a cross-lane candidate identity

**Severity:** 🔴 Retrieval-correctness risk  
**Finding:** Chunk, claim, graph, and temporal lanes cannot be fused safely when each ranks a different object type.  
**Resolution:** Added canonical `EvidenceCandidate`; graph/community objects resolve to underlying evidence before fusion.  
**Status:** RESOLVED.

## A44. Privacy/project scope was enforced too late

**Severity:** 🔴 Privacy risk  
**Finding:** ContextCompiler filtering occurs after retrieval, which could already leak disallowed evidence into scores, candidate pools, or traces.  
**Resolution:** Added immutable request context and pre-query scope filtering in every lane; ContextCompiler repeats it as defence in depth.  
**Status:** RESOLVED.

## A45. Localhost was mistaken for authorisation

**Severity:** 🔴 Local-service security risk  
**Finding:** `127.0.0.1` alone does not stop another local process or browser-origin attack from invoking mutating tools.  
**Resolution:** Added UDS permissions, bearer/capability token for HTTP, Origin/CORS restrictions, capability classes, and explicit remote-bind policy.  
**Status:** RESOLVED.

## A46. Federation retained provenance but lacked ACL semantics

**Severity:** 🔴 Privacy risk  
**Finding:** A result can preserve its project ID and still have been retrieved by an unauthorised caller.  
**Resolution:** Federation is deny-by-default; caller capabilities/allowed projects/vaults/privacy classes constrain every lane before querying.  
**Status:** RESOLVED.

## A47. Phase order exposed future intents before their data model existed

**Severity:** 🔴 Delivery/semantic risk  
**Finding:** Temporal/contradiction routing and contradiction-aware context were scheduled before the structured temporal claim ledger.  
**Resolution:** Claim-ledger foundation moved to Z2; planner intents are capability-gated; contradiction/global/DRIFT capabilities activate only when their subsystems exist.  
**Status:** RESOLVED.

## A48. Final diagram contradicted the authority model

**Severity:** 🔴 Specification-consistency failure  
**Finding:** The diagram showed Markdown only downstream of the compiler even though authored Markdown can be authoritative input.  
**Resolution:** Replaced diagram with evidence + authored Markdown + structured assertions feeding an authority-aware compiler.  
**Status:** RESOLVED.

## A49. Compiled Markdown “determinism” overclaimed model reproducibility

**Severity:** 🟠 Reproducibility risk  
**Finding:** LLM-generated summaries need not be byte-identical across runtime/hardware even with pinned settings.  
**Resolution:** Byte stability is required for deterministic projections; model-generated artifacts are cached by full identity and forced regeneration is judged by evidence/semantic oracle.  
**Status:** RESOLVED.

## A50. Authority conflicts were specified without a detection mechanism

**Severity:** 🟠 Consistency risk  
**Finding:** “Fail on conflict” was impossible to implement without generated-from/content hashes and compiler metadata.  
**Resolution:** Added required authority/projection metadata and generated-content hashes.  
**Status:** RESOLVED.

## A51. Claim schema lacked identity, vocabulary, provenance plurality, and typed locators

**Severity:** 🟠 Knowledge-quality risk  
**Finding:** Natural-language predicates and one byte-span provenance field would create duplicates and fail on PDF/image/media evidence.  
**Resolution:** Added schema/revision IDs, stable subject IDs, versioned predicates, typed literal metadata, multiple evidence entries, assertion metadata, and typed locators.  
**Status:** RESOLVED.

## A52. Bitemporal history could still be destructively updated

**Severity:** 🟠 Historical-integrity risk  
**Finding:** Valid/record time fields alone do not guarantee history survives implementation.  
**Resolution:** Added append-oriented claim revisions/events and a derived current view.  
**Status:** RESOLVED.

## A53. Incremental compilation ignored deletion/rename/orphan state

**Severity:** 🟠 Staleness risk  
**Finding:** Only changed-source recompilation was specified; deleted/moved evidence could leave immortal derived truth.  
**Resolution:** Added tombstones, rename identity, dependency invalidation, and current-view orphan/retraction policy while preserving historical raw evidence.  
**Status:** RESOLVED.

## A54. Exact IDs were unnecessarily routed through search

**Severity:** 🟠 Correctness/latency risk  
**Finding:** Primary keys/hashes/known paths should not depend on ranking.  
**Resolution:** Added direct resolver before FTS/dense retrieval.  
**Status:** RESOLVED.

## A55. FTS5 trigram misses general substrings shorter than three characters

**Severity:** 🟠 Exact-lookup gap  
**Finding:** Trigram is not a universal symbol lookup.  
**Resolution:** Direct lookup handles structured keys; trigram handles ≥3-character substrings; explicit literal/metadata fallback covers legitimate shorter tokens.  
**Status:** RESOLVED.

## A56. Context token budget was reasoner-agnostic

**Severity:** 🟠 Runtime correctness risk  
**Finding:** A hard-coded token budget is not portable across tokenizers/context limits.  
**Resolution:** ContextCompiler now receives target reasoner/tokenizer/context/output reservations and computes the evidence budget.  
**Status:** RESOLVED.

## A57. Retrieval traces could become a private-data warehouse

**Severity:** 🟠 Privacy risk  
**Finding:** Full queries/snippets/ranks are useful for debugging but sensitive and can also leak holdout instruments.  
**Resolution:** Added summary/full/protected-eval trace modes, retention/redaction/permission policy, and Boundary-gated export.  
**Status:** RESOLVED.

## A58. Community IDs were treated as timeless

**Severity:** 🟠 Graph-staleness risk  
**Finding:** Leiden partitions can split/merge after graph changes.  
**Resolution:** Communities and summaries are generation-scoped and record their source graph/algorithm/parameters.  
**Status:** RESOLVED.

## A59. Contradiction detection depended too heavily on semantic retrieval

**Severity:** 🟠 Recall risk  
**Finding:** A retrieval miss could become a contradiction miss.  
**Resolution:** Structured subject/predicate/scope/time matching generates candidates first; semantic retrieval expands them. Resolutions are append-only events.  
**Status:** RESOLVED.

## A60. Write-back policy had no mutation authority outcome

**Severity:** 🟠 Knowledge-quality risk  
**Finding:** Passing novelty/evidence checks did not say whether a candidate could auto-write, required approval, or was proposal-only.  
**Resolution:** Added `PROPOSE`, `AUTO_COMMIT`, and `REQUIRE_APPROVAL` policy outcomes.  
**Status:** RESOLVED.

## A61. Backend registry was not supply-chain reproducible

**Severity:** 🟠 Reproducibility/supply-chain risk  
**Finding:** Model name/version/dimension is insufficient to reproduce a local stack.  
**Resolution:** Added model source/revision/hash/licence/tokenizer plus Python/library/execution-backend and index-format identity.  
**Status:** RESOLVED.

## A62. Exact-provider fail-closed rule was accidentally LAB-only

**Severity:** 🔴 Known production failure regression  
**Finding:** The previous mock-fallback incident happened precisely on the production embedding path; FAST must not be weaker.  
**Resolution:** Exact requested-backend semantics now apply to every profile/backend.  
**Status:** RESOLVED.

## A63. Compaction “oracle” was undefined

**Severity:** 🟠 Evaluation-integrity risk  
**Finding:** The compactor could effectively judge its own output.  
**Resolution:** Z10 freezes a pre-compaction oracle manifest of durable IDs/evidence links/decisions/questions before the run.  
**Status:** RESOLVED.

## A64. Snapshot lease cleanup could pin generations forever after crashes

**Severity:** 🟠 Storage-lifecycle risk  
**Finding:** Reference counting without crash/expiry semantics leaks immutable generations.  
**Resolution:** Added persisted leases with heartbeat/expiry and conservative GC.  
**Status:** RESOLVED.

## A65. Project state lacked an as-of/freshness contract

**Severity:** 🟡 Operator-clarity gap  
**Finding:** A cold-start summary without revision/generation/freshness can present stale state as current.  
**Resolution:** Added `as_of`, active generation IDs, and per-subsystem staleness.  
**Status:** RESOLVED.

## A66. CLI/MCP compatibility had no deprecation policy

**Severity:** 🟡 Interface-stability gap  
**Finding:** “Until explicit deprecation” did not constrain silent semantic repurposing.  
**Resolution:** Added versioned contract/schema bumps, warnings, migration path, and declared removal window.  
**Status:** RESOLVED.

## A67. Failed-experiment retention could imply retaining unlimited heavy artifacts

**Severity:** 🟡 Storage-policy ambiguity  
**Finding:** Scientific memory and infinite binary retention are not the same requirement.  
**Resolution:** Durable experiment metadata/results/hashes/evidence pointers remain; reproducible heavy intermediates may be garbage-collected under policy.  
**Status:** RESOLVED.

# Appendix B — Remaining open research questions

These are legitimate unknowns. They are **not** placeholders required to understand the architecture.

1. Which dense embedding candidate, if any, beats the current MiniLM baseline on the new holdout set?
2. Does a small FAST reranker improve MRR enough to justify warm memory and latency?
3. Does sparse neural retrieval add value beyond FTS5 + dense on Zurvan's actual corpus?
4. Does multi-vector retrieval justify its disk/RSS cost?
5. Which community granularity best serves Zurvan's graph?
6. Does DRIFT-style retrieval outperform simpler graph expansion on the multi-hop subset?
7. What measured FAST RSS and end-to-end latency budgets should become hard gates after Z0/Z1 instrumentation?
8. Which operational events require long retention versus compaction after semantic write-back?
9. Which MCP clients used by Zurvan support the 2026 Tasks extension in practice?
10. Can multimodal page retrieval run acceptably on available LAB hardware without degrading core local workflows?

Each question must be answered experimentally, not by architecture preference.

---

# Appendix C — Implementation ordering rationale

The order is deliberate:

1. **Snapshots first** so later measurements are coherent and replayable.
2. **Observability and sealed evaluation second** so future changes are measurable without contaminating the holdout.
3. **Claim/temporal foundations third** so temporal/provenance/contradiction-aware retrieval has a real data model rather than placeholder semantics.
4. **Planner + direct/lexical routing** after scope and structured lookup exist.
5. **EvidenceCandidate fusion + ContextCompiler** before new models so architecture gains are separable from model gains.
6. **Rerankers/model tournament** only after the promotion system exists.
7. **KnowledgeCompiler/dependency DAG** after claim and retrieval contracts stabilise.
8. **Contradiction + community/DRIFT** only after graph/claim semantics are strong enough.
9. **Memory compaction** only after the system can freeze an oracle of what is durable.
10. **MCP/runtime modernisation** after internal authority/scope contracts stabilise.
11. **Sparse/multi-vector/multimodal** last as LAB experiments.

This avoids rebuilding the same interfaces repeatedly and keeps each promotion attributable to one class of change.
