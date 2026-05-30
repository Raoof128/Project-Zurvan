# Architecture Overview

Zurvan is built on a **local-first, markdown-centric** philosophy. It is designed to act as a structured, git-friendly knowledge vault that agents (or humans) can query, interact with, and expand over time.

## Core Principles
1. **Immutable Raw Sources**: Files placed in `raw/` are untrusted but immutable. They act as the single source of truth for citations.
2. **Markdown as the Database**: The `wiki/` directory contains generated Markdown. This makes the vault human-readable, easily diffable in Git, and directly compatible with Obsidian.
3. **Local SQLite Indices**: Search, graph relationships, and source registries are tracked in local SQLite files in `data/`. These can be safely deleted and rebuilt entirely from the Markdown wiki.
4. **No Internal LLM Loops**: Zurvan is a storage engine, not an agent. It provides APIs and CLI hooks for agents to call it.

## Directory Structure
- `raw/`: The input folder. Agents are strictly forbidden from writing here. Contains sources (PDF, MD, TXT).
- `wiki/`: The generated vault.
  - `wiki/sources/`: Markdown representations of the raw files.
  - `wiki/claims/`: Single-fact markdown files extracted from sources.
  - `wiki/concepts/`: Key definitions and concepts.
  - `wiki/decisions/`: Project decisions and rationales.
  - `wiki/index.md` & `wiki/log.md`: Aggregated indices.
- `data/`: Ephemeral SQLite caches.
  - `registry.sqlite`: Tracks ingested raw files to avoid duplicates.
  - `search.sqlite`: FTS5 text search and semantic embedding index.
  - `graph.sqlite`: Local knowledge graph mapping edges (wikilinks, frontmatter).
- `scripts/`: The core logic. See `docs/workflows_and_plans.md` for python script details.
- `eval/`: Retrieval evaluation harness.

## Data Flow
### 1. Ingestion (`scripts/ingest.py`)
Reads `raw/` files -> parses text -> creates `wiki/sources/<filename>.md` -> records hash in `registry.sqlite`.

### 2. Extraction (`scripts/extract.py`)
Reads `wiki/sources/` -> queries LLM -> validates schema -> writes individual Markdown claims/concepts with Obsidian-style wikilinks.

### 3. Graph & Search Rebuild
Scrapes the `wiki/` folder -> reconstructs SQLite indexes based purely on Markdown frontmatter and wikilinks.

### 4. Agent Context Retrieval
Agent queries via CLI or MCP -> Zurvan performs Hybrid Search (FTS5 + Semantic) -> Expands results via Graph Neighbours -> Returns unified Markdown context bundle.
