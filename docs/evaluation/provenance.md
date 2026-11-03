# Provenance Evaluation

`eval_provenance.py` evaluates saved Zurvan trace files against JSONL gold
sets. It is intentionally local-first: it reads trace JSON and gold metadata
from disk, validates payload hashes, and never reads from `raw/`.

## Gold Schema

Each line in `eval/provenance_gold.jsonl` is one evaluation case:

```json
{
  "id": "step2-fixture-001",
  "query": "step 2 provenance evaluation",
  "trace_path": "data/traces/trace-20260614T151617Z-prov0001.json",
  "expected_source_paths": ["wiki/provenance-step2.md"],
  "expected_event_types": ["retrieval.query", "retrieval.result", "context.assembled"],
  "expected_chunk_ids": ["chunk-step2-001"],
  "expect_graph_context": true,
  "notes": "Optional explanation"
}
```

`expected_chunk_ids` is optional today but part of the schema so later
claim-to-chunk faithfulness checks can be added without changing the file
shape.

## Gold Sets

- `eval/provenance_gold.jsonl`: passing baseline with six cases covering
  `search --trace`, `context --trace`, `context --graph --trace`, legacy
  coarse `retrieval`, a controlled Step 2 fixture, and a stale/superseded note
  case labelled for later policy-aware scoring.
- `eval/provenance_gold_negative.jsonl`: invariant/failure fixtures, including
  a raw-path leak and an incomplete context trace.
- `eval/provenance_gold_incomplete.jsonl`: isolated completeness failure used
  for threshold tests.
- `eval/provenance_gold_low_recall.jsonl`: isolated missing-expected-source
  case used for source-recall threshold tests.
- `eval/provenance_real_queries.jsonl`: frozen Step 2C real-corpus pilot query
  set, committed before trace generation.
- `eval/provenance_real_gold.jsonl`: Step 2C real-corpus pilot gold file that
  links frozen queries to generated traces.

## Metrics

Hard invariants run before graded scoring:

- `raw_leak_rate`: must be `0%`.
- `hash_integrity_rate`: must be `100%`.

If either invariant fails, the evaluator exits before printing the graded
metrics table.

Graded metrics:

- `expected_source_recall`: fraction of expected source paths present in
  `retrieval.result` or legacy `retrieval` result payloads.
- `provenance_completeness`: fraction of required event types and expected
  chunk IDs present in the trace.
- `graph_context_presence`: fraction of cases that expected graph context and
  included a `graph_context` event.

The current passing gold set reflects the implemented Step 1A/Step 2B scope. It
does not penalize missing future events such as `retrieval.fusion` or
`graph.expand`.

### `retrieval.fusion` weights: historical annotation (F5, 2026-11-04)

Traces written before 2026-11-04 carry `"weights": {"fts": 0.6, "embedding":
0.4}` in their `retrieval.fusion` payload while the scorer actually fused at
keyword 0.2 / semantic 0.8 (`scripts/hybrid_search.py`, `KEYWORD_WEIGHT`). The
scores in those traces are the scores that were served; only the declared
weights are wrong, so a replay that recomputes `fused_score` from the recorded
components and the recorded weights reconstructs the wrong number (1.0 / 0.5
gives 0.8 from the trace, 0.6 in reality). Historical traces are **not**
rewritten. Since 2026-11-04 the payload is produced by the single policy
object the scorer applies (`scripts/fusion_policy.py`) and carries
`"policy_id"`: a reader should key on `policy_id` when present and treat the
`weights` field of a trace without one as unreliable.

## Commands

```bash
PYTHONPATH=. python scripts/eval_provenance.py --validate
PYTHONPATH=. python scripts/eval_provenance.py --min-source-recall 1.0 --min-provenance-completeness 1.0
PYTHONPATH=. python scripts/cli.py eval provenance --min-source-recall 1.0 --min-provenance-completeness 1.0

# Expected failure fixtures
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_gold_negative.jsonl
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_gold_incomplete.jsonl --min-provenance-completeness 1.0
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_gold_low_recall.jsonl --min-source-recall 1.0

# Real-corpus pilot
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl
```

The Step 2C pilot report is in
`docs/evaluation/provenance-real-run-2026-06-14.md`.
