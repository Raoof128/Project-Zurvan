# Retrieval core: storage, contextual embeddings, and reranking

**Date:** 2026-09-14 (Australia/Sydney)
**Status:** Approved for planning
**Scope:** Sub-project A of four. B (capture workflow), C (agent ergonomics) and
D (provenance integrity, `2026-09-14-provenance-integrity-design.md`) get their
own cycles; C depends on this landing first, D is independent of all three.

---

## 1. Why

The corpus grew 4.6x on 2026-09-14 (43,528 -> 199,269 chunks). Retrieval quality
held — a good result — but the measured baseline leaves real headroom, and the
storage layer is hand-rolled:

| Dimension | Today |
| --- | --- |
| Top-1 / Top-3 / MRR | **68% / 72% / 0.71** (50-query gold set) |
| Failing queries | **14 of 50** |
| Search latency | **3.0 s** mean, warm |
| Peak RSS per query | ~894 MB |
| Index on disk | **2.11 GB** + a 306 MB `.vectors.npy` sidecar |
| Vector storage | JSON **text** in SQLite, parsed per query |
| Reranking | none |

Vectors are stored as JSON and parsed on every search. `scripts/vector_cache.py`
exists only to move that parse off the query path — it is a workaround for the
storage format, not a feature. `sqlite-vec` removes the need for it entirely.

## 2. Constraints

1. **Local-first is non-negotiable.** No network at query time, no API key.
   Chosen explicitly over a one-off LLM contextualisation pass (~$30-40, hours,
   and it would break "nothing leaves the machine" once).
2. **Markdown stays the source of truth.** `wiki/` is durable; `data/*.sqlite`
   is rebuildable local state. No change makes the wiki depend on the index.
3. **Invariant 4 applies:** any ranking or indexing change needs a documented
   before/after `eval_search` run in `CHANGELOG.md`.
4. **Frozen artifacts are untouchable:** `eval/provenance_real_*.jsonl`, their
   committed traces, and the 2C/1B metrics (86%/79%).
5. **The 50-query gold set is the only honest instrument.** At n=50 one query is
   2%, so every gate names the queries that moved, never just a delta.

## 3. The governing lesson

CHANGELOG 13h is the reason this is five experiments rather than one refactor.
Three separately sensible improvements — Reciprocal Rank Fusion, `bge-small`,
`bge-base` — each measured as a **loss** on this corpus, and only isolated
measurement revealed it. MTEB is a prior, not an oracle.

Two corollaries, both load-bearing:

- **Never combine two changes in one measurement.** If the result is worse you
  cannot attribute it.
- **Aggregate metrics are not proof of invariance.** Top-1/Top-3/MRR can hold
  while individual rankings churn underneath. A storage swap must be gated on
  per-query ordering, not on summary numbers.

## 4. The five experiments

```
Step 0   failure anatomy            -> rerankability rate (decides 2B)
  |
Phase 1  sqlite-vec storage         -> Gate A: ranking INVARIANT + faster
  |
Phase 1B KNN candidate policy       -> Gate A2: latency vs recall (may change ranking)
  |
Phase 2A contextual embedding input -> Gate B: Top-3 > 72%
  |
Phase 2B cross-encoder rerank       -> Gate C: Top-3 improvement vs Step 0's ceiling
```

Each is independently revertible. A rejected phase does not block the next.

---

### Step 0 — Failure anatomy (no production changes)

For each of the 14 failing queries, record where the expected document first
appears:

| Column | Question it answers |
| --- | --- |
| `fts_rank` | Did lexical retrieval find it? |
| `semantic_rank` | Did vector retrieval find it? |
| `fused_rank` | Did fusion demote it? |
| `in_top50` | Could a reranker possibly rescue it? |

Classify every failure as exactly one of:

`candidate_miss` · `fusion_ranking_miss` · `chunking_miss` · `query_mismatch` ·
`gold_annotation_issue`

**Output: a rerankability rate** = (failures whose expected document is in the
top-50) / 14. Decision rule, fixed in advance so the result cannot be
rationalised after the fact:

- **>= 8/14 rerankable** -> Phase 2B is justified; Step 0 also states the ceiling
  (best case: those failures become passes).
- **<= 4/14 rerankable** -> **reranking cannot fix these by construction**. Drop
  2B for now; recall and chunk representation (2A) come first.
- **5-7/14** -> build 2B only after 2A, and re-run Step 0 against the new index
  first, since 2A changes what is retrievable.

This is the step that can redirect the project, so it runs before any dependency
is installed.

---

### Phase 1 — `sqlite-vec` storage (ranking-invariant)

Replace JSON-text vectors, `scripts/vector_cache.py`, and the `.npy` sidecar
with a `vec0` table holding float32 vectors.

**Use `vec_distance_cosine()` as a scalar function over a full scan — NOT the
`vec0` KNN path.** This is the subtlest decision in the design:

Today every chunk receives a fused score, so a chunk with a strong keyword score
but a mediocre semantic score can still reach the top-10
(`fts_scores.get(chunk_id, 0.0)` is applied corpus-wide). Semantic KNN with
`k = N` makes chunks outside the semantic top-K invisible **even on a perfect
keyword match**. That is a candidate-policy change disguised as a storage
migration, and it would break invariance while appearing to be a pure swap.
Full-scan `vec_distance_cosine` is still exact and still SIMD-accelerated.

