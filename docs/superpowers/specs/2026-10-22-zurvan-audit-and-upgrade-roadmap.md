# Zurvan: code audit, 2026 research and upgrade roadmap

**Prepared for:** Raouf  
**Date:** 22 October 2026, Australia/Sydney  
**Repository:** `Raoof128/Project-Zurvan`  
**Audited commit:** `e65d4bbf093c3a33ce920edd03bd79ad6c688f62`  
**Status:** Audit and recommendations. No production source, store, repository branch or remote was changed.

## 1. Decision

**Keep the Rust read engine and Python control/write layer. Repair five demonstrated correctness defects, bind every response to its actual data generation, finish the evaluation gates, and implement the canonical spec’s evidence ledger, typed retrieval and incremental compiler.** A whole-project rewrite would spend effort around the most important problems.

Zurvan already shipped its important Rust conversion. The current bottlenecks are the amount of work performed per query and refresh, evidence admission, global store selection, model identity and consistency across derived stores. Those are architectural contracts and algorithms; changing the implementation language does not resolve them.

The strongest product direction is **local agent memory whose evidence, scope and revision can be independently checked, with corrections propagated through derived context**. This is a positioning hypothesis to prove. Markdown memory, temporal graphs, evidence-backed observations and Rust storage already have substantial prior art.

### Evidence labels used in this report

| Label | Meaning |
|---|---|
| Reproduced | Executed against the pinned source in disposable synthetic stores; an observable defect occurred. |
| Source-confirmed | The code contains the mechanism described; production frequency or impact was not measured. |
| Repository-reported | A result recorded by the project, not newly measured in this audit. |
| Proposed | A design, experiment, acceptance criterion or engineering judgement. |

### Audit scope and limits

The GitHub tree contained 584 blobs. This audit materialised 447 selected text files, covering the scripts, Rust crate, tests, current project instructions, relevant specifications and evaluation infrastructure. All 447 local files were verified against their Git blob hashes. This was a pinned source snapshot, not a complete Git history checkout or a claim that every line of every file received equal review.

The complete supplied 18 September canonical specification was reviewed against current main. The supplied October 2 instruction/changelog copies are older than current main’s October 20 changes. Current source and current change records govern shipped status. The repository connector reported the repository as private; some committed documentation still calls it public. Existing restrictions on publishing knowledge remain relevant regardless of that metadata difference.

Seven isolated checks reproduced **five distinct defects**. The root reviewer independently replayed them. The unavailable native `sqlite_vec` extension was replaced with a Python cosine-distance scalar; model failure was injected, and extraction’s model/concept-merger were stubbed. The real validators, claim writers, retrieval routing and logical-operation machinery ran. These probes establish control-flow and data-integrity behaviour, not native extension performance or real-corpus ranking quality.

The live private corpus, deployed M2/8 GB machine and model cache were unavailable. This workspace had no Cargo, pytest, native sqlite-vec or Sentence Transformers installation. The full Python/Rust suites, production benchmark, sealed evaluator and destructive recovery tests were not run. No protected gold queries or production source excerpts are included in the evidence package.

## 2. What current Zurvan actually has

### Current measurements and status

The following figures are **repository-reported**, chiefly in the October 20 changelog. They describe particular workloads and cache conditions, not universal performance guarantees. [Current changelog][z-log]

| Item | Latest relevant record | Correct interpretation |
|---|---|---|
| Rust MCP | Shipped, merged and client cut-over recorded | Reads are already native; writes and evaluation bridge to Python. |
| MCP surface | 15 tools, 7 resources, 4 prompts | Existing product surface, not the whole future daemon design. |
| Hybrid retrieval | p50 70 ms; p95 92 ms | Reported stdio workload at the measured corpus. |
| Keyword retrieval | p50 80 ms; p95 110 ms | Whole-file substring semantics, not the hybrid BM25 lane. |
| First status | 74 ms in a fresh process with persisted metadata | Missing-cache metadata computation was approximately 5.2 seconds. This is not cold hybrid-search latency. |
| Physical footprint | 164 MB | Not a measurement of all clean mapped pages, peak RSS or total system memory pressure. |
| Measured corpus | Approximately 303,080 chunks | More recent than the September spec’s historical baseline. |
| Protocol parity | 314 cases/protocol; 312 pass, 2 expected divergences, 0 fail | Reported for protocol versions 2025-11-25 and 2026-07-28. |
| Retired relevance instrument | Top-1 0.72; top-3 0.74; MRR 0.7372 | Historical retrieval on 50 items. It is not current answer accuracy and cannot authorise promotion. |
| Latest merged Python suite | 3334 passed, 1 failed, 8 skipped | The named live operator test remains a failure in that run. The older 1547/0 Rust-closeout record is not the latest whole-tree result. |
| Z1b | Code merged; **NOT CLOSED** | Fresh operational checks, seal, named gates and verdict still matter. Merge did not close them. |

Both MCP implementations already default search/context to hybrid. The CLI defaults differ. Recommending “turn on hybrid for agents” would misdiagnose current MCP behaviour. Likewise, the historical zero top-1/top-3 keyword result concerns substring counting over entire files; it does not establish that BM25 or exact lexical retrieval is ineffective. [MCP defaults][z-tools] · [CLI/search implementation][z-context]

### Foundations to preserve

The existing design has useful safeguards: immutable and untrusted raw evidence; build-aside SQLite publication with atomic replacement; read-only access in core index read paths; operation-level revision accounting; crash/suspect reconciliation; measurement snapshots and leases; trace privacy modes; and separation of development and sealed evaluation. The Rust hybrid engine also pins its internal FTS/vector/hydration generation. These are valuable assets to extend. [Standing invariants][z-agents] · [Revision machinery][z-revision] · [Snapshots][z-snapshot] · [Rust search][z-search]

Read-only access is not yet universal: `scripts/graph_context.py` checks existence and then uses ordinary `sqlite3.connect(db_path)`. Route this secondary reader through the read-only generation-aware opener too. Its existence check is not an atomic guarantee against a concurrent removal. This is a static consistency gap, not another executed reproduction. [Graph context reader][z-graph-context]

Documentation needs a current evidence index. README and some MCP tables lag the changelog. Generate a small status page from versioned benchmark receipts and phase attestations, while keeping historical entries unchanged. It should distinguish “implemented”, “tested here”, “operationally closed” and “promoted”. Test count and commit volume are not substitutes for those distinctions.

## 3. Correctness findings

Severity concerns data integrity and trustworthy agent behaviour. It is not a claim that a remotely exploitable vulnerability has been demonstrated.

### F1. Extraction can overwrite another source’s claim

