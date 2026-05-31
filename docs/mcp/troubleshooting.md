# MCP Troubleshooting

If your agent is having trouble connecting to Zurvan or executing tools, check the following:

## 1. Run the Doctor
```bash
python scripts/doctor_mcp.py
```
This script will immediately flag missing dependencies, invalid DB states, or insecure environment variables.

## 2. Verify with E2E Smoke Test
If the doctor says everything is fine but the agent still fails, run the E2E simulation:
```bash
python scripts/e2e_mcp_smoke.py
```
If this passes, the issue is on the client agent's side (e.g. Claude Code/Cursor configuration).

## 3. Common Errors

### "ModuleNotFoundError: No module named 'scripts'"
Your MCP client configuration is missing the `PYTHONPATH` environment variable. Ensure `PYTHONPATH` is set to the absolute path of the Zurvan root directory.

### "Write access denied"
You are trying to use write tools (like `zurvan_remember`), but the server is running in read-only mode. See the Setup Guides for how to enable Write Mode.

### "Unique constraint failed" during search
This happens if multiple markdown chunks have the exact same content hash. Run `python scripts/audit_wiki.py` to identify duplicates and clean up the wiki.

### "Unsupported transport"
Ensure `ZURVAN_MCP_TRANSPORT=stdio`. HTTP transport is not supported in Phase 6.5.
