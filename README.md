# Local-first LLM Wiki Knowledge Engine

*Inspired by [Andrej Karpathy's gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).*

A local, Markdown-based wiki knowledge engine that uses LLMs (to be plugged in) to ingest raw sources, extract claims, entities, and concepts, and generate a linked Markdown wiki.

## Goals
- Convert raw sources into a maintained Markdown wiki.
- Preserve raw sources as immutable source-of-truth.
- Generate source summaries, concepts, entities, claims, contradictions, and open questions.
- Maintain `wiki/index.md` and `wiki/log.md`.
- Git-friendly Markdown output.
- Include citation metadata for claims.

## Structure
- `raw/`: Immutable source documents (papers, notes, transcripts, etc.)
- `wiki/`: Generated and editable wiki (Markdown files)
- `scripts/`: Tooling to ingest and query the wiki
- `data/`: SQLite database for indexing and tracking
- `tests/`: Basic tests

## Setup
```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt # For testing/development
```

## Documentation
- [Workflows and Script Plans](docs/workflows_and_plans.md)
- [Extraction Test Matrix](docs/extraction_test_matrix.md)
- [Agent Rules](AGENTS.md)


## Usage
### Ingestion
```bash
python scripts/ingest.py raw/notes/example.md
```

### Extraction
```bash
python scripts/extract.py --source raw/notes/example.md
```

### Auditing
```bash
python scripts/audit_wiki.py
```

### Reliability Testing
```bash
python scripts/run_reliability_gauntlet.py raw/notes/example.txt raw/papers/example.pdf
```

## Agent CLI Memory Interface
Zurvan provides a CLI for agents (like Claude Code or Codex) to store and retrieve project knowledge securely without internal LLM calls.
```bash
# Add a decision
python scripts/cli.py decision add --title "Delay vector search" --reason "Reliability first" --status accepted --tags roadmap

# Add a claim with verified evidence
python scripts/cli.py claim add --text "Claim text" --source "docs/file.md" --evidence "Exact quote" --confidence high

# Export context bundle for agents
python scripts/cli.py context --topic "vector search" --limit 5
```

### 2. Search & Export Context
You can search through extracted claims, concepts, and notes.

```bash
# Basic keyword search
zurvan search "local-first architecture"

# Hybrid search (Keyword + semantic embeddings)
zurvan search "local-first architecture" --hybrid

# Export a context bundle for agents
zurvan context --topic "project roadmap" --limit 10

# Export context using hybrid search
zurvan context --topic "project roadmap" --hybrid --limit 10
```

### 3. Rebuild Search Index
Whenever new Markdown files are added or chunk logic changes, rebuild the search index:
```bash
zurvan index rebuild
zurvan index search
```

### Local Embeddings
By default, Zurvan uses deterministic mock embeddings for testing. You can enable local semantic embeddings if `sentence-transformers` is installed:
```bash
export ZURVAN_EMBED_PROVIDER=sentence_transformers
export ZURVAN_EMBED_MODEL=all-MiniLM-L6-v2
zurvan index search
```

### 4. Retrieval Evaluation
You can evaluate how well Zurvan finds expected paths for known queries. 
Add gold queries to `eval/search_gold.jsonl`.

```bash
# Validate gold dataset files exist
zurvan eval validate-gold

# Evaluate search
zurvan eval search --hybrid

# Require minimum Top-3 accuracy to pass CI/CD
zurvan eval search --hybrid --min-top3 0.8
```

### 5. Knowledge Graph Lite & Context Expansion
Zurvan connects nodes and claims using local graph capabilities without requiring Neo4j or external graph databases.
It can expand semantic search results with nearby graph nodes (Phase 5.5).

```bash
# Rebuild the local graph
zurvan graph rebuild

# Export expanded graph-assisted context bundle
zurvan context --topic "vector search roadmap" --hybrid --graph --limit 10

# Expand neighbours of a specific node
zurvan graph expand wiki/decisions/delay-vector-search.md --depth 2
```

# View graph stats
zurvan graph stats

# Find neighbours of a node
zurvan graph neighbours wiki/decisions/delay-vector-search.md

# Export graph
zurvan graph export --format markdown
zurvan graph export --format dot
```

### 6. Local MCP Server for Agent Integration (Phase 6)
Zurvan can act as a local Model Context Protocol (MCP) server over `stdio`, providing tools, resources, and prompts to Claude Code, Cursor, and other compatible agents. By default, it operates in a strict **read-only mode**.

#### Running the server
You can start the server manually for testing:
```bash
export ZURVAN_MCP_READONLY=1
export ZURVAN_MCP_TRANSPORT=stdio
export ZURVAN_MCP_ALLOW_RAW_READ=0
python scripts/mcp_server.py
```

#### Claude Code Config Example
Add this to your `claude.json` or MCP configuration to let Claude use Zurvan as its memory:

**Read-only mode (Default & Recommended)**
```json
{
  "mcpServers": {
    "zurvan": {
      "command": "python",
      "args": ["scripts/mcp_server.py"],
      "env": {
        "PYTHONPATH": ".",
        "ZURVAN_MCP_READONLY": "1",
        "ZURVAN_MCP_TRANSPORT": "stdio",
        "ZURVAN_MCP_ALLOW_RAW_READ": "0",
        "ZURVAN_EMBED_PROVIDER": "mock"
      }
    }
  }
}
```

**Write mode (Trusted Repositories Only)**
```json
{
  "mcpServers": {
    "zurvan": {
      "command": "python",
      "args": ["scripts/mcp_server.py"],
      "env": {
        "PYTHONPATH": ".",
        "ZURVAN_MCP_READONLY": "0"
      }
    }
  }
}
```

#### Security Notes
- **No shell execution:** MCP cannot run arbitrary shell commands.
- **Path boundaries:** Absolute paths and directory traversal (`../`) are blocked.
- **Raw protection:** Reading `raw/` files is blocked unless `ZURVAN_MCP_ALLOW_RAW_READ=1`.
- **Read-only default:** Write tools (`zurvan_remember`, `zurvan_decision_add`, etc.) are disabled by default.

### Querying
## LLM Providers
Zurvan supports multiple extraction providers via environment variables.

### Mock (Testing)
```bash
export ZURVAN_LLM_PROVIDER=mock
```

### Local Ollama
```bash
export ZURVAN_LLM_PROVIDER=ollama
export ZURVAN_LLM_MODEL=qwen2.5:7b
# export OLLAMA_BASE_URL=http://localhost:11434 (default)
```

### OpenAI Compatible
```bash
export ZURVAN_LLM_PROVIDER=openai
export ZURVAN_LLM_MODEL=gpt-4.5-preview
export OPENAI_API_KEY=your_key_here
```
