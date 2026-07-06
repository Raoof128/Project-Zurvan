# Zurvan as a global brain for Claude Code — design

**Date:** 2026-07-06 (Australia/Sydney)
**Status:** approved by Raouf (this doc records the approved design)
**Goal:** make Zurvan the persistent memory for every Claude Code session on
this machine — easy, fast, token-frugal, with hybrid search used well.

## Decisions (fixed by Raouf)

- **Scope:** global — all projects (Nexus Archive, SimurghForge, …), not just
  the Zurvan repo.
- **Recall:** session-start digest + on-demand MCP search. No per-prompt
  injection (token budget). Digest is titles/pointers only, ~150 tokens,
  once per session; Claude opens pages only when relevant
  (`zurvan_read_page`).
- **Write-back:** manual only. No Stop/SessionEnd/PreCompact hooks. The
  existing CLI commands (`decision add`, `claim add`, `question add`,
  `agent postedit`) remain the write path, prompted by CLAUDE.md/skill
  instructions only.
- **Freshness:** auto-reindex when stale, inside the SessionStart hook
  (incremental rebuild; reuses unchanged embeddings).

## Components

### 1. `zurvan agent prime` — two new flags

File: `scripts/agent_workflow.py` (+ arg wiring in `scripts/cli.py`).

- `--fix-stale` — if `_index_staleness()` reports STALE, run the incremental
  search reindex before printing, then report the fresh state. Failure to
  reindex must degrade to the existing STALE warning, never crash.
- `--project <name>` — replace the full orientation card with a lean digest
  for that project (~150-token cap):
  - decisions / claims / open questions whose frontmatter tags or titles
    match the project name (case-insensitive; pure keyword/frontmatter scan,
    **no embedding model load** — must run in <1 s);
  - one counts line (`N decisions, M claims, K open questions match`);
  - one closing pointer line (how to search deeper: `zurvan_search` MCP /
    `zurvan search --hybrid`);
  - if nothing matches: single line
    `No Zurvan knowledge for this project yet.` plus the pointer line.
- Flags compose: the global hook uses both.

### 2. Global SessionStart hook (`~/.claude/settings.json`, user scope)

- Command:
  `<ZURVAN_ROOT>/scripts/zurvan agent prime --fix-stale --project "$(basename "$CLAUDE_PROJECT_DIR")"`
  wrapped with a timeout and `|| true` so a Zurvan failure never blocks a
  session in another repo.
- Skips when `$CLAUDE_PROJECT_DIR` is the Zurvan repo itself — the repo-local
  hook already runs the full prime there (no double-fire). The repo-local
  hook gains `--fix-stale` too.

### 3. MCP server registered user-scope, read-only

- Register the existing `scripts/mcp_server.py` at user scope (reusing
  `install_mcp_config.py` logic / `claude mcp add --scope user`) so
  `zurvan_search`, `zurvan_read_page`, `zurvan_context`, graph tools exist in
  every session for on-demand recall.
- `ZURVAN_MCP_READONLY=1` stays; writes go through the CLI (manual
  write-back decision above).

### 4. Global `~/.claude/CLAUDE.md` section (~10 lines)

- Recall discipline: before re-deriving a past decision, project fact, or
  anything the digest hints at, `zurvan_search "<topic>"` (hybrid) first;
  open only the pages that matter.
- Write-back (manual): the four CLI commands, with one-line examples.

### 5. MCP-diet recommendation (advisory only, no auto-removal)

- Deliver a short list of currently-registered MCP servers that overlap
  Zurvan (e.g. mempalace) or look unused, as a recommendation for Raouf to
  trim — each active server costs a subprocess + schema tokens every
  session. Best-practice guidance is ~5–6 active servers.

## Explicitly out of scope

- Per-prompt auto-injection (token cost — rejected).
- PreCompact / Stop / SessionEnd hooks (rejected — write-back stays manual).
- Any retrieval-ranking or indexing-format change (would trigger the frozen
  eval re-run rule; not needed for this work).
- Background watcher/daemon (Approach C — revisit only if hook startup feels
  slow).
- R3 provenance events (not built; never claimed).

## Error handling

- Hook path: any failure prints nothing fatal and exits 0 (`|| true`);
  sessions in other repos must never be blocked by Zurvan.
- `--fix-stale` reindex failure: fall back to printing the STALE warning.
- `--project` with no matches: the two-line empty digest (never an error).

## Testing

- pytest: `--fix-stale` (stale → reindexes → fresh; reindex-failure fallback),
  `--project` (tag match, title match, case-insensitivity, empty digest,
  150-token/line cap), flag composition.
- Manual smoke: run the hook command from a non-Zurvan directory; open a
  Claude Code session in another repo and confirm digest + MCP tools.
- Quality gate: `pytest` 0 failed, `public_repo_guard.py`, `git diff --check`;
  frozen provenance golds untouched (re-verify unchanged: 86% recall).

## External references (validated the design)

Karpathy LLM-wiki pattern and pointer-not-content loading (~20× token saving)
are the working patterns in the field; SessionStart injection is the standard
mechanism. See CHANGELOG entry for links.