**Scale conversion must be explicit.** `sqlite-vec` returns cosine *distance*;
`_fuse` consumes a similarity mapped through `(cos + 1) / 2`. Convert
deliberately (`cos = 1 - distance`), and assert the conversion in a test. A
silent scale error here looks exactly like a ranking regression.

**Unchanged in this phase:** chunking, embedding model, query embedding, FTS,
the 0.2/0.8 fusion weights, candidate policy, result budgeting.

**Gate A — all three groups must hold:**

*Correctness*
- All 50 gold queries compared before/after
- Final hybrid top-N ordering **identical**
- Semantic candidate ordering **identical**
- Scores equal within 1e-6 (SIMD and NumPy float32 differ in the last bits;
  that is noise, not a regression). Order is the contract; this is a sanity bound.
- Deterministic tie-break on `chunk_id`

*Quality*
- Top-1 68%, Top-3 72%, MRR 0.71 — unchanged
- **Failure set identical** (the same 14 query IDs)

*Performance*
- Warm p50 < 300 ms, p95 < 500 ms
- Peak RSS below 400 MB (today: ~894 MB). A full scan over a vec0
  table should not materialise the corpus in Python at all.
- No JSON vector store, no `.vectors.npy` sidecar

**If ranking differs, reject Phase 1 even if it is fast.**

**Pin the dependency.** `sqlite-vec` is pre-v1 and its repository carries
experimental ANN/IVF/DiskANN work; pin an exact version and use the `vec0` path
deliberately rather than assuming future releases preserve today's behaviour.

---

### Phase 1B — KNN candidate policy (optional, separately gated)

Only if Phase 1's full scan misses the latency target. Trades recall for speed
by restricting the semantic candidate set.

**Gate A2:** latency improvement stated against measured recall loss. Ranking
*may* change here — that is the point — so the change is reported per query, not
hidden behind aggregates. Reject if any gold query leaves the top-3 that was not
already failing.

---

### Phase 2A — Contextual embedding input

Prepend document context to the text that is **embedded**:

```
{document title}
{heading > subheading}
{chunk text}
```

**The stored Markdown chunk is untouched.** Only the embedding input changes.
This preserves citations, `chunk_id`s, displayed text, and the Markdown source of
truth — and it means the change is reversible by reindexing alone.

Deterministic: no LLM, no per-chunk generation cost. This is the cheap half of
Anthropic's contextual-retrieval technique, chosen to respect constraint 1.

**Gate B:** Top-3 > 72%, **and** an inspection of *which* queries moved. At n=50,
74% vs 72% is one query — a named query, not a trend. Regressions are listed
explicitly; a net gain that breaks two previously-passing queries is not a win.

---

### Phase 2B — Cross-encoder rerank

Retrieve a hybrid candidate pool of 30-50, rerank with a local cross-encoder,
return 10. `sentence-transformers` is already a dependency, so the marginal cost
is model weights (~80 MB for `ms-marco-MiniLM-L-6-v2`), not a new stack.

**Only built if Step 0's rerankability rate justifies it.**

**Gate C:** Top-3 improvement measured against the ceiling Step 0 predicted. If
Step 0 said 10/14 failures are rerankable and reranking recovers 2, the model or
the pool size is wrong — investigate before accepting.

---

## 5. Evidence artifacts

One frozen report per stage under `docs/evaluation/`:

```
retrieval-step0-failure-analysis-<date>.md
retrieval-sqlite-vec-<date>.md
retrieval-knn-<date>.md
retrieval-contextual-embedding-<date>.md
retrieval-reranker-<date>.md
```

Each records: commit SHA · dependency versions · index size · query count ·
latency p50/p95 · peak RSS · Top-1 / Top-3 / MRR · failing query IDs · changed
rankings · **decision: ACCEPT / REJECT**.

A rejected experiment keeps its report. The record of what was measured and
discarded is worth as much as the record of what shipped — 13h is the proof.

## 6. Testing

- Step 0 ships as a script with unit tests over synthetic rank data; the report
  itself is an artifact, not a test.
- Phase 1 needs an equivalence test in the shape of
  `test_vectorised_scoring_matches_the_reference_cosine_fusion`: assert **order**
  as the contract and scores within tolerance as a sanity bound.
- A regression test must assert the distance->similarity conversion directly, so
  a scale error fails loudly instead of looking like a quality change.
- The existing suite (653 tests) must stay green at every phase boundary.
- `check.sh` gate 6 continues to evaluate the **full** 50-query set; the
  `max_queries` liveness switch added on 2026-09-14 is never used for a gate.

## 7. Risks

| Risk | Mitigation |
| --- | --- |
| Reranking doesn't help this corpus (13h's pattern) | Step 0 predicts the ceiling before anything is installed |
| KNN silently changes candidate policy | Split into Phase 1B with its own gate; Phase 1 uses full scan |
| Cosine distance vs similarity confusion | Explicit conversion + a dedicated test |
| `sqlite-vec` pre-v1 API drift | Pin an exact version; use `vec0` deliberately |
| One-query noise read as a trend | Every gate names the queries that moved |
| Aggregate metrics hide ranking churn | Gate A compares per-query ordering, not summaries |

## 8. Out of scope

Sub-project B (capture workflow) · sub-project C (agent ergonomics) · LLM-based
contextual generation · query expansion · changing the embedding model
(`bge-small` and `bge-base` both measured as losses in 13h; MiniLM stays until
evidence says otherwise).
