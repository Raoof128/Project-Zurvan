# Testing Guide

Zurvan has a robust multi-layered testing strategy enforced by a master quality gate.

## Running the Quality Gate

To run all tests, evaluations, and audits sequentially:
```bash
export PYTHONPATH=.
export ZURVAN_LLM_PROVIDER=mock
export ZURVAN_EMBED_PROVIDER=mock
bash scripts/check.sh
```

### What `check.sh` Does (22 stages)
1. **Public Repo Guard**: Ensures no absolute paths, tokens, or private files are tracked.
2. **Pytest Suite** (211 tests): Runs all unit and integration tests in the `tests/` directory.
3. **Reliability Gauntlet**: Processes sample raw files (PDF, MD, TXT) to verify the ingest/extract pipeline doesn't crash on messy data.
4. **Wiki Audit**: Runs `scripts/audit_wiki.py` to ensure all generated Markdown files have correct YAML frontmatter, valid links, and citations.
5. **Index Rebuild**: Rebuilds the search and graph databases.
6. **Retrieval Evaluation**: Runs `zurvan eval search` against `eval/search_gold.jsonl`. Must achieve at least a `0.6` Top-3 score to pass.
7. **Graph Context Expansion**: Tests `zurvan context --graph` logic.
8. **MCP Doctor**: Validates system health before MCP connections.
9. **MCP Server Tests**: Runs the full MCP test suite (security, tools, resources, smoke).
10. **MCP E2E Smoke Test**: Boots the MCP server over stdio and validates all tools/resources end-to-end.
11. **Agent Workflow Smoke Test**: Exercises `session start/close` and `agent preflight/postedit`.
12. **Version Check**: Verifies `zurvan version` output.
13. **Doctor Check**: Verifies `zurvan doctor` output.
14. **Snapshot**: Creates a snapshot and verifies the manifest.
15. **Workspace Registry**: Registers a test project and verifies listing/current.
16. **Federation**: Tests cross-project `search-all` and `context-all`.
17. **Decision Memory**: Exercises `decisions-all` and `decisions-stale`.
18. **Policy Radar**: Exercises `radar policies`, `contradictions`, `drift`.
19. **Evidence Pack Builder**: Builds, inspects, and exports a pack.
20. **Report Composer**: Composes, validates, and exports a report.
21. **Review Workbench**: Boots the review server and verifies it starts cleanly.
22. **Review Audit & Index**: Exercises `review audit` and `review index rebuild`.

## Writing Tests
- All unit tests should be placed in the `tests/` directory.
- Use `pytest` fixtures where appropriate.
- When writing tests that modify memory, use isolated temporary directories or the provided mock fixtures to prevent contaminating the actual `wiki/` directory.

## Testing the MCP Server
To manually run just the MCP security and smoke tests:
```bash
python -m pytest tests/test_mcp_security.py tests/test_mcp_tools.py tests/test_mcp_resources.py tests/test_mcp_server_smoke.py
python scripts/e2e_mcp_smoke.py
```
