---
title: "MCP write mode stays disabled by default"
type: decision
status: "accepted"
tags:
  - "mcp"
  - "security"
  - "readonly"
  - "write-mode"
  - "defaults"
---

# MCP write mode stays disabled by default

## Reason
Write mode allows agents to mutate the knowledge graph (add notes, claims, decisions). Enabling it by default would let any MCP client silently modify project memory without explicit user intent. Read-only is the safe zero-trust default; write mode must be opted into via the --write flag when running mcp_server.py.

## Status
accepted
