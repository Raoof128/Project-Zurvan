# Local-first LLM Wiki Knowledge Engine

*Inspired by [Andrej Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).*

A local, Markdown-based wiki knowledge engine that uses LLMs to ingest raw sources, extract claims, entities, and concepts, and generate a linked Markdown wiki.

## Goals
- Convert raw sources into a maintained Markdown wiki.
- Preserve raw sources as immutable source-of-truth.
- Generate source summaries, concepts, entities, claims, contradictions, and open questions.
- Maintain `wiki/index.md` and `wiki/log.md`.
- Git-friendly Markdown output.
- Include citation metadata for claims.

## Project Documentation
Detailed documentation is split into dedicated files:
- [Setup Guide](docs/SETUP.md): Instructions for installation and initialisation.
- [Architecture Overview](docs/ARCHITECTURE.md): Design principles, data flow, and directory structure.
- [Environment Variables](docs/ENVIRONMENT.md): Configuration for LLMs, embeddings, and security.
- [Integration & API Guide](docs/API.md): Usage of the CLI and the local MCP server.
- [Testing Guide](docs/TESTING.md): Running the quality gates and evaluations.
- [Troubleshooting](docs/TROUBLESHOOTING.md): Common errors and fixes.
- [Deployment](docs/DEPLOYMENT.md): Deployment notes and limitations.
- [Workflows and Script Plans](docs/workflows_and_plans.md): Detailed python scripting breakdowns.
- [Extraction Test Matrix](docs/extraction_test_matrix.md): Formats successfully handled by the extraction gauntlet.
- [Agent Rules](AGENTS.md): Strict invariants for AI agent interaction.

## Quick Start
```bash
pip install -r requirements.txt
export PYTHONPATH=.
```

### 1. Ingestion & Extraction
```bash
# Ingest an immutable source document
python scripts/ingest.py raw/notes/example.md

# Extract knowledge via configured LLM provider
python scripts/extract.py --source wiki/sources/example.md.md
```

### 2. Search & Expand Context
```bash
# Keyword Search
zurvan search "local-first architecture"

# Hybrid Search (Keyword + semantic embeddings)
zurvan search "local-first architecture" --hybrid

# Export expanded graph-assisted context bundle
zurvan context --topic "project roadmap" --hybrid --graph --limit 10
```

### 3. MCP Server (Agent Memory Integration)
Zurvan can act as a local Model Context Protocol (MCP) server over `stdio` to provide structured long-term memory to agents like Claude Code or Cursor. It operates in **read-only mode** by default.

```bash
# Verify system readiness
python scripts/doctor_mcp.py

# Generate client configuration
python scripts/install_mcp_config.py --client claude-code --readonly
```
For full configuration details, see the Client Integration Pack:
- [Claude Code Setup](docs/mcp/claude-code.md)
- [Cursor Setup](docs/mcp/cursor.md)
- [Codex-style Agents](docs/mcp/codex-style-agents.md)
- [MCP Security](docs/mcp/security.md)
- [MCP Troubleshooting](docs/mcp/troubleshooting.md)

## Quality Gates
Run the full testing sequence (Unit tests, extraction gauntlet, wiki audit, eval, graph tests, MCP tests) to ensure the engine is fully functional before committing changes:
```bash
bash scripts/check.sh
```
