# Zurvan

> **Local-first LLM knowledge engine** — ingest any document, extract structured knowledge, and query it via hybrid search, graph expansion, or MCP agent memory.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-218%20passing-brightgreen)](docs/TESTING.md)
[![Phase](https://img.shields.io/badge/phase-18%20%E2%9C%85-blueviolet)](CHANGELOG.md)
[![Obsidian](https://img.shields.io/badge/Obsidian-compatible-7c3aed?logo=obsidian&logoColor=white)](docs/obsidian/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio%20server-orange)](docs/mcp/)

*Inspired by [Andrej Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).*

---

## What it does

Zurvan turns raw documents (Markdown, PDF, plain text, images) into a **linked, searchable, git-friendly wiki** — then exposes that wiki to AI agents via a local MCP server.

| Capability | Detail |
|---|---|
| **Ingestion** | MD · PDF · TXT · images (pending-visual stub) |
| **Extraction** | Claims · concepts · entities · decisions — via any LLM provider |
| **Search** | SQLite FTS5 + semantic embeddings (hybrid) |
| **Graph** | Local knowledge graph; wikilink-aware; graph-neighbour expansion |
| **Agent memory** | MCP stdio server — read-only by default, opt-in write mode |
| **Audit traces** | Local JSON + Markdown traces for replayable agent provenance |
| **Living wiki** | Concept/entity pages compound additively across sources |
| **Multi-project** | Federate search and decisions across independent vaults |
| **Evidence → Reports** | Pack → compose → review → publish, fully local, redacted |
| **Obsidian** | Open the repo root as a vault — colour-coded graph, 7 node types |

---

## Quick Start

```bash
pip install -r requirements.txt
export PYTHONPATH=.
```

### 1. Ingest a source

```bash
python scripts/ingest.py raw/notes/my-doc.md
```

### 2. Extract knowledge

```bash
# Uses mock LLM by default — set ZURVAN_LLM_PROVIDER=openai|anthropic for real extraction
python scripts/extract.py --source wiki/sources/my-doc.md.md
```

### 3. Search and retrieve context

```bash
# Hybrid keyword + semantic search
zurvan search "local-first architecture" --hybrid

# Save results as a wiki synthesis page
zurvan search "local-first architecture" --hybrid --save

# Opt-in retrieval trace
zurvan search "local-first architecture" --hybrid --trace

# Graph-assisted context bundle for an agent
zurvan context --topic "project roadmap" --hybrid --graph --limit 10

# Opt-in context trace with graph provenance
zurvan context --topic "project roadmap" --hybrid --graph --trace

# Render as Markdown table or Marp slides (stdout only)
zurvan context --topic "project roadmap" --format table
zurvan context --topic "project roadmap" --format marp
```

### 4. Inspect audit traces

```bash
zurvan trace list
zurvan trace inspect trace-20260614T010203Z-abcdef12
zurvan trace validate trace-20260614T010203Z-abcdef12
zurvan trace replay trace-20260614T010203Z-abcdef12
```

---

## LLM Providers

| Provider | Env var | Notes |
|---|---|---|
| `mock` *(default)* | — | Deterministic; safe for dev/test |
| `openai` | `OPENAI_API_KEY` | GPT-4o / GPT-5 |
| `anthropic` | `ANTHROPIC_API_KEY` | Claude via raw `urllib` — no SDK dependency |

```bash
export ZURVAN_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

See [Environment Variables](docs/ENVIRONMENT.md) for all options.

---

## MCP Server — Agent Memory Integration

Zurvan acts as a local [Model Context Protocol](https://modelcontextprotocol.io/) server over `stdio`, giving agents like Claude Code and Cursor structured long-term memory.

```bash
# Verify system readiness
python scripts/doctor_mcp.py

# Generate client configuration (read-only by default)
python scripts/install_mcp_config.py --client claude-code --readonly
```

**Available MCP tools:** `zurvan_search` · `zurvan_context` · `zurvan_remember` · `zurvan_decision_add` · `zurvan_graph_stats` · `zurvan_graph_neighbours` · and more.

> Write mode is disabled by default. To opt in: `python scripts/mcp_server.py --write`.
> See [MCP Security](docs/mcp/security.md) for the full security model.

Client setup guides: [Claude Code](docs/mcp/claude-code.md) · [Cursor](docs/mcp/cursor.md) · [Codex-style Agents](docs/mcp/codex-style-agents.md)

---

## Obsidian Integration

Open the repo root as an Obsidian vault for a colour-coded graph view and seamless Markdown editing.

1. Open Obsidian → **Open folder as vault** → select `Zurvan/`
2. The vault excludes `data/`, `scripts/`, `tests/`, and `raw/` automatically

**Graph colour groups** (pre-configured in `.obsidian/graph.json`):

| Colour | Node type |
|---|---|
| 🟠 Orange | Decisions |
| 🔵 Blue | Claims |
| 🟣 Purple | Concepts |
| 🟢 Green | Sessions |
| 🔴 Red | Contradictions |
| 🟡 Gold | Entities |
| 🩵 Teal | Syntheses |

See [Obsidian Setup Guides](docs/obsidian/) for plugin recommendations and graph-view tips.

---

## Agent Workflow Orchestration

Structure AI agent sessions so context is safely preserved before and after edits.

```bash
# Start a session
zurvan session start --topic "Database refactor"

# Load dense pre-edit context
zurvan agent preflight --topic "database"

# Record changes made
zurvan agent postedit --summary "Updated schema" --files db.py --checks "pytest"

# Close session
zurvan session close --topic "Database refactor" --summary "Done" --checks "pytest"
```

See [Agent Workflow Guides](docs/agent-workflows/) for Claude Code, Codex, and Cursor specifics.

---

## Multi-Project Workspace

A single Zurvan installation can manage multiple independent vaults. Project paths are stored in `~/.zurvan/projects.json` — never committed.

```bash
# Register vaults
zurvan project register --name my-project --path /path/to/project
zurvan project list
zurvan project use my-project

# Cross-vault search and context
zurvan project search-all "MCP security"
zurvan project context-all --topic "agent memory"

# Cross-vault decision memory
zurvan project decisions-all
zurvan project decisions-similar "read-only MCP"
zurvan project decisions-conflicts
zurvan project decisions-stale --days 90

# Policy radar — detect contradictions across vaults
zurvan project radar scan
zurvan project radar contradictions
zurvan project radar drift

# Federation health
zurvan project federation doctor
```

See [Federation Guides](docs/federation/overview.md) for the privacy model.

---

## Evidence, Reports & Publication

Build citation-backed, redacted evidence packs and compose structured reports — entirely offline.

```bash
# Evidence
zurvan evidence pack
zurvan evidence export

# Reports
zurvan report compose
zurvan report validate

# Review workbench (localhost only)
zurvan review serve

# Publish
zurvan publish export --format markdown
zurvan publish bundle
```

All output is stored in `~/.zurvan/` — outside the git repo.

---

## Snapshots

```bash
zurvan doctor                                        # System health check
zurvan version                                       # Version info
zurvan snapshot create                               # Backup (excludes raw/)
zurvan snapshot restore zurvan_snapshot_XYZ.tar.gz --force
```

See [Release Packaging Guides](docs/release/) for portability and migration details.

---

## Architecture

```
raw/          ← Immutable source documents (never edited)
wiki/         ← Generated Markdown vault (human-readable, Obsidian-compatible)
  sources/    ← One stub per ingested file
  claims/     ← Single-fact files with citations
  concepts/   ← Key definitions (additively compounded across sources)
  entities/   ← Named entities (also compounded)
  decisions/  ← Project decisions and rationales
  syntheses/  ← Query-derived pages written via --save
  traces/     ← Markdown trace mirrors
  log.md      ← Grep-parseable audit log
data/         ← Ephemeral SQLite caches (rebuild any time from wiki/)
  registry.sqlite   ← Ingestion deduplication
  search.sqlite     ← FTS5 + embeddings
  graph.sqlite      ← Knowledge graph (wikilinks + frontmatter)
  traces/           ← Local trace JSON records
scripts/      ← Core pipeline logic
```

Full design details: [Architecture Overview](docs/ARCHITECTURE.md) · [Workflows & Script Plans](docs/workflows_and_plans.md)

---

## Quality Gate

```bash
bash scripts/check.sh
```

Runs 22 stages: unit tests · extraction gauntlet · wiki audit · retrieval eval · graph tests · MCP smoke · evidence/report/publication pipeline · review workbench.

**Current:** 218 tests passing, 0 failing. See [Testing Guide](docs/TESTING.md).

---

## Feature History

| Phase | Feature |
|---|---|
| 1 | Local Knowledge Vault |
| 2 | Structured Document Extraction |
| 3 | Agent-Facing CLI Memory Interface |
| 4 | Local Hybrid Search (FTS5 + Embeddings) |
| 5 | Knowledge Graph |
| 6 | Local MCP Server |
| 7 | Agent Workflow Orchestration + Obsidian Integration |
| 8 | Release Packaging + Snapshots |
| 9 | Multi-Project Workspace |
| 10 | Cross-Project Federation |
| 11 | Cross-Project Decision Memory |
| 12 | Cross-Project Policy Radar |
| 13 | Evidence Pack Builder |
| 14 | Report Composer |
| 15 | Local Report Review Workbench |
| 16 | Review Workbench Hardening + UX Polish |
| 17 | Export & Publication Pack |
| **18 ✅** | **Living Wiki + Provider Expansion** (Anthropic, additive merge, `--save`, `--format table/marp`, image skeleton) |

---

## Documentation

| Guide | Description |
|---|---|
| [Setup](docs/SETUP.md) | Installation and initialisation |
| [Architecture](docs/ARCHITECTURE.md) | Design principles, data flow, directory structure |
| [Environment Variables](docs/ENVIRONMENT.md) | LLM providers, embeddings, security config |
| [API & CLI Reference](docs/API.md) | Full CLI command reference |
| [Testing Guide](docs/TESTING.md) | Quality gates and evaluation harness |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common errors and fixes |
| [Deployment](docs/DEPLOYMENT.md) | Deployment notes and limitations |
| [Workflows & Scripts](docs/workflows_and_plans.md) | Detailed pipeline logic |
| [Extraction Test Matrix](docs/extraction_test_matrix.md) | Formats handled by the gauntlet |
| [Agent Rules](AGENTS.md) | Strict invariants for AI agent interaction |
| [MCP Integration](docs/mcp/) | Claude Code, Cursor, Codex-style agent setup |
| [Obsidian Setup](docs/obsidian/) | Vault config, plugins, graph view |
| [Federation](docs/federation/) | Multi-vault search and privacy model |
| [Agent Workflows](docs/agent-workflows/) | Per-tool session workflow guides |

---

## Contributing

See [AGENTS.md](AGENTS.md) for the invariants all contributors (human and AI) must follow — particularly around `raw/` immutability, citation integrity, and public repo safety.

Run `bash scripts/check.sh` before submitting any changes.
