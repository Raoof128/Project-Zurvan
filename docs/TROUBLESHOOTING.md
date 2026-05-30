# Troubleshooting Guide

### 1. `ModuleNotFoundError` when running scripts
**Error**: `ModuleNotFoundError: No module named 'scripts'`
**Fix**: Ensure you have set the python path to the project root.
```bash
export PYTHONPATH=.
```

### 2. Search Index / Graph Out of Sync
**Error**: Searches are returning files that were deleted, or missing new files.
**Fix**: Rebuild the ephemeral SQLite databases.
```bash
python scripts/cli.py index rebuild
python scripts/cli.py graph rebuild
```

### 3. MCP Write Tools Returning "Blocked"
**Error**: `Write access denied. Zurvan MCP server is running in read-only mode.`
**Fix**: The MCP server prioritises safety. If you need your agent to write to the wiki, you must explicitly configure your client's environment variables to disable read-only mode:
```bash
export ZURVAN_MCP_READONLY=0
```

### 4. Hybrid Search Fails or Returns Poor Results
**Error**: Semantic search is breaking or returning weird chunks.
**Fix**: Verify your embedding provider in `.env`. If you don't have `sentence-transformers` installed, ensure it is set to mock.
```bash
export ZURVAN_EMBED_PROVIDER=mock
```

### 5. `check.sh` Fails on Retrieval Evaluation
**Error**: `Search Evaluation Results: Top-3 accuracy: 33% ... FAILED`
**Fix**: The quality gate requires a minimum retrieval accuracy (currently `0.6` MRR/Top-3) against the gold standard dataset (`eval/search_gold.jsonl`). If you modified the chunking logic or deleted wiki files, you may need to update the gold dataset or restore the lost context.