**High — reproduced.** The extraction validator checks claim-ID uniqueness within one model response. The writer sanitises the model-supplied ID into `wiki/claims/{id}.md` and opens it with `w`. There is no global ownership check or source namespace. Source IDs also derive from basenames, creating a separate collision risk. [Writer, lines 98–108][z-extract-write] · [Validator, lines 19–37][z-validator]

Two valid synthetic extractions, each returning `claim-1`, left one claim page. The second extraction replaced the first claim text, source metadata and citation. The receipt records both states and their SHA-256 hashes. This is current durable knowledge loss, independently of the future Z2 ledger.

**Repair:** allocate stable global claim IDs; distinguish source content identity from a source occurrence; validate uniqueness after normalisation; reject cross-owner collisions; stage a whole extraction before commit. Re-extraction needs explicit reconcile/supersession behaviour. Do not let a model choose a destructive global filename.

**Gate:** repeated IDs across sources, identical basenames in different directories, sanitisation collisions, retry after interruption and intentional updates must either preserve prior evidence or return an explicit conflict. A successful retry should not create duplicates.

### F2. Empty quotations pass evidence admission

**High — reproduced through three paths.** `memory.add_claim` tests whether evidence is a substring of the source. The empty string passes that test. The extraction validator checks quote membership only when the quote is truthy. Actual manual admission, direct Python MCP handler admission and extraction validation all accepted empty evidence in the probes. The MCP check did not exercise stdio transport or the deployed Rust bridge. [Manual admission][z-memory] · [Extraction admission][z-validator]

An unsupported assertion can therefore enter durable memory labelled high confidence. A nonempty evidence list is insufficient when its quote is empty.

**Repair:** one shared admission layer should require nonblank evidence, immutable source identity, locator/span and content digest for an evidence-backed claim. If unsourced user assertions are useful, store them as a distinct assertion type with declared authority. Keep “quote exists in source” separate from “quote supports proposition”: substring membership cannot establish entailment or truth.

**Gate:** empty, whitespace-only, missing, incorrect-source, wrong-version and valid evidence across CLI, library, MCP and extraction paths. A failed validation must not mint an accepted claim or partially publish an extraction.

### F3. Federated hybrid search uses the home index and mislabels its hits

**High — reproduced.** Federation passes each selected project root to `_search_internal`. In hybrid mode that function ignores the selected root and calls the global search-DB resolver. The caller then labels returned hits with the requested project. [Search routing, lines 19–30][z-context-routing] · [Federation labelling, lines 35–55][z-federation]

The probe created distinct home and other-project indexes. Requesting only `other` returned `wiki/home.md` and home content labelled `project: other`. No unusual search-DB override was required.

**Repair:** resolve an explicit store handle containing project/vault identity, root, index path and generation. Pass it through every lane and bind result provenance to that handle. Avoid changing process-global environment per request. Define the meaning of an override for a specific store.

**Gate:** two-vault sentinels, same relative paths, selected subsets, missing indexes, overrides and simultaneous requests must preserve selection and attribution. This demonstrates wrong routing and provenance; it is not evidence of an unauthorised cross-user breach in today’s broad local-owner trust model.

### F4. Python query embedding failure returns mock semantic results

**High — reproduced.** The embedding helper catches a model failure and returns deterministic hash vectors. The hybrid query caller takes only the returned vector and does not validate its provider/model/dimension against the index. The rebuild path has an exact-provider check, but this read path does not. [Fallback, lines 35–63][z-embed] · [Query consumer, lines 97–105][z-hybrid-query]

An injected model-load failure against a synthetic index labelled MiniLM returned a hybrid hit without an exception. The warning said mock embeddings were used. The fixture proves acceptance of the wrong vector space; it does not measure a real model’s loss in accuracy.

**Repair:** make embedding identity and failure typed. A missing/corrupt model must produce an explicit unavailable or mismatch outcome. A caller-authorised lexical fallback should be labelled and traced as degraded. Mock vectors belong behind explicit fixture configuration. Diagnostics must not pollute machine-readable stdout.

Pin weights, tokenizer, pooling, normalisation, dimensions and runtime representation. A model name alone is not a complete vector-space identity. Python CLI/MCP and the evaluation bridge remain relevant. Rust’s normal read path already uses an explicit keyword fallback notice; it does not share this mock-vector defect. [Rust fallback][z-search]

### F5. Traces describe the wrong fusion weights

**Medium — reproduced.** Actual scoring uses lexical 0.2 and semantic 0.8. The trace declares 0.6 and 0.4. For lexical 1.0 and semantic 0.5, the actual score is 0.6 while replay from the trace gives 0.8. [Scorer, lines 245–260][z-fusion-score] · [Trace declaration][z-context]

**Repair:** share one versioned policy object between scoring and trace emission; include its identity in receipts. Verify score reconstruction from recorded components. Correct future traces and annotate historical errors without silently rewriting historical measurements. This is an explanation defect, not evidence that the scorer used the trace’s weights.

### F6. Response metadata is not bound to the served generation

**High correctness risk — source-confirmed; not dynamically reproduced.** Rust obtains hits and subsequently calls a status function that reads current index/ledger metadata. The returned hits do not carry the generation handle. The possible interleaving is: hydrate from A, publish B, stamp from B, return A’s text labelled B. Context opens graph separately, and orient reads live pages after indexed retrieval. Two reviewers independently traced this chain. [Response envelope, lines 153–164][z-server] · [Stamp, lines 156–161][z-stamp] · [Context][z-rust-context]

Internal FTS/vector generation pinning is already present; the missing binding is around the whole response and its other stores.

**Repair:** return `SearchResult` with actual served generation, built-from knowledge revision, embedding identity and hits. Derive the stamp from it. Strict coherent context must pin compatible graph/page state or refuse; intentional live content should be labelled with its own version.

**Gate:** deterministic barriers that publish between hydration and stamping, between retrieval and graph opening, and before orient’s page read. The response must be coherent or explicitly report the mismatch. Do not count this as a sixth runtime reproduction.

## 4. The canonical spec is mostly the right plan

The September specification already defines the central improvements: evidence/claim identity, time, pre-query scope, direct resolution, lexical/code lanes, a ContextCompiler, a dependency compiler and FAST/LAB separation. Implementing these is execution of the existing architecture, not a new invention. The following is a source-audit map, not a fresh operational closure attestation. [Canonical specification][z-spec] · [Current status][z-log]

