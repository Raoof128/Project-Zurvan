# Zurvan Evaluation Harness

This folder contains gold datasets for evaluating Zurvan's intelligence features (like retrieval).

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
