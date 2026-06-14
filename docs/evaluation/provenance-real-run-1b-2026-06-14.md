# Provenance Real-Corpus Re-Run (Step 1B) — 2026-06-14

## Summary

Step 1B enriched the retrieval trace with two additions and re-ran the **same
12 frozen real-corpus queries** from the Step 2C pilot:

1. `retrieval.fusion` — a new event recording the existing hybrid fusion
   (weights + per-chunk ranks). Observe-only; ranking is unchanged.
2. `context.assembled.dropped` — now genuinely populated with `budget`-reason
   drops instead of always being an empty stub.

The graph dimension is scored via the **existing** `graph_context` event (no new
`graph.expand` event was added — that would have duplicated `graph_context`).

This run is a separate artifact (new trace IDs `…-r1b0001..0012`, new gold
`eval/provenance_real_gold_1b.jsonl`). The original Step 2C pilot
(`provenance-real-run-2026-06-14.md`, gold `provenance_real_gold.jsonl`) is left
**frozen and untouched** to preserve pre-registration integrity.

## Scope

- Query set frozen: identical 12 queries from `provenance_real_queries.jsonl`.
  No query was re-picked or dropped.
- R3 (MCP/tool-call tracing) remained frozen.
- Retrieval ranking and graph behaviour were **not** changed — the included
  top-`limit` result set is identical to a direct limit fetch; the wider
  candidate pool only exposes the over-budget remainder as observable drops.
- One indexing fix: derived trace mirrors (`wiki/traces/*.md`) are no longer
  indexed. They are self-referential audit artifacts that pollute retrieval
  with the query's own terms. (`scripts/chunk.py`, `scripts/context_export.py`.)

## Method

```bash
PYTHONPATH=. python scripts/rebuild_search_index.py     # 3395 chunks (trace mirrors excluded)
PYTHONPATH=. python scripts/graph_build.py
# regenerate 12 enriched traces under r1b IDs, write eval/provenance_real_gold_1b.jsonl
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold_1b.jsonl --validate
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold_1b.jsonl
```

## Results

```text
Cases: 12
raw_leak_rate: 0%
hash_integrity_rate: 100%
trace_validate_rate: 12/12
trace_replay_rate: 12/12
expected_source_recall: 79%
provenance_completeness: 100%
graph_context_presence: 100%
traces_with_retrieval_fusion: 12/12
context_traces_with_genuine_drops: 7/7   (reason: budget)
```

## Interpretation

`provenance_completeness` remains 100%, but it now measures a **richer
pipeline**. The Step 2C ceiling scored a 3-event subset
(`retrieval.query`, `retrieval.result`, `context.assembled`). This run adds
`retrieval.fusion` to the scored expectation for every (hybrid) query, and all
12 traces genuinely contain it — so completeness stays 100% over a larger
denominator.

Two cautions on reading this number. First, with the conditional scorer
(`retrieval.fusion` expected *iff* the query is hybrid), `provenance_completeness`
measures **instrumentation coverage** — "did the trace capture the events this
command type is supposed to capture" — not reconstruction faithfulness. It will
read ~100% on any correct run. It is a useful integrity check, but it is **not**
the RQ1 headline; `expected_source_recall` is. Adding a keyword-only query would
*not* "make completeness honest" (fusion is correctly not expected there, so
completeness stays 100%); what it buys is validation of the scorer's **negative
branch** on real data (see Residual). Second, the substantive gain this run does
deliver is qualitative: `context.assembled.dropped` is no longer an empty stub —
7/7 context traces record real `budget` drops with concrete chunk IDs.

### Recall: the 2C and 1B numbers are NOT comparable

`expected_source_recall` is 79% here vs 86% in the 2C pilot. **Do not read this
as an enrichment delta or a regression.** The enrichment is ranking-neutral
(`_apply_budget` keeps the identical top-`limit` slice; unit-tested), so it
cannot move recall for a fixed index. The two numbers are scored against
**different index builds**:

- The 2C pilot's traces were generated from an index that did not contain trace
  mirrors (they were created as a side effect of that very run).
- This 1B run rebuilds the index from the current corpus. A naive rebuild
  *included* the `wiki/traces/*.md` mirrors and crashed recall to **57%** — the
  mirrors *crowd out* real sources (they deflate recall, they do not inflate it).
  Excluding them restored recall to 79%.

So the mirror fix did not "clean up" the 86% number — the 2C index never had
mirrors. It prevented *this* rebuild from introducing them. The residual 86%→79%
gap is **corpus/index drift between two builds** (more competing docs now push
two genuinely borderline queries — `real-retrieval-graph-03`,
`real-hard-ambiguous-01` — below their pilot recall) and cannot be fully
attributed without reconstructing the exact 2C index. Treat the two pilots as
non-comparable. The frozen 2C pilot still scores 86% against its own committed
traces; it is left untouched.

## Honest framing

> Completeness is 100% over currently instrumented enriched retrieval
> provenance. This is instrumentation coverage, not "complete provenance":
> `graph.expand` is not a separate event (graph is scored via `graph_context`),
> and MCP/tool-call tracing (R3) is not built yet.

## Residual

- Still 12 queries — pilot, not benchmark.
- The all-hybrid frozen set cannot exercise the scorer's **negative branch** on
  real data. Adding ≥1 keyword-only query (fusion must *not* be demanded) and ≥1
  non-graph query (graph must *not* be demanded) would validate that the
  conditional scoring is correct, not just that the positive path fires. This is
  a scorer-robustness check, not a completeness falsification.
- `context.assembled.dropped` reasons are currently limited to `budget`;
  `dedupe` and relevance-threshold reasons remain future work.
- The ~21% missing source recall is unanalysed and must not back any paper claim
  until the misses are categorised (genuine retrieval gap vs. annotation vs.
  corpus drift).
- No MCP tracing yet; no standalone `graph.expand` event.
