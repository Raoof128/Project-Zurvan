# AGENTS.md — archived change entries

Older `AGENTS.md` change entries, moved here verbatim on 2026-07-04 to keep the
constraints file small. Newest entries live in [../AGENTS.md](../AGENTS.md).

### 2026-07-04 (Australia/Sydney) — Remaining-functions audit (write path) + `zurvan` operating skill
**Raouf:**
- **Scope:** Phase 25 — audit the remaining core modules (memory/write path + infra: `memory`, `safe_write`, `wiki_merge`, `db`, `filename_utils`, `validate_extraction`, `config`, `workspace`, `llm`); smoke the research/publication subsystem entry points; ship a project skill so any LLM works with Zurvan professionally
- **Summary:** All 78 scripts compile. `llm.py` (provider layer), `db.py`, `config.py`, `workspace.py`, `filename_utils.py`, `validate_extraction.py`, `wiki_merge.py` are sound; research/publication CLI entry points (`version`, `doctor`, `trace/evidence/report/project list`, `graph stats`, `federation stats`) all run without runtime errors. **Two real write-path defects fixed.** **(W1 — data corruption, `safe_write.py`)** `escape_yaml_string` used `yaml.dump` with default width, which **line-wraps long scalars**; the naive line-by-line frontmatter parsers (`graph_build.parse_frontmatter`, `wiki_merge._parse_fm`) then truncate a wrapped `title:`/`status:` value at the first line. Added `width=2**20, allow_unicode=True` so values stay on one line (round-trips through a real YAML parser). **(W2 — silent data loss, `memory.py`)** `add_decision`/`add_note` derived the filename purely from the title slug, so two titles slugging identically (`Use SQLite!` vs `Use SQLite?` → `use-sqlite`) **overwrote each other** (`write_file_safely` uses mode `w`). Added `_unique_path` (numeric suffix on collision), matching how claims/questions already avoid collisions. Non-defects noted: `merge_extraction`/`extract`/`audit_wiki` still write via CWD-relative `wiki/` (safe through the `zurvan` CLI which anchors `cwd=PROJECT_ROOT`; known follow-up); `is_valid_zurvan_project` requires `scripts/`, so knowledge-only federated projects work via `search-all` but not `--project X` (by design); `safe_write.is_safe_path` raw-block is case-sensitive like the read path was (write targets are fixed `wiki/` locations, low risk). **Skill:** added `.claude/skills/zurvan/SKILL.md` — a professional operating manual (first-30-seconds orientation, the 6 hard rules, mental model, read/write/ingest paths, the mandatory change protocol, environment, and the gotchas learned across Phases 21–25: ambient-embed-env test dependency, `wiki/log.md` test-noise reverts, leave `.obsidian/graph.json`, guard blocks `.pdf`, gitignored ingest pages, frozen-gold re-verify).
- **Files Changed:** `scripts/safe_write.py`, `scripts/memory.py`, `tests/test_memory.py` (+2), `tests/test_cli.py` (loosened over-specific note-filename assertion for collision-safety), `.claude/skills/zurvan/SKILL.md` (new).
- **Verification:** `pytest` → **271 passed, 0 failed** (+2). `escape_yaml_string` on a long value now has no newline and round-trips; two colliding decision titles now produce two files (`…-2.md`). Frozen provenance golds unchanged (86% recall, 100% completeness, 0% raw leak). `public_repo_guard.py` + `git diff --check` clean. Research subsystem entry points smoke-run clean.
- **Follow-ups:** Deep line-by-line of the full research/publication subsystem (`review_*`, `report_*`, `publish/publication`, `evidence_*`, `decision_*`, `contradiction_radar`, `policy_*`, `snapshot`, `federation`, `install_mcp_config`, `e2e_mcp_smoke`) is still worthwhile — this pass smoke-tested them, not line-audited. Migrate `ingest`/`extract`/`audit_wiki` off CWD-relative `wiki/` writes. Consider a periodic test-isolation cleanup so `wiki/log.md`/`note-*.md` stop accumulating.

### 2026-07-04 (Australia/Sydney) — Graph + trace audit (3 render/consistency fixes)
**Raouf:**
- **Scope:** Phase 24 — file-by-file audit of the graph stack (`graph_schema`, `graph_query`, `graph_context`, `graph_export`, `graph_build`) and the trace stack (`trace_schema`, `trace_writer`, `trace_validate`, `trace_replay`, `eval_provenance`, CLI trace handlers)
- **Summary:** Both stacks are largely sound. Trace core is well-built and left untouched (it is frozen-adjacent): `hash_payload` canonical JSON, `trace_validate` checks required fields + id regexes + duplicate event_ids + payload-hash integrity, `TraceStore` enforces safe ids and `_ensure_under` traversal guards, `eval_provenance` invariant gate (raw-leak / hash-integrity / validation) — all correct; CLI `trace list/inspect/validate/replay` handle errors with proper exit codes. Graph schema has the right indexes. Three real defects fixed. **(G1 — render, `trace_replay.py`)** `replay_trace_file` renders each event payload into a Markdown table cell without escaping `|`; a payload value containing a pipe broke the table (2 of 103 committed trace events currently affected). Pipes are now escaped `\|` (GFM un-escapes inside the cell); verified on real `trace-…-real0004`. **(G2 — render, `graph_export.py`)** `export_dot` wrote node/edge labels with unescaped `"`/`\`, so a title with a quote produced malformed DOT Graphviz can't parse (0 titles have `"` today but 36 contain a backslash → malformed labels). Added `_dot_escape` (backslash then quote); live DOT export of 1975 labels now has 0 unbalanced-quote lines. **(G3 — consistency, `graph_build.py`)** `build_graph` indexed 24 `wiki/traces/` replay-mirror pages as graph nodes, even though the search chunker excludes `traces/` as derived + self-referential; added `traces` to `exclude_dirs`. Graph rebuild 879→855 nodes (−24 mirrors), 1096 edges. Non-defects noted: `graph_query.trace_node` is a "placeholder" whose DFS includes edges one hop past `depth` and dedups O(n²) (low-traffic, left); `graph_context.type_rank` has dead branches for node types graph_build never emits (`contradiction`/`open_question`); `eval_provenance` resolves each trace path twice (micro-inefficiency).
- **Files Changed:** `scripts/trace_replay.py`, `scripts/graph_export.py`, `scripts/graph_build.py`, `tests/test_trace_replay.py` (+1), `tests/test_graph_export.py` (+1), `tests/test_graph_build.py` (+1).
- **Verification:** `pytest` → **269 passed, 0 failed** (+3). Live: real pipe-containing trace replays to a well-formed 5-column row; real DOT export well-formed; `graph rebuild` → 855 nodes / 1096 edges with 0 `wiki/traces/` nodes. **Frozen provenance golds unchanged** (2C 86% recall, 100% completeness, 0% raw leak, 100% graph presence) — they score committed trace JSON, not the live graph, so G3 has no effect on them. `public_repo_guard.py` + `git diff --check` clean.
- **Follow-ups:** `trace_node` could be promoted from placeholder to a real bounded traversal if `zurvan graph trace` gets used; `graph_context.type_rank` dead branches can be dropped or backed by real contradiction/question node types.

