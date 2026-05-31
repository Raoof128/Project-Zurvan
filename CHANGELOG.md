## Change Log

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
