# Environment Variables

Zurvan uses environment variables to configure LLM providers, embedding models, and MCP server safety settings.

> **Note:** Never commit your environment variables or API keys to the repository. Use a `.env` file or export them locally.

## Core Configuration

| Variable | Default | Description |
|---|---|---|
| `PYTHONPATH` | `.` | Must be set to the repository root for scripts to resolve local imports. |

## LLM Providers

Controls which provider Zurvan uses for extracting concepts, claims, and entities from raw sources.

| Variable | Options / Example | Description |
|---|---|---|
| `ZURVAN_LLM_PROVIDER` | `mock`, `openai`, `ollama`, `anthropic` | The provider engine to use. `mock` is the default — no API calls, safe for all testing. |
| `ZURVAN_LLM_MODEL` | `mock`, `gpt-4o`, `qwen2.5:7b`, `claude-sonnet-4-6` | The specific model identifier for the chosen provider. Defaults per provider: mock→`mock`, openai→`gpt-4o`, ollama→`llama3`, anthropic→`claude-sonnet-4-6`. |
| `OPENAI_API_KEY` | `sk-...` | Required if `ZURVAN_LLM_PROVIDER=openai`. |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Required if `ZURVAN_LLM_PROVIDER=anthropic`. Uses raw HTTPS — no Anthropic SDK needed. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | (Optional) Override the default Ollama server URL. |

## Embeddings

Controls the embedding model used for semantic/hybrid search (`zurvan search --hybrid`).

| Variable | Options / Example | Description |
|---|---|---|
| `ZURVAN_EMBED_PROVIDER` | `mock`, `sentence_transformers` | Defaults to `mock`. Use `sentence_transformers` for actual local semantic search. |
| `ZURVAN_EMBED_MODEL` | `all-MiniLM-L6-v2` | The model to load when using `sentence_transformers`. |

## MCP Server Security

These variables strictly control what an external agent (like Claude Code) can do when connected via the Model Context Protocol.

| Variable | Default | Description |
|---|---|---|
| `ZURVAN_MCP_READONLY` | `1` | If `1`, the MCP server blocks all tools that write to the wiki (e.g. adding claims or notes). Set to `0` **only** in trusted local repositories. |
| `ZURVAN_MCP_TRANSPORT` | `stdio` | The transport layer for MCP. Currently, only `stdio` is supported. |
| `ZURVAN_MCP_ALLOW_RAW_READ` | `0` | If `1`, allows the MCP server to read files inside the `raw/` directory. By default, agents are blocked from accessing raw sources. |

## Example `.env` or `.bashrc` config

```bash
# Local-only (no API keys required)
export PYTHONPATH=.
export ZURVAN_LLM_PROVIDER=ollama
export ZURVAN_LLM_MODEL=qwen2.5:7b
export ZURVAN_EMBED_PROVIDER=sentence_transformers
export ZURVAN_EMBED_MODEL=all-MiniLM-L6-v2

# Cloud provider (Anthropic)
export ZURVAN_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
# ZURVAN_LLM_MODEL defaults to claude-sonnet-4-6 when provider=anthropic
```
