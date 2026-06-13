# Codex MCP Integration

Codex (the OpenAI CLI) supports MCP servers natively via `codex mcp` and the
`[mcp_servers.<name>]` table in `~/.codex/config.toml`.

## 1. Using the Config Generator
```bash
python scripts/install_mcp_config.py --client codex --readonly
```
This prints both an `Option A` ready-to-run `codex mcp add ...` command and an
`Option B` TOML block for `~/.codex/config.toml`. The generator uses
`sys.executable` (an absolute interpreter path) so the launch does not depend on
Codex's `PATH` resolving to an interpreter that has the `mcp` package installed.

## 2. CLI Setup (recommended)
```bash
codex mcp add zurvan \
  --env PYTHONPATH=/absolute/path/to/Project-Zurvan \
  --env ZURVAN_MCP_READONLY=1 \
  --env ZURVAN_MCP_TRANSPORT=stdio \
  --env ZURVAN_MCP_ALLOW_RAW_READ=0 \
  --env ZURVAN_EMBED_PROVIDER=mock \
  -- /absolute/path/to/python3 /absolute/path/to/Project-Zurvan/scripts/mcp_server.py
```
Use `ZURVAN_MCP_READONLY=0` only if you want the agent to be able to write to the
knowledge base (decisions, claims, notes, questions).

## 3. Manual Setup
Add to `~/.codex/config.toml`:
```toml
[mcp_servers.zurvan]
command = "/absolute/path/to/python3"
args = ["/absolute/path/to/Project-Zurvan/scripts/mcp_server.py"]
env = { PYTHONPATH = "/absolute/path/to/Project-Zurvan", ZURVAN_MCP_READONLY = "1", ZURVAN_MCP_TRANSPORT = "stdio", ZURVAN_MCP_ALLOW_RAW_READ = "0", ZURVAN_EMBED_PROVIDER = "mock" }
```

## Verify
```bash
codex mcp get zurvan     # shows the configured server
codex mcp list           # lists all servers
codex mcp remove zurvan  # to uninstall
```

Then in a Codex session the `zurvan_*` tools (search, context, graph, eval) and
`zurvan://` resources become available.
