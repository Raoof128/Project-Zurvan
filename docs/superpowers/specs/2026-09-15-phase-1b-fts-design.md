# Phase 1B-FTS: bounded lexical candidates

**Date:** 2026-09-15 (Australia/Sydney)
**Status:** Approved for planning — not started
**Parent:** `2026-09-14-retrieval-core-design.md` (sub-project A)
**Supersedes the ordering of:** Phase 1B (KNN), which was specified first and is
now deferred behind this experiment.

---

## 1. Why this, and why before KNN

Phase 1 measured where a query's time actually goes, warm, on 184,171 chunks:

| Stage | mean | p50 | max |
| --- | --- | --- | --- |
| Query embedding | 17 ms | 11 ms | 111 ms |
| **FTS keyword** | **309 ms** | 288 ms | 1507 ms |
| Semantic scan + Python fusion | 392 ms | 341 ms | 1891 ms |

The keyword side matches a mean of **118,348 rows per query**, max **158,598 of
184,171** — because the query is built as `"t1" OR "t2" OR ...`, so any common
term sweeps most of the corpus. Phase 1's conclusion was that **`sqlite-vec` was
not the remaining latency problem; corpus-wide candidate generation is.**

Two consequences fix the order of work:

1. **Native SQL fusion is deferred.** Even reducing the semantic 392 ms to zero
   leaves roughly 326 ms of MEAN stage cost (FTS 309 + embed 17). **That figure is
not a p50 and must not be reported as one** — stage medians are not additive and
a mean is not a median. The counterfactual is computed from the paired per-query
timing records, not by subtracting one stage's mean from an end-to-end p50. The
directional conclusion (the keyword side dominates what remains) survives; the
number does not. And if bounded candidates
   shrink the Python loop, optimising it may never earn its complexity. Do not
   optimise machinery this experiment may make irrelevant.
2. **Bounded FTS is measured before semantic KNN**, never together. Two
   candidate-policy changes in one measurement cannot be attributed — the
   governing lesson from CHANGELOG 13h, where RRF, `bge-small` and `bge-base`
   each measured as a loss and only isolation revealed it.

## 2. Why this is a safer trade than KNN

They are not symmetric, and the asymmetry is the reason to do this one first.

Semantic KNN hides a chunk from scoring entirely: outside the semantic top-K it
is invisible **even on a perfect keyword match**. Bounding FTS, with the semantic
full scan retained, does something much weaker — a chunk outside the lexical
top-N simply scores `keyword_score = 0.0`, exactly as every non-matching chunk
does today, and still receives its full semantic score and can still reach the
top-10 on it. The chunks being dropped are by construction the *weakest* lexical
matches in a set already dominated by common-word noise.

That is a real candidate-policy change and is gated as one. It is just a far
smaller blast radius than KNN, which is why it goes first.

## 3. Constraints (unchanged from the parent spec)

Local-first; Markdown stays the source of truth; invariant 4 (documented
before/after `eval_search`); frozen artifacts untouchable; the 50-query gold set
is the only honest instrument, so every gate names the queries that moved.

**Baseline freeze.** A chunk count is not a baseline. Immediately before the
experiment, freeze and record: **commit SHA + corpus digest + chunk count + gold
set digest + embedding provider/model**. Three different corpus sizes (184,002 /
184,171 / 184,185) already appear across this project's specs, and a before/after
measured on different corpora is not attributable to the change.

**Held constant:** chunking, embedding model, query embedding, the 0.2/0.8
fusion weights, `MIN_HYBRID_SCORE`, the semantic full scan, the canonical
ordering rule (`score DESC, chunk_id ASC`), and result budgeting. Only the
lexical candidate set changes.

## 4. Candidates to measure

Independently, one per run:

| Variant | Mechanism | Note |
| --- | --- | --- |
| **A. `LIMIT N` by rank** | `ORDER BY rank ASC, chunk_id ASC LIMIT ?`, sweeping N | SQLite documents `ORDER BY rank` as logically equivalent to default BM25 but optimised for ordered/limited FTS5 retrieval. `chunk_id ASC` makes the cutoff deterministic when BM25 ties at the N boundary — the lexical tie rule must bite BEFORE fusion, not only after |
| **B. High-DF term pruning** | drop terms matching > X% of the corpus | Attacks the cause (common words) rather than the symptom |
| **C. AND-biased matching** | require the rarest k terms | Largest recall risk; measure last |

Start with A, because N sweeps cleanly and the recall loss is directly
observable.

**Tuning and certifying on the same 50 queries is overfitting, and the report
says so.** Sweeping N until the gold set passes and then reporting performance on
that same set is a regression suite, not a held-out evaluation. That is useful
engineering and a weak research claim; whichever is intended must be stated. A
pre-registered N-selection rule or a separate dev set would make the claim
defensible. B is the more principled fix if A's N proves corpus-size-sensitive.

