# Phase 1B starting baseline — frozen

**Date:** 2026-09-15 (Australia/Sydney) · **Decision: BASELINE ACCEPTED**
**Measured after** the corpus-hygiene cycle, on a store proven to stop moving.

| | |
| --- | --- |
| Corpus | **184,002 chunks**, provider `sentence_transformers` / `all-MiniLM-L6-v2`, BLOB vectors |
| Top-1 / Top-3 / MRR | **0.70 / 0.74 / 0.73** |
| Failures | **13** |
| Per-query ranks | `phase-1b-baseline-ranks-2026-09-15.json` |

This does not replace the Phase 1 report, which stays exactly as measured at
184,171 chunks. The lineage is:

```
Phase 1 frozen corpus      184,171   0.70 / 0.74 / 0.7297 / 13
        |
        v  test-write and write-back contamination discovered and removed
Phase 1B canonical corpus  184,002   0.70 / 0.74 / 0.73   / 13
```

Returning to the same figures after removing ~90 artifact pages is the evidence
that the cycle was corrective, not disruptive.

## Why the per-query ranks are recorded, and why the gold TEXT is not

The first measurement of this baseline came out at Top-1 **0.68**, and naming the
one query that moved took three rounds, because no prior report recorded
per-query ranks — only failures. They are recorded now, by gold query NUMBER.

The text is deliberately omitted. An indexed artifact that reproduces the
measuring instrument becomes a retrieval candidate for it — which is exactly the
defect that caused the 0.68 (below). An evaluation artifact must not be able to
answer the queries it evaluates.

## Contamination removed in this cycle

Six distinct writers were putting test or session output into the live corpus:

| # | Writer | Left behind | Found by |
| --- | --- | --- | --- |
| 1 | `tests/test_cli.py::test_cli_remember` | 48 notes | inspection |
| 2 | MCP smoke write-mode session | 28 notes, 4 decisions, 7 claims | inspection |
| 3 | `check.sh` gate 11 (session start/close, postedit) | 6 session pages | disk-vs-index diff |
| 4 | `tests/test_mcp_tools.py::test_tool_zurvan_remember` | 187 log appends | conftest guard |
| 5 | `tests/test_wiki_merge.py` (8 tests) | 17 log appends per run | conftest guard |
| 6 | **This session's own write-back** quoting a gold query verbatim | Top-1 0.70 -> 0.68 | per-query rank trace |

**Four of the six were partial isolation that looked complete**: a patched store
root with an unpatched log; a redirected page with a module-level `PROJECT_ROOT`
behind it; a write paired with an `os.remove` cleanup; and an exclusion rule that
covered a directory but not verbatim quotation. That pattern, not any single bug,
is what the fixes are aimed at.

### #6 in detail, because it is the subtle one

`wiki/decisions/exclude-docsevaluationretrieval-from-indexing--but.md` — a
decision written this session ABOUT preventing evaluation contamination — quoted
gold query #44 verbatim in its reason text, and outranked that query's real
answer. The same quote had also been written into the `AGENTS.md` changelog
entry, which is indexed. Measured repair:

```
decision page quoting the query    -> real answer rank 3, decision page rank 1
quote removed from the decision    -> real answer rank 2, AGENTS.md  rank 1
quote removed from AGENTS.md too   -> real answer rank 1
```

This is a DIFFERENT mechanism from the `docs/evaluation/retrieval/` exclusion.
That one keeps reports about the retriever out of the corpus. This one cannot be
solved by exclusion, because decisions and changelog entries are legitimately
indexed knowledge. The rule is editorial: **refer to gold queries by number;
never reproduce their text in an indexed page.**

## Structural fixes

- **`ZURVAN_ROOT`** overrides `config.PROJECT_ROOT` and
  `safe_write.get_project_root()`, so a test or smoke can run against a
  throwaway store. Isolation, not cleanup — a crash between a write and its
  cleanup still contaminates.
- **`CODE_ROOT` / store-root split** in `session.py` and `agent_workflow.py`.
  They derived one root from `__file__` and used it for BOTH templates and the
  store, so `ZURVAN_ROOT` could not reach them.
- **`tests/conftest.py`** fails the session if the live store moves — file counts
  across `wiki/`, `decisions/`, `claims/`, `sources/`, `sessions/`, plus
  `log.md` LENGTH. The log was exempted in the first version on the grounds that
  it is append-only; that exemption is what let #4 and #5 through.
- **`check.sh` gate 11** runs its writes under a throwaway root and fails if it
  writes nothing there or leaks a session page.

## Verification

`pytest` -> **698 passed, 0 failed, 0 errors** with the guard active.
`check.sh` -> **all 23 gates passed**, and measured corpus-inert: wiki 97 /
decisions 107 / claims 248 / sessions 0 / sources 3822 / log 11,534 lines,
identical before and after. Index verified a faithful image of disk: 184,002
chunks re-derived independently by the chunker, identical IDs, every one
embedded, zero orphans either direction, real (non-mock) vectors.

## Follow-ups

1. **Ten modules still derive their own root from `__file__`** (`doctor.py`,
   `snapshot.py`, `restore_snapshot.py`, `install_mcp_config.py`,
   `review_server.py`, `version.py`, `public_repo_guard.py`, `cli.py`'s trace
   store, and both smokes' own `ROOT`). Only the two that write into `wiki/` were
   fixed. Any new writer copying the pattern reopens the hole.
2. **The disk-vs-index diff belongs in `check.sh` as a gate.** It is the only
   check here that cannot have a blind spot, and it found #3 after a count-based
   guard passed the same run clean.
3. The 67 duplicate pages remain queued as their own gated cleanup.
4. `wiki/log.md` holds 187 historical `Note Added - MCP test` entries and the
   older merge-test appends. They are inert history now that the writers are
   fixed; rewriting them would edit the log's own record.
