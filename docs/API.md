# Integration & API Guide

Zurvan offers two primary methods for external integration: the Command Line Interface (CLI) and the Model Context Protocol (MCP) server.

## 1. CLI Interface (`scripts/cli.py`)

The CLI is designed for bash scripts or direct human/agent interaction on the local machine. 
*Note: Make sure to set `export PYTHONPATH=.` before running scripts.*

### Search & Retrieval
```bash
# Keyword Search
python scripts/cli.py search "architecture"

# Hybrid Search (Keyword + Semantic)
python scripts/cli.py search "architecture" --hybrid

# Context Bundle Export
python scripts/cli.py context --topic "vector search" --limit 10

# Graph-Assisted Context Bundle
python scripts/cli.py context --topic "vector search" --hybrid --graph
```

### Writing Memory
```bash
# Add a decision
python scripts/cli.py decision add --title "Use SQLite" --reason "Simple and local" --status accepted --tags architecture

# Add a claim
python scripts/cli.py claim add --text "SQLite is fast" --source "docs/architecture.md" --evidence "SQLite is fast" --confidence high
```

## 2. MCP Server (`scripts/mcp_server.py`)

Zurvan provides a native MCP server implementation to directly plug into agents like Claude Code or Cursor.

### Transport & Security
- **Transport**: Supports `stdio` only.
- **Security**: Starts in strictly **read-only** mode by default (`ZURVAN_MCP_READONLY=1`). To enable write tools, you must explicitly set `ZURVAN_MCP_READONLY=0` in your environment. Absolute paths, `../` traversal, and raw folder access are strictly blocked.

### Exposed MCP Tools
- **Read**: `zurvan_search`, `zurvan_context`, `zurvan_graph_stats`, `zurvan_graph_neighbours`, `zurvan_graph_expand`, `zurvan_eval_search`, `zurvan_validate_gold`.
- **Write** (if enabled): `zurvan_remember`, `zurvan_decision_add`, `zurvan_claim_add`, `zurvan_question_add`.

### Exposed MCP Resources
- `zurvan://wiki/index`
- `zurvan://wiki/log`
- `zurvan://wiki/overview`
- `zurvan://wiki/open-questions`
- `zurvan://graph/stats`
- `zurvan://eval/baseline`

### Exposed MCP Prompts
- `zurvan_project_brief`
- `zurvan_pre_edit_context`
- `zurvan_post_edit_memory`
- `zurvan_research_audit`
