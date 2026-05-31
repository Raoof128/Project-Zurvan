# Contradiction Detection

Zurvan heuristically detects possible contradictions across different projects.

Run the detector:
```bash
zurvan project decisions-conflicts
```

## How It Works
Contradiction detection relies on:
1. High keyword overlap in titles and excerpts.
2. The decisions exist in *different* projects.
3. The decisions have diverging `status` fields (e.g. `accepted` vs `rejected`) OR are sufficiently similar across different projects to warrant a policy review.

**Note**: This is a heuristic detection algorithm. It does not use LLMs or cloud endpoints, so the output contains "Conflict Candidates", not guaranteed contradictions.
