# Provenance Evaluation

`eval_provenance.py` evaluates saved Zurvan trace files against a JSONL gold
set. It is intentionally local-first: it reads trace JSON and gold metadata
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

The current gold set reflects the implemented Step 1A scope. It does not
penalize missing future events such as `retrieval.fusion` or `graph.expand`.

## Commands

```bash
PYTHONPATH=. python scripts/eval_provenance.py --validate
PYTHONPATH=. python scripts/eval_provenance.py --min-source-recall 1.0 --min-provenance-completeness 1.0
PYTHONPATH=. python scripts/cli.py eval provenance --min-source-recall 1.0 --min-provenance-completeness 1.0
```
