# MCP Security 🔐

Zurvan is designed around a strict "Local-First, Safe-by-Default" philosophy. When connecting an AI agent to Zurvan via MCP, you are giving it access to project knowledge.

## The Threat Model
Agents can hallucinate, get stuck in loops, or be subjected to prompt injections from raw documents. To mitigate this, Zurvan employs the following invariants:

1. **Read-Only by Default**
   - `ZURVAN_MCP_READONLY=1` is enforced unless explicitly overridden.
   - Write tools (`zurvan_remember`, `zurvan_claim_add`) will immediately return a security block message rather than executing.

2. **Raw Folder Protection**
   - The `raw/` directory is considered the untrusted source of truth.
   - MCP cannot read files in this directory unless `ZURVAN_MCP_ALLOW_RAW_READ=1` is explicitly set.
   - *Never* set `ZURVAN_MCP_ALLOW_RAW_READ=1` if `raw/` contains external, unverified data that might contain prompt injections.

3. **No Shell Execution**
   - The MCP server only exposes semantic tools and python-backed data retrieval. It does not provide the agent with a bash shell or terminal access.

4. **Path Traversal Blocking**
   - Every read and write tool uses `scripts/safe_write.py` and `mcp_security.py` to enforce path boundaries. Absolute paths and `../` attempts are hard-blocked.

## Best Practices
- Run `python scripts/doctor_mcp.py` to ensure your environment is secure before connecting.
- Run `bash scripts/check.sh` regularly to audit the integrity of the wiki if you use Write Mode.
