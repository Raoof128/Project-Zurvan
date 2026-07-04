## Change Log

### 2026-07-04 (Australia/Sydney) — Ingest "Claude's Constitution" + full-ingestion fixes
**Raouf:**
- **Scope:** Phase 21 — ingest the Claude's Constitution PDF fully into local memory; fix two defects that made "full" ingestion impossible (truncated source text + unsplittable large chunks)
- **Summary:** Ingested `raw/papers/claudes-constitution_webPDF_26-02.02a.pdf` (191,856 chars, clean pypdf extraction) into local memory: registered source, full-text source page, stub concept/claim, **203 retrieval chunks**. **No LLM extraction run** — provider is `mock` (no API keys, no ollama), and mock extraction would fabricate claims/concepts citing the constitution, violating the no-fabrication rule. **(Fix 1 — `ingest.py`)** `create_source_page` stored only `text[:1000]` (a preview) → now stores the **complete** extracted text so the whole document is chunked and searchable. Added `source_page_stem()`: derived pages use a sanitised, extension-free slug so PDF/DOCX-derived pages are guard-safe (no `.pdf`/`.docx` substring in the tracked-candidate name) and drop the `.md.md` double suffix; source-page / stub-concept-claim / index writers anchored to `PROJECT_ROOT` (matches `ingest_image_stub`, makes them CWD-independent + unit-testable). **(Fix 2 — `chunk.py`)** heading-only splitting left the heading-less PDF prose as one 191k-char chunk — unsearchable (the sentence-transformer embeds only its first ~256 tokens; BM25 dilutes to noise). Added size-bounded sub-splitting: any heading-section over `MAX_CHUNK_CHARS` (1000, sized to the embedder window) is packed on line boundaries into ≤1000-char chunks, single over-long lines hard-wrapped. Small sections keep the **legacy chunk_id** (no `::idx::` segment), so ~94% of the corpus is byte-identical and reuses stored embeddings. Ingested source/concept/claim pages are gitignored by design ("private — generated from other projects"); they live in the working tree + local indices, so only the reusable code + tests + this log are committed.
- **Files Changed:** `scripts/ingest.py`, `scripts/chunk.py`, `tests/test_chunk.py` (+3), `tests/test_ingest.py` (+2). Local (gitignored) state: `raw/papers/claudes-constitution_webPDF_26-02.02a.pdf`, `wiki/sources/claudes-constitution_webPDF_26-02_02a.md` (full text, 194 KB), `wiki/concepts/AutoConcept-…`, `wiki/claims/Claim-…`, rebuilt `data/search.sqlite` (203 constitution chunks, corpus max chunk 191,874→1000 chars) + `data/graph.sqlite`.
- **Verification:** `pytest` → **262 passed, 0 failed** (+5). `eval_search --hybrid --min-top3 0.6` before/after the chunker change: **identical** (top-1 67%, top-3 100%, MRR 0.778) — the ~6% re-split touched no gold answer. Frozen provenance golds unchanged (2C 86% recall, 100% completeness/hash, 0% raw leak, 100% graph). Live search: constitution is the top hit for "Claude's character and values", "how should Claude handle harmful requests", "broad safety and honesty" with `hybrid_score` 0.94 (kw 1.0 / sem 0.86) and relevant snippets. `public_repo_guard.py` + `git diff --check` clean.
- **Follow-ups:** Claims/concepts/summaries for the constitution are stub-only until a real provider (`ZURVAN_LLM_PROVIDER=anthropic`/`ollama`) runs `extract.py` — do not treat the stub concept/claim as evidence. Pytest still writes real entries to `wiki/log.md` (test-isolation leak; reverted before commit) — worth fixing by monkeypatching `LOG_FILE`. `MAX_CHUNK_CHARS` (1000) can be retuned if a future eval regression appears.

### 2026-07-04 (Australia/Sydney) — Agent memory in practice (E1–E5)
**Raouf:**
- **Scope:** Phase 20 — make Zurvan a working memory for LLM agents: real embeddings, write-back habit, real-project federation, global CLI, staleness detection
- **Summary:** **(E1)** Semantic search is now real: `embed.py` caches SentenceTransformer models and accepts explicit provider/model; `search_hybrid` embeds queries with the provider/model **stored in the index** (mixing providers gave meaningless cosine scores); `db_path` params throughout; `test_hybrid_search` uses a temp index so pytest can no longer silently downgrade the real index to mock; `.claude/settings.json` sets `sentence_transformers`/`all-MiniLM-L6-v2`; live index rebuilt (3409 embeddings, 65s); eval gate passes (top-1 67%, top-3 100%, MRR 0.778). **(E2)** Write-back habit: `agent prime` closes with the decision/claim/question/postedit nudge; CLAUDE.md documents what to record. **(E3)** Federation refactored to run Zurvan's own retriever/graph in-process against each registered project's root (`_search_internal root=`, `expand_graph_context db_path=`) — previously it spawned `python -c "from scripts...."` inside the target repo, requiring every project to embed the whole engine; knowledge-only projects (wiki/+docs/) are now first-class, keyword federation needs no index, federated paths are project-relative. Registered real projects `[private-project]` ([private-project]) and `[private-project]` ([private-project]) with minimal wiki scaffolds; verified 3-project federated search, no warnings. **(E4)** `scripts/zurvan` self-locating wrapper symlinked to `/opt/homebrew/bin/zurvan` — callable from any project. **(E5)** `agent prime` now reports index freshness (newest file mtime vs newest `indexed_at`): fresh / STALE+command / missing.
- **Files Changed:** `scripts/embed.py`, `scripts/hybrid_search.py`, `scripts/rebuild_search_index.py`, `scripts/context_export.py`, `scripts/cross_project_search.py`, `scripts/cross_project_context.py`, `scripts/evidence_collect.py`, `scripts/agent_workflow.py`, `scripts/zurvan` (new), `.claude/settings.json`, `CLAUDE.md`, `docs/ENVIRONMENT.md`, `docs/API.md`, tests: `test_hybrid_search.py` (rewritten, temp-index), `test_cross_project_search.py` (rewritten, knowledge-only e2e), `test_cross_project_context.py`, `test_agent_workflow.py` (+3), `test_cli.py` (+1). Machine state: `~/.zurvan/projects.json` (+[private-project], +[private-project]), `/opt/homebrew/bin/zurvan` symlink, minimal `wiki/` scaffolds in [private-project] and [private-project] (+`AGENTS.md` stub in [private-project]).
- **Verification:** `pytest` → 257 passed, 0 failed (+6 over Phase 19). Index confirmed `sentence_transformers/all-MiniLM-L6-v2`; semantic scores meaningful on conceptual queries; `eval_search --hybrid --min-top3 0.6` passes. Frozen provenance golds untouched (score committed traces). Federated `search-all` returned results from all 3 valid projects, 0 warnings. `zurvan version/prime/search --json` verified from `/private/tmp` via the PATH wrapper (anaconda `python3` carries `sentence_transformers`+`mcp`). `public_repo_guard.py` + `git diff --check` clean per commit.
- **Follow-ups:** [private-project]/[private-project] wikis are scaffolds — content accrues via the write-back habit; build their search indexes (`zurvan --project <name> index search`) once they have content to make hybrid federation live. If PATH ever resolves a `python3` without `sentence_transformers`, queries fall back to mock **with a printed warning** (degraded, visible). `raoof128` registry entry is stale/invalid — deregister or repair someday.

### 2026-07-04 (Australia/Sydney) — Agent UX + R4 retrieval quality
**Raouf:**
- **Scope:** Phase 19 (agent ergonomics) + R4 (retrieval quality) — 7 features, one commit each (`efa4efc`…`c777c8e`)
- **Summary:** Made Zurvan cheaper and more effective for LLM agents to use. **(19a — `efa4efc`)** Added `CLAUDE.md`: a ~50-line agent quickstart (commands, hard rules, change protocol, layout) so sessions stop re-deriving basics. **(19b — `5db220f`)** Restructured `AGENTS.md`: the 7 rules + standing invariants (frozen artifacts, eval-rerun requirement, R3 status, quality gate) now open the file; all but the two most recent dated entries moved verbatim to `docs/agents-history.md` — a pure move, 44/44 entry headings verified preserved. **(19c — `d20305c`)** Machine-parseable output: `zurvan search --json`, `context --format json`, `eval search --json`, `eval provenance --json`. Compact payloads (repo-relative paths, scores, single-line snippets ≤200/300 chars, `trace_path` when tracing). Output format only — eval JSON is asserted equal to the returned metrics dict in tests; scoring untouched. **(19d — `d5636ef`)** New MCP tool `zurvan_read_page` (12 tools total) wrapping the existing `resource_file` gate (repo-relative only, no traversal, `raw/` blocked by default, 256 KB cap): many MCP clients never fetch resources, which forced context bundles to dump full text up front; now agents search compactly and open only the pages they need. **(19e — `fa77d83`)** Incremental index rebuild: since `chunk_id` hashes path+heading+text, unchanged chunks reuse their stored embedding (guarded by a provider/model probe so switching embedders re-embeds); an unchanged corpus re-index computes 1 embedding instead of ~3.4k. **(19f — `c777c8e`)** `zurvan agent prime`: topic-free ~300-token orientation card (rules digest, graph/index health, open-question count, recent activity), wired as a Claude Code SessionStart hook via `.claude/settings.json`; `agent preflight` remains the deep per-topic bundle. **(R4a — `69c5c53`)** FTS5 keyword index now uses `tokenize='porter unicode61'`. This is a documented ranking change: hybrid `eval_search` before → after: top-1 33% → **67%**, top-3 100% → 100% (0.6 gate passes), MRR 0.556 → **0.778**. It closes the single genuine lexical miss from the R1B analysis — query "citations" vs heading "Citation" scored kw 0.000 then, and `docs/publication/citation-appendix.md` now scores kw 0.862 on the live corpus. **(R4b — `d3931f2`)** `context --max-per-source N` (default 2, `0` restores old behaviour) dedupes a dominating source's chunks before the limit budget, recovering the R1B cutoff near-misses (one source previously took 3/5 slots); excess chunks appear in traces as `dropped` with new reason `source_dedupe` alongside `budget`. Search ranking is not touched by R4b — it shapes context assembly only.
- **Files Changed:** `CLAUDE.md` (new), `AGENTS.md`, `docs/agents-history.md` (new), `.claude/settings.json` (new), `scripts/context_export.py`, `scripts/eval_search.py`, `scripts/eval_provenance.py`, `scripts/cli.py`, `scripts/mcp_tools.py`, `scripts/mcp_server.py`, `scripts/rebuild_search_index.py`, `scripts/agent_workflow.py`, `docs/API.md`, `tests/test_context_export.py`, `tests/test_eval_search.py`, `tests/test_eval_provenance.py`, `tests/test_mcp_tools.py`, `tests/test_hybrid_search.py`, `tests/test_agent_workflow.py`, `tests/test_cli.py`
- **Verification:** Full `pytest` → **251 passed, 0 failed** (was 238; +13 new tests). Frozen provenance golds re-scored **unchanged** after both R4 changes (they score committed traces): 2C pilot 86% recall, 1B 79%, both 0% raw leak / 100% hash integrity / 100% completeness / 100% graph presence. `eval_search --hybrid --min-top3 0.6` passes. Embedding reuse measured live: 3400 reused / 1 computed (the probe) on an unchanged corpus. `zurvan_read_page` verified registered on the server and blocking `../`, absolute, and `raw/` paths. `agent prime` asserted < 4k chars. `public_repo_guard.py` + `git diff --check` clean at every commit.
- **Follow-ups:** R4a/R4b improve the **live** retriever; the frozen 1B gold's 79% recall is a property of its committed traces and was deliberately not re-generated. A future step should re-trace the 12 frozen queries as a *new* artifact (2C/1B pattern) to quantify the post-R4 recall gain honestly. R3 (MCP/tool-call provenance) remains unbuilt.

