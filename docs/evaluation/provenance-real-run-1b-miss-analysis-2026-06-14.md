# Step 1B Recall Miss-Analysis — 2026-06-14

Companion to `provenance-real-run-1b-2026-06-14.md`. That run reported
`expected_source_recall = 79%` (macro, per-query average) on the 12 frozen
real-corpus queries. This document categorises **every** missed expected source
so the residual gap can be attributed before any paper claim is made.

**This is a read-only analysis.** No retrieval ranking, scoring, indexing, graph
behaviour, or trace schema was changed. The frozen 2C pilot and the Step 1B
real-run traces were not mutated. Findings were produced by replaying the frozen
gold (`eval/provenance_real_gold_1b.jsonl`) against the committed traces and by
re-ranking each missing query with the existing `scripts/hybrid_search.py` over
the current `data/search.sqlite`.

## Method

1. For each of the 12 gold items, collect the `source_path` values emitted in the
   trace's `retrieval.result` event (exactly what the scorer's `_source_paths`
   reads) and diff against `expected_source_paths`.
2. For each **missed** expected source, check four things with no behaviour change:
   - **Exists?** — is the file on disk?
   - **Indexed?** — does `data/search.sqlite.chunks` contain chunks for it?
   - **Threshold?** — does its best chunk clear the hard-coded `hybrid_score > 0.2`
     relevance gate in `search_hybrid`?
   - **Rank?** — where does its best chunk land in a wide ranked pool, vs. the
     query's `limit`?
3. Assign a category and a follow-up.

### Caveat on rank numbers

The 12 traces are frozen against the index build that generated them (~3395
chunks). This re-ranking runs against the **current** index (3284 chunks — the
corpus has drifted slightly since the run). Exact rank integers are therefore
approximate. The categorical findings — *exists / indexed / clears threshold /
inside-vs-outside cutoff* — are robust: every missed file is still present and
indexed, and the observed top-`limit` set in each frozen trace matches the
current ranking's structure (e.g. the same single-source domination in
`real-retrieval-graph-03`).

## Headline numbers

- **Queries with a miss:** 4 of 12 (the other 8 score 100%).
- **Missed expected links:** 6 of 23 total expected links.
  - Macro recall (per-query avg, the reported metric): **79%**.
  - Micro recall (link-level): 17/23 = **74%**.
- **Missed links that are missing from disk:** 0.
- **Missed links that are unindexed:** 0.
- **Missed links below the 0.2 relevance threshold:** 0.
- **All 6 misses are "expected source ranked below the query's `limit` cutoff".**
  The interesting question is *why* each ranked low.

## Per-miss records

### 1. `real-retrieval-graph-03` — "cross project search federation privacy" (limit 5, graph)

- Expected: `docs/federation/privacy-model.md`, `docs/federation/cross-project-search.md`
- Retrieved (top-5): `workflows_and_plans.md` ×3, `wiki/note-…-federation.md`, `docs/federation/cross-project-search.md`
- Missed: `docs/federation/privacy-model.md` — **exists ✓, indexed ✓ (5 chunks), clears threshold ✓** (hybrid ≈ 0.600), best chunk **rank ≈ 6** vs limit 5.
- The sibling `cross-project-search.md` was retrieved at rank 5 (hybrid ≈ 0.615); the two federation docs are effectively tied and one fell one slot short.
- **Compounding structural factor:** `workflows_and_plans.md` occupied **3 of the 5 slots** (3 of its chunks ranked 1–3). Budgeting is per-chunk, so a single source can crowd out distinct expected sources.
- **Category:** ranked just outside cutoff (near-miss) + single-source chunk domination.
- **Follow-up:** dedupe-by-source before applying the budget (or distinct-source budgeting). This one miss is recoverable without any ranking change.

### 2. `real-evidence-report-02` — "compose structured reports from evidence packs" (limit 5)

- Expected: `docs/review/overview.md`, `docs/review/citation-review.md`
- Retrieved (top-5): `docs/evidence/workflows.md`, `docs/reports/overview.md`, `docs/reports/cli.md`, `docs/API.md`, `docs/evidence/overview.md`
- Missed `docs/review/overview.md` — exists ✓, indexed ✓ (1 chunk), clears threshold ✓ (hybrid ≈ 0.469), rank ≈ 15.
- Missed `docs/review/citation-review.md` — exists ✓, indexed ✓ (1 chunk), clears threshold ✓ but barely (hybrid ≈ 0.238), ranked very deep.
- The query literally says "**reports**" and "**evidence packs**"; the retriever returned `docs/reports/*` and `docs/evidence/*` — arguably the *correct* answer to the query as written. The gold expects the `docs/review/*` workbench docs instead.
- **Category:** annotation / query-design mismatch (both links). This is the 0% query and the clearest case where 79% **understates** retrieval quality.
- **Follow-up:** re-annotate the gold (expect reports/evidence docs) **or** reword the query to name the review workbench. Do not "fix" retrieval — it behaved correctly.

### 3. `real-hard-ambiguous-01` — "agent workflow memory safety" (limit 5, graph)

