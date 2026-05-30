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

### What `check.sh` Does
1. **Pytest Suite**: Runs unit and integration tests in the `tests/` directory.
2. **Reliability Gauntlet**: Processes the `docs/extraction_test_matrix.md` files (simulating ingestion and extraction across PDF, MD, TXT) to ensure the pipeline doesn't crash on messy data.
3. **Wiki Audit**: Runs `scripts/audit_wiki.py` to ensure all generated Markdown files possess correct YAML frontmatter, valid links, and citations.
4. **Index Rebuild**: Rebuilds the search and graph databases.
5. **Retrieval Evaluation**: Runs `zurvan eval search` against `eval/search_gold.jsonl`. Must achieve at least a `0.6` Top-3 score to pass.
6. **Graph Context Expansion**: Tests the `zurvan context --graph` logic.
7. **MCP Server Smoke Test**: Boots the local MCP server over stdio and executes `scripts/e2e_mcp_smoke.py` to verify tools, prompts, resources, and security boundaries.

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