### 2026-07-04 (Australia/Sydney)
**Raouf:**
- **Scope:** Full project audit — file-by-file bug hunt + fixes
- **Summary:** Read every script in `scripts/` and fixed 9 confirmed defects, none touching R3 scope, retrieval ranking semantics, trace schema, or the frozen eval artifacts. **Environment:** the shared interpreter had `starlette` 1.3.1 (upgraded transitively by `mcp`) alongside `fastapi` 0.111.0, which still passes the removed `on_startup` kwarg — the review workbench could not even construct its app (`TypeError: Router.__init__`), failing 8 tests. Upgraded fastapi to 0.139.0 and pinned `fastapi>=0.133.0` (first release accepting `starlette>=1.0`). **Crash fixes:** `search_hybrid` crashed with `fts5: syntax error` on any query containing a bareword FTS5 keyword (`AND`/`OR`/`NOT`/`NEAR`) — each term is now passed as a quoted FTS5 string (identical matching and BM25 ranking for plain tokens, verified); symbol-only queries also handled. `extract.py` opened `data/extractions/` and `wiki/summaries/` for write without `makedirs` (fresh-tree crash) and joined claim tags with a literal backslash-n, producing malformed one-line YAML — fixed both and repaired the one affected wiki page. **CWD-independence (data-loss class):** `chunk.py` scanned `wiki/`+`docs/` relative to the CWD, so `zurvan index search` from any other directory (e.g. an MCP-launched process) deleted `data/search.sqlite` and rebuilt it with 0 chunks; it now walks `PROJECT_ROOT` while keeping source paths repo-relative, so every pre-existing chunk ID is byte-identical. `memory.add_claim` validated the source path against the CWD, breaking `zurvan_claim_add` from MCP. `cli.py`'s subprocess-backed commands used a bare `python`, CWD-relative script paths, **and discarded the child's exit code** — `zurvan eval search --min-top3 0.6` reported success even when the gate failed; a shared `_run_script()` now uses `sys.executable`, anchors `cwd=PROJECT_ROOT`, and propagates return codes. **Safety/robustness:** `cross_project_search` interpolated the raw query into generated Python executed in a subprocess (quotes broke the code; effectively code injection) — the query now travels as an argv element; all federation subprocesses use `sys.executable`; the federated `snippet` field (previously always `None`) now carries real excerpt text into `context-all`/evidence packs. `context_export._save_synthesis` wrote absolute machine paths into tracked `wiki/syntheses/` frontmatter for keyword-mode saves — now repo-relative (public-repo-safety rule). `review_audit.audit_report`'s corrupt-report early return omitted `stats`, so one bad `report.json` crashed `review index rebuild` and the dashboard.
- **Files Changed:** `requirements.txt`, `scripts/hybrid_search.py`, `scripts/chunk.py`, `scripts/extract.py`, `scripts/memory.py`, `scripts/cross_project_search.py`, `scripts/cross_project_context.py`, `scripts/evidence_collect.py`, `scripts/cli.py`, `scripts/context_export.py`, `scripts/review_audit.py`, `wiki/claims/claim-dummy-001.md` (data repair), `tests/test_chunk.py`, `tests/test_hybrid_search.py`, `tests/test_cross_project_search.py`, `tests/test_memory.py`, `tests/test_extract.py` (new), `tests/test_cli.py`
- **Verification:** Full `pytest` → **238 passed, 0 failed** (session baseline: 220 passed, 1 failed, 7 errors; +10 new regression tests covering every fix class). Frozen gold sets re-scored bit-identical: 2C pilot 86% recall, 1B 79%, both 0% raw leak / 100% hash integrity / 100% completeness / 100% graph presence. `eval_search --hybrid --min-top3 0.6` → top-3 100%. Post-fix index rebuild verified to preserve the full pre-fix chunk-ID set. Functional spot checks: FTS keyword query returns results instead of raising; `scan_markdown_files()` from `/private/tmp` finds 847 files (was 0). `public_repo_guard.py` passed; `git diff --check` clean; `python -m compileall scripts/` OK; test-noise appended to `wiki/log.md` by the suite was reverted.
- **Follow-ups:** Migrate `ingest.py`/`extract.py`/`audit_wiki.py` internals off CWD-relative `wiki/` writes (currently safe through the CLI since subprocesses now run with `cwd=PROJECT_ROOT`). Consider a friendlier error when `report_export`'s belt-and-braces redaction rewrites 64-char content hashes to `[REDACTED_TOKEN]`. R3 (MCP/tool-call provenance) remains frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Step 2D — RQ1 provenance research checkpoint (freeze before R3)
- **Summary:** Consolidated and froze the retrieval/context provenance subsystem as the RQ1 baseline. All three feature branches landed on `main` (`phase-r1-trace-core` → `phase-r1b-provenance-events` → `phase-r1b-followups`, merge `021572e`). Added `docs/evaluation/provenance-rq1-checkpoint-2026-06-14.md` capturing branch lineage, verified metrics, the miss-analysis summary, safe-vs-unsafe claims, limitations, and the R3 scope; tagged the commit `rq1-provenance-checkpoint-2026-06-14`. No code or behaviour change — documentation + tag only.
- **Files Changed:** `docs/evaluation/provenance-rq1-checkpoint-2026-06-14.md` (new), `AGENTS.md`, `CHANGELOG.md`. Git tag `rq1-provenance-checkpoint-2026-06-14`.
- **Verification (on `main` @ 021572e):** 2C frozen pilot (`provenance_real_gold.jsonl`) → recall 86%, raw leak 0%, hash 100%, completeness 100%, graph 100%, validate OK. 1B enriched re-run (`provenance_real_gold_1b.jsonl`) → recall 79%, raw leak 0%, hash 100%, completeness 100%, graph 100%, validate OK. Full `pytest` → 228 passed (2 dependency warnings). `public_repo_guard.py` passed. `git diff --check` clean.
- **Follow-ups:** Begin **R3** — opt-in, audit-safe MCP/tool-call + memory/resource provenance events; no behaviour changes; no benchmark yet. Keep phrasing as "100% instrumentation coverage over currently implemented retrieval/context provenance events" (not "complete provenance"): R3 unbuilt and graph is scored via `graph_context`, not a standalone `graph.expand`.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R1B follow-ups — recall miss-analysis (#2) + scorer negative-branch tests (#1)
- **Summary:** Two read-only/coverage analysis tasks on `phase-r1b-provenance-events` after the draft PR. No R3; no change to retrieval ranking, scoring, indexing, graph behaviour, or trace schema; the frozen 2C pilot and the 1B real-run traces were not mutated. **(#2) Recall miss-analysis.** Categorised the residual behind `expected_source_recall = 79%` (macro) / 74% (micro). Of 23 expected links across 12 queries, 6 are missed (4 queries; the other 8 score 100%). Every missed file **exists, is indexed, and clears the `hybrid_score > 0.2` gate** — all 6 are ranking-cutoff outcomes, zero are data-loss/exclusion. Categories: 2 cutoff near-misses (`federation/privacy-model` rank≈6 vs limit 5, compounded by `workflows_and_plans.md` taking 3/5 slots via per-chunk budgeting; `agent-workflows/claude-code` rank≈10), 2 annotation/query-design mismatches (`real-evidence-report-02`: query names "reports"/"evidence packs", retriever returned those, gold expected `docs/review/*` — 79% understates quality here), 1 over-broad gold (`mcp/security` on the diffuse "agent workflow memory safety"), 1 genuine lexical gap (`publication/citation-appendix`: query "citations" gets `kw=0.000` vs heading "Citation" under the non-stemming FTS5 tokenizer; survives on semantics → hybrid 0.209). Conclusion: only 1 of 6 misses is a clean retrieval defect; 79% is a conservative floor, not a corpus-drift/data-loss signal. **(#1) Scorer negative-branch tests.** Added two regression tests proving the conditional scorer discriminates: the same fusion-less trace scores 100% completeness against a keyword-only gold (fusion not demanded) but is incomplete against a hybrid gold; the same graphless trace keeps `graph_context_presence` 1.0 for a non-graph gold (case skipped) but fails the graph gate for a graph gold. Proven to bite by temporarily mutating the scorer unconditional (both red), then reverting.
- **Files Changed:**
  - `docs/evaluation/provenance-real-run-1b-miss-analysis-2026-06-14.md` — new (per-miss records, category breakdown, safe/unsafe-claims)
  - `docs/evaluation/provenance-real-run-1b-miss-analysis-2026-06-14.json` — new (machine-readable sidecar)
  - `tests/test_eval_provenance.py` — `+2` (`…does_not_demand_fusion_for_keyword_only_query`, `…does_not_score_graph_for_non_graph_query`)
  - No production code changed.
- **Verification:** `pytest` → 228 passed (was 226; +2), 0 failed. Both new tests verified red on a mutated (unconditional) scorer and green after `git checkout -- scripts/eval_provenance.py` (no production change committed). Frozen 1B gold re-scored 79% recall / 100% completeness / 100% graph presence — unchanged. `public_repo_guard.py` passed; `git diff --check` clean; `compileall` OK.
- **Follow-ups:** Genuine lexical miss is fixable via FTS stemming/Porter tokenizer or plural query expansion; cutoff near-misses via source-dedupe before budgeting — both deferred (would touch indexing/ranking). `real-evidence-report-02` warrants gold re-annotation. R3 MCP/tool-call tracing remains frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R1B — provenance event enrichment (`phase-r1b-provenance-events`)
- **Summary:** Lifted the RQ1 provenance ceiling beyond the Step 2C 3-event subset. (1) Added a new `retrieval.fusion` event recording the existing hybrid fusion — weights `{fts:0.6, embedding:0.4}` and per-chunk `{fts_score, embedding_score, fused_score, rank}` — emitted only when `mode == hybrid`. Observe-only: `_apply_budget` keeps the identical top-`limit` slice, so ranking is unchanged. (2) Populated `context.assembled.dropped` with genuine `{chunk_id, reason:"budget"}` drops from the over-budget candidate remainder, plus an explicit `dropped_reason: "no_dropped_context"` when empty so "nothing dropped" is asserted, not incidental. (3) Deliberately did **not** add `graph.expand`: the existing `graph_context` event already records `--graph` expansion and is scored via the existing `expect_graph_context` gold flag; a parallel event would fragment the one-event-per-stage model. (4) Fixed a retrieval confound surfaced by the re-run — derived trace mirrors (`wiki/traces/*.md`) were indexed and polluted results with the query's own terms; now excluded from both the FTS index (`chunk.py`) and the keyword glob (`context_export.py`). The frozen 12-query Step 2C pilot was re-run as a **separate** artifact under new `…-r1b` trace IDs and `eval/provenance_real_gold_1b.jsonl`, leaving the original 2C pilot (gold + traces) frozen for pre-registration integrity.
- **Files Changed:**
  - `scripts/trace_schema.py` — `retrieval.fusion` added to `ALLOWED_EVENT_TYPES`
  - `scripts/context_export.py` — `_fusion_payload()`, `_apply_budget()`, `_assembled_context_payload(dropped=…)` + empty-case reason, fusion event emit, trace-mirror skip in keyword search
  - `scripts/chunk.py` — `scan_markdown_files()` excludes `wiki/traces/`
  - `tests/test_provenance_enrichment.py` — new (7 tests: schema, fusion payload, dropped empty/non-empty, genuinely-dropping budget fixture, hybrid emits / keyword omits fusion)
  - `tests/test_chunk.py` — `+1` (trace-mirror exclusion)
  - `tests/test_trace_retrieval_integration.py` — updated to the new event shape (fusion + dropped_reason)
  - `eval/provenance_real_gold_1b.jsonl`, `data/traces/…-r1b00001..12` (+ wiki mirrors), `docs/evaluation/provenance-real-run-1b-2026-06-14.md` — new enriched re-run artifacts
- **Verification:** `pytest` → 226 passed (was 218 on this branch; +8), 0 failed. Enriched re-run: `eval_provenance --validate` OK; validate 12/12, replay 12/12; `retrieval.fusion` present in 12/12 traces; genuine non-empty `dropped` (reason `budget`) on 7/7 context traces with real chunk IDs; `provenance_completeness` 100% over the enriched pipeline; `graph_context_presence` 100%; `raw_leak_rate` 0%; `hash_integrity_rate` 100%. `expected_source_recall` 79% (down from 86%) attributed to corpus drift, not the ranking-neutral enrichment — the frozen 2C pilot still scores 86% against its own committed traces. `public_repo_guard.py` passed; `git diff --check` clean; `compileall` OK.
- **Follow-ups:** The all-hybrid frozen set cannot exercise the "fusion legitimately absent" path (no keyword-only query) — a future query would honestly test the conditional denominator. `dropped` reasons currently limited to `budget`; `dedupe` and relevance-threshold reasons are future work. R3 MCP/tool-call tracing remains frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2C — real-corpus provenance pilot
- **Summary:** Added an auditable real-corpus pilot run before R3. First committed `eval/provenance_real_queries.jsonl` as a frozen 12-query set (`48a8c27`) with manual expected-source annotations from wiki/docs. Then generated traces using existing `search --trace`, `context --trace`, and selected `context --graph --trace`; linked them in `eval/provenance_real_gold.jsonl`; and documented results in `docs/evaluation/provenance-real-run-2026-06-14.md`. The pilot scored 0% raw leak rate, 100% hash integrity, 12/12 trace validate/replay, 86% expected source recall, 100% built-scope provenance completeness, and 100% graph context presence. No R3, MCP tracing, retrieval ranking, graph behavior, trace schema, or evaluator scoring changes.
- **Files Changed:** `eval/provenance_real_queries.jsonl`, `eval/provenance_real_gold.jsonl`, `data/traces/trace-20260614T171000Z-real*.json`, `wiki/traces/trace-20260614T171000Z-real*.md`, `docs/evaluation/provenance-real-run-2026-06-14.md`, `docs/evaluation/provenance.md`, `docs/workflows_and_plans.md`, `eval/README.md`
- **Verification:** Search and graph indexes rebuilt for the run. All 12 frozen queries generated traces successfully. `eval_provenance.py --gold eval/provenance_real_gold.jsonl --validate` passed. `eval_provenance.py --gold eval/provenance_real_gold.jsonl` reported 12 cases, 0% raw leak rate, 100% hash integrity, 86% expected source recall, 100% provenance completeness, and 100% graph context presence. Trace validate/replay rate was 12/12. `python -m compileall scripts/eval_provenance.py scripts/cli.py` passed. `pytest tests/test_eval_provenance.py` → 7 passed. Full `pytest` → 218 passed, 2 dependency warnings. `public_repo_guard.py` passed. `git diff --check` passed.
- **Follow-ups:** Treat this as a pilot, not a benchmark. Expand real-world gold and annotation review before making real-world provenance completeness claims. R3 remains frozen until merge/review.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2B — stronger provenance gold set
- **Summary:** Expanded provenance evaluation from one controlled fixture to a six-case passing baseline plus isolated negative/failure gold files. Positive cases now cover `search --trace`, `context --trace`, `context --graph --trace`, legacy coarse `retrieval`, the original controlled Step 2 fixture, and a stale/superseded note case labelled for later policy-aware scoring. Negative fixtures cover raw-path invariant failure, incomplete trace completeness failure, and missing expected source recall failure. No MCP tracing, retrieval ranking, graph behavior, or schema changes.
- **Files Changed:** `eval/provenance_gold.jsonl`, `eval/provenance_gold_negative.jsonl`, `eval/provenance_gold_incomplete.jsonl`, `eval/provenance_gold_low_recall.jsonl`, `data/traces/trace-20260614T16170*.json`, `tests/test_eval_provenance.py`, `README.md`, `docs/TESTING.md`, `docs/evaluation/provenance.md`, `docs/workflows_and_plans.md`, `eval/README.md`
- **Verification:** Step 2B red tests failed on the missing gold cases/files. `python -m compileall scripts/eval_provenance.py scripts/cli.py` passed. Focused provenance+trace tests → 27 passed. Full `pytest` → 218 passed, 2 dependency warnings. Positive provenance gold validates and scores 100% across built-scope metrics on 6 cases. Negative fixtures fail as expected for raw leak, completeness threshold, and source-recall threshold. All Step 2B traces validate. `public_repo_guard.py` passed. `git diff --check` passed.
- **Follow-ups:** R3 remains frozen until merge/review. Larger real-world gold sets are still needed before claiming real-world provenance completeness.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2 — provenance evaluation harness
- **Summary:** Added `scripts/eval_provenance.py` and `eval/provenance_gold.jsonl` to evaluate saved trace provenance with hard invariant gates before graded scoring. The harness checks `raw_leak_rate=0%` and `hash_integrity_rate=100%` before scoring `expected_source_recall`, `provenance_completeness`, and `graph_context_presence`. Gold schema includes optional `expected_chunk_ids` for later claim-to-chunk faithfulness without a file-shape rewrite. CLI wiring added as `zurvan eval provenance`.
- **Files Changed:** `scripts/eval_provenance.py`, `scripts/cli.py`, `eval/provenance_gold.jsonl`, `data/traces/trace-20260614T151617Z-prov0001.json`, `tests/test_eval_provenance.py`, `docs/evaluation/provenance.md`, `eval/README.md`, `docs/API.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `README.md`
- **Verification:** TDD red run failed on missing `scripts.eval_provenance`; CLI red run failed on missing `eval provenance` action. `python -m compileall scripts/eval_provenance.py scripts/cli.py scripts/trace_schema.py scripts/trace_validate.py scripts/trace_replay.py scripts/context_export.py` passed. Focused provenance+trace tests → 25 passed. Full `pytest` → 216 passed, 2 dependency warnings. `eval_provenance.py --validate` passed. CLI `eval provenance` returned 100% for all built-scope metrics. `public_repo_guard.py` passed. `git diff --check` passed.
- **Follow-ups:** R3 MCP trace integration remains frozen. Future provenance events `retrieval.fusion` and `graph.expand` are intentionally outside the current scoring scope.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 1A — minimal trace granularity enrichment
- **Summary:** Added fine-grained retrieval trace events `retrieval.query`, `retrieval.result`, and `context.assembled` while keeping legacy coarse `retrieval` traces valid under `zurvan.trace.v1`. Context assembly now records ordered included chunk IDs and dropped entries; keyword traces derive deterministic fallback chunk IDs for trace provenance only. Retrieval ranking, scoring, stdout behavior, schema version, and payload-hash rules are unchanged.
- **Files Changed:** `scripts/trace_schema.py`, `scripts/context_export.py`, `tests/test_trace_replay.py`, `tests/test_trace_retrieval_integration.py`, `README.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `docs/audits/phase-r2-retrieval-trace-integration-audit-2026-06-14.md`
- **Verification:** `python -m compileall scripts/trace_schema.py scripts/trace_writer.py scripts/trace_validate.py scripts/trace_replay.py scripts/context_export.py scripts/hybrid_search.py scripts/graph_context.py scripts/cli.py` passed. Focused trace tests → 20 passed. Full `pytest` → 211 passed, 2 dependency warnings. Temp-root E2E `context --trace` produced `retrieval.query` → `retrieval.result` → `context.assembled`; `trace validate` and `trace replay` accepted it. Legacy single-`retrieval` replay regression passed. `public_repo_guard.py` passed. `git diff --check` passed.
- **Follow-ups:** Build Step 2 provenance evaluation harness next. R3 MCP trace integration remains frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 — retrieval trace integration
- **Summary:** Added opt-in retrieval tracing for `search --trace` and `context --trace` without changing normal retrieval output or ranking when tracing is off. Traces reuse the Phase R1 schema and write JSON to `data/traces/` plus Markdown mirrors to `wiki/traces/`. Retrieval events record command, query, mode, limit, result count, source paths, and available keyword/semantic/hybrid scores; graph-enabled context traces also record graph depth, node count, relation, node type, title, and source node ID. Unsafe trace IDs now fail cleanly without traceback.
- **Files Changed:** `scripts/context_export.py`, `scripts/cli.py`, `scripts/trace_schema.py`, `tests/test_trace_retrieval_integration.py`, `tests/test_trace_schema.py`, `README.md`, `docs/API.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `docs/audits/phase-r2-retrieval-trace-integration-audit-2026-06-14.md`
- **Verification:** `PYTHONPATH=. pytest tests/test_trace_schema.py tests/test_trace_writer.py tests/test_trace_validate.py tests/test_trace_replay.py tests/test_trace_cli.py tests/test_trace_retrieval_integration.py` → 19 passed. `PYTHONPATH=. pytest` → 210 passed, 2 dependency warnings. `public_repo_guard.py` passed. `git diff --check` passed in the branch. E2E temp-root `context --trace` and `search --trace` produced replayable traces; unsafe `../raw/secret` trace ID returned nonzero cleanly.
- **Follow-ups:** Phase R3 MCP trace integration remains frozen until provenance granularity and `eval_provenance.py` are complete.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R1 — local audit trace core
- **Summary:** Added `zurvan.trace.v1` local audit traces with deterministic payload hashes, safe trace/event IDs, JSON storage under `data/traces/`, Markdown mirrors under `wiki/traces/`, validation, and replay. Added CLI commands: `zurvan trace list`, `inspect`, `validate`, and `replay`. Replay validates trace integrity before rendering and does not execute tools, read raw sources, or call networks.
- **Files Changed:** `scripts/trace_schema.py`, `scripts/trace_writer.py`, `scripts/trace_validate.py`, `scripts/trace_replay.py`, `scripts/cli.py`, `tests/test_trace_schema.py`, `tests/test_trace_writer.py`, `tests/test_trace_validate.py`, `tests/test_trace_replay.py`, `tests/test_trace_cli.py`, `data/traces/.gitkeep`, `wiki/traces/.gitkeep`, `README.md`, `docs/API.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `docs/audits/phase-r1-trace-core-audit-2026-06-14.md`, `docs/audits/phase-r1-trace-core-audit-2026-06-14.html`
- **Verification:** R1 was rechecked as part of the Phase R2 branch gate. Trace and retrieval-trace focused tests → 19 passed. Full `pytest` → 210 passed, 2 dependency warnings. `public_repo_guard.py` passed. `git diff --check` passed in the branch.
- **Follow-ups:** Retrieval tracing shipped in R2. MCP trace integration remains Phase R3 and is intentionally frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP install — verify Claude Code + add Codex client
- **Summary:** Installed/verified the Zurvan MCP server on both local agents. **Claude Code:** already registered in `~/.claude.json` and reported `✔ Connected` by `claude mcp list`, pointing at the live `scripts/mcp_server.py`, so it auto-picks-up all the recent server improvements — no reinstall needed. **Codex:** added globally with `codex mcp add zurvan` using the absolute Anaconda interpreter (`/opt/anaconda3/bin/python3`, which has `mcp` 1.25 + `pydantic` 2.12) and the absolute server path; verified via `codex mcp get zurvan` and a launch smoke-test (`list_tools()` → 11 tools). Made it reproducible: extended `scripts/install_mcp_config.py` with a `codex` client target that emits both a ready-to-run `codex mcp add` command and a `[mcp_servers.zurvan]` TOML block (uses `sys.executable` so the launch doesn't depend on Codex's PATH). Added `docs/mcp/codex.md`.
- **Files Changed:**
  - `scripts/install_mcp_config.py` — `get_codex_config()`, `render_codex_toml()`, `render_codex_cli()`, `codex` choice
  - `tests/test_install_mcp_config.py` — 3 new tests (codex config, render, main)
  - `docs/mcp/codex.md` — New Codex integration guide
  - (machine state) `~/.codex/config.toml` — `zurvan` MCP server added
- **Verification:** `pytest` → 191 passed (was 188; +3), 0 failed. `claude mcp list` → `zurvan ✔ Connected`. `codex mcp get zurvan` shows the server; launch smoke-test exposed 11 tools with the Codex interpreter+env. `public_repo_guard.py` passed.
- **Follow-ups:** None.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP server — per-argument schema docs + structured output
- **Summary:** Implemented the two follow-ups from the MCP audit. (1) Added `Annotated[..., Field(description=...)]` to every parameter of all 11 tools, so the JSON input schema now carries per-argument descriptions (not just the tool-level docstring) — the LLM sees exactly what each arg means. Added numeric bounds where sensible (`limit` 1–50, `depth` 1–5, `min_top3` 0–1) so out-of-range calls are rejected with a clear schema error. (2) Added structured output to `zurvan_graph_stats`: it now returns a `GraphStats` TypedDict (`{nodes, edges}`), so FastMCP emits a proper `outputSchema` and `structuredContent` alongside a JSON text fallback — machine-parseable for clients that support it. Kept the text-rich tools (`search`/`context`) as curated human+LLM-readable text by design, since that format already carries all fields and reads better than raw JSON.
- **Files Changed:**
  - `scripts/mcp_server.py` — `Annotated`/`Field` per-arg descriptions + bounds on all tools; `GraphStats` TypedDict structured output
  - `scripts/mcp_tools.py` — `tool_zurvan_graph_stats_struct()` returning typed `{nodes, edges}`
  - `tests/test_mcp_tools.py` — test for the structured stats result
- **Verification:** `pytest` → 188 passed (was 187; +1), 0 failed. `e2e_mcp_smoke.py` → full pass. Confirmed per-arg descriptions present in `inputSchema` and that `zurvan_graph_stats` returns both `structuredContent` `{'nodes': 830, 'edges': 746}` and a JSON text fallback with a generated `outputSchema`. `public_repo_guard.py` passed.
- **Follow-ups:** None.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP server full audit — LLM usability + correctness fixes
- **Summary:** Audited all five MCP modules and fixed both correctness bugs and (the main goal) discoverability for calling LLMs. **Biggest win:** every one of the 11 `@mcp.tool()` wrappers in `mcp_server.py` exposed an *empty* description to the LLM — FastMCP reads the wrapper's `__doc__`, but the rich docstrings lived only on the underlying `tools.*` functions. Rewrote `mcp_server.py` with detailed, model-facing docstrings (what/when/args/returns) for every tool, resource, and prompt, plus `description=` on all resources. Added `Literal` enums so the schema tells the LLM the valid values: `zurvan_remember.type`, `zurvan_decision_add.status`, `zurvan_claim_add.confidence`. **Correctness:** (1) `mcp_security.is_safe_path` and `mcp_resources.resource_file` anchored to `Path(".")`/CWD instead of `PROJECT_ROOT`, so `zurvan://file/{path}` broke when the server launched from another directory — now anchored to `PROJECT_ROOT`. (2) `tool_zurvan_eval_search`/`tool_zurvan_validate_gold` shelled out via `subprocess.run(["python", "scripts/eval_search.py"...])` (relative path + wrong-interpreter risk + CWD-dependent); rewrote to run in-process with `contextlib.redirect_stdout` capture (also prevents eval `print()` from corrupting the stdio JSON-RPC stream) and catch `SystemExit`. (3) Dropped the no-op `depth` arg from `zurvan_graph_neighbours` (it was accepted but never used). (4) `zurvan_remember` silently discarded `type`; it is now preserved as the note's first tag. (5) `zurvan_search` now returns heading + a whitespace-collapsed snippet per hit, not just path+score, so the LLM can judge relevance without extra reads.
- **Files Changed:**
  - `scripts/mcp_server.py` — Rich docstrings + `Literal` enums; dropped neighbours `depth`; resource descriptions
  - `scripts/mcp_tools.py` — In-process eval/validate w/ stdout capture; search snippets; `remember` honours `type`
  - `scripts/mcp_security.py` — `is_safe_path` anchored to `PROJECT_ROOT`
  - `scripts/mcp_resources.py` — `resource_file` reads via `PROJECT_ROOT`
- **Verification:** `pytest` → 187 passed, 0 failed. `scripts/e2e_mcp_smoke.py` → full pass (tools/resources/prompts exposed, read-only write blocked, write-mode works, raw/traversal blocked, in-process eval works). Confirmed all 11 tool descriptions are now non-empty via `mcp.list_tools()`. Verified `resource_file('wiki/index.md')` reads correctly from `/tmp` (was broken pre-fix) while `../` traversal stays blocked. `public_repo_guard.py` passed.
- **Follow-ups:** Consider per-parameter `Annotated[..., Field(description=...)]` for even richer arg docs, and structured JSON tool output, if a future client benefits.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Full audit — update OpenAI default model (GPT-5.x) + temperature safety
- **Summary:** Closed the second documented follow-up (deferred from Phase 18 to "a separate small PR"). Verified current OpenAI API model naming against official docs (developers.openai.com): `gpt-4o` is superseded by the GPT-5 family. Bumped the openai provider default from `gpt-4o` to `gpt-5.4-mini` (current cost-efficient model with JSON mode + large context; override via `ZURVAN_LLM_MODEL`, e.g. `gpt-5.5`). Critically, the GPT-5 family and o-series reasoning models **reject** any non-default `temperature` (HTTP 400 "Only the default (1) value is supported"), but `_call_openai` unconditionally sent `temperature=0.0` — so a naive model bump would have broken every OpenAI call. Added `_openai_supports_custom_temperature()` which omits the `temperature` field for `gpt-5*` and `o1/o3/o4*` models while still sending it for legacy models (gpt-4o etc.). `response_format=json_object` retained (still supported). Updated `docs/ENVIRONMENT.md` to match; left the Phase 18 design spec as a historical record.
- **Files Changed:**
  - `scripts/llm.py` — `gpt-5.4-mini` default + conditional temperature gating
  - `tests/test_llm.py` — 4 new tests (default model, GPT-5 + o-series omit temperature, legacy sends it)
  - `docs/ENVIRONMENT.md` — Updated model column/defaults + temperature note
- **Verification:** `pytest` → 187 passed (was 183; +4 new), 0 failed. `public_repo_guard.py` passed.
- **Follow-ups:** None outstanding from the documented list.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Full audit — finish CWD-independence for remaining non-MCP scripts
- **Summary:** Completed the outstanding follow-up from the 2026-06-03 PROJECT_ROOT migration. Audited every script for working-directory–dependent file I/O and fixed the three genuine offenders. `graph_build.py` walked `os.walk('.')`, so building from any directory other than the repo root silently produced an empty graph; it now walks `PROJECT_ROOT` while keeping node identity repo-relative (via `os.path.relpath`) so existing `node_id` hashes and path-based type detection are unchanged. `get_file_content()` now resolves relative paths against `PROJECT_ROOT` before reading. `graph_export.py` default Markdown/DOT output paths are now absolute (`PROJECT_ROOT/data/...`) and the `makedirs` call is guarded against a bare filename. `eval_search.py` now resolves the gold file, every `expected_paths` existence check, and the keyword-fallback `wiki/`+`docs/` globs against `PROJECT_ROOT` via a new `_resolve()` helper. Confirmed the remaining `data/`-prefixed strings in `snapshot.py` (joined onto its own `ROOT`) and `public_repo_guard.py` (compared against repo-relative `git ls-files` output) are correct by design and need no change.
- **Files Changed:**
  - `scripts/graph_build.py` — Walk `PROJECT_ROOT`, relative node identity, abs content reads
  - `scripts/graph_export.py` — Absolute default export paths + guarded `makedirs`
  - `scripts/eval_search.py` — `_resolve()` helper for gold file, expected paths, and globs
- **Verification:** `pytest` → 183 passed, 0 failed. Ran `graph_build`, `eval_search --validate`, and `graph_export` from `/tmp` (with `PYTHONPATH` set): graph built 830 nodes / 746 edges (was 0 before the fix), gold validated, export wrote to the repo `data/` dir. `eval_search --hybrid --min-top3 0.6` → top-3 100%. Node `path` column confirmed still repo-relative (`AGENTS.md`).
- **Follow-ups:** Review OpenAI model default in `llm.py` (GPT-5.x) — left untouched as it is a configuration judgment, not a correctness bug.

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** Fix: CWD-independent absolute paths via PROJECT_ROOT
- **Summary:** Eliminated all relative `data/` and `wiki/` path strings so Zurvan works correctly regardless of the process working directory. Added `PROJECT_ROOT = Path(__file__).parent.parent.resolve()` to `scripts/config.py`. Updated 8 scripts to import and use it: `graph_query.py`, `graph_schema.py`, `hybrid_search.py`, `memory.py`, `context_export.py`, `mcp_resources.py`, `wiki_merge.py`, `ingest.py`, `rebuild_search_index.py`. Also fixed `rebuild_search_index.py` to use `INSERT OR IGNORE` to handle the 30 pre-existing duplicate chunk_ids in the wiki. Updated 4 test files (`test_wiki_merge.py`, `test_context_export.py`, `test_ingest.py`, `test_cli.py`) to replace `monkeypatch.chdir` with `monkeypatch.setattr(..., "PROJECT_ROOT", tmp_path)` so tests remain isolated without relying on CWD. Restored `~/.claude.json` MCP config to clean `python` command (no bash wrapper needed).
- **Files Changed:**
  - `scripts/config.py` — Added `PROJECT_ROOT`
  - `scripts/graph_query.py`, `scripts/graph_schema.py` — Absolute `data/graph.sqlite`
  - `scripts/hybrid_search.py` — Absolute `data/search.sqlite`
  - `scripts/rebuild_search_index.py` — Absolute `data/search.sqlite` + `INSERT OR IGNORE`
  - `scripts/memory.py` — Absolute all `wiki/` paths
  - `scripts/context_export.py` — Absolute wiki glob + syntheses dir
  - `scripts/mcp_resources.py` — Absolute `wiki/` and `eval/` paths
  - `scripts/wiki_merge.py` — Absolute `wiki/log.md`
  - `scripts/ingest.py` — Absolute `wiki/sources` and `data/image_manifest.json`
  - `tests/test_wiki_merge.py` — Replace `chdir` with `setattr PROJECT_ROOT`
  - `tests/test_context_export.py` — Replace `chdir` with `setattr PROJECT_ROOT`
  - `tests/test_ingest.py` — Replace `chdir` with `setattr PROJECT_ROOT`
  - `tests/test_cli.py` — Fix assertion to accept absolute path in output
- **Verification:** `pytest` → 183 passed, 0 failed. `public_repo_guard.py` passed.
- **Follow-ups:** Remaining scripts (`eval_search.py`, `graph_build.py`, `extract.py`, etc.) still use relative paths but are not MCP-critical; can be migrated incrementally.

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** Add Apache 2.0 LICENSE
- **Summary:** Added LICENSE file (Apache 2.0, copyright 2026 Mohammad Raouf Abedini). Added license badge to README.md.
- **Files Changed:** `LICENSE`, `README.md`
- **Verification:** `python scripts/public_repo_guard.py` passed.
- **Follow-ups:** None.

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** README — Full professional rewrite
- **Summary:** Rewrote README.md from scratch as a polished, structured document. Added shields.io badges (Python 3.10+, 183 tests passing, Phase 18, Obsidian-compatible, MCP). Replaced flat Goals list with a capability table. Replaced inconsistent `python scripts/cli.py` calls with unified `zurvan` CLI. Removed duplicate multiproject code block that duplicated content under "Features by Phase". Added: LLM provider table, Obsidian 7-node-type colour table, architecture directory tree, feature history table (18 phases), full documentation index table. Sections reordered for logical flow: pitch → quick start → features → architecture → quality gate → history → docs → contributing.
- **Files Changed:** `README.md`
- **Verification:** `python scripts/public_repo_guard.py` passed.
- **Follow-ups:** Add `LICENSE` file — currently absent from the repo.

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** Full Documentation Audit — Phase 18 Sync
- **Summary:** Audited all 60+ docs files. Updated the 6 stale files to reflect Phase 18 changes. Fixed a broken Markdown code block in README.md. Added Anthropic provider + ANTHROPIC_API_KEY to ENVIRONMENT.md. Expanded ARCHITECTURE.md with wiki/syntheses/, wiki/entities/, data/image_manifest.json, new scripts, and updated data-flow section. Added --save/--format CLI examples to API.md along with Evidence/Reports/Review Workbench command groups. Updated TESTING.md with accurate stage count (22) and test count (183). Added Phase 18 workflow section (18a/18b/18c) to workflows_and_plans.md.
- **Files Changed:**
  - `docs/ENVIRONMENT.md` — Added anthropic provider, ANTHROPIC_API_KEY, per-provider model defaults
  - `docs/ARCHITECTURE.md` — New directories, new scripts, rewritten data-flow section
  - `docs/API.md` — --save/--format flags, Evidence/Reports/Publication/Review CLI sections
  - `docs/TESTING.md` — Stage count (22) and test count (183) updated
  - `docs/workflows_and_plans.md` — Phase 18 workflow section added
  - `README.md` — Broken code block fixed, --save/--format examples in Quick Start
- **Verification:** `python scripts/public_repo_guard.py` passed. All 6 docs render clean Markdown. `git push` succeeded.
- **Follow-ups:** None. Docs are now current through Phase 18.

### 2026-06-02 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 18: Living Wiki + Provider Expansion
- **Summary:** (18a) Refactored llm.py into a provider registry with mock as default when ZURVAN_LLM_PROVIDER is unset; added Anthropic/Claude via raw urllib with no SDK. (18b) Created wiki_merge.py as canonical concept/entity writer — pages now compound across sources via additive merge; migrates legacy source_id frontmatter; added --save to zurvan context and zurvan search to file answers into wiki/syntheses/ with microsecond-safe filenames; standardised log.md to grep-parseable ## [date] format with shared formatter. (18c) Complete image-aware skeleton: image files, embedded Markdown refs, remote URL logging, PDF best-effort detection — all produce pending-visual stubs with manifest JSON entry, no OCR or network. Added --format table/marp stdout rendering; --save always writes canonical Markdown.
- **Files Changed:**
  - `scripts/filename_utils.py` — New shared sanitize_filename()
  - `scripts/llm.py` — Provider registry + Anthropic + mock default
  - `scripts/wiki_merge.py` — Canonical merge writer + shared log formatter
  - `scripts/extract.py` — Route concept/entity pages through merge_extraction(); image guard; embedded image scan
  - `scripts/ingest.py` — New log format; image detection + manifest JSON; embedded image logging
  - `scripts/context_export.py` — --save (context + search), --format table/marp
  - `scripts/cli.py` — --save and --format flags wired
  - `scripts/chunk.py` — Fix chunk_id collision (use full text not text[:50])
  - `scripts/memory.py` — Rename local sanitize_filename to _make_note_slug to avoid confusion with shared utility
  - `tests/test_filename_utils.py`, `tests/test_llm.py`, `tests/test_wiki_merge.py`, `tests/test_context_export.py`, `tests/test_ingest.py` — New/extended tests
- **Verification:** pytest → 183 passed, 0 failed. check.sh passed after 18a, 18b, and 18c milestones.
- **Follow-ups:** Review OpenAI model default (GPT-5.x). Phase 19+: image extraction via OCR/vision provider.

### 2026-06-02 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 18b — wiki_merge.py log formatter skeleton
- **Summary:** TDD implementation of `scripts/wiki_merge.py` with the shared `append_log_event()` formatter and 5 wrapper functions (`append_log_ingest`, `append_log_merge`, `append_log_save`, `append_log_image_skip`). Log entries are grep-parseable (`## [YYYY-MM-DD] kind | parts`) and pipe characters inside parts are escaped as `\|`. Used direct file I/O rather than `append_file_safely` because `safe_write.is_safe_path` hardcodes the project root, which blocks writes to `tmp_path` in pytest fixtures.
- **Files Changed:**
  - `scripts/wiki_merge.py` — created: shared log formatter + 4 wrappers
  - `tests/test_wiki_merge.py` — created: 6 log-format tests
- **Verification:** `PYTHONPATH=. pytest tests/ -q` → 151 passed, 0 failed (was 145 before; +6 new).
- **Follow-ups:** Phase 18c will add merge logic (slug resolution, source dedup, wiki page writing) to `wiki_merge.py`.

### 2026-06-02 (Australia/Sydney)
**Raouf:**
- **Scope:** Full Project Audit — Test Fix + Deprecation Cleanup
- **Summary:** Ran a full codebase audit. Found and fixed 1 hard test failure and 7 Starlette deprecation warnings, plus 1 tarfile deprecation warning. The test failure was a time-bomb: `test_find_stale_decisions` used a hardcoded `created_at` date of `"2026-05-01T12:00:00"` for a `pending` decision (d2), which passed the `age > 30 days` stale check as of 2026-06-02. Fixed by making d2's date dynamic (5 days ago). Starlette 0.50+ changed `TemplateResponse` signature from `(name, context_with_request)` to `(request, name, context)` — updated all 10 call sites in `review_routes.py`. Added `filter="data"` to `tar.extract()` in `restore_snapshot.py` for Python 3.14 compatibility.
- **Files Changed:**
  - `tests/test_decision_compare.py` — Made d2 `created_at` dynamic via `datetime.now() - timedelta(days=5)`
  - `scripts/review_routes.py` — Updated all 10 `TemplateResponse` calls to Starlette 0.50+ signature
  - `scripts/restore_snapshot.py` — Added `filter="data"` to `tar.extract()` call
- **Verification:** `python -m pytest tests/ -q` → 131 passed, 0 failed, 2 external warnings (SwigPy, not fixable).
- **Follow-ups:** Upgrade sentence-transformers if SwigPy warnings become an issue in CI.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 17: Export & Publication Pack
- **Summary:** Built local, safe publication pack generator for reviewed reports. Supports exporting to Markdown, JSON, HTML (and gracefully stubbed PDF/DOCX dependencies) and packaging into Zip bundles. Integrated strict publication safety blocking token-like strings, absolute paths, and emails by default. Outputs strictly target local ZURVAN_CONFIG_DIR (`~/.zurvan/publications/`) to prevent leaking private reports into the public repository. Included citation appendix generation that alerts on missing references.
- **Files Changed:**
  - `scripts/publication_export.py`, `scripts/publication_bundle.py`, `scripts/publication_citations.py`, `scripts/publication_safety.py`, `scripts/publication_templates.py` - Core logic for safe, decoupled export.
  - `docs/publication/*.md` - Documentation for overview, formats, appendix, safety, workflows.
  - `tests/test_publication_*.py` - Complete test suite for formats, bundling, redaction safety blocks, and appendix structure.
  - `scripts/cli.py` - Added `publish export/bundle/citations/validate`.
  - `scripts/check.sh` - Added automated publication validation tests to the quality gate.
  - `scripts/public_repo_guard.py` - Blocked `.pdf`, `.docx` files globally and enforced `.zurvan/publications/` is outside tracked scope.
- **Verification:** Ran `bash scripts/check.sh` locally alongside `pytest`. The pipeline hit a 100% pass rate. Verified `public_repo_guard` catches stray references safely and that empty appendix citations are caught properly.
- **Follow-ups:** Proceed to Phase 18: Template Externalisation or another scaling phase.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 14: Report Composer
- **Summary:** Built the local Phase 14 Report Composer. It safely transforms Evidence Packs into structured Markdown and JSON reports without relying on LLM or cloud endpoints. Uses predefined deterministic templates (e.g. executive_summary, technical_audit, evidence_digest). Integrates existing redaction safeguards to completely scrub evidence of private keys and paths before final output. Included a strict validation engine ensuring every claim maps directly to citations and warns if sections lack sufficient evidence. Outputs default to safe off-repo directories (`~/.zurvan/reports/`) to maintain public repo safety.
- **Files Changed:**
  - `scripts/report_compose.py` - Core composition, templating and validation
  - `scripts/report_export.py` - Markdown and JSON structure export
  - `scripts/cli.py` - Added `zurvan report compose/list/inspect/export/validate`
  - `scripts/public_repo_guard.py` & `.gitignore` - Added `reports/` block list
  - `tests/test_report_*.py` - Test suite for report creation and export
  - `scripts/check.sh` - Included Phase 14 report smoke test
  - `docs/reports/*.md` - Documentation for overview, templates, CLI, and safety
  - `README.md` & `docs/workflows_and_plans.md` - Marked Phase 14 as complete
- **Verification:** Ran `bash scripts/check.sh`, resulting in 100% pass for unit and smoke tests, alongside the `test_report_compose.py` passing the validation structure check correctly categorizing missing sections as warnings.
- **Follow-ups:** Proceed to Phase 15: Local Report UI / Review Workbench.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 13: Evidence Pack Builder
- **Summary:** Implemented a robust Evidence Pack Builder capable of securely aggregating claims, decisions, contradictions, graph context, and search results into redacted, shareable bundles without requiring cloud connectivity, remote synchronization, or LLM summarization. Integrated data export pipelines supporting Markdown and JSON formats, alongside an automatic redaction utility guarding sensitive information like paths and API credentials. Output evidence packs are strictly stored locally outside the public workspace to protect data integrity and uphold safety constraints.
- **Files Changed:**
  - `scripts/evidence_pack.py` - Core pack orchestration
  - `scripts/evidence_collect.py` - Safe cross-project evidence collection
  - `scripts/evidence_manifest.py` - Evidence packing manifest generation
  - `scripts/evidence_redact.py` - Security redactions for paths and tokens
  - `scripts/evidence_export.py` - Local bundle exports (Markdown/JSON)
  - `docs/evidence/*.md` - Documentation updates
  - `tests/test_evidence_*.py` - Complete test coverage
  - `scripts/cli.py` - Evidence builder interface
  - `scripts/check.sh` - Add tests to CI
- **Verification:** Successfully ran all unit tests for evidence generation, validation, redaction logic, and smoke-tested local pack generation using `check.sh`.
- **Follow-ups:** Proceed to Phase 14: Report Composer.
### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 12: Cross-Project Contradiction + Policy Radar
- **Summary:** Added `zurvan project radar scan`, `contradictions`, `policies`, `drift`, and `report`. Built local heuristic detection for contradictions across decisions, claims, and policies based on positive/negative keyword lists and categorical overlap. Included rules to ensure safe handling of public repos, MCP write restrictions, and directory immutability.
- **Files Changed:**
  - `scripts/policy_rules.py` (created)
  - `scripts/claim_federation.py` (created)
  - `scripts/contradiction_radar.py` (created)
  - `scripts/policy_radar.py` (created)
  - `scripts/cli.py` (modified)
  - `docs/policy-radar/` (created)
- **Verification:** Unit tests added in `tests/test_policy_rules.py`, `test_claim_federation.py`, `test_contradiction_radar.py`, `test_policy_radar.py`. Smoke tests added to `check.sh` ran successfully.
- **Follow-ups:** Proceed to Phase 13: Evidence Pack Builder.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 11: Cross-Project Decision Memory
- **Summary:** Enabled Zurvan to scan, cache, and compare decisions across all federated projects. Added `zurvan project decisions-all`, `decisions-similar`, `decisions-conflicts`, and `decisions-stale`. Built heuristic algorithms to detect repeating architectural patterns and possible contradictions (e.g., conflicting defaults across projects) without relying on cloud endpoints, LLMs, or cross-project data copying. Cached decisions locally in `~/.zurvan/cache/` to ensure public-repo safety.
- **Files Changed:**
  - `scripts/decision_memory.py`, `scripts/decision_compare.py`, `scripts/decision_federation.py`
  - `docs/decisions/*.md`
- **Verification:** Unit tests added in `tests/test_decision_*`. E2E smoke tests added to `check.sh`.
- **Follow-ups:** Proceed to Phase 12 (Cross-Project Contradiction + Policy Radar).

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 10: Cross-Project Search + Federation
- **Summary:** Added a safe, local-first federation layer allowing users to search (`search-all`) and build context (`context-all`) across multiple registered projects. Includes strict privacy limits (no absolute path leaks, no data copying) and validation via `federation stats` and `doctor`.
- **Files Changed:**
  - `scripts/federation.py`, `scripts/cross_project_search.py`, `scripts/cross_project_context.py`
  - `scripts/cli.py`
  - `docs/federation/*.md`, `docs/workflows_and_plans.md`, `README.md`
  - `tests/test_federation.py`, `tests/test_cross_project_search.py`, `tests/test_cross_project_context.py`
  - `scripts/check.sh`
- **Verification:** Built mock tests for federation parsing and grouping. Validated isolation by running cross-project subprocesses inside target directories. Verified via federated smoke tests in `check.sh`.
- **Follow-ups:** Prepare for Phase 11: Cross-Project Decision Memory.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 9: Multi-Project Workspace Support
- **Summary:** Added safe, isolated multi-project workspaces driven by a local registry (`~/.zurvan/projects.json`). Projects can be registered, listed, switched, and targeted cleanly using `zurvan project` and `--project`. Private absolute paths are now decoupled from the public repository entirely.
- **Files Changed:**
  - `scripts/config.py`, `scripts/project_registry.py`, `scripts/workspace.py`
  - `scripts/cli.py`
  - `tests/test_workspace.py`, `tests/test_project_registry.py`, `tests/test_config.py`
  - `docs/workspaces/*.md`
  - `scripts/check.sh`
- **Verification:** Unit tests confirm atomic registry writing, config path overriding, safety filters against `raw/` indexing, and path traversal blocking. All E2E smoke tests in `check.sh` pass using a temporary registry mock.
- **Follow-ups:** Prepare for Phase 10: Cross-Project Search.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 8: Release Packaging + Versioned Snapshots
- **Summary:** Added safe, local-first release packaging via `zurvan snapshot`. Added system health checks via `zurvan doctor` and version reporting via `zurvan version`. Built strict path safety into the restore mechanism to prevent traversal outside the project or into `raw/`.
- **Files Changed:**
  - `scripts/snapshot.py`, `scripts/restore_snapshot.py`, `scripts/doctor.py`, `scripts/version.py`, `scripts/cli.py`
  - `docs/release/*.md`
  - `tests/test_snapshot.py`, `tests/test_restore_snapshot.py`, `tests/test_doctor.py`, `tests/test_version.py`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
  - `scripts/check.sh`
- **Verification:** Unit tests confirm that `snapshot` excludes `raw/`, `restore` blocks unsafe paths and enforces `--force`, and `doctor` accurately detects missing components. Smoke tests in `check.sh` run successfully.
- **Follow-ups:** Prepare for a stable 1.0 release branch.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7.5: Obsidian Integration Pack
- **Summary:** Configured Zurvan as a first-class Obsidian vault. Added YAML-compliant templates for all core node types, created safe `.obsidian/` configurations to filter out non-knowledge directories, and documented vault setup strategies.
- **Files Changed:**
  - `wiki/templates/claim.md`, `decision.md`, `concept.md`, `entity.md`, `source.md`, `session.md`, `contradiction.md`
  - `.obsidian/app.json`, `.obsidian/core-plugins.json`, `.obsidian/templates.json`
  - `docs/obsidian/setup.md`, `recommended-plugins.md`, `graph-view.md`, `workflows.md`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Ensured `.obsidian/` filter ignores `data/`, `scripts/`, `raw/`, etc., keeping the UI clean. Templates conform to existing `graph_build.py` node logic.
- **Follow-ups:** Prepare for Phase 8: Release Packaging.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7: Agent Workflow Orchestration
- **Summary:** Added structured local session management (`session start`, `session close`, `agent preflight`, `agent postedit`) to seamlessly onboard agents like Claude Code, Codex, and Cursor before and after edits. Provided templates and explicit workflow documentation.
- **Files Changed:**
  - `wiki/sessions/`
  - `scripts/templates/preflight.md`, `scripts/templates/postedit.md`, `scripts/templates/session_start.md`, `scripts/templates/session_close.md`
  - `scripts/session.py`, `scripts/agent_workflow.py`
  - `tests/test_session.py`, `tests/test_agent_workflow.py`
  - `scripts/cli.py`, `scripts/check.sh`
  - `docs/agent-workflows/claude-code.md`, `docs/agent-workflows/codex.md`, `docs/agent-workflows/cursor.md`, `docs/agent-workflows/human.md`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Built fully tested subcommands via Pytest and incorporated smoke tests in `check.sh`.
- **Follow-ups:** Prepare for Phase 8: Release Packaging + Versioned Snapshots.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 6.5: MCP Client Integration Pack
- **Summary:** Added `scripts/doctor_mcp.py` to assert system health before connection and `scripts/install_mcp_config.py` to generate safe MCP configurations for clients like Claude Code and Cursor. Added comprehensive client setup guides in `docs/mcp/`. Added explicit warnings when bypassing read-only defaults.
- **Files Changed:**
  - `docs/mcp/claude-code.md`, `docs/mcp/cursor.md`, `docs/mcp/codex-style-agents.md`, `docs/mcp/security.md`, `docs/mcp/troubleshooting.md`
  - `scripts/doctor_mcp.py`, `scripts/install_mcp_config.py`
  - `tests/test_doctor_mcp.py`, `tests/test_install_mcp_config.py`
  - `scripts/check.sh`, `scripts/e2e_mcp_smoke.py`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** `doctor_mcp.py` successfully detected missing dependencies and unsafe states. `install_mcp_config.py` correctly printed configurations. Pytest suite expanded and passed. MCP E2E smoke test continues to pass reliably without causing SQLite constraint collisions in subsequent runs.
- **Follow-ups:** Prepare for Phase 7 (if any) or continue improving knowledge engine extractions.
### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7: Comprehensive Documentation Audit
- **Summary:** Conducted a full audit of documentation. Fixed markdown errors in README, decoupled technical guides into specific files (`SETUP.md`, `ARCHITECTURE.md`, `API.md`, `ENVIRONMENT.md`, `TESTING.md`, `TROUBLESHOOTING.md`, `DEPLOYMENT.md`). Addressed duplicate chunk_id in `open-questions.md` breaking hybrid search tests.
- **Files Changed:**
  - `README.md`
  - `docs/SETUP.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/ENVIRONMENT.md`, `docs/TESTING.md`, `docs/TROUBLESHOOTING.md`, `docs/DEPLOYMENT.md`
  - `wiki/open-questions.md`
- **Verification:** Ran `check.sh` quality gate, passing all unit tests, reliability gauntlet, audits, index rebuilding, retrieval evaluation, and MCP E2E tests.
- **Follow-ups:** Maintain these documentation invariants as the project expands.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Local MCP Server for Agent Integration (Phase 6)
- **Summary:** Added MCP SDK based server to expose Zurvan memory securely via stdio. Added tools, resources, and prompts wrappers over existing Zurvan functions. Implemented `mcp_security.py` to enforce read-only defaults, path validation, and block arbitrary file reads/execution.
- **Files Changed:**
  - `scripts/mcp_server.py`, `scripts/mcp_tools.py`, `scripts/mcp_resources.py`, `scripts/mcp_prompts.py`, `scripts/mcp_security.py`
  - `tests/test_mcp_*.py`
  - `requirements.txt`, `requirements-dev.txt`, `scripts/check.sh`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Security tests passed. Smoke test via `check.sh` successfully verified MCP components.
- **Follow-ups:** Use the MCP server with Claude Code or Cursor to securely interact with the knowledge engine.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** E2E Smoke Test (Phase 5.5 Finalization)
- **Summary:** Created full E2E test script (`scripts/e2e_smoke.sh`) and fixed exit codes in `scripts/cli.py` and `scripts/memory.py` so memory actions failing return correctly. The E2E tests fully simulate the entire Zurvan pipeline.
- **Files Changed:**
  - `scripts/e2e_smoke.sh` (Created)
  - `scripts/cli.py`, `scripts/memory.py` (Fixed return values)
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Script executed successfully from beginning to end with all features active.
- **Follow-ups:** Prepare for Phase 6: MCP Server for Agent Integration.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Graph-Assisted Context Expansion (Phase 5.5)
- **Summary:** Upgraded `zurvan context` to support `--graph` which pairs hybrid search results with related graph nodes up to a specified depth. Added grouping by node type (Decisions, Claims, etc.).
- **Files Changed:**
  - `scripts/graph_context.py`, `tests/test_graph_context.py`
  - `scripts/context_export.py`, `scripts/cli.py`, `scripts/check.sh`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Unit tests passed. Smoke test via `check.sh` successfully retrieved search and graph neighbors combined. Handled missing `graph.sqlite` gracefully.
- **Follow-ups:** Prepare for Phase 6: MCP Server for Agent Integration.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Knowledge Graph Lite (Phase 5)
- **Summary:** Implemented local Markdown-aware graph layer. Added nodes/edges SQLite schema and python scripts to parse frontmatter, wikilinks, and evidence lines into relationships. Added CLI commands for rebuild, stats, neighbours, trace, and export.
- **Files Changed:**
  - `scripts/graph_schema.py`, `scripts/graph_build.py`, `scripts/graph_query.py`, `scripts/graph_export.py`
  - `tests/test_graph_*.py`
  - `scripts/cli.py`, `scripts/check.sh`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Tests passed. `check.sh` smoke test passed successfully. Raw files correctly ignored.
- **Follow-ups:** Prepare for Phase 5.5: Graph-assisted context expansion to blend semantic search with graph proximity.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Seed Gold Knowledge (Phase 4.6)
- **Summary:** Added `validate_gold_dataset` to ensure eval sets target real files. Seeded the wiki with actual CLI-generated documents for the eval dataset. Added `zurvan eval validate-gold`.
- **Files Changed:**
  - `eval/search_gold.jsonl`
  - `scripts/eval_search.py`, `scripts/cli.py`, `scripts/check.sh`
  - `tests/test_eval_search.py`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Ran `check.sh` successfully with `min-top3 0.6` threshold.
- **Follow-ups:** Graph retrieval can now begin in Phase 5 since retrieval is meaningful and validated.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Retrieval Evaluation Harness (Phase 4.5)
- **Summary:** Added `eval_search.py` and metrics to compute MRR, Top-1, and Top-3 accuracy against a gold dataset (`search_gold.jsonl`).
- **Files Changed:**
  - `eval/search_gold.jsonl`, `eval/README.md`
  - `scripts/eval_search.py`, `scripts/metrics.py`
  - `tests/test_eval_search.py`
  - `scripts/cli.py`, `scripts/check.sh`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** Ran evaluations locally. Failing to meet minimum threshold correctly sets exit code 1. Tests pass.
- **Follow-ups:** Use this evaluation gate to test retrieval quality before expanding to graph retrieval.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Local Hybrid Search (Phase 4)
- **Summary:** Added `search.sqlite` with FTS5 and mock/local embeddings to support `zurvan search --hybrid` and `zurvan context --hybrid`. Kept embeddings optional to prevent dependency bloat.
- **Files Changed:**
  - `scripts/chunk.py`
  - `scripts/embed.py`
  - `scripts/rebuild_search_index.py`
  - `scripts/hybrid_search.py`
  - `scripts/cli.py`
  - `scripts/context_export.py`
  - `tests/test_chunk.py`, `tests/test_embed.py`, `tests/test_hybrid_search.py`
  - `README.md`, `docs/workflows_and_plans.md`, `AGENTS.md`, `CHANGELOG.md`
- **Verification:** SQLite search index built correctly, `tests/test_hybrid_search.py` passed, and hybrid results generated.
- **Follow-ups:** Prepare to tune hybrid search weights if needed in production.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Initial Project Setup
- **Summary:** Set up the directory structure and foundational files for the local-first LLM Wiki knowledge engine.
- **Files Changed:**
  - `README.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
  - `requirements.txt`
  - `scripts/ingest.py`
  - `scripts/query.py`
  - `scripts/audit_wiki.py`
  - `scripts/rebuild_index.py`
  - `wiki/index.md`, `wiki/log.md`, `wiki/overview.md`, `wiki/open-questions.md`
- **Verification:** Created files successfully, directory structure in place.
- **Follow-ups:** Wire `llm.py` to an active provider and run full PDF tests.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** LLM Provider & PDF Stress Testing
- **Summary:** Implemented Phase 3 real LLM provider support (OpenAI/Ollama/Mock) and verified PDF ingestion/extraction workflow.
- **Files Changed:**
  - `scripts/llm.py`
  - `tests/test_llm.py`
  - `README.md`
  - `docs/workflows_and_plans.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Verification:** Unit tests for provider logic pass. PDF workflow successfully extracted and audited.
- **Follow-ups:** Ready for heavy PDF processing. Do not move to vector search yet.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Extraction Reliability Gauntlet
- **Summary:** Implemented Phase 3.5 to safely batch test the pipeline on various file formats and log extraction successes/failures without batch crashing.
- **Files Changed:**
  - `docs/extraction_test_matrix.md`
  - `scripts/run_reliability_gauntlet.py`
  - `tests/test_gauntlet.py`
  - `requirements-dev.txt`
  - `requirements.txt`
  - `README.md`
  - `docs/workflows_and_plans.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Verification:** Unit tests for gauntlet isolation pass.
- **Follow-ups:** Manually execute the gauntlet on all 5 specified file types to fill out the matrix, then prepare for vector search.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Agent-Facing CLI Memory Interface
- **Summary:** Implemented Phase 3.6 CLI (`zurvan` emulation) to allow agents to write, search, and export context without internal LLMs. Added path traversal safety.
- **Files Changed:**
  - `scripts/cli.py`
  - `scripts/memory.py`
  - `scripts/safe_write.py`
  - `scripts/context_export.py`
  - `tests/test_cli.py`
  - `tests/test_memory.py`
  - `tests/test_context_export.py`
  - `README.md`
  - `docs/workflows_and_plans.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Verification:** CLI commands, context searches, and `safe_write` boundaries tested and passed via pytest.
- **Follow-ups:** Prepare for Phase 4: Local embeddings + hybrid search.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Quality Gate (Test-Creator)
- **Summary:** Created `scripts/check.sh` as the master quality gate to run unit tests, reliability gauntlet, and audits in sequence.
- **Files Changed:**
  - `scripts/check.sh`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Verification:** Script executed successfully and all three gate layers passed.
- **Follow-ups:** Keep adding new tests to this script as the project expands.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Documentation
- **Summary:** Added detailed workflow and python script plan documentation to complete the MVP structure.
- **Files Changed:**
  - `docs/workflows_and_plans.md`
  - `README.md`
  - `AGENTS.md`
  - `CHANGELOG.md`
- **Verification:** Markdown structure validated.
- **Follow-ups:** Integrate LLM API into `ingest.py`.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 15: Local Report UI / Review Workbench
- **Summary:** Built a local-only FastAPI UI to inspect evidence packs and review composed reports before exporting them. Bound strictly to localhost by default and restricted path access to safely prevent any raw data leakage. Validates citation integrity interactively via web dashboard to spot unsupported claims or empty sections manually.
- **Files Changed:**
  - `scripts/review_server.py`, `scripts/review_routes.py`, `scripts/review_models.py`, `scripts/review_safety.py` - Core web application backend logic and security validations.
  - `templates/` and `static/` - HTML, CSS, JS frontend rendering for reports.
  - `scripts/cli.py` - Wired up `zurvan review serve/list/open`
  - `scripts/check.sh` - Added automated smoke test and routing test suite for review workbench.
  - `docs/review/*.md` - Documentation for overview, usage and safety
  - `README.md` & `docs/workflows_and_plans.md` - Marked Phase 15 as complete.
- **Verification:** Ran `bash scripts/check.sh`, resulting in 100% pass for unit and smoke tests. Verified the review endpoints properly export markdown and correctly reject invalid queries / prevent path traversals.
- **Follow-ups:** Proceed to the next phases on optimizing or scaling the workbench.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 16: Review Workbench Hardening + UX Polish
- **Summary:** Enhanced the local report review cockpit with stronger safety checks and UX improvements. Added automatic secret scanning (emails, API keys, absolute paths) to flag unsafe exported content. Strengthened citation validation to catch unmapped or missing claims before final export. Polished the UI with clear status badges, a dedicated warnings panel, dynamic dashboard summary metrics, and a reviewer checklist. Fully integrated `zurvan review audit` and `zurvan review index rebuild` commands into the CLI.
- **Files Changed:**
  - `scripts/review_audit.py` & `scripts/review_index.py` - Core auditing and local indexing logic.
  - `docs/review/hardening.md` & `docs/review/reviewer-checklist.md` - Operational guidelines for safety and workflows.
  - `tests/test_review_audit.py` & `tests/test_review_index.py` - Unit test coverage for edge cases like secret detection and manifest validation.
  - `scripts/review_routes.py`, `scripts/cli.py`, `templates/*.html`, `static/review.css` - Endpoint plumbing, UI/CSS updates, and command hooks.
  - `README.md`, `docs/workflows_and_plans.md`, `scripts/check.sh` - Project structure and checklist documentation logic.
- **Verification:** Ran `bash scripts/check.sh`, which hit a 100% pass rate. Verified `zurvan review audit` cleanly flags unmapped citations, and `zurvan review index rebuild` properly isolates without leaking absolute local paths into the registry.
- **Follow-ups:** Prepare for Phase 17 involving potential new integrations or scaling report formats.
