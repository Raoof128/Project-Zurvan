# Architecture Overview

Zurvan is built on a **local-first, markdown-centric** philosophy. It is designed to act as a structured, git-friendly knowledge vault that agents (or humans) can query, interact with, and expand over time.

## Core Principles
1. **Immutable Raw Sources**: Files placed in `raw/` are untrusted but immutable. They act as the single source of truth for citations.
2. **Markdown as the Database**: The `wiki/` directory contains generated Markdown. This makes the vault human-readable, easily diffable in Git, and directly compatible with Obsidian.
3. **Local SQLite Indices**: Search, graph relationships, and source registries are tracked in local SQLite files in `data/`. These can be safely deleted and rebuilt entirely from the Markdown wiki.
4. **No Internal LLM Loops**: Zurvan is a storage engine, not an agent. It provides APIs and CLI hooks for agents to call it.

## Directory Structure
- `raw/`: The input folder. Agents are strictly forbidden from writing here. Contains sources (PDF, MD, TXT, images).
- `wiki/`: The generated vault.
  - `wiki/sources/`: Markdown representations of raw files. Image files get `pending-visual` stubs.
  - `wiki/claims/`: Single-fact markdown files extracted from sources.
  - `wiki/concepts/`: Key definitions and concepts. Pages compound across sources via additive merge.
  - `wiki/entities/`: Named entities extracted from sources. Also compounded additively.
  - `wiki/decisions/`: Project decisions and rationales.
  - `wiki/syntheses/`: Query-derived synthesis pages written via `--save` (e.g. `zurvan context --save`).
  - `wiki/index.md` & `wiki/log.md`: Aggregated indices. `log.md` uses grep-parseable `## [YYYY-MM-DD] kind | ...` format.
- `data/`: Ephemeral SQLite caches and metadata.
  - `registry.sqlite`: Tracks ingested raw files to avoid duplicates.
  - `search.sqlite`: FTS5 text search and semantic embedding index.
  - `graph.sqlite`: Local knowledge graph mapping edges (wikilinks, frontmatter).
  - `image_manifest.json`: Catalogue of all image files detected during ingestion (pending-visual status).
- `scripts/`: The core logic. See `docs/workflows_and_plans.md` for python script details.
- `eval/`: Retrieval evaluation harness.

## Data Flow
### 1. Ingestion (`scripts/ingest.py`)
- **Text sources** (TXT, MD, PDF): parses text → creates `wiki/sources/<filename>.md` → records hash in `registry.sqlite` → appends `## [date] ingest | …` entry to `wiki/log.md`.
- **Image files** (PNG, JPG, GIF, WebP): creates a `pending-visual` stub in `wiki/sources/` → appends entry to `data/image_manifest.json` → logs `image-skip` event.
- **Embedded image refs** in Markdown/PDF: detected and logged as `image-skip` events without downloading or OCR.

### 2. Extraction (`scripts/extract.py`)
Reads `wiki/sources/` → queries LLM → validates schema → routes concept/entity pages through `scripts/wiki_merge.py:merge_extraction()` for **additive, idempotent compounding** across multiple sources. Image source files are skipped automatically.

### 3. Compounding Wiki (`scripts/wiki_merge.py`)
Canonical writer for concept and entity pages. Each new source adds a `## Evidence from <source_id>` section rather than overwriting. Migrates legacy `source_id:` frontmatter to the multi-source `sources:` list. All log writes go through the shared `append_log_event()` formatter.

### 4. Graph & Search Rebuild
Scrapes the `wiki/` folder → reconstructs SQLite indexes based purely on Markdown frontmatter and wikilinks.

### 5. Agent Context Retrieval
Agent queries via CLI or MCP → Zurvan performs Hybrid Search (FTS5 + Semantic) → Expands results via Graph Neighbours → Returns unified Markdown context bundle.
- `--save` flag files the synthesis as `wiki/syntheses/YYYY-MM-DD-<slug>.md`.
- `--format table` renders results as a Markdown table; `--format marp` as a Marp slide deck. Both are stdout only; `--save` always writes canonical Markdown regardless of `--format`.