- Expected: `docs/agent-workflows/codex.md`, `docs/agent-workflows/claude-code.md`, `docs/mcp/security.md`
- Retrieved (top-5): `wiki/note-cli-memory-test.md`, `docs/evaluation/provenance-real-run-2026-06-14.md`, `docs/workflows_and_plans.md`, `wiki/note-e2e-smoke-memory-note.md`, `docs/agent-workflows/codex.md`
- Missed `docs/agent-workflows/claude-code.md` — exists ✓, indexed ✓ (5 chunks), clears threshold ✓, hybrid ≈ 0.651 (strong), **rank ≈ 10**. A near-miss outranked by low-value wiki memory-notes that happen to match "memory".
- Missed `docs/mcp/security.md` — exists ✓, indexed ✓ (3 chunks), clears threshold ✓ (hybrid ≈ 0.347), **rank ≈ 138**. The query barely touches MCP security.
- The 4-word query is deliberately diffuse ("ambiguous" category) and the 3-doc gold is generous for it. `claude-code.md` is a cutoff near-miss; `mcp/security.md` is a genuine gap *given how little the query constrains it*.
- **Category:** `claude-code.md` → ranked outside cutoff (near-miss); `mcp/security.md` → query ambiguity / over-broad annotation.
- **Follow-up:** for the near-miss, the same source-dedupe/limit consideration as #1. For `mcp/security.md`, accept it as a true limitation of vague queries, or tighten the gold.

### 4. `real-hard-ambiguous-02` — "publication safety citations export" (limit 10)

- Expected: `docs/publication/safety.md`, `docs/publication/citation-appendix.md`, `docs/publication/export-formats.md`
- Retrieved (top-10): `export-formats.md`, `wiki/note-…-security-model.md`, `review/citation-review.md`, `publication/safety.md`, `publication/overview.md`, … (10 distinct sources)
- Missed `docs/publication/citation-appendix.md` — exists ✓, indexed ✓ (1 chunk), clears threshold ✓ but barely (hybrid ≈ 0.209), ranked beyond 10.
- **Root cause is lexical:** keyword score = **0.000**. The query token is "citation**s**" (plural); the doc heading is "Citation Appendix". The FTS5 tokenizer does not stem, so "citations" ≠ "citation" and the keyword channel contributes nothing. The doc survives on semantic similarity alone (0.522 → 0.4 × 0.522 ≈ 0.209).
- **Category:** tokenization / morphology gap (no stemming). The one **genuine retrieval weakness** in the set.
- **Follow-up:** enable a stemming / Porter tokenizer on the FTS table (or query expansion for plurals). This is a real, fixable retrieval improvement — but out of scope here (no ranking/indexing changes in this task).

## Category breakdown (6 missed links)

| Category | Links | Which |
|---|---|---|
| Ranked just outside cutoff (near-miss, recoverable) | 2 | graph-03 `privacy-model`, hard-01 `claude-code` |
| Annotation / query-design mismatch (gold debatable) | 2 | evidence-report-02 `review/overview`, `review/citation-review` |
| Query ambiguity / over-broad gold | 1 | hard-01 `mcp/security` |
| Tokenization / no-stemming gap (genuine retrieval weakness) | 1 | hard-02 `publication/citation-appendix` |
| Missing file | 0 | — |
| Not indexed (excluded) | 0 | — |
| Below relevance threshold | 0 | — |
| Corpus-drift deletion | 0 | — |

## What 79% does and does not reflect

- **It does not reflect a missing or unindexed corpus.** Every expected source
  exists and is indexed. Zero misses are data-loss or exclusion artefacts.
- **It does not primarily reflect "corpus drift" in the data-loss sense.** The
  index did shrink (~3395 → 3284 chunks since the run), but no *expected* source
  was lost. The drift only matters as *more competing chunks*, which is the same
  thing as the cutoff near-misses below.
- **~2 of 6 misses are ranking-cutoff near-misses** — config/instrumentation
  artefacts (limit, per-chunk budgeting), not retrieval-quality failures. A
  source-dedupe before budgeting would recover `real-retrieval-graph-03`
  outright.
- **~2 of 6 misses are annotation / query-design** — for
  `real-evidence-report-02` the retriever arguably returned the *better* answer
  to the query as written. Here 79% **understates** real quality.
- **1 of 6 is an over-broad gold** on an intentionally ambiguous query.
- **Only 1 of 6 (`publication/citation-appendix`) is a clean, genuine retrieval
  gap** — and it is a specific, nameable lexical limitation (no stemming on a
  plural), not a diffuse "retrieval is weak" signal.

## Safe vs. unsafe claims

**Safe to claim:**
- On this 12-query pilot, no recall miss was caused by a missing, unindexed, or
  sub-threshold source; all misses were ranking-cutoff outcomes.
- The single reproducible retrieval defect surfaced is a tokenizer/stemming gap
  (plural query term failing to keyword-match a singular heading).
- At least one miss (`real-evidence-report-02`) is an annotation/query-design
  artefact where the retriever's output is defensible, so 79% is a conservative
  floor, not a ceiling, of retrieval quality on this set.

**Not safe to claim:**
- Any precise recall *delta* vs. the 2C pilot (86%) — different index builds,
  reaffirmed here (see run doc).
- That "79% retrieval quality" generalises — it is a 12-query pilot, and 4 of the
  6 misses are gold/cutoff artefacts rather than retrieval failures, so the
  number is not a clean retrieval-quality estimate in either direction.
- That fixing stemming "would raise recall to X%" without re-running — out of
  scope; this task changed no ranking or indexing.

## Reproduction

```bash
# replay frozen gold vs frozen traces (unchanged scorer)
PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold_1b.jsonl
# per-miss ranking was produced read-only via scripts/hybrid_search.search_hybrid
# over the current data/search.sqlite; no files under data/ or eval/ were modified.
```
