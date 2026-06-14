# Phase R2 Retrieval Trace Integration Audit

Date: 2026-06-14  
Scope: Phase R2 retrieval trace integration local patch

## Verdict

Phase R2 is ready for branch review after implementation verification.

The change keeps tracing explicit via `--trace` and does not alter normal `search` or `context` output when tracing is disabled.

## Files Reviewed

- `scripts/context_export.py`
- `scripts/cli.py`
- `scripts/trace_schema.py`
- `tests/test_trace_schema.py`
- `tests/test_trace_retrieval_integration.py`
- `README.md`
- `docs/API.md`
- `docs/workflows_and_plans.md`

## Security And Behaviour Guardrails

- Trace creation is opt-in only.
- Trace IDs are still validated by the Phase R1 trace schema.
- Trace files are written through `TraceStore`, which constrains outputs to `data/traces/` and `wiki/traces/`.
- Trace replay still validates payload hashes before rendering.
- Trace payload source paths are normalized to repo-relative paths when possible.
- Retrieval ranking logic was not modified.
- Raw sources are not read by the trace integration layer.

## Observed E2E Evidence

The R2 E2E run used a temporary project root with isolated wiki files.

- `context --trace --trace-id trace-20260614T131415Z-r2ctx001` wrote JSON under `data/traces/` and a Markdown mirror under `wiki/traces/`.
- `search --trace --trace-id trace-20260614T131415Z-r2srch01` wrote JSON under `data/traces/` and a Markdown mirror under `wiki/traces/`.
- Both commands printed `Trace written: ...` with the generated trace JSON path.
- `trace validate trace-20260614T131415Z-r2ctx001` returned `Trace trace-20260614T131415Z-r2ctx001 is valid.`
- `trace replay trace-20260614T131415Z-r2ctx001` rendered a replay table with `command: context`, query, mode, result count, and repo-relative source path.
- `trace validate trace-20260614T131415Z-r2srch01` returned `Trace trace-20260614T131415Z-r2srch01 is valid.`
- `trace replay trace-20260614T131415Z-r2srch01` rendered a replay table with `command: search`, query, mode, result count, and repo-relative source path.
- Unsafe trace ID `../raw/secret` returned nonzero with `unsafe trace_id: ../raw/secret` and no traceback.

## Verification Results

```bash
PYTHONPATH=. pytest tests/test_trace_schema.py tests/test_trace_writer.py tests/test_trace_validate.py tests/test_trace_replay.py tests/test_trace_cli.py tests/test_trace_retrieval_integration.py
PYTHONPATH=. pytest
PYTHONPATH=. python scripts/public_repo_guard.py
git diff --check
```

Observed results:

- Trace and retrieval-trace tests: 19 passed.
- Full test suite: 210 passed, 2 dependency warnings.
- Public repo guard: passed.
- Diff whitespace check: passed.

## Step 1A Granularity Enrichment

Step 1A enriches retrieval trace resolution before the provenance evaluation harness.

Event-schema diff:

- Added `retrieval.query`: command, query, mode, and limit.
- Added `retrieval.result`: command, ordered result list, result count, source path, chunk ID, heading, and available keyword/semantic/hybrid scores.
- Added `context.assembled`: ordered `included_chunk_ids` and `dropped` entries shaped as `{chunk_id, reason}`.
- Kept legacy `retrieval` in the allowed event types for old R1/R2 traces.
- Kept `schema_version` as `zurvan.trace.v1`.
- Kept the deterministic payload-hash rule unchanged.

Scope controls:

- No retrieval ranking, scoring, fusion, or stdout behavior change.
- Tracing remains opt-in through `--trace`.
- Token-budget capture is deferred.
- R3 MCP trace integration remains frozen until provenance evaluation ships.

Step 1A observed results:

- Focused trace tests: 20 passed.
- Full test suite: 211 passed, 2 dependency warnings.
- Temp-root E2E trace generated ordered events: `retrieval.query`, `retrieval.result`, `context.assembled`.
- `trace validate` accepted the generated Step 1A trace.
- `trace replay` rendered the generated Step 1A trace in event order.
- Legacy single-`retrieval` trace validation and replay regression: passed.
- Public repo guard: passed.
- Diff whitespace check: passed.

## Residual Risks

- R2 records retrieval metadata but does not yet instrument MCP tool calls directly. That belongs to Phase R3.
- Trace payloads prove what retrieval returned to the trace writer; they do not prove semantic truth of retrieved content.
