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

# Save search results as a synthesis page (wiki/syntheses/)
python scripts/cli.py search "architecture" --hybrid --save

# Context Bundle Export
python scripts/cli.py context --topic "vector search" --limit 10

# Graph-Assisted Context Bundle
python scripts/cli.py context --topic "vector search" --hybrid --graph

# Save context as a synthesis page
python scripts/cli.py context --topic "vector search" --save

# Render context as a Markdown table (stdout only)
python scripts/cli.py context --topic "vector search" --format table

# Render context as a Marp slide deck (stdout only)
python scripts/cli.py context --topic "vector search" --format marp
```

### Writing Memory
```bash
# Add a decision
python scripts/cli.py decision add --title "Use SQLite" --reason "Simple and local" --status accepted --tags architecture

# Add a claim
python scripts/cli.py claim add --text "SQLite is fast" --source "docs/architecture.md" --evidence "SQLite is fast" --confidence high
```

### Evidence, Reports & Publication
```bash
# Build an evidence pack (stored in ~/.zurvan/evidence/)
python scripts/cli.py evidence build --topic "search architecture" --hybrid --graph

# List and inspect packs
python scripts/cli.py evidence list
python scripts/cli.py evidence inspect <pack-id>

# Export a pack
python scripts/cli.py evidence export <pack-id> --format markdown

# Compose a report from a pack
python scripts/cli.py report compose --pack <pack-id> --template evidence_digest

# Validate and export a report
python scripts/cli.py report validate <report-id>
python scripts/cli.py report export <report-id> --format markdown

# Publish a report bundle
python scripts/cli.py publish export <report-id>
python scripts/cli.py publish bundle <report-id>
```

### Review Workbench
```bash
# Launch local review UI (http://127.0.0.1:8768)
python scripts/cli.py review serve

# Audit citations and detect unsafe content
python scripts/cli.py review audit

# Rebuild review index
python scripts/cli.py review index rebuild
```

### Audit Traces
```bash
# List locally saved trace records
python scripts/cli.py trace list

# Inspect a trace JSON document
python scripts/cli.py trace inspect <trace-id>

# Validate trace schema, required fields, and payload hashes
python scripts/cli.py trace validate <trace-id>

# Render a deterministic Markdown replay without executing tools
python scripts/cli.py trace replay <trace-id>
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