## 5. Gate A2-FTS

*Quality — the binding constraint*
- **No previously-passing Top-3 query may become a failure.** A single
  regression rejects the variant regardless of latency.
- **AND no aggregate regression beyond a predeclared tolerance:** Top-1 ≥ 0.68
  and MRR ≥ 0.71 (baseline 0.70 / 0.73, tolerance one query). The Top-3-only
  gate is insufficient — every rank-1 answer could fall to rank 3 with Top-3
  still reading 0.74 while Top-1 and MRR collapse.
- **Paired per-query rank deltas recorded for all 50**, not only the movers.
- Top-1 / Top-3 / MRR reported against the Phase 1 baseline
  **0.70 / 0.74 / 0.7297, 13 failures on 184,171 chunks**.
- **Every changed query is named by OPAQUE ID, never by its text.** The report
  is a tracked artefact, so literal gold text in it is an
  `INSTRUMENT → PUBLIC_ARTIFACT` transfer — the exact class Boundary blocks.
  (The `RETRIEVAL_CORPUS` path is already closed: `docs/evaluation/retrieval/` is
  excluded from indexing. The publication path is not.) Cleartext stays local.
- Named by id, with its before/after rank. At n=50 one
  query is 2%; aggregates alone are not evidence.
- The 13 known failures may move freely — they are already failures.

*Performance*
- p50 / p95 / mean, warm, model preloaded, all 50 gold queries.
- **ACCEPT threshold, frozen before testing:** end-to-end p50 ≤ 450 ms AND p95
  no worse than baseline. Without a predeclared number, a variant that preserves
  recall and saves 2 ms could be labelled ACCEPT.
- **Fresh process per variant.** `ru_maxrss` is a high-water mark that never
  falls, so running variants sequentially in one process contaminates every later
  measurement. Each variant loads the same model and captures its own baseline
  and peak.
- Stage breakdown re-measured, so the next bottleneck is identified rather than
  assumed.
- Retrieval-path **delta RSS** against a model-resident baseline (see below).

*Reporting*
- One frozen report: `docs/evaluation/retrieval/fts-candidates-<date>.md`,
  recording commit SHA, N (or X, or k), corpus size, every changed query, and
  **ACCEPT / REJECT**. A rejected variant keeps its report.

## 6. Corrections this phase inherits from Phase 1

Both are prospective. Neither is applied retroactively to make Phase 1 pass.

**The ordering contract.** Gate A's "final ordering identical to the reference"
is unachievable for tied chunks, because the reference had no tie rule. The
contract for every future retrieval gate is:

> For unequal scores, ordering must match the reference exactly. For exact ties,
> both implementations must obey **`score DESC, chunk_id ASC`**.

Implemented once, in `hybrid_search._Ranked`; the heap and `_fuse` defer to it.
`chunk_id` is compared as an opaque string — no hash, hex or width assumption.

**The memory criterion.** `peak RSS < 400 MB` was measured in Phase 1 as an
invalid attribution metric: RSS is 22 MB at import, 866 MB once the
sentence-transformers model loads, and 866 MB after 50 searches — retrieval adds
nothing measurable, and the threshold is unreachable in-process whatever storage
does. Replacement, for this and later phases:

```
delta_RSS = peak RSS during retrieval  -  model-resident baseline RSS
```

measured for old and new paths **under the same preloaded model**. The
requirement actually worth gating is: *retrieval must not materialise O(corpus)
vectors or text in Python memory.*

## 7. Prerequisite

The Gate A invariance harness used in Phase 1 lives in a scratchpad. Promote it
to a repo script with unit tests before 1B-FTS starts, because this phase's
discipline is *re-run invariance after each change*, and a harness that exists
only in a session cannot do that. It must take its corpus size from the index
rather than a literal — Phase 1's copy printed a hardcoded `184,185` while
measuring 184,171, which was caught before it reached a report.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| A bound that helps latency quietly costs recall | Gate rejects on ANY newly-failing Top-3 query, not on aggregates |
| N tuned to 50 queries and to this corpus size | Sweep N and report the curve; prefer B if the good N tracks corpus size |
| Bounded FTS and KNN measured together | Separate phases, separate reports — 13h |
| p95 outliers assumed to be row count | Phase 1 measured 1507 ms on 27,878 rows vs faster runs on 158,598 — understand this BEFORE choosing a bound |

## 9. Out of scope

Semantic KNN (only if still needed afterwards) · native SQL fusion (deferred,
re-profile after this) · the 67 duplicate pages (separately gated cleanup) ·
Phase 2A / 2B, unchanged in the parent spec.