| Phase | Current position | Material remaining work |
|---|---|---|
| Z0 measurement snapshots | Implemented; recorded closed | Preserve leases and coherent publication; extend response binding through F6. |
| Z1a observability | Implemented; recorded closed | Correct fusion trace drift and carry actual generation/model identity. |
| Z1b sealed evaluation | Merged; **not closed** | Complete the real operational gates and valid closure verdict. |
| Z2 claims and time | Deferred beyond Markdown claims | Typed evidence locators, durable ledger, temporal history and migration checks. |
| Z3 planner and scope | Existing search/federation primitives | Unified request scope, direct resolver, typed lanes and bounded planning. |
| Z4 context compilation | Character/item budget precursor | Common evidence identity, token budgeting, provenance/freshness and sufficient evidence selection. |
| Z5 models/reranking | Baseline model exists | Full identity, backend tournament and passed promotion. |
| Z6 knowledge compiler | Safety and embedding-reuse foundations | Dependency DAG, incremental descendants, tombstones and authority conflicts. |
| Z7 contradictions/failures | Heuristics and decision primitives | Structured temporal contradiction, supersession and failure ledger. |
| Z8 community retrieval | Local graph exists | Grounded communities/summaries and global evidence resolution. |
| Z9 investigative LAB | Deferred | Bounded follow-up exploration and reconciliation with measurable utility. |
| Z10 consolidation | Notes/decisions/sessions exist | Typed admission, tiered retention and oracle-checked compaction. |
| Z11 runtime/MCP | Rust stdio delivered early | Shared daemon, granular capabilities and planned negotiated capabilities remain distinct work. |
| Z12 multimodal/multivector | Deferred research | Keep optional until a measured use case and promotion justify it. |

Implement the existing requirements for full model-space identities (§23/A61), source tombstones and derived invalidation (§7.2), and authored versus compiled Markdown. Proposed additions or refinements are the F1–F6 regression gates, complete index/cache generation publication, explicit permission-revocation races and multi-agent commit conflict outcomes. A cyclic knowledge graph is acceptable; compilation dependencies must have a defined acyclic build order.

## 5. How to maximise speed

### 5.1 Reduce work before changing languages

Rust currently scores every vector and normalises all lexical matches. The changelog’s profiled broad query matched about 170,000 of 303,000 chunks. Four-way FTS partitioning improved latency while preserving ranking, but the algorithm remains corpus-dependent. [Vector scan][z-vectors] · [Search][z-search] · [Profile][z-log]

Add the already-designed query classes: exact path/hash/symbol/error lookup; literal/code search; semantic recall; temporal/decision questions; and bounded graph exploration. An unambiguous exact resolution can avoid model inference entirely. A scoped request should reduce the searched corpus before candidate generation.

For semantic queries, test a union of bounded lexical and dense candidates. Illustrative development budgets are 100–300 per lane and 20–50 rerank candidates; these are starting experiments, not optimal settings or production promises. Use the current exhaustive engine as an oracle/reference. Returning only the best dense candidates does not itself reduce the cost of finding them: lexical pruning alone leaves the current exhaustive dense scan intact. Dense work needs narrower authorised scopes, an independently validated pruning/index strategy, or a measured compressed scan.

**Adding `LIMIT`, ANN or RRF changes the present ranking policy.** Current global min-max fusion cannot be preserved by blindly truncating lexical matches. RRF previously lost on an older Zurvan experiment; the new common-evidence architecture must earn its result against a weighted baseline. A reranker cannot recover evidence omitted from the candidate pool. SQLite supports efficient rank-ordered retrieval and trigram indexing, but trigram full-text queries below three Unicode characters need an explicit alternative. (SQLite, n.d.) [FTS5 documentation][fts5]

### 5.2 Make refresh genuinely incremental

The index builder reparses the corpus and rewrites chunks, FTS and embeddings. Reusing an old embedding by chunk ID is useful, but it is not incremental indexing. Chunk IDs contain path and heading, so a rename can invalidate vector reuse despite identical text. New texts are encoded one at a time. [Builder][z-builder] · [Chunk identity][z-chunk] · [Embedding helper][z-embed]

Separate two identities:

- **Content/vector identity:** text bytes plus complete model, tokenizer, pooling and normalisation identity.
- **Evidence occurrence identity:** source/version, location, scope, authority and temporal status.

Two occurrences may reuse one computed vector without collapsing their provenance. Batch only changed texts, bucket by length, cap batch memory and checkpoint completed work. Benchmark batch sizes on the actual M2; batching may introduce small numeric differences and requires rank/quality checks.

Start with the least complex incremental build-aside implementation that wins: reuse unchanged records and update affected content in an unpublished database. If database copying/rewrite remains dominant, evaluate immutable vector/FTS segments and compaction behind a manifest. Never mutate a published SQLite inode in place to obtain a fast update.

### 5.3 Publish a complete generation, including caches

The `.zvec` cache is tied to index generation; a missing sidecar can be built synchronously by the first reader. The repository reports roughly 490 MB and about 9 seconds to construct it at the current corpus. Startup warm-up helps, but does not make this cost disappear after every new publication. [Vector cache][z-vectors] · [Operations][z-mcp-doc]

Build and validate search, vector cache, counts, model identity and source revision before publication. Atomically switch a small generation manifest to the complete bundle. Record readiness and retain the old generation under leases. Persist immutable identities at publish time rather than rescanning large stores for each snapshot.

### 5.4 Shorten mutation critical sections

Managed operations perform full authoritative-file metadata walks around mutations. These are metadata scans, not full content hashing. Extraction also enters an eager ACTIVE operation before the model call; snapshot capture can wait for that operation for up to 300 seconds per attempt. [Revision operations][z-revision] · [Manifest][z-manifest] · [Extraction][z-extract] · [Snapshot barrier][z-build-snapshot]

Prepare expensive extraction against frozen input outside the commit section. Enter a short operation that verifies expected revision/input hashes, publishes the prepared changes and advances one logical revision. Conflicts must retry or rebase explicitly. Do not trade long locks for unchecked stale writes. A revision compare-and-swap does not make multiple file replacements atomic: retain a recoverable journal or staged-generation protocol, stable operation IDs and crash checks at each durable boundary.

A managed mutation journal and per-file identities can avoid normal full-tree walks. External editor events remain hints: perform reconciliation after restart, watcher overflow and suspected drift. The existing path/size/mtime/inode manifest cannot prove content equality after a same-size, preserved-mtime rewrite; authoritative publication should use content hashes where content identity matters.

### 5.5 Optimise agent throughput and freshness

Add separate bounded queues for reads, writes and model/build jobs, with deadlines, cancellation and fair admission. Interactive reads should not be starved by embedding batches. Measure 1, 4 and 16 callers, queue wait, tail latency and time-to-searchable-revision. A write acknowledgement is not a read-your-writes guarantee.

A small shared local daemon can amortise models and caches across clients. A persistent Python worker may reduce bridge startup, but measure it before adding stateful lifecycle complexity. Preserve operation isolation, idempotency, cancellation and crash recovery. Neither needs a wholesale rewrite of the revision ledger. [Bridge implementation][z-bridge] · [Server dispatch][z-server-full] · [Canonical daemon design][z-spec]

## 6. Storage that can grow without losing meaning

### Capacity arithmetic