### 2026-07-04 (Australia/Sydney) — CLI + search audit (keyword-search docs blind spot)
**Raouf:**
- **Scope:** Phase 23 — file-by-file audit of the CLI (`cli.py`) and the search stack (`hybrid_search`, `context_export._search_internal`, `embed`, `rebuild_search_index`, `eval_search`, legacy `query`/`rebuild_index`)
- **Summary:** The CLI is sound — `_run_script` propagates child exit codes (verified: `eval validate-gold` on a missing path → exit 1), the `search`/`context`/`eval` handlers pass args correctly, and every subcommand referenced by a handler is registered (incl. `eval validate-gold`, line 369). Search core is sound too: `search_hybrid` embeds the query with the provider/model **stored in the index**, `_index_embedding_config` makes the index the source of truth, incremental rebuild reuse works. **One real defect fixed (`context_export._search_internal`, keyword mode):** it globbed **`wiki/` only**, so `zurvan search <term>` (without `--hybrid`) silently could not find any `docs/` page — even though hybrid mode, the chunk index (`scan_markdown_files` → wiki + docs), and `eval_search`'s own keyword branch all include `docs/`. Confirmed live: a query for text living only in `docs/ENVIRONMENT.md`/`docs/API.md` returned only `wiki/` notes. It also returned **absolute** `source_path`s (leaking machine paths into stdout and saved syntheses) while hybrid returned repo-relative. Fixed both: keyword mode now scans `wiki/` + `docs/` and returns repo-relative paths (idempotent with federation's `_relative_source_path`, so cross-project search is unaffected). Non-defects noted for the record: the `hybrid_score > 0.2` cutoff is weak because `(cos+1)/2` lifts orthogonal chunks to 0.5 (quality nuance, not touched — it's a ranking change); `embed.get_embedding` silently returns mock for unsupported providers (openai/anthropic aren't embed providers); legacy `query.py` is dead (not CLI-wired); `index rebuild` (nav `index.md`) vs `index search` (retrieval index) are confusingly named but documented and CWD-safe via `_run_script(cwd=PROJECT_ROOT)`.
- **Files Changed:** `scripts/context_export.py` (`_search_internal` keyword branch), `tests/test_context_export.py` (+1 keyword-finds-docs regression; +2 pre-existing non-hermetic format-table tests pinned to a tmp corpus — their sentinel term literally lives in a `docs/` plan file, so scanning docs/ correctly surfaced it).
- **Verification:** `pytest` → **266 passed, 0 failed** (+1) under the ambient env. Live: keyword `zurvan search` now returns `docs/API.md` #1 for a docs-only query with all-relative paths; `eval_search --hybrid --min-top3 0.6` unchanged (top-1 67% / top-3 100% / MRR 0.778) — keyword `_search_internal` is not on the hybrid gate path, so no ranking delta. Frozen provenance golds unchanged. `public_repo_guard.py` + `git diff --check` clean.
- **Follow-ups:** Keyword scoring is still crude (unique-keyword presence, substring, no field weighting) — fine as the non-hybrid fallback; hybrid is the real retriever. Consider a `--min-score` or revisiting the `0.2` cutoff if hybrid ever returns weak long tails (would need a documented eval re-run).

### 2026-07-04 (Australia/Sydney) — MCP + Obsidian audit (3 fixes + a test-isolation fix)
**Raouf:**
- **Scope:** Phase 22 — file-by-file audit of the MCP server and Obsidian-compatibility layer; fix confirmed defects
- **Summary:** MCP layer is otherwise sound — server boots, 12 tools / 6 resources + 1 template / 4 prompts register, traversal/absolute paths blocked, writes blocked by default, resources point to real files; `.obsidian/` config valid (`useMarkdownLinks:false` → native `[[wikilinks]]`, sensible ignore filters). Three real defects fixed, two of them confirmed live. **(F1 — security, HIGH, `mcp_security.py`)** `enforce_read_only` blocked only the exact string `"1"`, so `ZURVAN_MCP_READONLY=true`/`yes`/empty **enabled writes** (fail-open) — a user setting `=true` to lock it would unlock it. Now fails **closed**: writes allowed only when the value is exactly `"0"` (whitespace-trimmed); every other value stays read-only. **(F2 — security, MEDIUM/macOS, `mcp_security.py`)** the `raw/` block was case-sensitive, so `is_safe_path("Raw/secret.md")` returned True and an agent could read untrusted `raw/` content by changing case on a case-insensitive filesystem; the top-level component is now compared case-insensitively. **(F3 — Obsidian compat, `graph_build.py`)** the wikilink parser didn't strip Obsidian's `[[target|alias]]` / `[[target#heading]]` syntax, so aliased/heading links never resolved to a node and their graph edges were silently dropped; alias/heading are now stripped before matching (corpus has 0 such links today, so this is future-proofing the "Obsidian-compatible" claim). **(F4 — test isolation, `tests/test_hybrid_search.py`)** uncovered while running the gate: the repo's own `.claude/settings.json` exports `ZURVAN_EMBED_PROVIDER=sentence_transformers`, which leaked into pytest and made the module-scoped `tmp_index` fixture build a real (slow, non-deterministic) index — breaking `test_query_embedding_follows_index_provider` (which asserts the index stores `mock`) whenever the suite runs without a manual `unset`. The fixture now pins the mock provider for the build and restores the prior env; the suite is hermetically green under the ambient session env and faster (115s→72s).
- **Files Changed:** `scripts/mcp_security.py`, `scripts/graph_build.py`, `tests/test_mcp_security.py` (+2), `tests/test_graph_build.py` (+1), `tests/test_hybrid_search.py` (fixture).
- **Verification:** `pytest` → **265 passed, 0 failed** (+3) under the ambient `ZURVAN_EMBED_PROVIDER=sentence_transformers` env (previously 1 failed without a manual unset). Live re-checks: `ZURVAN_MCP_READONLY=true` now blocks writes; `is_safe_path("Raw/…")` now False; MCP server still boots with 12 tools; `graph rebuild` OK (879 nodes / 1096 edges). Frozen provenance golds unchanged (2C 86% recall, 100% completeness, 0% raw leak). `public_repo_guard.py` + `git diff --check` clean.
- **Follow-ups:** No ranking/index change here, so no `eval_search` delta required. `zurvan_read_page`/`resource_file` cap files at 256 KB — a fully-ingested large PDF source page (e.g. the 194 KB constitution) is under the cap but a bigger one would be rejected; consider paging if that becomes real. Obsidian `templates.json` points at `wiki/templates` (exists); leave as-is.

