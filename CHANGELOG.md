## Change Log

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