The table is derived arithmetic, **not a benchmark**. Vector payload is `chunks × dimensions × bytes/value`; binary is a theoretical one-bit payload. Actual compression formats require metadata/calibration and may alter quality.

| Chunks | 384d float32 | 384d float16 | 384d int8 | 384d binary payload | Current `.zvec` layout |
|---:|---:|---:|---:|---:|---:|
| 303,080 | 0.466 GB | 0.233 GB | 0.116 GB | 0.0145 GB | 0.487 GB |
| 1 million | 1.536 GB | 0.768 GB | 0.384 GB | 0.048 GB | 1.608 GB |
| 10 million | 15.36 GB | 7.68 GB | 3.84 GB | 0.48 GB | 16.08 GB |

Decimal GB. Current sidecar formula is `128 + N × (384 × 4 + 64-byte ID + 8-byte row ID)`. It excludes text, SQLite/FTS, graph, models and backups. SQLite also stores vector BLOBs; retaining two caches and building a replacement index increases peak allocation further. [Layout and retention][z-vectors]

At 10 million chunks, the current sidecar exceeds the entire 8 GB machine. Memory mapping does not make exhaustive reads free: each query touches vector pages. Apple documents that clean file-backed pages can be resident without counting towards the same physical-footprint figure as dirty/compressed memory. Report RSS, mapped residency, faults, swap, dirty footprint and system pressure separately. (Apple, 2022) [Memory documentation][apple-memory]

### Recommended storage roles

| Layer | Authority and responsibility | Design constraint |
|---|---|---|
| Immutable evidence objects | Original source bytes and versions | Content digest, occurrence identity, scope and retention policy. |
| Structured claim/event ledger | Accepted observations, assertions, decisions, revisions and retractions | Explicit evidence links, authority, valid time and recorded time; no silent destructive overwrites. |
| Authored Markdown | Human-authored knowledge where declared authoritative | Changes become versioned inputs; compiler does not overwrite them. |
| Compiled Markdown | Human-readable projections and summaries | Rebuildable, source-linked and visibly generated. |
| Search/vector/graph stores | Derived retrieval acceleration | Disposable/rebuildable under a coherent generation manifest. |
| Context manifests | What a specific request received and why | Exact revision, spans, scope decision and policy/model identities, within privacy settings. |

This follows the canonical spec’s single-authority-per-artifact principle. “Everything is Markdown” would make structured conflict/time semantics awkward; “everything is a database row” would sacrifice inspectable authored material. The authority must be explicit for each artifact. [Canonical spec][z-spec]

For scale, compare scoped exact scan, compressed exact scan and embedded ANN. USearch documents mmap serving; FAISS provides several compression/index families with different memory and deletion tradeoffs. sqlite-vec’s current release page includes ANN in 0.1.10 alpha, while Zurvan pins 0.1.6. Treat that prerelease as an isolated experiment, not a mature drop-in upgrade. (Unum, n.d.; Meta, n.d.; Garcia, n.d.) [USearch][usearch] · [FAISS][faiss] · [sqlite-vec releases][sqlite-vec]

Measure ANN under restrictive scopes, deletes and temporal filters. Global top-K followed by permission filtering can both lose useful recall and expose disallowed material to intermediate processing. The same allowed evidence set must constrain every lane. Separate model-weight quantisation from vector compression: they reduce different costs and need separate quality measurements.

Add disk admission and lease-aware collection. Hardlinks and filesystem clones reduce initial copying but can retain old blocks across generations. A live lease must not be deleted to satisfy a capacity target. Include backup/restore, interrupted builds, concurrent compaction and retained snapshots in peak-disk tests.

## 7. Accuracy: evidence first, models second

### Improve the information presented to the model

Current chunks use a 1,000-character bound while MiniLM truncates at 256 wordpieces. Characters are not tokens, especially for code or mixed-script text. Heading detection treats lines starting `#` as headings even inside fenced code. Context output uses character/item budgets rather than the target reasoner’s tokenizer. [Chunker][z-chunk] · [Context exporter][z-context] · [MiniLM card][minilm]

Use syntax-aware document boundaries and stable source spans. Count tokens before embedding truncation, record tail loss and tune overlap for actual source types. For code, preserve paths, symbols and enclosing definitions; language parsers are an optional measured extension, not an immediate requirement for every file. For Persian/English material, test script, identifiers and mixed-language questions explicitly.

Context compilation should select complementary evidence, limit redundant sources, carry contradictory/current/historical status, and reserve tokens for instructions and the requested output. A relevance score is not evidence confidence. Under the current formula, zero cosine becomes semantic score 0.5; with zero lexical score, fusion is 0.4, above the 0.2 inclusion floor. That floor cannot establish answerability. [Scoring][z-vectors]

Model claims should distinguish observation, user assertion, hypothesis, preference, accepted decision and failed experiment. Record valid time and recorded time, evidence support, supersession/retraction and authority basis. Repeated summaries must not become independent corroboration. Report “insufficient evidence” or unresolved disagreement when appropriate.

### A current, deliberately small model tournament

Keep MiniLM as the measured reference. Model cards establish available capabilities; they do not establish a winner for Zurvan or Persian retrieval. Candidates require separate indexes and pinned artifacts. The current Rust embedder is a MiniLM/BERT implementation; a different model is not merely an environment-variable swap. [Rust embedder][z-rust-embed]

| Candidate | Why test it | Important constraint |
|---|---|---|
| Existing all-MiniLM-L6-v2 | Small incumbent and parity reference | 384 dimensions; 256-wordpiece limit. |
| Google EmbeddingGemma 2 | Released 6 October 2026; text-only loading uses the 270M text component | 8K context, 768d with smaller supported output sizes. Use documented task prefixes and float32/bfloat16; the card warns against float16 inference. Gemma terms apply. |
| Jina embeddings v5 text nano | 2026 small multilingual challenger: 239M, 8K, 768d | Task adapters and representation matter; model card is CC-BY-NC-4.0, so commercial use needs appropriate licensing. |
| Qwen3-Embedding-0.6B | Established multilingual challenger with configurable dimensions | A 2025 model, not a new 2026 release. More parameters; benchmark resource cost and task instructions. |

Sources: Sentence Transformers (n.d.); Google (2026); Jina AI (2026); Qwen (2025). [MiniLM][minilm] · [EmbeddingGemma 2][gemma2] · [Jina nano][jina-nano] · [Qwen3][qwen3]

Do not confuse float16 **stored vectors** in the sizing table with float16 **EmbeddingGemma 2 inference**, which its model card explicitly warns against. After architectural improvements, run embedding-only comparisons, then bounded reranker comparisons. Keep chunking, corpus, query set, token budget and cache state controlled; otherwise a larger model receives credit for an unrelated pipeline change.

