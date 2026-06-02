# Open Questions

Unresolved questions extracted from sources.

## Q: Should MCP write mode stay disabled by default?
- **ID**: 3a794ad5
- **Status**: resolved — **Yes, read-only stays the default.** Write mode mutates the knowledge graph; any MCP client could silently modify project memory without explicit user intent. Opt-in via `--write` flag. See `wiki/decisions/mcp-write-mode-stays-disabled-by-default.md`.
- **Tags**: mcp, security, readonly
