---
title: "Zurvan is the global Claude Code brain: digest + on-demand recall"
type: decision
status: "accepted"
tags:
  - "zurvan"
  - "claude-code"
  - "memory"
  - "hooks"
---

# Zurvan is the global Claude Code brain: digest + on-demand recall

## Reason
Raouf chose session-start ~150-token project digest + on-demand MCP over per-prompt injection (token cost) and over automatic write-back (manual only); index auto-reindexes when stale via SessionStart hook. Implemented Phase 27: project_digest + agent prime --for-project/--fix-stale, global startup|clear hook via scripts/zurvan (skips Zurvan repo), user-scope MCP fixed to read-only. Flag is --for-project (not --project, which cli.py reserves as a global workspace switch).

## Status
accepted