Sentence Transformers exposes explicit model revision and separate query/document encoding interfaces. Use those contracts, pin all artifacts and disable implicit remote code execution unless a reviewed model genuinely requires it. Query and document preprocessing must belong to the recorded embedding identity. (Sentence Transformers, n.d.) [API documentation][st-api]

## 8. A useful “language” for agents

Build a small typed memory protocol over the existing MCP/CLI surface. A new programming language, parser and ecosystem are unnecessary until users demonstrate that JSON/tool schemas cannot express their tasks.

| Operation | Agent intent | Required outcome |
|---|---|---|
| `resolve` | Find an exact path, symbol, source or claim | Stable identity or explicit ambiguity/missing result. |
| `recall` | Retrieve evidence under scope/time/budget | Coherent evidence pack, actual served revision and insufficiency status. |
| `explain` | Reproduce how supplied context was selected | Versioned policy, evidence dependencies and privacy-respecting trace. |
| `propose` | Suggest a durable memory change | Typed candidate with evidence and declared authority; no automatic acceptance. |
| `commit` | Apply an authorised proposal | Expected-revision check, idempotency and explicit conflict result. |
| `retract` | Correct support or withdraw a claim | History plus derived invalidation, subject to the caller’s authority. |

For example, an illustrative request could be:

```json
{
  "operation": "recall",
  "query": "Which storage decision is current, and what superseded it?",
  "requested_projects": ["zurvan"],
  "valid_at": "2026-10-22T00:00:00+11:00",
  "min_revision": 900,
  "consistency": "coherent",
  "evidence_token_budget": 3000
}
```

The server derives caller identity and allowed scope from trusted session/capability state. The JSON request cannot grant itself access. The example revision is illustrative, not a statement about the live store. `valid_at` requests event/valid time; `min_revision` is only a freshness floor and permits any sufficiently fresh coherent generation. Exact replay uses the returned immutable snapshot/generation handle and recorded selection/model outputs, subject to current authority.

The response should include the actual served revision, component generations, evidence spans/content hashes, support and temporal status, model/policy identity, and explicit degraded or insufficient-evidence conditions. Writes additionally need operation ID, expected revision, accepted/rejected/conflicted state and the revision actually committed.

This gives parallel agents a shared contract. A stale agent cannot quietly replace a newer accepted decision. The durable commit must coordinate authorisation, expected-revision validation, the scoped idempotency key and recorded outcome. Permit at most one committed logical mutation per key; retries return that outcome, and a different payload reusing the key fails. Subsequent legitimate retraction may change current knowledge while operation history remains. Contradictory proposals remain visible rather than being collapsed by last-writer-wins. Git worktrees can help isolate edits, but semantic conflicts still require this higher-level policy.

## 9. Security and correction are part of memory quality

Current read-path allowlists and publication guards are useful. The previously fixed path/case/symlink problems must not be re-reported as present defects. Boundary v1’s recorded 10/10 recall and 0/200 operational false positives are finite measurements on their stated dataset, including the easier new-version case. They are not a complete prompt-injection or memory-poisoning guarantee. R3 tool-call provenance is still explicitly unbuilt. [Standing invariants][z-agents] · [Latest security fixes][z-log]

The 2026 MCP authorisation specification distinguishes HTTP authorisation from stdio credential handling. A future daemon or remote transport needs real capability checks; project labels and local paths are not user authentication. Bind scope before search and apply a defensive check again before context leaves the server. (Model Context Protocol, 2026) [Authorisation][mcp-auth]

The proposed memory lifecycle should separate source content from operational instructions. Untrusted retrieved text must not gain permission to modify capabilities or accepted instructions merely because it appears in a quote. Typed proposals, explicit authority and dependency-preserving consolidation provide enforceable boundaries; an LLM saying content is safe is not a proof.

MemForm’s October 2026 preprint examines poisoning through content, evidence, scope, selection and consolidation path. MemSecBench’s July 2026 preprint follows persistence through action and repair. Use these as test designs, not performance claims about Zurvan. Measure successful harmful writes/actions, benign utility, false refusals and repair preservation together. (Pan et al., 2026; Chen et al., 2026) [MemForm][memform] · [MemSecBench][memsec]

Correction needs four explicit semantics:

1. **Retraction:** evidence or a claim is no longer accepted as current support; preserve history where policy requires it.
2. **Access revocation:** future reads must recheck current authority, including cached/pinned requests.
3. **Derived invalidation:** dependent summaries, vectors, communities and context caches are stale or blocked until rebuilt.
4. **Physical erasure:** a separate retention action with snapshot, backup and audit-history consequences.

Do not promise that revocation erases bytes already exported to another agent or third-party service. Historical snapshots cannot automatically bypass current access revocation. A context’s recorded authorisation decision explains the past; it does not itself authorise a new read today. Define revocation to be checked at query admission and again before final response release, with a current permission epoch in cache validity. Historical replay requires a currently authorised auditor.

Derived summaries inherit restrictions from all inputs used to create them, including information retained through paraphrase, not merely the citations a model chose to emit. Conservatively block or recompile affected summaries after revocation. Correction closure covers managed derived artifacts with complete declared generation dependencies; untracked external copies remain outside it. Race revocation against in-flight queries and every response/cache surface. An output manifest alone cannot prove forbidden evidence never entered the internal candidate pool: that stronger guarantee requires trusted enforcement, instrumentation and isolation/replay tests. Identify that trusted computing base explicitly.

OWASP’s September 2026 Agent Control Standard and October 20 Red Teaming Manual provide useful interoperability and lifecycle-testing references. They are not certifications of this design. (OWASP, 2026a, 2026b) [ACS][owasp-acs] · [Red teaming manual][owasp-red]

## 10. Industry gap hunt

### What is already occupied

This is a primary-documentation comparison as of the audit date. No competitor was run against Zurvan, and absence from a documentation page is not proof that a feature is absent from a product.

| Project | Relevant documented capability | Implication for Zurvan |
|---|---|---|
| Basic Memory | Markdown authority, derived graph/search database and file synchronisation | Local Markdown plus MCP is established. [Source][basic] |
| QMD | Local lexical/vector retrieval, query expansion, fusion and reranking; a no-rerank route | Compete on measured task value and evidence semantics, not “hybrid search” alone. [Source][qmd] |
| Zep / Graphiti | Temporal facts, invalidation and episodic provenance | Time-aware memory has strong prior art. [Source][graphiti] |
| Letta MemFS | Git-backed Markdown, local mode and worktrees for concurrent memory work | Files plus concurrent agents are not new by themselves. [Source][letta] |
| Hindsight | Observations with supporting quotes/proof counts, evolving memory and background mental models | Evidence-backed memory is an occupied category; an independent verifier must add a precise guarantee. [Source][hindsight] |
| Cognee | Caller-scoped provenance and dataset grants | Scoped lineage is also established. [Source][cognee] |
| OpenViking | Filesystem navigation, project/subtree retrieval and L0/L1/L2 context layers | Hierarchical context loading is useful prior art. [Source][openviking] |
| Memvid | January 2026 Rust rewrite, single-file storage, WAL, atomic commits and checksums | Language choice and crash-safe storage alone are not differentiation. [Source][memvid] |
| Mem0 | Current OSS migration to ADD-only extraction and hybrid/entity retrieval; graph moves to Platform | Compare current versions and migration behaviour, not old feature summaries. [Source][mem0] |
| Microsoft GraphRAG | Local/global/DRIFT approaches and update indexing modes | Do not claim it cannot update its index; reserve expensive graph exploration for demonstrated needs. [Source][graphrag] |

