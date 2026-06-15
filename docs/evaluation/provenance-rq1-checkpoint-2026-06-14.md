# RQ1 Provenance — Research Checkpoint (2026-06-14)

Frozen checkpoint of the retrieval/context provenance subsystem before starting
**R3 (MCP/tool-call tracing)**. This is the evidentiary baseline for the claim
that Zurvan can capture, validate, replay, and evaluate retrieval/context
provenance.

**Git tag:** `rq1-provenance-checkpoint-2026-06-14`

## Repository state at checkpoint

```text
branch:            main
HEAD:              021572e
origin/main:       synced
working tree:      clean
full test suite:   228 passed (2 dependency warnings)
public_repo_guard: passed
git diff --check:  clean
R3 (MCP tracing):  not started (intentionally frozen)
```

### Branch lineage (landed via --no-ff, one by one)

```text
1133efe  main base (pre-provenance: papers/design ingest)
  └─ 7c68644  Merge phase-r1-trace-core  — R1/R2 trace core (suite: 218)
       └─ a6d9b73  Merge phase-r1b-provenance-events — event enrichment (suite: 226)
            └─ 021572e  Merge phase-r1b-followups — miss-analysis + scorer tests (suite: 228)
```

All three feature branches are retained on origin for reference. PR #2 (the R1B
draft, base `phase-r1-trace-core`) was closed as superseded after the direct
merges.

## What the subsystem does

Zurvan captures, validates, replays, and evaluates retrieval/context provenance
over both controlled fixtures and a 12-query real-corpus pilot. Trace records
(`zurvan.trace.v1`) carry sha256 payload hashes and record, per retrieval:

- `retrieval.query` — the query, mode, limit
- `retrieval.result` — ranked results with source paths and scores
- `retrieval.fusion` — hybrid fusion weights `{fts:0.6, embedding:0.4}` + per-chunk
  ranks (observe-only; emitted only when `mode == hybrid`; ranking unchanged)
- `context.assembled` — included chunk IDs + genuinely-populated `dropped`
  (reason `budget`, or explicit `no_dropped_context` when none)
- `graph_context` — graph expansion payload for `--graph` runs

Evaluation gates (`scripts/eval_provenance.py`): hard invariants
(`raw_leak_rate`, `hash_integrity_rate`) run **before** graded metrics
(`expected_source_recall`, `provenance_completeness`, `graph_context_presence`),
plus trace validate/replay.

## Metrics (verified on `main` at 021572e)

### Frozen 2C pilot (`eval/provenance_real_gold.jsonl`)

```text
Cases: 12
raw_leak_rate:            0%
hash_integrity_rate:      100%
trace validate:           passed
expected_source_recall:   86%
provenance_completeness:  100%
graph_context_presence:   100%
```

### Step 1B enriched re-run (`eval/provenance_real_gold_1b.jsonl`)

```text
Cases: 12
raw_leak_rate:            0%
hash_integrity_rate:      100%
trace validate:           passed
expected_source_recall:   79%
provenance_completeness:  100%   (instrumentation coverage; see below)
graph_context_presence:   100%
traces_with_retrieval_fusion: 12/12
context_traces_with_genuine_drops: 7/7 (reason: budget)
```

**The 2C (86%) and 1B (79%) recall numbers are NOT comparable** — they are scored
against different index builds (the 1B re-run rebuilt the index after excluding
trace mirrors). The enrichment is ranking-neutral, so it cannot move recall for a
fixed index. See `provenance-real-run-1b-2026-06-14.md`.

## Miss-analysis summary (the 79% residual)

Full detail: `provenance-real-run-1b-miss-analysis-2026-06-14.md` (+ `.json`).

- 6 missed links of 23, across 4 of 12 queries (other 8 score 100%).
- **Zero** misses are missing, unindexed, or below the relevance threshold —
  every missed file exists, is indexed, and clears the `hybrid_score > 0.2` gate.
  All 6 are ranking-cutoff outcomes.

| Category | Links |
|---|---|
| Ranked just outside cutoff (recoverable near-miss) | 2 |
| Annotation / query-design mismatch (gold debatable) | 2 |
| Query ambiguity / over-broad gold | 1 |
| Tokenization / no-stemming gap (genuine retrieval weakness) | 1 |
| Missing / not-indexed / below-threshold / corpus-drift deletion | 0 |

Only 1 of 6 is a clean retrieval defect (a plural query term failing to keyword-
match a singular heading under the non-stemming FTS5 tokenizer). At least one
miss is an annotation artefact where the retriever's output is defensible — so
79% is a conservative floor, not a clean retrieval-quality estimate.

## What can be claimed

> Zurvan captures, validates, replays, and evaluates retrieval/context provenance
> over both controlled fixtures and a 12-query real-corpus pilot. The system
> records query, result, fusion, assembled context, graph-context payloads,
> dropped context, and payload hashes under `zurvan.trace.v1`, with evaluation
> gates for raw-path leakage, hash integrity, replayability, expected source
> recall, provenance coverage, and graph-context presence.

On completeness, phrase as:

> 100% instrumentation coverage over currently implemented retrieval/context
> provenance events.

## What must NOT be claimed

- **Not** "100% complete provenance." R3/MCP tool-call tracing is not built, and
  graph expansion is scored through `graph_context`, not a standalone
  `graph.expand` event. `provenance_completeness` measures instrumentation
  coverage of currently-implemented events, not reconstruction faithfulness.
- **No** precise recall delta between the 2C and 1B pilots (different index builds).
- **No** generalisation of "79% retrieval quality" — 12-query pilot, and 4 of 6
  misses are gold/cutoff artefacts rather than retrieval failures.

## Limitations

- Pilot scale: 12 real-corpus queries — pilot evidence, not benchmark evidence.
- All-hybrid frozen real set; the scorer's negative branch (fusion/graph not
  demanded) is validated by unit tests, not yet by a real keyword-only query.
- `context.assembled.dropped` reasons limited to `budget` (dedupe/threshold future).
- One genuine lexical retrieval gap (no FTS stemming) is documented but not fixed
  here (would touch indexing/ranking — out of the provenance scope).
- R3 (MCP/tool-call + memory/resource tracing) not started.

## Next: R3 — MCP/tool-call provenance (scoped, not started)

Question: *Can Zurvan reconstruct MCP tool-call provenance safely?* Opt-in /
audit-safe, no behaviour changes, no benchmark yet. Candidate events:
`mcp.tool.requested`, `mcp.tool.allowed`, `mcp.tool.denied`, `mcp.tool.result`,
`memory.write`, `memory.write.denied`, `resource.read`, `resource.read.denied`.
