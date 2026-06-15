# Provenance Real-Corpus Pilot Run - 2026-06-14

## Summary

Step 2C ran the provenance evaluator against 12 frozen real-corpus queries
drawn from the existing Zurvan wiki/docs. This is an initial pilot result, not
a benchmark.

The query set was frozen in commit `48a8c27` before traces were generated. Each
query includes manually annotated expected source paths selected from the
wiki/docs before inspecting trace output.

## Scope

- R3 remained frozen.
- No MCP tracing was added.
- Retrieval ranking, graph behavior, trace schema, and evaluator scoring logic
  were not changed.
- Scores are reported for all frozen queries; no query was dropped.
- Provenance completeness is scored against built-scope events only:
  `retrieval.query`, `retrieval.result`, `context.assembled`, and
  `graph_context` for graph-labelled queries.

Known ceiling: `retrieval.fusion`, `graph.expand`, and meaningful
`context.assembled.dropped` reasons are not implemented yet.

## Method

1. Wrote `eval/provenance_real_queries.jsonl` with 12 fixed queries and
   expected source annotations.
2. Committed the frozen query set before trace generation.
3. Rebuilt local search and graph indexes.
4. Generated traces using existing `search --trace`, `context --trace`, and
   selected `context --graph --trace` commands.
5. Wrote `eval/provenance_real_gold.jsonl` linking frozen query metadata to the
   generated trace files.
6. Ran `eval_provenance.py` and trace validate/replay checks.

## Commands

```bash
PYTHONPATH=. python scripts/rebuild_search_index.py
PYTHONPATH=. python scripts/graph_build.py
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl --validate
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl
PYTHONPATH=. python scripts/cli.py trace validate <trace-id>
PYTHONPATH=. python scripts/cli.py trace replay <trace-id>
```

Final branch-gate commands:

```bash
python -m compileall scripts/eval_provenance.py scripts/cli.py
PYTHONPATH=. pytest tests/test_eval_provenance.py
PYTHONPATH=. pytest
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

## Results

```text
Cases: 12
raw_leak_rate: 0%
hash_integrity_rate: 100%
trace_validate_rate: 12/12
trace_replay_rate: 12/12
expected_source_recall: 86%
provenance_completeness: 100%
graph_context_presence: 100%
```

## Trace IDs

| Query ID | Category | Trace ID |
| --- | --- | --- |
| real-mcp-safety-01 | MCP safety / trace system | `trace-20260614T171000Z-real0001` |
| real-mcp-safety-02 | MCP safety / trace system | `trace-20260614T171000Z-real0002` |
| real-trace-system-03 | MCP safety / trace system | `trace-20260614T171000Z-real0003` |
| real-retrieval-graph-01 | retrieval / graph context | `trace-20260614T171000Z-real0004` |
| real-retrieval-graph-02 | retrieval / graph context | `trace-20260614T171000Z-real0005` |
| real-retrieval-graph-03 | retrieval / graph context | `trace-20260614T171000Z-real0006` |
| real-evidence-report-01 | evidence packs / reports | `trace-20260614T171000Z-real0007` |
| real-evidence-report-02 | evidence packs / reports | `trace-20260614T171000Z-real0008` |
| real-decision-policy-01 | decision memory / policy radar | `trace-20260614T171000Z-real0009` |
| real-decision-policy-02 | decision memory / policy radar | `trace-20260614T171000Z-real0010` |
| real-hard-ambiguous-01 | hard / ambiguous | `trace-20260614T171000Z-real0011` |
| real-hard-ambiguous-02 | hard / ambiguous | `trace-20260614T171000Z-real0012` |

## Per-Query Recall

| Query ID | Recall | Notes |
| --- | ---: | --- |
| real-mcp-safety-01 | 100% | Hit both MCP safety expected sources. |
| real-mcp-safety-02 | 100% | Hit Codex MCP setup source. |
| real-trace-system-03 | 100% | Hit R2 audit source. |
| real-retrieval-graph-01 | 100% | Hit delayed vector search decision. |
| real-retrieval-graph-02 | 100% | Hit workflow/graph context source. |
| real-retrieval-graph-03 | 100% | Hit both federation privacy/search sources. |
| real-evidence-report-01 | 100% | Hit evidence overview and redaction docs. |
| real-evidence-report-02 | 0% | Retrieved evidence docs, but missed expected review/report docs. |
| real-decision-policy-01 | 100% | Hit decision memory and stale-decision docs. |
| real-decision-policy-02 | 100% | Hit policy radar overview, drift, and contradiction docs. |
| real-hard-ambiguous-01 | 67% | Hit agent workflow docs, missed MCP security. |
| real-hard-ambiguous-02 | 67% | Hit publication safety/export docs, missed citation appendix. |

## Interpretation

The run demonstrates that Zurvan can evaluate provenance on real repository
content with validated/replayable traces and no raw-path leakage. The 86%
expected source recall is a useful pilot signal: most expected sources were
retrieved, while ambiguous/report-style queries exposed real retrieval misses.

This should be phrased as:

> Zurvan has a validated provenance evaluator and an initial real-corpus pilot
> result.

It should not be phrased as proof of real-world provenance completeness.

## Residual Risks

- The run has only 12 queries and should be treated as a pilot, not a benchmark.
- Expected sources were manually annotated; a larger study should use a reviewed
  annotation protocol.
- `provenance_completeness` is built-scope only and does not evaluate missing
  future events such as `retrieval.fusion` or `graph.expand`.
- `context.assembled.dropped` remains structurally present but empty, so dropped
  chunk explanations are not measured.
- Real traces are committed because they contain event metadata, paths, chunk
  IDs, and scores, not source text. Public repo safety still depends on
  `public_repo_guard.py` and review of future trace payload expansions.
