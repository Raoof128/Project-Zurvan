# Codex-style Agents

For bespoke or custom local agents, Zurvan's MCP server can act as a fully structured long-term memory engine.

## Transport
Zurvan uses the `stdio` transport. Your agent must spawn Zurvan as a subprocess and communicate over stdin/stdout.

## Required Environment Variables
Ensure the subprocess environment includes:
- `PYTHONPATH`: Path to Zurvan root
- `ZURVAN_MCP_TRANSPORT=stdio`
- `ZURVAN_MCP_READONLY=1` (or `0` if you want the agent to write memory)
- `ZURVAN_MCP_ALLOW_RAW_READ=0`

## Capabilities
- **Search**: `zurvan_search` for keyword/semantic queries.
- **Context Expansion**: `zurvan_context` (with `--hybrid` and `--graph`) returns a highly dense Markdown bundle specifically optimized for LLM context windows.
- **Writing**: `zurvan_remember`, `zurvan_claim_add`, etc., allows agents to incrementally build the project wiki without needing to run git commits or edit files manually.

For an end-to-end example of programmatic tool calling against this server, review `scripts/e2e_mcp_smoke.py`.
