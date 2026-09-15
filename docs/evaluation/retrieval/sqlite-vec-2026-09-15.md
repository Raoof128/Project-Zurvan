# Phase 1 — `sqlite-vec` storage swap: frozen experiment report

**Date:** 2026-09-15 (Australia/Sydney) · **Base commit:** `b962a92`
**Decision: GATE A NOT ACCEPTED** — correctness and storage passed; the
pre-registered performance thresholds were not met.

The implementation is kept. It is ranking-neutral, ~4x faster and 3.3x smaller
on disk. It simply does not reach thresholds that, as this report shows, storage
changes alone cannot reach.

## Versions and corpus

| | |
| --- | --- |
| `sqlite-vec` package / loaded extension | **0.1.6 / v0.1.6** (asserted equal by test) |
| Embedding provider / model | `sentence_transformers` / `all-MiniLM-L6-v2`, 384-d |
| Corpus | **184,171 chunks**, 4,481 source files |
| Gold set | 50 queries, full set (no `max_queries` sampling) |

### Corpus arithmetic (three figures appear in this work; all three reconcile)

```
184,185   before any change
184,105   rebuild 1 — ALL of docs/evaluation/ excluded            (-80)
184,171   rebuild 2 — only docs/evaluation/retrieval/ excluded    (-14 from 184,185)
          184,105 + 66 restored provenance chunks = 184,171
```

Rebuild 1 was reverted because the blanket exclusion removed
`docs/evaluation/provenance.md`, the sole expected answer for gold query *"how
are saved trace files scored against a gold set"* — manufacturing an
`index_coverage_miss`, the class Step 0 exists to detect. Only reports about
RETRIEVAL close a measurement loop, so only those are excluded. **All figures in
this report are on 184,171.**

## Gate A — correctness

| Criterion | Result |
| --- | --- |
| Final hybrid top-10 ordering identical | **50/50** |
| Final hybrid top-10 set identical | **50/50** |
| Real semantic inversions (score gap > 1e-6) | **0** |
| Max abs delta, semantic score, every chunk x query | **3.398e-07** (bound 1e-6) |
| Max abs delta, fused score, top-10 | **2.719e-07** (bound 1e-6) |
| Deterministic tie-break on `chunk_id` | **PASS** (see contract correction) |
| Top-1 / Top-3 / MRR | **0.70 / 0.74 / 0.7297** — unchanged |
| Failure set | **same 13 query IDs** |

Both paths were run against the same index, the same query embedding and the
same FTS scores, so the chunk set is identical by construction and the only
variable is where vectors live and who does the arithmetic. The reference is the
pre-Phase-1 implementation reproduced verbatim (numpy float64 matrix-vector
cosine, zero-norm guard, `(cos+1)/2`, bounded heap).

Remaining semantic ordering differences are confined to float32-vs-float64
noise: the earliest positional difference across the 50 queries is at **rank
218**, with a score gap of **6.807e-08** — three orders of magnitude below the
tolerance, and far below any rank that can reach a top-10.

### Gate A contract correction (the finding that mattered most)

Gate A originally demanded *"final ordering identical"* AND *"deterministic
tie-break on `chunk_id`"*. **Those two cannot both hold**, because the reference
had no tie rule to be identical to.

The first run measured 48/50. The two differing queries were not reranked: in
both cases the contenders were byte-identical chunks from duplicate wiki pages —
`wiki/sources/medium_article.md` vs `medium_article.md.md`, and a Codex article
held under two slugs — carrying identical vectors and therefore exactly equal
scores. Equal scores were resolved by whichever row the scan reached first: the
heap displaced only on `hybrid > heap[0][0]`, and `_fuse`'s stable sort inherited
`_hydrate`'s SQLite fetch order. That is an implementation accident standing in
for a ranking rule, and Phase 1 merely changed the accident.

The contract is therefore corrected, prospectively and for both paths:

> For unequal scores, ordering must match the reference exactly. For exact ties,
> both implementations must obey one canonical rule: **`score DESC, chunk_id ASC`**.

Implemented as `hybrid_search._Ranked`, a single ordering definition the heap and
`_fuse` both defer to. `chunk_id` is compared as an opaque string — no hash, hex
or fixed-width assumption. Under the corrected contract: **50/50**.

