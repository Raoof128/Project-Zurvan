## Change Log

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