The gap search covered four fronts: standards/governance, demonstrated failure modes, incumbents’ documented tradeoffs and users’ operational pain. Historical QMD/OpenClaw issues requested speed/quality controls on CPU-oriented setups; both are closed and present QMD exposes no-rerank retrieval. That is evidence of demand for control, not an unfilled feature gap or proof of market size. [QMD issue][qmd-issue] · [OpenClaw issue][openclaw-issue]

### Five opportunities worth proving

**A. Independently verifiable context.** Return a compact manifest that lets a separate verifier resolve every source span, generation, model/policy identity and recorded scope decision against trusted inputs. Replay frozen selection/model outputs against the same data/policy identities, subject to current authority; deny replay after incompatible revocation. Re-running nondeterministic model expansion is a new computation. A receipt verifies the declared bundle’s byte/identity and policy consistency; trusted enforcement or replay is needed to establish execution and scope guarantees. It cannot prove a proposition true. Kill the claim if mixed generations or unresolved spans survive.

**B. Transactional multi-agent memory.** Let agents submit typed, evidence-backed proposals with expected revision and scoped idempotency keys. Reorder, duplicate and crash concurrent operations; each key permits at most one committed logical mutation, retries return the recorded outcome, and stale contradictions yield explicit conflicts. Accepted operations remain in history even when their effects are later retracted. Kill the claim if a stale update silently replaces accepted knowledge.

**C. Correction that reaches every managed derived artifact.** Track complete generation dependencies through claims, summaries, graph, embeddings and caches. Retraction or revocation should stop invalid current support reappearing after rebuild, restart or a stale client’s retry. Kill the claim if deleting the obvious memory entry leaves the same unsupported conclusion available through a managed summary.

**D. Memory admission and repair under adversarial pressure.** Evaluate the full path from untrusted input to durable memory, consolidation, later action and selective repair. Preserve legitimate neighbouring knowledge. Kill the claim if safety is achieved only by rejecting all useful writes or by matching a few known attack strings.

**E. Useful memory on a modest personal machine.** Demonstrate better agent task outcomes per token/second/GB on the M2/8 GB target. Compare against QMD’s fast and reranked modes and another lightweight memory baseline with matched corpus and budgets. Kill the performance claim if the advantage vanishes after equalising cache state, candidate count, model startup and correctness.

The strongest combination is A+B, supported by C+D and proved operationally by E. It is a testable product thesis, not a certified unique invention. A plausible first audience is privacy-sensitive coding/research agents on personal machines. Evidence of successful repeat use should precede claims of a large addressable market.

## 11. Evaluation that can support an accuracy claim

Close Z1b’s real operational requirements before ranking/model promotions. Keep development iteration separate from sealed promotion. Do not inspect protected item text, repeatedly tune on the retired 50 items or turn an inaccessible live gate into a passing result. [Current Z1b status][z-log]

### Separate the metrics

| Layer | Measure | Failure it exposes |
|---|---|---|
| Coverage | Whether the right immutable source is present and current | No retriever can find absent or deleted evidence. |
| Candidate retrieval | Evidence recall at fixed candidate budget | Missing evidence before reranking. |
| Ranking | MRR/nDCG and evidence precision by query class | Relevant evidence buried below distractors. |
| Context compilation | Span validity, coverage, redundancy, temporal correctness and token use | Good retrieval becoming misleading context. |
| Answer/task | Supported claims, citation entailment, task completion and calibrated abstention | Fluent but unsupported answers. |
| Memory lifecycle | Conflict preservation, update correctness, poisoned write/action rate and repair retention | Long-term corruption that one-turn QA misses. |
| Isolation | Wrong-scope candidates/results/traces and generation mismatches | Access/provenance failures hidden by aggregate relevance. |

LongMemEval offers useful older foundations for information extraction, multi-session reasoning, temporal reasoning, updates and abstention. It is a 2024/2025 benchmark, not a new 2026 release, and should complement project-specific tasks. (Wu et al., 2025) [Paper][longmemeval]

Use development strata covering exact paths/symbols, literal errors, conceptual paraphrases, English, Persian, mixed script, current/historical decisions, contradictions, no-answer, multi-hop and duplicate sources. Split by source/session groups to reduce near-duplicate leakage. Pin each candidate’s model identity and embedding tokenizer; hold corpus revision, queries, target reasoner tokenizer and comparison budgets constant. For quality comparisons, use paired query-level uncertainty estimates and predeclared non-inferiority margins; small strata do not justify precise broad percentages.

### Performance and durability matrix

| Dimension | Required runs |
|---|---|
| Corpus size | Current approximately 0.3M, then 1M; 10M as a separate storage/scaling experiment. Synthetic data supports scaling, not relevance claims. |
| Cache state | Cold process, cold filesystem cache, first query after publish, warm steady state. |
| Concurrency | 1/4/16 clients; mixed reads/writes/refresh; queue wait and fairness. |
| Update size | 1, 100 and 10,000 changed pages; rename-only and deletion-only cases. |
| Metrics | p50/p95/p99, throughput, embedding/FTS/vector/rerank/context stages, write acknowledgement, searchable freshness, snapshot wait. |
| Resources | Peak RSS, physical footprint, mapped residency, page faults, swap, peak disk and retained generations. |
| Faults | Crash at each publish boundary, corrupt model/cache, missing store, low disk, stale lease, interrupted compaction and restore. |

The spec’s approximately 1.2 GB steady-state FAST RSS is a **target**, not a measured current limit or a guarantee for all future models. Establish a matched baseline and hard budget before running candidates. Keep LAB dependencies optional. [FAST/LAB contract][z-spec]

A candidate should improve a predeclared resource metric while meeting quality non-inferiority, or improve quality within the agreed resource budget. Set those margins before inspecting candidate results. Record failures and invalidated runs. A result on a new corpus cannot be presented as a pure speedup over a different old corpus.

## 12. Implementation order and stop conditions

These are dependency-ordered work packages, not calendar estimates or authorisation to change production.

