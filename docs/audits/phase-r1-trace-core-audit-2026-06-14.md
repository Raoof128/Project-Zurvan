# Phase R1 Trace Core Audit

Date: 2026-06-14  
Scope: Phase R1 trace core local patch

## Verdict

Phase R1 is ready for branch review.

The trace core is local-first, Git-friendly, and constrained to Markdown plus JSON outputs. The audit found no reportable security finding in the Phase R1 diff.

## Files Reviewed

- `scripts/trace_schema.py`
- `scripts/trace_writer.py`
- `scripts/trace_validate.py`
- `scripts/trace_replay.py`
- `scripts/cli.py`
- `tests/test_trace_schema.py`
- `tests/test_trace_writer.py`
- `tests/test_trace_validate.py`
- `tests/test_trace_replay.py`
- `tests/test_trace_cli.py`
- `README.md`
- `docs/API.md`
- `docs/workflows_and_plans.md`
- `data/traces/.gitkeep`
- `wiki/traces/.gitkeep`

## Threat Model

Phase R1 accepts local CLI input and local trace JSON. Trace contents may be attacker-influenced if an agent or user writes malformed trace files. The important assets are raw sources, existing wiki content, trace integrity, local filesystem boundaries, and reviewer trust in replay output.

Security invariants:

- Trace IDs must not allow path traversal.
- Trace files must stay under `data/traces/`.
- Markdown mirrors must stay under `wiki/traces/`.
- Replay must not execute tools, read raw sources, or call networks.
- Replay must refuse traces with invalid schema or payload hashes.
- Trace payload hashing must be deterministic.

## E2E Evidence

Happy path:

- Created a temporary trace project root.
- Wrote a trace with two events through `TraceStore`.
- Confirmed JSON output under `data/traces/`.
- Confirmed Markdown mirror under `wiki/traces/`.
- Ran `trace list`.
- Ran `trace inspect`.
- Ran `trace validate`.
- Ran `trace replay`.

Negative path:

- Invalid payload hash caused `trace validate` to return nonzero.
- Invalid payload hash caused `trace replay` to return nonzero.
- Traversal-style trace ID `../raw/secret` was rejected before path use.
- Missing trace ID returned nonzero.
- Writer rejected unsafe trace ID `../escape`.

## Verification Commands

```bash
python -m compileall scripts/trace_schema.py scripts/trace_writer.py scripts/trace_validate.py scripts/trace_replay.py scripts/context_export.py scripts/hybrid_search.py scripts/graph_context.py scripts/cli.py
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

## Security Review

No reportable finding.

Controls confirmed:

- `TRACE_ID_RE` and `EVENT_ID_RE` reject path-like and malformed IDs.
- `TraceStore._paths_for()` validates trace IDs before building paths.
- `TraceStore._ensure_under()` confirms resolved paths remain under trace directories.
- `validate_trace_file()` reports missing required fields, unsafe IDs, unsupported event types, duplicate event IDs, non-object payloads, and payload hash mismatches.
- `replay_trace_file()` validates the trace before rendering Markdown.
- CLI trace commands return nonzero for unsafe IDs, missing traces, validation failures, and replay failures.

## Residual Risks

- Phase R1 records and replays trace files but does not yet integrate trace capture into retrieval, graph, or MCP tools. That belongs to later R2/R3 phases.
- The trace schema validates structure and payload integrity, not semantic truth. A trace can still contain false payload content if the producer wrote false data.
- `--project-root` is an internal CLI test/audit override. Normal user routing should prefer `--project`.

## Conclusion

Phase R1 satisfies its scoped requirements:

- Local JSON trace storage.
- Git-friendly Markdown mirrors.
- Trace list, inspect, validate, and replay commands.
- Path traversal resistance.
- Payload hash integrity checks.
- Replay without execution side effects.