### 2026-07-04 (Australia/Sydney) — Ingest "Claude's Constitution" + full-ingestion fixes
**Raouf:**
- **Scope:** Phase 21 — ingest the Claude's Constitution PDF fully into local memory; fix two defects that made "full" ingestion impossible (truncated source text + unsplittable large chunks)
- **Summary:** Ingested `raw/papers/claudes-constitution_webPDF_26-02.02a.pdf` (191,856 chars, clean pypdf extraction) into local memory: registered source, full-text source page, stub concept/claim, **203 retrieval chunks**. **No LLM extraction run** — provider is `mock` (no API keys, no ollama), and mock extraction would fabricate claims/concepts citing the constitution, violating the no-fabrication rule. **(Fix 1 — `ingest.py`)** `create_source_page` stored only `text[:1000]` (a preview) → now stores the **complete** extracted text so the whole document is chunked and searchable. Added `source_page_stem()`: derived pages use a sanitised, extension-free slug so PDF/DOCX-derived pages are guard-safe (no `.pdf`/`.docx` substring) and drop the `.md.md` double suffix; source-page / stub-concept-claim / index writers anchored to `PROJECT_ROOT` (matches `ingest_image_stub`, makes them CWD-independent + unit-testable). **(Fix 2 — `chunk.py`)** heading-only splitting left the heading-less PDF prose as one 191k-char chunk — unsearchable (the sentence-transformer embeds only its first ~256 tokens; BM25 dilutes to noise). Added size-bounded sub-splitting: any heading-section over `MAX_CHUNK_CHARS` (1000, sized to the embedder window) is packed on line boundaries into ≤1000-char chunks, single over-long lines hard-wrapped. Small sections keep the **legacy chunk_id** (no `::idx::` segment), so ~94% of the corpus is byte-identical and reuses stored embeddings. Ingested source/concept/claim pages are gitignored by design ("private — generated from other projects"); they live in the working tree + local indices, so only the reusable code + tests + this log are committed.
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
- **Scope:** Phase 19 (agent ergonomics) + R4 (retrieval quality) — 7 features landed as separate commits
- **Summary:** Made Zurvan cheaper and more effective for LLM agents. **(19a)** `CLAUDE.md` agent quickstart (commands, hard rules, change protocol). **(19b)** Restructured `AGENTS.md`: rules + standing invariants first; all but the two most recent entries archived verbatim in `docs/agents-history.md` (44/44 headings verified preserved). **(19c)** `--json` output for `search`, `context --format json`, `eval search`, `eval provenance` — compact machine-parseable payloads (repo-relative paths, single-line snippets, `trace_path`); output format only, scoring asserted unchanged in tests. **(19d)** New MCP tool `zurvan_read_page` wrapping the existing `resource_file` safety gate (12 tools total) so agents keep search compact and open only needed pages. **(19e)** Incremental index rebuild: embeddings reused for unchanged `chunk_id`s (content-hashed), guarded by a provider/model probe — re-index of an unchanged corpus computes 1 embedding instead of ~3.4k. **(19f)** `zurvan agent prime`: topic-free ~300-token orientation card, wired as a Claude Code SessionStart hook in `.claude/settings.json`. **(R4a)** FTS5 `tokenize='porter unicode61'` — documented ranking change: hybrid eval top-1 33%→67%, top-3 100%→100% (0.6 gate passes), MRR 0.556→0.778; closes the R1B lexical miss ("citations" vs "Citation": kw 0.000→0.862 live). **(R4b)** `context --max-per-source` (default 2, 0 disables) caps a dominating source's chunks before budgeting, recovering the R1B cutoff near-misses; excess recorded in traces as `dropped` with reason `source_dedupe`.
- **Files Changed:** `CLAUDE.md` (new), `AGENTS.md`, `docs/agents-history.md` (new), `scripts/context_export.py`, `scripts/eval_search.py`, `scripts/eval_provenance.py`, `scripts/cli.py`, `scripts/mcp_tools.py`, `scripts/mcp_server.py`, `scripts/rebuild_search_index.py`, `scripts/agent_workflow.py`, `.claude/settings.json` (new), `docs/API.md`, tests: `test_context_export.py` (+5), `test_eval_search.py` (+1), `test_eval_provenance.py` (+1), `test_mcp_tools.py` (+3), `test_hybrid_search.py` (+2), `test_agent_workflow.py` (+1), `test_cli.py`
- **Verification:** `pytest` → 251 passed, 0 failed (was 238; +13). Frozen provenance golds re-scored unchanged after both R4 changes: 2C 86% recall, 1B 79%, 0% raw leak / 100% hash / 100% completeness / 100% graph. `eval_search --hybrid --min-top3 0.6` passes (top-3 100%). Embedding reuse measured live: 3400 reused / 1 computed on an unchanged corpus. `agent prime` output < 4k chars (asserted). `public_repo_guard.py` passed per commit; `git diff --check` clean.
- **Follow-ups:** R4a/R4b improve the *live* retriever; the frozen 1B gold's 79% is a historical artifact of its committed traces and intentionally not re-run against the new index. A future R4 benchmark should re-trace the 12 frozen queries as a new artifact (2C/1B style) to quantify the recall gain. R3 remains unbuilt.