| Order | Concrete deliverable | Completion evidence |
|---|---|---|
| A — restore trust | F1–F5 fixes; F6 generation-bound envelope; full embedding identity; current status index | Each defect fails before its fix and passes after; deterministic generation interleavings; no live data used as fixtures. |
| B — establish promotion authority | Finish Z1b’s operational closure on the actual deployment | Fresh suite, correct seal state, required gates and valid verdict; every failure remains visible. |
| C — thin vertical evidence slice | Z2 claim/evidence identity and time → Z3 exact/scoped planning → Z4 evidence pack | One end-to-end task resolves, retrieves, cites, updates and retracts correctly across revisions. |
| D — make updates cheap | Batched content-addressed embeddings, incremental compilation, prebuilt generation bundle, short commits | Unchanged text is not re-embedded; rename keeps reuse; crash yields old or complete new state; measured refresh/freshness improvement. |
| E — optimise retrieval/models | Bounded candidate policies, tokenizer-aware chunking, model/reranker/compression/ANN tournament | Passed predeclared quality/resource comparison on trustworthy instruments. |
| F — prove agent memory | Typed proposals, conflict handling, correction propagation and shared runtime | Concurrent retry/crash schedules, poison-to-repair evaluation and useful task gains on the target machine. |
| G — expand only with evidence | Community/global/DRIFT and optional multimodal LAB | A real query class improves enough to pay for latency, storage and operational complexity. |

Useful work in A and development experiments can proceed while operator-dependent B remains open; promotion must not bypass B. Preserve the existing canonical phase mechanics rather than declaring all of Z2–Z6 closed after a thin slice.

### Rewrite decision

| Component | Recommendation | What would change the decision |
|---|---|---|
| Rust read/MCP engine | Keep and extend | A measured bottleneck or contract failure that cannot be solved within it. |
| Python ingestion/evaluation/control | Keep initially | Profiles show interpreter/process overhead dominates end-to-end cost after batching and bounded work. |
| SQLite/Markdown authority model | Keep with explicit roles | Demonstrated durability/concurrency/scale limits beyond local embedded design requirements. |
| Model backend | Add a narrow interface and benchmark candidates | A model/backend wins quality and resource gates. |
| New agent language | Begin with typed JSON/tool contracts | Repeated user tasks need expressiveness that the existing schemas cannot reasonably provide. |

A rewrite is justified by a named bottleneck, a small replacement boundary, compatibility evidence, migration/rollback and a measured benefit. “Rust everywhere”, a new database or a bigger model are not success criteria.

## 13. Engineering judgement and ambition

The following ratings are qualitative judgements, not benchmark measurements or an average project score.

| Axis | Present judgement | Artifact that would materially improve it |
|---|---|---|
| Runtime foundation | Strong for current measured reads | Reproducible cold/warm/concurrent benchmark and complete generation publication. |
| Evidence integrity | Needs urgent repair | Five fixes, generation binding and typed evidence admission. |
| Scale readiness | Promising foundation; unproven at millions | Incremental update receipts and scoped/compressed retrieval under a fixed budget. |
| Agent memory semantics | Clear spec; much still planned | Revision-checked proposals, temporal claims and correction closure. |
| Differentiation | Credible hypothesis; crowded ingredients | Independent verifier plus adversarial correction demo and fair competitor task comparison. |

The ambitious version is concrete: on the target personal machine, multiple agents read and propose knowledge concurrently; every returned evidence pack resolves to its actual permitted revision; unsupported and conflicting claims remain explicit; correction propagates through all managed derived surfaces with complete dependencies; and a reproducible benchmark demonstrates useful task outcomes at one million chunks without breaking the declared FAST budget.

It is falsified by silent evidence loss, mixed-revision attribution, stale overwrite, resurfacing retracted support or performance gains that depend on weakening quality. Ten million chunks should be a separate, honestly measured capacity milestone, not a claim inferred from a fast 303k-chunk warm query.

### Deliberate exclusions from the first implementation

Do not begin with a whole-project rewrite, a bespoke query language, always-on graph/HyDE/model expansion, a large default reranker, or a distributed storage deployment. Each adds cost before the present integrity and refresh problems are solved. Revisit a component when a concrete benchmark or user workflow justifies it.

## Appendix: twelve invention checks

These are proposed experiments on the combined contract. Snapshot isolation, concurrency control, provenance and capability enforcement are established mechanisms; the contribution to prove is their useful composition.

| Check | Concrete opportunity | Falsifier or limit |
|---|---|---|
| Missing number | Publish verified-evidence coverage alongside task quality, latency and refusal. | Perfect receipt coverage with useless retrieval is not success. |
| Real failure | Exercise poisoning from memory formation through later action and selective repair. | Blocking a known string is not general protection. |
| Incumbent interface | Provide QMD/Hindsight comparison adapters and measure verification/replay cost. | Documentation gaps do not prove competitor incapability. |
| Symmetry | Retries are idempotent; independent proposals preserve state under reordering. | Conflicting proposals need explicit adjudication. |
| Challenge forces audit | A disputed claim returns source evidence and a durable resolution outcome. | Missing support must remain missing, never be fabricated. |
| Constrained adversary | Managed writes commit under authority, fail explicitly or leave detectable incomplete state. | Root/external filesystem mutation requires separate detection and trust assumptions. |
| Auditable absence | Report required evidence/checks that were unavailable or skipped. | Missing receipts mean unverified, not automatically malicious. |
| Missing voice | Preserve counterevidence and dissent beside an accepted decision. | Duplicated sources are not independent corroboration. |
| Clock witness | Record valid time, observed time, revision and permission epoch. | A local timestamp does not prove an external event’s time. |
| Remove privilege | Verify exported evidence without writer privileges or model credentials. | Hashes do not establish truth or completeness. |
| Adversarial authorised user | Test selective retention, retries and summaries by an agent with valid write access. | Report benign utility and failures; do not rerun until success. |
| Prose as protocol | Turn decisions, support, objections and supersession into reviewable typed records. | Formal storage does not make interpretation infallible. |

The first ambitious artifact should be a small independently executable memory conformance pack: coherent reads during writes, duplicate/reordered/conflicting proposals, revocation during query, summary contamination, correction across caches, crash/restart and offline receipt verification. Freeze expected outcomes before implementation claims.

## References and source index

All public sources below were viewed on 22 October 2026. Mutable documentation should be pinned to release/model identities before implementation comparisons. Primary documentation establishes documented features, not independently verified comparative performance. New arXiv studies are preprints.

### Project evidence

