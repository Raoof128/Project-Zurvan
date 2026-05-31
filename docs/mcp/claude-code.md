# Claude Code MCP Integration

Claude Code supports the Model Context Protocol directly via its `claude.json` configuration file.

## 1. Quick Setup
Run the Zurvan configuration generator to easily create the setup block:
```bash
python scripts/install_mcp_config.py --client claude-code --readonly
```

## 2. Manual Setup (Read-Only)
Add the following to your project's `.claude.json` or the global config:

```json
{
  "mcpServers": {
    "zurvan": {
      "command": "python",
      "args": ["scripts/mcp_server.py"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/Project-Zurvan",
        "ZURVAN_MCP_READONLY": "1",
        "ZURVAN_MCP_TRANSPORT": "stdio",
        "ZURVAN_MCP_ALLOW_RAW_READ": "0",
        "ZURVAN_EMBED_PROVIDER": "mock"
      }
    }
  }
}
```

## 3. Write Mode (Advanced)
If you want Claude Code to act as an autonomous researcher that can add claims, decisions, and questions to your wiki:

1. Read [security.md](security.md) to understand the risks.
2. Change `"ZURVAN_MCP_READONLY"` to `"0"` in your configuration.
3. Test it by asking Claude Code: `Can you add a decision to Zurvan about our choice of framework?`

## Verifying
After restarting Claude Code, you can type `/mcp` or `mcp servers` (depending on the version) to ensure the `zurvan` server is connected and the tools are exposed.