### 2026-07-04 (Australia/Sydney)
**Raouf:**
- **Scope:** Full project audit — file-by-file bug hunt + fixes (no R3, no ranking/schema/eval-scoring changes)
- **Summary:** Audited every script in `scripts/`. Fixed 9 confirmed defects: (1) env: `starlette` 1.3.1 (pulled in by `mcp`) removed the `on_startup` kwarg that `fastapi` 0.111 still passes — review workbench + 8 tests were broken; upgraded fastapi to 0.139.0 and raised the `requirements.txt` floor to `>=0.133.0` (first release without a starlette upper bound). (2) `hybrid_search`: unquoted FTS5 terms crashed on bareword keywords (`AND`/`OR`/`NOT`/`NEAR`) — terms now quoted; identical match set/ranking for plain tokens. (3) `chunk.py`: CWD-relative scan meant `zurvan index search` from any foreign CWD (e.g. MCP) silently wiped and rebuilt an EMPTY index — now anchored to `PROJECT_ROOT` with repo-relative identity; chunk IDs verified byte-identical from repo root. (4) `extract.py`: claim tags were joined with a literal `\n` producing malformed YAML (confirmed in `wiki/claims/claim-dummy-001.md`, also repaired); missing `makedirs` for `data/extractions/` + `wiki/summaries/` crashed on fresh trees. (5) `memory.add_claim`: CWD-relative source check broke `zurvan_claim_add` via MCP — resolves against `PROJECT_ROOT`. (6) `cross_project_search`: query was f-string-interpolated into generated Python (quote-in-query breakage / code injection) and spawned bare `python`; query now travels via argv, `sys.executable` everywhere (also `cross_project_context`, `evidence_collect`); `snippet` field was always `None`, now real text. (7) `cli.py`: subprocess commands (`audit`, `index`, `eval search/validate-gold`, `graph rebuild/export`) used bare `python`, CWD-relative paths, and swallowed child exit codes — `zurvan eval search --min-top3` could never fail; new `_run_script()` anchors to `PROJECT_ROOT` and propagates return codes. (8) `context_export._save_synthesis`: keyword-mode `--save` wrote absolute machine paths into tracked wiki frontmatter — now repo-relative. (9) `review_audit`: corrupt-report early return omitted `stats`, crashing the dashboard index rebuild with KeyError.
- **Files Changed:** `requirements.txt`, `scripts/hybrid_search.py`, `scripts/chunk.py`, `scripts/extract.py`, `scripts/memory.py`, `scripts/cross_project_search.py`, `scripts/cross_project_context.py`, `scripts/evidence_collect.py`, `scripts/cli.py`, `scripts/context_export.py`, `scripts/review_audit.py`, `wiki/claims/claim-dummy-001.md`, `tests/test_chunk.py` (+2), `tests/test_hybrid_search.py` (+2), `tests/test_cross_project_search.py` (+1), `tests/test_memory.py` (+1), `tests/test_extract.py` (new, 2), `tests/test_cli.py` (+2)
- **Verification:** `pytest` → 238 passed, 0 failed (baseline was 220 passed / 1 failed / 7 errors; +10 new regression tests). Frozen provenance golds re-scored unchanged: 2C 86% recall, 1B 79%, both 0% raw leak / 100% hash / 100% completeness / 100% graph. `eval_search --hybrid --min-top3 0.6` → top-3 100%. Rebuilt search index chunk-ID set verified identical to pre-fix (only diff = test-injected dummy row + the repaired claim file's own root chunk). Functional: `search_hybrid("search AND rescue")` no longer raises; `scan_markdown_files()` finds 847 files from `/private/tmp` (was 0). `public_repo_guard.py` passed; `git diff --check` clean; `compileall` OK.
- **Follow-ups:** `ingest.py`/`extract.py`/`audit_wiki.py` still write via CWD-relative `wiki/` when run directly (safe via `zurvan` CLI now that subprocesses anchor `cwd=PROJECT_ROOT`) — migrate incrementally. `report_export`'s extra redaction pass mangles 64-char content hashes into `[REDACTED_TOKEN]` (safety-over-fidelity; review someday). Legacy `query.py`/`rebuild_index.py` remain CWD-relative Phase-1 tools.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Step 2D — RQ1 provenance research checkpoint (freeze before R3)
- **Summary:** Froze the retrieval/context provenance subsystem as the RQ1 evidentiary baseline now that all three feature branches (`phase-r1-trace-core` → `phase-r1b-provenance-events` → `phase-r1b-followups`) are merged into `main` at `021572e`. Added a single consolidated checkpoint doc (lineage, verified metrics, miss-analysis summary, safe/unsafe claims, limitations, R3 scope) and tagged the commit `rq1-provenance-checkpoint-2026-06-14`. No code/behaviour change. Verified on `main`: 2C frozen pilot recall 86%, 1B enriched re-run recall 79% — both 0% raw leak / 100% hash / 100% completeness / 100% graph presence; full suite 228 passed.
- **Files Changed:** `docs/evaluation/provenance-rq1-checkpoint-2026-06-14.md` (new). Git tag `rq1-provenance-checkpoint-2026-06-14`.
- **Verification:** Both gold sets validate; `eval_provenance` re-run confirms 86% / 79% recall as above; `pytest` → 228 passed; `public_repo_guard.py` passed; `git diff --check` clean.
- **Follow-ups:** Next is **R3** — opt-in, audit-safe MCP/tool-call + memory/resource provenance (`mcp.tool.requested/allowed/denied/result`, `memory.write[.denied]`, `resource.read[.denied]`). No behaviour changes, no benchmark yet. Phrase completeness as "100% instrumentation coverage over currently implemented events", never "100% complete provenance".

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R1B follow-ups — recall miss-analysis (#2) + scorer negative-branch tests (#1)
- **Summary:** Two post-PR analysis tasks on `phase-r1b-provenance-events`; no R3, and no change to retrieval ranking/scoring/indexing/graph/schema, nor to the frozen 2C pilot or 1B real-run traces. **(#2)** Categorised every missed expected source behind the 79% recall: 6 missed links / 23 across 4 of 12 queries. Read-only finding — **none** is a missing, unindexed, or sub-threshold source; all 6 rank below the query's `limit`. Breakdown: 2 cutoff near-misses (recoverable, one compounded by single-source chunk domination taking 3/5 slots), 2 annotation/query-design mismatches (retriever arguably correct → 79% understates quality), 1 over-broad gold on an ambiguous query, 1 genuine lexical gap (no FTS stemming: query "citations" ≠ heading "Citation"). **(#1)** Added two scorer negative-branch tests proving the conditional discriminates — `retrieval.fusion` is not demanded for a keyword-only gold, and graph is not scored for a non-graph gold, while the same fusion-/graph-less trace fails when those events *are* expected. Verified by mutation (made the scorer unconditional → both tests red → reverted).
- **Files Changed:** `docs/evaluation/provenance-real-run-1b-miss-analysis-2026-06-14.md` + `.json` (new, analysis only), `tests/test_eval_provenance.py` (+2 negative-branch tests). No production code changed.
- **Verification:** `pytest` → 228 passed (+2). New tests proven to bite via scorer mutation (red), then green after `git checkout`. Frozen 1B gold re-scores 79%/100%/100% unchanged. `public_repo_guard.py` passed; `git diff --check` clean; `compileall` OK.
- **Follow-ups:** Stemming/Porter tokenizer (or plural query expansion) would close the one genuine lexical miss; source-dedupe before budgeting would recover the cutoff near-misses — both out of scope here (no ranking/indexing change). Re-annotate `real-evidence-report-02` (gold expects review docs; query points at reports/evidence). R3 still frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R1B — provenance event enrichment (`phase-r1b-provenance-events`)
- **Summary:** Raised the RQ1 provenance ceiling from a 3-event subset toward audit completeness. Added one genuinely-new event type `retrieval.fusion` (records existing hybrid weights `{fts:0.6, embedding:0.4}` + per-chunk ranks; observe-only, hybrid only) and populated the existing `context.assembled.dropped` with real `budget` drops + explicit `dropped_reason: no_dropped_context` for the empty case. Did **not** add `graph.expand` — the `graph_context` event already covers `--graph` expansion and is scored via the existing `expect_graph_context` gold flag (a parallel event would fragment the model). Fixed a retrieval confound found during the re-run: derived trace mirrors (`wiki/traces/*.md`) were being indexed and polluting results with the query's own terms — now excluded from indexing + keyword search. Re-ran the frozen 12-query Step 2C pilot as a **separate** artifact (new `…-r1b` trace IDs + `provenance_real_gold_1b.jsonl`), leaving the original 2C pilot frozen.
- **Files Changed:** `scripts/trace_schema.py` (whitelist `retrieval.fusion`), `scripts/context_export.py` (`_fusion_payload`, `_apply_budget`, `_assembled_context_payload` dropped + reason, fusion emit, trace-mirror skip), `scripts/chunk.py` (exclude `wiki/traces/`), `tests/test_provenance_enrichment.py` (new, 7), `tests/test_chunk.py` (+1), `tests/test_trace_retrieval_integration.py` (new event shape), `eval/provenance_real_gold_1b.jsonl` + `data/traces/…-r1b*` + `docs/evaluation/provenance-real-run-1b-2026-06-14.md` (new)
- **Verification:** `pytest` → 226 passed (+8). Enriched re-run: validate 12/12, replay 12/12, `retrieval.fusion` 12/12, genuine `dropped` on 7/7 context traces (reason `budget`), `provenance_completeness` 100% over the richer pipeline, `graph_context_presence` 100%. Recall 86%→79% (corpus drift, ranking-neutral; frozen 2C pilot still scores 86%). `public_repo_guard.py` passed; `git diff --check` clean; `compileall` OK.
- **Follow-ups:** All-hybrid frozen set can't exercise the "fusion legitimately absent" path — a future keyword-only query would test the conditional denominator. `dropped` reasons limited to `budget` (dedupe/threshold future). R3 MCP tracing still frozen.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2C — real-corpus provenance pilot
- **Summary:** Added an auditable real-corpus pilot before R3. Committed `eval/provenance_real_queries.jsonl` first as a frozen 12-query set (`48a8c27`) with manual expected-source annotations from wiki/docs. Then generated traces using existing `search --trace`, `context --trace`, and selected `context --graph --trace`; linked them in `eval/provenance_real_gold.jsonl`; and documented the run in `docs/evaluation/provenance-real-run-2026-06-14.md`. Pilot metrics: 0% raw leak rate, 100% hash integrity, 12/12 trace validate/replay, 86% expected source recall, 100% built-scope provenance completeness, 100% graph context presence. No R3, MCP tracing, ranking, graph behavior, schema, or evaluator scoring changes.
- **Files Changed:** `eval/provenance_real_queries.jsonl`, `eval/provenance_real_gold.jsonl`, `data/traces/trace-20260614T171000Z-real*.json`, `wiki/traces/trace-20260614T171000Z-real*.md`, `docs/evaluation/provenance-real-run-2026-06-14.md`, `docs/evaluation/provenance.md`, `docs/workflows_and_plans.md`, `eval/README.md`
- **Verification:** Search/graph indexes rebuilt; all 12 queries traced successfully; real gold validation passed; real evaluation reported 12 cases, 0% raw leaks, 100% hash integrity, 86% expected source recall, 100% provenance completeness, 100% graph context presence; trace validate/replay rate 12/12; compileall passed; provenance tests `7 passed`; full suite `218 passed` (2 dependency warnings); `public_repo_guard.py` passed; `git diff --check` passed.
- **Follow-ups:** Treat as pilot evidence, not benchmark evidence; expand real-world gold and annotation review later; keep R3 frozen until merge/review.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2B — stronger provenance gold set
- **Summary:** Expanded provenance evaluation beyond one controlled fixture. Passing baseline now has six cases: `search --trace`, `context --trace`, `context --graph --trace`, legacy coarse `retrieval`, the original Step 2 controlled fixture, and a stale/superseded note case labelled for later. Added isolated negative/failure gold files for raw-path invariant failure, incomplete trace completeness failure, and missing expected source recall failure. No MCP tracing, retrieval ranking, graph behavior, or schema changes.
- **Files Changed:** `eval/provenance_gold*.jsonl`, `data/traces/trace-20260614T16170*.json`, `tests/test_eval_provenance.py`, `README.md`, `docs/TESTING.md`, `docs/evaluation/provenance.md`, `docs/workflows_and_plans.md`, `eval/README.md`
- **Verification:** Step 2B red tests failed on missing gold cases/files; compileall passed; focused provenance+trace tests `27 passed`; full suite `218 passed` (2 dependency warnings); positive gold validates and scores 100% across built-scope metrics on 6 cases; negative fixtures fail as expected; all Step 2B trace fixtures validate; `public_repo_guard.py` passed; `git diff --check` passed.
- **Follow-ups:** Keep R3 frozen; expand to larger real-world gold before making real-world provenance completeness claims.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 Step 2 — provenance evaluation harness
- **Summary:** Added local `eval_provenance.py` plus `eval/provenance_gold.jsonl` to evaluate saved trace provenance. Hard invariants (`raw_leak_rate=0%`, `hash_integrity_rate=100%`) run before graded metrics (`expected_source_recall`, `provenance_completeness`, `graph_context_presence`). Gold schema includes optional `expected_chunk_ids` for future claim-to-chunk faithfulness. Added CLI command `zurvan eval provenance`.
- **Files Changed:** `scripts/eval_provenance.py`, `scripts/cli.py`, `eval/provenance_gold.jsonl`, `data/traces/trace-20260614T151617Z-prov0001.json`, `tests/test_eval_provenance.py`, `docs/evaluation/provenance.md`, `eval/README.md`, `docs/API.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `README.md`
- **Verification:** TDD red run failed on missing module; CLI red run failed on missing action; compileall passed; focused provenance+trace tests `25 passed`; full suite `216 passed` (2 dependency warnings); `eval_provenance.py --validate` passed; CLI `eval provenance` returned 100% built-scope metrics; `public_repo_guard.py` passed; `git diff --check` passed.
- **Follow-ups:** R3 remains frozen; `retrieval.fusion` and `graph.expand` are not scored until implemented.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase R2 retrieval trace — Step 0 reconcile + Step 1A granularity
- **Summary:** Step 0 reconciled stale test counts (`201/10` → reproduced `210/19`) across README/docs/CHANGELOG and R1/R2 audits (commit `44a76f2`). Step 1A added granular, opt-in retrieval provenance events — `retrieval.query`, `retrieval.result`, `context.assembled` — while keeping legacy `retrieval` valid, `schema_version=zurvan.trace.v1`, and the payload-hash rule unchanged (commit `cc87e5d`). No ranking/scoring/fusion/stdout change; tracing opt-in via `--trace`.
- **Files Changed:** `scripts/trace_schema.py`, `scripts/context_export.py`, `tests/test_trace_replay.py`, `tests/test_trace_retrieval_integration.py`, `CHANGELOG.md`, `README.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `docs/audits/phase-r2-retrieval-trace-integration-audit-2026-06-14.md`
- **Verification:** focused trace suite `20 passed`; full suite `211 passed` (2 dependency warnings); legacy single-`retrieval` replay regression passed; `public_repo_guard.py` passed; `git diff --check` passed on branch.
- **Follow-ups:** `context.assembled.dropped` always empty (awaits token-budget policy); `retrieval.fusion`/`graph.expand` not yet implemented; branch pushed, NOT merged to `main` (dirty `main` + `wiki/index.md:669`); next is Step 2 `eval_provenance.py`.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP install — verify Claude Code + add Codex client
- **Summary:** Claude Code already `✔ Connected` (live `mcp_server.py`, no reinstall needed). Added Codex via `codex mcp add zurvan` (absolute Anaconda python + absolute server path; verified `codex mcp get` + launch smoke-test, 11 tools). Made it reproducible: added a `codex` target to `install_mcp_config.py` (emits `codex mcp add` command + `[mcp_servers.zurvan]` TOML via `sys.executable`) and `docs/mcp/codex.md`.
- **Files Changed:** `scripts/install_mcp_config.py`, `tests/test_install_mcp_config.py`, `docs/mcp/codex.md` (+ machine `~/.codex/config.toml`)
- **Verification:** `pytest` → 191 passed (+3). `claude mcp list` → connected. `codex mcp get zurvan` OK. `public_repo_guard.py` passed.
- **Follow-ups:** None.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP server — per-argument schema docs + structured output
- **Summary:** Added `Annotated[..., Field(description=...)]` to every parameter of all 11 MCP tools (per-arg descriptions + bounds: `limit` 1–50, `depth` 1–5, `min_top3` 0–1). Added structured output to `zurvan_graph_stats` via a `GraphStats` TypedDict — FastMCP now emits `outputSchema` + `structuredContent` `{nodes, edges}` plus a JSON text fallback. Text-rich tools (`search`/`context`) intentionally kept as curated text.
- **Files Changed:** `scripts/mcp_server.py`, `scripts/mcp_tools.py`, `tests/test_mcp_tools.py`
- **Verification:** `pytest` → 188 passed (+1). `e2e_mcp_smoke.py` full pass. Per-arg descriptions confirmed in inputSchema; graph_stats returns structuredContent + outputSchema. `public_repo_guard.py` passed.
- **Follow-ups:** None.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** MCP server full audit — LLM usability + correctness fixes
- **Summary:** All 11 MCP tools had empty descriptions (FastMCP reads the wrapper `__doc__`, not the `tools.*` docstrings) — rewrote `mcp_server.py` with rich model-facing docstrings + `Literal` enums (`remember.type`, `decision.status`, `claim.confidence`) + resource descriptions. Fixed CWD bugs: `is_safe_path`/`resource_file` now anchor to `PROJECT_ROOT`. Eval tools run in-process with stdout capture (was a relative `subprocess` to `python`) — also avoids corrupting the stdio stream. Dropped no-op `depth` from `zurvan_graph_neighbours`; `zurvan_remember` now keeps `type` as a tag; `zurvan_search` returns heading+snippet.
- **Files Changed:** `scripts/mcp_server.py`, `scripts/mcp_tools.py`, `scripts/mcp_security.py`, `scripts/mcp_resources.py`
- **Verification:** `pytest` → 187 passed. `e2e_mcp_smoke.py` full pass. Tool descriptions confirmed non-empty; `resource_file` works from `/tmp`; traversal blocked. `public_repo_guard.py` passed.
- **Follow-ups:** Optional: per-arg `Field(description=...)` and structured JSON output.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Full audit — update OpenAI default model (GPT-5.x) + temperature safety
- **Summary:** Verified current OpenAI model naming against official docs; bumped openai default `gpt-4o` → `gpt-5.4-mini` (override via `ZURVAN_LLM_MODEL`). Added `_openai_supports_custom_temperature()` so the `temperature` field is omitted for GPT-5 family and o-series models (they 400 on non-default temperature) but still sent for legacy models. Updated `docs/ENVIRONMENT.md`.
- **Files Changed:** `scripts/llm.py`, `tests/test_llm.py`, `docs/ENVIRONMENT.md`
- **Verification:** `pytest` → 187 passed (+4 new). `public_repo_guard.py` passed.
- **Follow-ups:** None outstanding from the documented list.

### 2026-06-14 (Australia/Sydney)
**Raouf:**
- **Scope:** Full audit — finish CWD-independence for remaining non-MCP scripts
- **Summary:** Closed the documented PROJECT_ROOT follow-up. `graph_build.py` now walks `PROJECT_ROOT` (was `os.walk('.')`, which produced an empty graph from any other CWD) while keeping node identity repo-relative; `get_file_content()` reads via absolute paths. `graph_export.py` default export paths are absolute with a guarded `makedirs`. `eval_search.py` resolves the gold file, expected-path checks, and fallback globs against `PROJECT_ROOT` via a new `_resolve()` helper. `snapshot.py` and `public_repo_guard.py` confirmed correct by design (own `ROOT` join / `git ls-files`-relative).
- **Files Changed:** `scripts/graph_build.py`, `scripts/graph_export.py`, `scripts/eval_search.py`
- **Verification:** `pytest` → 183 passed. Ran graph build / eval / export from `/tmp`: 830 nodes / 746 edges (was 0), gold validated, export to repo `data/`. `eval_search --hybrid --min-top3 0.6` → top-3 100%.
- **Follow-ups:** Review OpenAI model default in `llm.py` (GPT-5.x) — config judgment, not a bug.

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** Fix: CWD-independent absolute paths via PROJECT_ROOT
- **Summary:** Added `PROJECT_ROOT = Path(__file__).parent.parent.resolve()` to `scripts/config.py`. Updated 9 scripts and 4 test files to use it. MCP server now works from any working directory. 183 tests pass.
- **Files Changed:** `scripts/config.py`, `scripts/graph_query.py`, `scripts/graph_schema.py`, `scripts/hybrid_search.py`, `scripts/rebuild_search_index.py`, `scripts/memory.py`, `scripts/context_export.py`, `scripts/mcp_resources.py`, `scripts/wiki_merge.py`, `scripts/ingest.py`, `tests/test_wiki_merge.py`, `tests/test_context_export.py`, `tests/test_ingest.py`, `tests/test_cli.py`
- **Verification:** `pytest` → 183 passed, 0 failed. `public_repo_guard.py` passed.
- **Follow-ups:** Remaining scripts using relative paths (`eval_search.py`, `graph_build.py`, `extract.py`, etc.) are non-MCP-critical; migrate incrementally.

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
- **Summary:** Rewrote README.md from scratch. Added badges (Python, tests, phase, Obsidian, MCP). Replaced flat Goals list with a capability table. Unified CLI syntax to use `zurvan` command throughout. Removed duplicate multiproject code block that appeared under Features by Phase. Added LLM provider table, Obsidian node-type colour table, architecture directory tree, feature history table, and full documentation index table. Sections: What it does · Quick Start · LLM Providers · MCP Server · Obsidian · Agent Workflow · Multi-Project · Evidence/Reports · Snapshots · Architecture · Quality Gate · Feature History · Documentation · Contributing.
- **Files Changed:** `README.md`
- **Verification:** `python scripts/public_repo_guard.py` passed.
- **Follow-ups:** Add LICENSE file (no license currently present).

### 2026-06-03 (Australia/Sydney)
**Raouf:**
- **Scope:** Full Documentation Audit — Phase 18 Sync
- **Summary:** All six stale docs updated to match Phase 18 implementation. ENVIRONMENT.md now lists anthropic as a valid ZURVAN_LLM_PROVIDER option with ANTHROPIC_API_KEY. ARCHITECTURE.md accurately describes wiki/syntheses/, wiki/entities/, data/image_manifest.json, wiki_merge.py, filename_utils.py, and the image/compounding/synthesis data flows. API.md documents --save and --format flags and post-Phase-12 CLI command groups. TESTING.md stage/test counts corrected. workflows_and_plans.md has Phase 18 section. README.md broken code block fixed.
- **Files Changed:** `docs/ENVIRONMENT.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/TESTING.md`, `docs/workflows_and_plans.md`, `README.md`
- **Verification:** `public_repo_guard.py` passed. Pushed to origin/main.
- **Follow-ups:** None. Docs current through Phase 18.

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
- **Scope:** Full Project Audit — Test Fix + Deprecation Cleanup
- **Summary:** 131/131 tests pass after fixing a time-bomb test failure (hardcoded date now > 30 days old in `test_find_stale_decisions`), updating all 10 Starlette `TemplateResponse` calls to the 0.50+ signature, and adding `filter="data"` to `tar.extract()` for Python 3.14 compat.
- **Files Changed:** `tests/test_decision_compare.py`, `scripts/review_routes.py`, `scripts/restore_snapshot.py`
- **Verification:** `pytest` → 131 passed, 0 failed.
- **Follow-ups:** Monitor SwigPy warnings from sentence-transformers dependency if CI tightens.


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

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 11: Cross-Project Decision Memory
- **Summary:** Enabled Zurvan to scan, cache, and compare decisions across all federated projects. Added `zurvan project decisions-all`, `decisions-similar`, `decisions-conflicts`, and `decisions-stale`. Built heuristic algorithms to detect repeating architectural patterns and possible contradictions (e.g., conflicting defaults across projects) without relying on cloud endpoints, LLMs, or cross-project data copying. Cached decisions locally in `~/.zurvan/cache/` to ensure public-repo safety.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 10: Cross-Project Search + Federation
- **Summary:** Added `zurvan project search-all` and `context-all` to federate searches across multiple isolated local knowledge bases. Ensured strict privacy by preventing file copying, absolute path leakage, and cloud dependencies. Read-only federation operations use subprocess execution per-project to prevent data bleed. Added `federation stats` and `doctor` commands to monitor network health.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 9: Multi-Project Workspace Support
- **Summary:** Decoupled private workspace paths from the public repository by introducing a local config directory (`~/.zurvan/projects.json`). Implemented `zurvan project register`, `list`, `current`, `use`, `doctor`, and `snapshot`. Added a global `--project <name>` argument to override the project root for commands like `search` and `context`. Guaranteed full path safety by strictly validating Zurvan project structure and rejecting `raw/` paths.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 8: Release Packaging + Versioned Snapshots
- **Summary:** Added `zurvan version`, `zurvan doctor`, and `zurvan snapshot` commands to make the system portable and safely recoverable. Snapshots intentionally exclude `raw/` by default to prevent data leakage. Restores require explicit confirmation and take automatic safety backups, explicitly blocking traversal paths or writes into `raw/`.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7.5: Obsidian Integration Pack
- **Summary:** Configured Zurvan as a first-class Obsidian vault. Added templates (`wiki/templates/`) for all core knowledge node types and created safe Obsidian settings (`.obsidian/`) to hide internal script and data directories. Added full documentation (`docs/obsidian/`) for vault setup and plugin recommendations.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7: Agent Workflow Orchestration
- **Summary:** Added structured local session management (`session start`, `session close`, `agent preflight`, `agent postedit`) to seamlessly onboard agents like Claude Code, Codex, and Cursor before and after edits. Provided templates and explicit workflow documentation.

### 2026-05-31 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 6.5: MCP Client Integration Pack
- **Summary:** Added `scripts/doctor_mcp.py` to assert system health before connection and `scripts/install_mcp_config.py` to generate safe MCP configurations for clients like Claude Code and Cursor. Added comprehensive client setup guides in `docs/mcp/`. Added explicit warnings when bypassing read-only defaults.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Phase 7: Comprehensive Documentation Audit
- **Summary:** Conducted a full audit of documentation. Fixed markdown errors in README, decoupled technical guides into specific files (`SETUP.md`, `ARCHITECTURE.md`, `API.md`, `ENVIRONMENT.md`, `TESTING.md`, `TROUBLESHOOTING.md`, `DEPLOYMENT.md`). Addressed duplicate chunk_id in `open-questions.md` breaking hybrid search tests.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Local MCP Server for Agent Integration (Phase 6)
- **Summary:** Added `mcp_server.py` and tools/resources/prompts to expose Zurvan via the Model Context Protocol (stdio). Implemented strict safety rules including a read-only mode by default and no arbitrary file reads/execution.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** LLM Provider & PDF Stress Testing
- **Summary:** Added real LLM provider support and PDF extraction. Do not add vector search yet! Ensure basic extractions are robust first.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Extraction Reliability Gauntlet
- **Summary:** Implemented Phase 3.5 testing gauntlet. Do not move to vector search until the matrix is fully verified with messy real-world files.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Agent-Facing CLI Memory Interface
- **Summary:** Implemented Phase 3.6 CLI interface for agents to securely interact with the knowledge base. No vector search yet.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Local Hybrid Search (Phase 4)
- **Summary:** Added local hybrid search (SQLite FTS5 + Mock/Local embeddings). Do not add graph retrieval, MCP, or web UI yet. Stay local-first.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Retrieval Evaluation Harness (Phase 4.5)
- **Summary:** Added `eval/search_gold.jsonl` and metrics. Always evaluate retrieval accuracy before advancing.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Seed Gold Knowledge (Phase 4.6)
- **Summary:** Added validation step to check gold file paths exist before eval. Seeded missing knowledge files. Enforced `min-top3 0.6` in `check.sh`. No graph retrieval yet.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Graph-Assisted Context Expansion (Phase 5.5)
- **Summary:** Added `zurvan context --graph` and `zurvan graph expand` to retrieve graph neighbours along with hybrid search results.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Knowledge Graph Lite (Phase 5)
- **Summary:** Implemented local SQLite-backed graph layer extracting nodes and edges from Markdown wikilinks, frontmatter, and paths. Graph retrieval is pending Phase 5.5.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** Quality Gate (Test-Creator)
- **Summary:** Added `scripts/check.sh` to enforce testing invariants (pytest, gauntlet, audit) sequentially.

### 2026-05-30 (Australia/Sydney)
**Raouf:**
- **Scope:** E2E Smoke Test (Phase 5.5 Finalization)
- **Summary:** Created full E2E test script (`scripts/e2e_smoke.sh`) and fixed exit codes in `scripts/cli.py` and `scripts/memory.py` so memory actions failing return correctly. The E2E tests fully simulate the entire Zurvan pipeline.

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
