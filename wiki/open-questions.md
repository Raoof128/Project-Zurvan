# Open Questions

Unresolved questions extracted from sources.

## Q: Should MCP write mode stay disabled by default?
- **ID**: 3a794ad5
- **Status**: resolved — **Yes, read-only stays the default.** Write mode mutates the knowledge graph; any MCP client could silently modify project memory without explicit user intent. Opt-in via `--write` flag. See `wiki/decisions/mcp-write-mode-stays-disabled-by-default.md`.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 0d3f7d7e
- **ID**: 8bc14085
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 396e5628
- **ID**: f6725193
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly

## Q: Should MCP write mode stay disabled by default? 6143ee53
- **ID**: c5a58827
- **Reason**: Write mode can change project memory, so read-only should remain the safe default.
- **Tags**: mcp, security, readonly
