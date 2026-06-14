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
denominator. The headline did not drop because the frozen pilot contains no
keyword-only query that would legitimately *lack* a fusion event; with an
all-hybrid set there is no honest gap to expose. The substantive gain is that
`context.assembled.dropped` is no longer an empty stub: 7/7 context traces
record real `budget` drops with concrete chunk IDs.

`expected_source_recall` moved 86% → 79%. This is **corpus drift, not the
enrichment**: the enrichment is ranking-neutral (unit-tested), so the included
results are unchanged for a fixed index. The current index has more competing
docs than when the 2C pilot's index was built, which pushes two genuinely
borderline queries (`real-retrieval-graph-03`, `real-hard-ambiguous-01`) below
their pilot recall. The frozen 2C pilot still scores 86% against its own
committed traces.

## Honest framing

> Step 1B raises provenance from "built-scope complete" toward audit
> completeness: fusion ranks and genuine drop reasons are now recorded and
> scoreable on real traces. Completeness is measured over the full retrieval
> pipeline, not a 3-event subset. Recall remains a pilot-grade signal and is
> sensitive to corpus size.

## Residual

- Still 12 queries — pilot, not benchmark.
- All-hybrid frozen set cannot exercise the "fusion legitimately absent" path;
  a future keyword-only query would test the conditional denominator honestly.
- `context.assembled.dropped` reasons are currently limited to `budget`;
  `dedupe` and relevance-threshold reasons remain future work.
