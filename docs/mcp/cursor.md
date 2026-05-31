# Cursor MCP Integration

Cursor supports MCP to extend its context and tooling directly in the editor.

## 1. Using the Config Generator
```bash
python scripts/install_mcp_config.py --client cursor --readonly
```

## 2. Manual Setup
1. Open Cursor Settings.
2. Navigate to **Features** > **MCP Servers**.
3. Click **+ Add new MCP server**.
4. Configure as follows:
   - **Name**: `zurvan`
   - **Type**: `command`
   - **Command**: `python /absolute/path/to/Project-Zurvan/scripts/mcp_server.py`

*Note: Cursor's UI for environment variables might require you to set them in the shell before launching Cursor, or by wrapping the command in a bash script if the UI lacks an `env` mapping. Example wrapper script:*

```bash
#!/bin/bash
export PYTHONPATH=/absolute/path/to/Project-Zurvan
export ZURVAN_MCP_READONLY=1
export ZURVAN_MCP_TRANSPORT=stdio
export ZURVAN_MCP_ALLOW_RAW_READ=0
export ZURVAN_EMBED_PROVIDER=mock
python /absolute/path/to/Project-Zurvan/scripts/mcp_server.py
```
Point Cursor to this bash script instead of `python` directly.

## Testing
Once added, you can ping Cursor's chat: `@zurvan Search the knowledge base for "architecture decisions".`
