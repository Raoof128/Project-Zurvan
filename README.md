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

## Obsidian Integration
Zurvan is highly compatible with [Obsidian](https://obsidian.md/). You can open the entire repository as a vault to get a beautiful graph view and seamless Markdown editing.

To use Zurvan with Obsidian:
1. Open Obsidian and select **Open folder as vault**.
2. Select the root `Zurvan/` directory.

We have included safe `.obsidian/` configurations that automatically ignore non-knowledge folders (like `data/` and `scripts/`) to keep your vault clean.
See the [Obsidian Setup Guides](docs/obsidian/) for plugin recommendations and graph-view setup.

## Agent Workflow Orchestration
Zurvan includes tools to structure AI agent sessions (for Claude Code, Codex, Cursor, etc.). By running these before and after edits, agents can safely maintain context and memory.

```bash
# 1. Start a session
python scripts/cli.py session start --topic "Database refactor"

# 2. Get dense pre-edit context
python scripts/cli.py agent preflight --topic "database"

# 3. Record changes
python scripts/cli.py agent postedit --summary "Updated schema" --files db.py --checks "pytest"

# 4. Close session
python scripts/cli.py session close --topic "Database refactor" --summary "Done" --checks "pytest"
```
See the [Agent Workflow Guides](docs/agent-workflows/) for tool-specific instructions.

## Snapshots & Versioning
Zurvan supports lightweight, local snapshots to backup or migrate your knowledge graph.
```bash
# Check system health and version
zurvan doctor
zurvan version

# Create a snapshot (excludes raw/ by default for safety)
zurvan snapshot create

# Restore a snapshot (requires --force)
zurvan snapshot restore zurvan_snapshot_XYZ.tar.gz --force
```
See the [Release Packaging Guides](docs/release/) for details on portability and backups.

## Managing Multiple Projects (Phase 9)
Zurvan allows a single CLI installation to manage multiple independent knowledge bases (vaults) on your local machine securely without committing paths to a public repo.

```bash
# Register a project
zurvan project register --name my-vault --path .

# List projects
zurvan project list

# Switch default project
zurvan project use my-vault

# Search across all projects
zurvan project search-all "architecture"

# Decision Memory
zurvan project decisions-all
zurvan project decisions-conflicts

# Policy Radar
zurvan project radar scan
zurvan project radar contradictions
zurvan project radar drift
zurvan --project my-vault search "architecture"
```
*Note: Your local workspace registry is safely stored in `~/.zurvan/projects.json` and is explicitly ignored by Git to protect your absolute paths.*

### 🔎 Cross-Project Federation & Decision Memory
- **Cross-Project Federation**: Search, context, and decision memory across multiple vaults.
- **Policy Radar**: Heuristic detection of cross-project contradictions and policy drift.
- **Evidence Pack Builder**: Generate shareable, citation-backed, redacted evidence bundles locally.

## Features by Phase

- Phase 1: Local Knowledge Vault
- Phase 2: Structured Document Extraction
- Phase 3: Agent-Facing CLI Memory Interface
- Phase 4: Local Hybrid Search (FTS5 + Embeddings)
- Phase 5: Knowledge Graph Lite
- Phase 6: Local MCP Server
- Phase 7: Agent Workflow Orchestration & Obsidian Integration
- Phase 8: Release Packaging & Snapshots
- Phase 9: Multi-Project Workspace Support
- Phase 10: Cross-Project Federation
- Phase 11: Cross-Project Decision Memory
- Phase 12: Cross-Project Policy Radar
- Phase 13: Evidence Pack Builder
- Phase 14: Report Composer
- Phase 15: Local Report Review Workbench
- Phase 16: Review Workbench Hardening + UX Polish
- Phase 17: Export & Publication Pack
- **Phase 18 ✅** — Living Wiki + Provider Expansion (Anthropic provider, cross-source merge, --save, log format contract, image skeleton, --format table/marp)

```bash
# Register a project
zurvan project register --name tizbin --path /Users/you/tizbin

# Search across all registered projects
zurvan project search-all "MCP security"

# Build context from multiple projects
zurvan project context-all --topic "agent memory"

# Decision Memory
zurvan project decisions-all
zurvan project decisions-similar "read only mcp"
zurvan project decisions-conflicts
zurvan project decisions-stale --days 90
zurvan project decision-memory rebuild
```

### Privacy Guarantee

# Check federation health
zurvan project federation doctor
```
See the [Federation Guides](docs/federation/overview.md) for details on the privacy model and workflows.

## Quality Gates
Run the full testing sequence (Unit tests, extraction gauntlet, wiki audit, eval, graph tests, MCP tests) to ensure the engine is fully functional before committing changes:
```bash
bash scripts/check.sh
```