## Gate A — performance: NOT MET

Warm, 50 gold queries, model preloaded:

| Metric | Measured | Gate | Verdict |
| --- | --- | --- | --- |
| mean | 954.6 ms | — | ~4x better than 2.9-4.0 s |
| p50 | **701.8 ms** | < 300 ms | **FAIL** |
| p95 | **2531.6 ms** | < 500 ms | **FAIL** |
| Peak RSS | 924 MB | < 400 MB | **criterion invalid — see below** |
| Index on disk | **0.69 GB**, no sidecar | no JSON, no `.npy` | **PASS** (was 1.96 GB + 283 MB) |

### Why the thresholds are unreachable by storage work

| Stage | mean | p50 | max |
| --- | --- | --- | --- |
| Query embedding | 17 ms | 11 ms | 111 ms |
| FTS keyword | **309 ms** | 288 ms | 1507 ms |
| Semantic scan + fuse (Python) | 392 ms | 341 ms | 1891 ms |

The keyword side matches a mean of **118,348 rows per query** (max 158,598 of
184,171) because the query is `"term1" OR "term2" OR ...`, so common words sweep
most of the corpus. Even if the semantic 392 ms were reduced to zero, p50 would
land near **326 ms** — still over the gate. **The remaining bottleneck is
corpus-wide candidate generation, not vector storage.** Bounding it is a
candidate-policy change and belongs in its own gated experiment, not in a
storage swap.

### The RSS criterion is an invalid attribution metric

| Point | RSS |
| --- | --- |
| At import | 22 MB |
| After the sentence-transformers model loads | **866 MB** |
| After all 50 searches | **866 MB** |

Retrieval adds **no measurable memory**. `< 400 MB` process RSS is unreachable
in-process whatever the storage layer does, and the retired 894 MB figure was
measuring the same model. Marked **INVALID AS AN ATTRIBUTION METRIC — not
passed**, and not retroactively reinterpreted as a pass.

Replacement, for future phases only: measure **retrieval-path delta RSS** against a
model-resident baseline, comparing old and new paths under the same preloaded
model. The requirement actually worth gating is *retrieval must not materialise
O(corpus) vectors or text in Python memory*, for which there is now direct
architectural evidence — streaming scan, bounded heap, text hydrated only for
survivors.

## Sub-step 1b — removing the global `ORDER BY`

`scan_similarities` sorted all rows with `ORDER BY d ASC, chunk_id ASC`, measured
at 2.92 s against 1.37 s for the same scan without it. The sort was also the only
thing making tied results deterministic — a ranking rule inside a storage
function. With the rule moved to `_Ranked`, the sort was removed and invariance
re-run: **identical to the pre-removal run in every figure** (50/50, 0 real
inversions, same deltas, same 13 failures). Ranking-neutral, and the gain is in
the latency table above.

## The 13 failing gold queries (unchanged from post-0A)

```
are emails and API keys removed from shared packs
can an agent write to my knowledge base by default
do files get copied between projects during federation
how are decisions that nobody resolved surfaced
how do I connect Claude Code to the knowledge base
how do the pieces of the system fit together
how is the test suite structured and what gates it
what commands should I run before saying a change is done
what gets stripped out before an evidence bundle is shared
what is this project and what is it for
what stops a prompt injection in an ingested document from reaching the agent
which formats can I export a report to
why must claim evidence be quoted word for word
```

## What this experiment actually established

**`sqlite-vec` was not the remaining latency problem. Corpus-wide candidate
generation is.** That is a result the staged design was built to expose, and it
redirects Phase 1B: bounded LEXICAL candidates should be measured before
semantic KNN, since the FTS side is the larger and previously unexamined cost.
Native SQL fusion is deferred — if bounded candidates shrink the Python loop,
optimising it may never be worth the complexity.

## Verification

`pytest` -> **693 passed, 0 failed**. `public_repo_guard` passed.
`git diff --check` clean. Index verified a faithful image of disk: 184,171 chunks
derived independently by the chunker, identical IDs, every one embedded, zero
orphans in either direction. Rebuild ran with the provider pinned
(`ZURVAN_EMBED_PROVIDER=sentence_transformers`), reusing 184,105 vectors and
computing 66 — no vector was re-embedded by accident and none is mock.
