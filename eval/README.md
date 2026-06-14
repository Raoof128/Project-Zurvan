# Zurvan Evaluation Harness

This folder contains gold datasets for evaluating Zurvan's intelligence features, including retrieval quality and trace provenance.

## search_gold.jsonl

A JSONL file containing known questions and the paths to the Markdown files that *should* be returned when those questions are searched.

### Format

```json
{
  "query": "search query text",
  "expected_paths": ["wiki/some/file.md"],
  "min_score": 0.5,
  "notes": "Optional explanation"
}
```

### Running the evaluation

```bash
zurvan eval search --hybrid
```

## provenance_gold*.jsonl

JSONL files containing saved trace files and the provenance facts expected to
be recoverable from them.

### Format

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

### Running the provenance evaluation

```bash
zurvan eval provenance --min-source-recall 1.0 --min-provenance-completeness 1.0
```

### Included gold sets

- `provenance_gold.jsonl`: passing baseline with six Step 2B cases.
- `provenance_gold_negative.jsonl`: raw-path and incomplete-trace failure fixtures.
- `provenance_gold_incomplete.jsonl`: isolated completeness-threshold failure.
- `provenance_gold_low_recall.jsonl`: isolated source-recall-threshold failure.
- `provenance_real_queries.jsonl`: frozen Step 2C real-corpus pilot query set.
- `provenance_real_gold.jsonl`: Step 2C real-corpus pilot gold linked to generated traces.