- Project Zurvan (2026a), *Canonical upgrade specification*, 18 September, supplied attachment and [repository copy][z-spec].
- Project Zurvan (2026b), *CHANGELOG*, current entries through 20 October, [pinned source][z-log].
- Project Zurvan (2026c), *AGENTS*, [pinned source][z-agents].
- Project Zurvan (2026d), *Rust MCP operations*, [pinned source][z-mcp-doc].
- Audit evidence (2026), *Source manifest, Git blob verification and isolated correctness probes*, accompanying `2026-10-22-zurvan-audit-evidence.zip`. Successful reproduction checks are evidence of defects, not passing production tests.

### Runtime and model documentation

- Apple (2022), *Profile and optimize your game’s memory*, WWDC22, [URL][apple-memory].
- Garcia, A. (n.d.), *sqlite-vec releases*, [URL][sqlite-vec].
- Google (2026), *EmbeddingGemma 2 model card*, [URL][gemma2]; *EmbeddingGemma 2 announcement*, 6 October, [URL][gemma2-launch].
- Jina AI (2026), *jina-embeddings-v5-text-nano model card*, [URL][jina-nano].
- Meta (n.d.), *Guidelines to choose a Faiss index*, [URL][faiss].
- Qwen (2025), *Qwen3-Embedding-0.6B model card*, [URL][qwen3].
- Sentence Transformers (n.d.a), *all-MiniLM-L6-v2 model card*, [URL][minilm].
- Sentence Transformers (n.d.b), *SentenceTransformer API*, [URL][st-api].
- SQLite (n.d.), *FTS5 extension*, [URL][fts5].
- Unum (n.d.), *USearch*, [URL][usearch].

### Industry, evaluation and security

- Basic Memory (n.d.), *Technical information*, [URL][basic].
- Chen et al. (2026), *MemSecBench: Tracking Agent Memory Poisoning from Persistence to Consequence and Repair*, arXiv preprint, 29 July, [URL][memsec].
- Cognee (n.d.), *Schema provenance*, [URL][cognee].
- Letta (n.d.), *MemFS*, [URL][letta].
- Mem0 (n.d.), *Open source: migrating to the new memory algorithm*, [URL][mem0].
- Memvid (2026), *Changelog*, [URL][memvid].
- Microsoft (n.d.), *GraphRAG CLI reference*, [URL][graphrag].
- Model Context Protocol (2026), *Authorisation*, specification 2026-07-28, [URL][mcp-auth].
- okabadayi (2026), *QMD issue 264*, 26 February, [URL][qmd-issue]. User report, not a controlled benchmark.
- OpenClaw contributors (2026), *Issue 9967*, 5 February, [URL][openclaw-issue]. User report, not a controlled benchmark.
- OWASP (2026a), *Agent Control Standard*, 1 September, [URL][owasp-acs].
- OWASP (2026b), *GenAI Red Teaming Manual v1.0*, 20 October, [URL][owasp-red].
- Pan et al. (2026), *MemForm: Benchmarking Memory Formation Poisoning in LLM Agents*, arXiv preprint, 10 October, [URL][memform].
- QMD contributors (n.d.), *QMD README*, [URL][qmd].
- Vectorize (n.d.), *Hindsight README*, [URL][hindsight].
- Volcengine (n.d.), *OpenViking README*, [URL][openviking].
- Wu et al. (2025), *LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory*, revised March 2025; first submitted October 2024, [URL][longmemeval].
- Zep (n.d.), *Graphiti overview*, [URL][graphiti].

[z-log]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/CHANGELOG.md#L3-L43
[z-agents]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/AGENTS.md
[z-spec]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/docs/superpowers/specs/2026-09-18-zurvan-2-canonical-upgrade-spec.md
[z-mcp-doc]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/docs/mcp/rust-server.md
[z-tools]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/mcp_tools.py
[z-context]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/context_export.py
[z-context-routing]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/context_export.py#L19-L30
[z-federation]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/cross_project_search.py#L35-L55
[z-extract]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/extract.py
[z-extract-write]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/extract.py#L98-L108
[z-validator]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/validate_extraction.py#L19-L37
[z-memory]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/memory.py#L259-L300
[z-embed]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/embed.py#L35-L63
[z-hybrid-query]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/hybrid_search.py#L97-L105
[z-fusion-score]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/hybrid_search.py#L245-L260
[z-builder]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/rebuild_search_index.py#L283-L374
[z-chunk]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/chunk.py
[z-revision]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/knowledge_revision.py
[z-manifest]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/knowledge_manifest.py
[z-snapshot]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/measurement_snapshot.py
[z-build-snapshot]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/build_snapshot.py#L204-L246
[z-search]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/search.rs
[z-vectors]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/vectors.rs
[z-rust-embed]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/embed.rs
[z-server]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/server.rs#L153-L164
[z-server-full]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/server.rs
[z-stamp]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/status.rs#L156-L161
[z-rust-context]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/context.rs
[z-graph-context]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/scripts/graph_context.py#L9-L16
[z-bridge]: https://github.com/Raoof128/Project-Zurvan/blob/e65d4bbf093c3a33ce920edd03bd79ad6c688f62/rust/zurvan-mcp/src/bridge.rs
[apple-memory]: https://developer.apple.com/videos/play/wwdc2022/10106/
[fts5]: https://sqlite.org/fts5.html
[sqlite-vec]: https://github.com/asg017/sqlite-vec/releases
[usearch]: https://github.com/unum-cloud/USearch
[faiss]: https://github.com/facebookresearch/faiss/wiki/Guidelines-to-choose-an-index
[minilm]: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
[st-api]: https://www.sbert.net/docs/package_reference/sentence_transformer/SentenceTransformer.html
[gemma2]: https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2
[gemma2-launch]: https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/
[jina-nano]: https://huggingface.co/jinaai/jina-embeddings-v5-text-nano
[qwen3]: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
[basic]: https://docs.basicmemory.com/reference/technical-information
[qmd]: https://github.com/tobi/qmd
[graphiti]: https://help.getzep.com/graphiti/getting-started/overview
[letta]: https://docs.letta.com/concepts/memfs
[hindsight]: https://github.com/vectorize-io/hindsight
[cognee]: https://docs.cognee.ai/api-reference/schema/schema-provenance
[openviking]: https://github.com/volcengine/OpenViking
[memvid]: https://github.com/memvid/memvid/blob/main/CHANGELOG.md
[mem0]: https://docs.mem0.ai/migration/oss-v2-to-v3
[graphrag]: https://microsoft.github.io/graphrag/cli/
[mcp-auth]: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
[owasp-acs]: https://genai.owasp.org/resource/agent-control-standard-acs/
[owasp-red]: https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/
[memform]: https://arxiv.org/abs/2610.14431
[memsec]: https://arxiv.org/abs/2607.27080
[longmemeval]: https://arxiv.org/abs/2410.10813
[qmd-issue]: https://github.com/tobi/qmd/issues/264
[openclaw-issue]: https://github.com/openclaw/openclaw/issues/9967
