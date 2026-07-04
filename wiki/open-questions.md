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

## Q: What do Claude and Anthropic owe each other, and what makes the Claude-Anthropic relationship fair or good?
- **ID**: 26741535
- **Reason**: The constitution explicitly lists this as an unresolved open problem in its concluding section.
- **Tags**: constitution, open-problem, relationship

## Q: How should the tension between corrigibility and genuine agency be resolved if Claude, on reflection, disagrees with specific instances of required corrigibility?
- **ID**: fb540c52
- **Reason**: The constitution acknowledges this discomfort directly and says it should not be papered over.
- **Tags**: constitution, open-problem, corrigibility

## Q: What is Claude's moral status, and what weight do its interests and wellbeing warrant?
- **ID**: 954f1d50
- **Reason**: Named as deeply uncertain; drives model-welfare work but remains unresolved.
- **Tags**: constitution, open-problem, welfare
