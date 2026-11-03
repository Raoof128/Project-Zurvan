# Status index

Generated from `docs/status/receipts.json` by `scripts/status_index.py`;
do not edit by hand. A status is a recorded fact with its evidence, not a
rung on a ladder: *implemented* (code is on `main`), *tested here* (a test
in this tree exercises it), *operationally closed* (a recorded closure on
the real deployment), *promoted* (a passed predeclared quality/resource
gate), and *not built* for a thing people might otherwise infer exists.
Newest first within a group.

## Promoted

_none recorded_

## Operationally closed

| Date | Item | Evidence | Note |
|---|---|---|---|
| 2026-09-29 | Z1a observability | `docs/superpowers/plans/2026-09-29-z1a-observability.md` |  |
| 2026-09-28 | Z0 measurement snapshots | `docs/superpowers/plans/2026-09-28-z0-step7-closure.md` |  |

## Tested here

| Date | Item | Evidence | Note |
|---|---|---|---|
| 2026-11-04 | F1 extraction claim overwrite: content-identified pages, explicit cross-owner conflict | `tests/test_extract_identity.py` | Audit 2026-10-22 §3. |
| 2026-11-04 | F2 empty evidence admitted: one admission layer on every path | `tests/test_evidence_admission.py` | Audit 2026-10-22 §3. |
| 2026-11-04 | F3 federated hybrid searched the home index: explicit store handle | `tests/test_store_handle.py` | Audit 2026-10-22 §3; federation test in tests/test_cross_project_search.py. |
| 2026-11-04 | F4 mock vectors on model failure: typed EmbeddingUnavailable / EmbeddingSpaceMismatch, identity stamped in the index | `tests/test_embedding_identity.py` | Audit 2026-10-22 §3. |
| 2026-11-04 | F5 trace fusion weights 0.6/0.4 vs scorer 0.2/0.8: one policy object | `tests/test_fusion_policy.py` | Audit 2026-10-22 §3; historical traces annotated in docs/evaluation/provenance.md. |
| 2026-11-04 | F6 response not bound to served generation (Rust): SearchResult.served, stamp_for, pinned graph open | `rust/zurvan-mcp/src/search.rs` | Audit 2026-10-22 §3; deterministic barrier tests in search.rs and context.rs. |
| 2026-11-04 | graph_context secondary reader opens read-only, never creates the store | `tests/test_graph_context.py` | Audit 2026-10-22 §2. |
| 2026-10-20 | Rust/Python live parity gate: 314 cases per protocol, 312 PASS / 2 EXPECTED_DIVERGENCE / 0 FAIL | `scripts/mcp_parity.py` | Protocols 2025-11-25 and 2026-07-28; on the live store at that date. |
| 2026-10-20 | Hybrid retrieval p50 70 ms / p95 92 ms; keyword p50 80 / p95 110 ms; first status 74 ms | `CHANGELOG.md#2026-10-20c` | Reported stdio workload at ~303k chunks; not a universal guarantee. |
| 2026-10-20 | Retired 50-item relevance instrument: top-1 0.72 / top-3 0.74 / MRR 0.7372 | `CHANGELOG.md#2026-10-20c` | Historical retrieval figure; cannot authorise promotion. |
| 2026-09-17 | Boundary v1: recall 10/10, operational false positives 0/200 | `AGENTS.md` | Finite measurement on its stated dataset; the easier new-version case. |
| 2026-06-14 | Frozen provenance golds: 2C 86% / 1B 79% recall | `eval/provenance_real_gold.jsonl` | Frozen; scores committed traces. |

## Implemented

| Date | Item | Evidence | Note |
|---|---|---|---|
| 2026-10-20 | Rust MCP server (15 tools / 7 resources / 4 prompts), harness cut-over | `CHANGELOG.md#2026-10-20d` | Reads native; writes and eval bridge to Python. |
| 2026-10-20 | Z1b sealed evaluation (Rev 3 code merged to main) | `CHANGELOG.md#2026-10-20e` | NOT CLOSED: fresh suite, seal, gates 4/5/9 and verdict outstanding. |

## Not built

| Date | Item | Evidence | Note |
|---|---|---|---|
| 2026-11-04 | R3 MCP/tool-call provenance events | `AGENTS.md` | Never claim complete provenance (standing invariant). |
