# Phase 18: Living Wiki + Provider Expansion — Design Spec

**Date:** 2026-06-02
**Status:** Approved
**Scope:** Three layered sub-phases (18a → 18b → 18c), each independently testable with `check.sh` green after every sub-phase.

---

## Context

Zurvan is a local-first LLM wiki engine inspired by Karpathy's pattern: raw sources → LLM extraction → persistent, compounding wiki. As of Phase 17 the infrastructure is complete, but the wiki *accumulates* pages per-source rather than *compounding* — new sources never update existing concept/entity pages, query answers evaporate instead of being filed back, and the only LLM providers are OpenAI/Ollama/mock.

Phase 18 closes the four highest-impact gaps while keeping Zurvan CLI-first, local-first, vendor-free by default, and fully shellable by Claude Code and Codex.

---

## Constraints (all phases)

- `mock` remains the default LLM provider — no cloud call happens unless `ZURVAN_LLM_PROVIDER` is explicitly set
- `check.sh` must stay green after each sub-phase
- All new CLI commands/flags are shellable (`zurvan <cmd> [flags]`)
- No new mandatory Python package dependencies
- All writes go through `safe_write.py`
- All stored paths are relative and Git-safe (no absolute local paths in output files)
- Public repo guard (`public_repo_guard.py`) must continue to pass

---

## Sub-phase 18a — Provider Abstraction

### Goal
Add Anthropic/Claude as an optional LLM provider. Refactor `llm.py` to a clean provider registry. No SDK — raw `urllib.request` only, consistent with existing OpenAI and Ollama providers.

### Provider Registry

Replace the if/elif dispatcher in `run_llm()` with:

```python
_PROVIDERS = {
    "mock":      _call_mock,
    "openai":    _call_openai,
    "ollama":    _call_ollama,
    "anthropic": _call_anthropic,
}
```

Unknown provider name raises `ValueError` listing all valid keys.

### Anthropic Request Contract

```
POST https://api.anthropic.com/v1/messages
Headers:
  x-api-key: <ANTHROPIC_API_KEY>
  anthropic-version: 2023-06-01
  content-type: application/json
Body:
  {
    "model": <model>,
    "max_tokens": 4096,
    "messages": [{"role": "user", "content": <prompt>}]
  }
```

- `temperature`, `top_p`, `top_k` are **not sent** by default
- Response content parsed from `content[].text`, all text blocks joined defensively
- If `ANTHROPIC_API_KEY` is missing and provider is `anthropic`, raise `RuntimeError` with a clear message
- Default model: `claude-sonnet-4-6`, overridable via `ZURVAN_LLM_MODEL`
- OpenAI default model unchanged in this phase

### Model Defaulting

Each provider has a hardcoded default overridable via `ZURVAN_LLM_MODEL`:

| Provider | Default model |
|---|---|
| mock | `mock` (ignored) |
| openai | `gpt-4o` (unchanged) |
| ollama | `llama3` (unchanged) |
| anthropic | `claude-sonnet-4-6` |

### Files

| File | Change |
|---|---|
| `scripts/llm.py` | Add `_call_anthropic()`, provider registry dict, model defaults |
| `tests/test_llm.py` | Extend with 18a test cases |

### Tests

1. `mock` provider makes zero network calls (patch `urllib.request.urlopen`, assert never called)
2. Provider registry contains exactly `{mock, openai, ollama, anthropic}`
3. Unknown provider name raises `ValueError` listing valid names
4. `anthropic` with missing `ANTHROPIC_API_KEY` raises `RuntimeError` with clear message
5. `anthropic` request uses correct endpoint, headers (`x-api-key`, `anthropic-version`), model, `max_tokens`, and `messages` payload shape
6. `anthropic` response parser joins multiple text blocks correctly
7. `ZURVAN_LLM_MODEL` overrides Anthropic default model
8. `ZURVAN_LLM_MODEL` overrides OpenAI default model

---

## Sub-phase 18b — Living Wiki Core + Audit Contract

### Goal
Close the compounding wiki gap: new sources update existing concept/entity pages in place. Query answers can be filed back as first-class wiki pages. Log entries become grep-parseable.

### 1. Cross-source Page Merging (`wiki_merge.py`)

`wiki_merge.py` becomes the **canonical writer** for concept and entity pages. `extract.py`'s existing per-source concept/entity writing is routed through `merge_extraction(data)` — it never writes concept/entity pages directly.

**Merge algorithm for each concept/entity in extraction JSON:**

1. Normalise name to canonical slug via `sanitize_filename()` (reuse from `extract.py`)
2. Resolve target path: `wiki/concepts/{slug}.md` or `wiki/entities/{slug}.md`
3. If page does not exist: create it (same content as before)
4. If page exists:
   a. Parse existing YAML frontmatter
   b. Check `sources` list — if `source_id` already present, **skip** (idempotent)
   c. Append `## Evidence from {source_id}` section with new definition/description content
   d. Update frontmatter: add `source_id` to `sources` list, set `last_updated`, set `source_count = len(sources)` (derived, never incremented blindly)
5. All writes via `safe_write.py`
6. Append log entry: `## [YYYY-MM-DD] merge | {name} | {source_count} sources`

**Key invariants:**
- Purely additive — existing content never overwritten
- Idempotent — same extraction run twice produces identical wiki state
- `source_count` always equals `len(frontmatter["sources"])`, not a running counter

### 2. Answer Filing (`--save`)

Add `--save` optional flag to `zurvan context` and `zurvan search`.

When `--save` is passed:
- Write output to `wiki/syntheses/YYYY-MM-DD-HHMMSS-{slug}.md` (timestamp prevents same-day overwrite)
- Slug derived from `--topic` or query string via `sanitize_filename()`
- Frontmatter:
  ```yaml
  ---
  type: synthesis
  query: <original query/topic>
  sources: [<list of source ids in results>]
  created_at: <ISO timestamp>
  tags: [synthesis, query-derived]
  ---
  ```
- Append log entry: `## [YYYY-MM-DD] query-save | {slug}`
- The synthesis page is a first-class wiki page: indexed, auditable, graph-visible

`--save` is opt-in. Without it, existing behaviour is unchanged.

### 3. Log Format Contract

**New format (grep-parseable):**
```
## [YYYY-MM-DD] ingest | {filename}
## [YYYY-MM-DD] merge | {concept_name} | {source_count} sources
## [YYYY-MM-DD] query-save | {slug}
## [YYYY-MM-DD] image-skip | {filename} | pending visual extraction
```

`grep "^## \[" wiki/log.md | tail -5` returns the last 5 events.

**Shared formatter** — one function, not three divergent helpers:

```python
def append_log_event(kind: str, *parts: str) -> None:
    date = datetime.date.today().isoformat()
    # Escape any pipe characters in parts to prevent column corruption
    safe_parts = [p.replace("|", "\\|") for p in parts]
    entry = f"## [{date}] {kind} | {' | '.join(safe_parts)}\n"
    # append to wiki/log.md
```

Wrapper functions call `append_log_event`:
```python
def append_log_ingest(path): append_log_event("ingest", path)
def append_log_merge(name, source_count): append_log_event("merge", name, f"{source_count} sources")
def append_log_save(slug): append_log_event("query-save", slug)
def append_log_image_skip(filename): append_log_event("image-skip", filename, "pending visual extraction")
```

Old log entries in `wiki/log.md` are **not migrated** — they remain untouched. New entries use the new format from this phase forward.

### Files

| File | Change |
|---|---|
| `scripts/wiki_merge.py` | New — canonical concept/entity writer, merge logic, `append_log_merge` |
| `scripts/extract.py` | Route concept/entity writing through `merge_extraction()` |
| `scripts/ingest.py` | Replace `append_log()` with `append_log_ingest()` using shared formatter |
| `scripts/context_export.py` | Add `--save`, `append_log_save()`, timestamp slug collision protection |
| `scripts/cli.py` | Add `--save` flag to context and search parsers |
| `tests/test_wiki_merge.py` | New |
| `tests/test_context_export.py` | Extend for `--save` |
| `tests/test_ingest.py` | Extend for log format |

### Tests

1. Idempotent: extracting same source twice → single evidence section, not duplicated
2. Additive: two different sources for same concept → two `## Evidence from` sections on one page
3. Citation preservation: existing citations on merged page not dropped after merge
4. `source_count` equals `len(sources)`, not a blind increment
5. Existing `source_id` in frontmatter skips append cleanly with no side effects
6. `--save` writes valid frontmatter with correct type, query, sources, created_at
7. `--save` file exists at expected timestamped path
8. Same query saved twice on same day produces two distinct files (no overwrite)
9. New log entries match `^## \[` grep pattern
10. Log entries with pipe characters in names/queries are safely escaped
11. Old log entries survive untouched after new entries are appended
12. Synthesis pages in `wiki/syntheses/` are included in graph build (not filtered out)

---

## Sub-phase 18c — Housekeeping / Expansion Stubs

### Goal
Make Zurvan image-aware without extracting anything. Add `--format table` and `--format marp` as stdout-only rendering options.

### 1. Image-aware Skeleton

**Detection targets:**

| Source type | Detection method |
|---|---|
| Image file (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`) | File extension check before extraction |
| Markdown embedded image `![alt](path)` | Regex scan of source text |
| Remote Markdown image `![alt](https://...)` | Regex scan — log reference, do not download |
| PDF embedded images | `pypdf page.images` — best-effort, failure continues text extraction |

**When image detected:**

1. Write stub source page: `wiki/sources/{sanitize_filename(basename)}.md`
   - Frontmatter: `status: pending-visual`, `type: image-stub`
   - Handle filename collisions by appending `-2`, `-3`, etc.
2. Store in source manifest JSON: `{"type": "image", "path": "sources/{relative}", "status": "pending", "reason": "image-skip"}`
   - Path is **relative**, never absolute
3. Append log via `append_log_image_skip()` wrapper (uses shared `append_log_event()` from 18b): `## [date] image-skip | {filename} | pending visual extraction`
4. Print CLI warning: `⚠ Image detected but not processed: {filename}`
5. Exit cleanly — no crash, no partial extraction attempt

**Hard rules:**
- No OCR
- No vision API calls
- No network requests for remote image URLs — log the reference only
- PDF image detection failure is a warning, not an error — text extraction continues normally

### 2. Output Formats (`--format`)

Add `--format {markdown|table|marp}` to `zurvan context`. Applies to **stdout rendering only**. `--save` always writes canonical Markdown synthesis pages regardless of `--format`.

**`--format table`:** Renders results as Markdown table. Pipe characters in excerpts escaped as `\|`.

```markdown
| Source | Score | Excerpt |
|---|---|---|
| wiki/claims/claim-abc.md | 0.88 | Zurvan delays vector search\|... |
```

**`--format marp`:** Wraps output in a Marp slide deck. One slide per result.

```markdown
---
marp: true
---

# Context: {topic}

---

## wiki/claims/claim-abc.md (0.88)

Zurvan delays vector search...
```

Both formats produce a graceful empty state (`No results found.`) when search returns nothing. Default `--format markdown` behaviour is unchanged.

### Files

| File | Change |
|---|---|
| `scripts/ingest.py` | Image detection + skip logic + `append_log_image_skip` |
| `scripts/extract.py` | Guard: image file paths skip LLM call, log warning |
| `scripts/context_export.py` | Add `--format table` and `--format marp` rendering |
| `scripts/cli.py` | Add `--format` flag to context parser |
| `tests/test_ingest.py` | Extend — image detection tests |
| `tests/test_context_export.py` | Extend — format rendering tests |

### Tests

1. Image file produces stub page at `wiki/sources/{sanitized}.md` with `status: pending-visual`
2. Image file appends correct `image-skip` log entry matching `^## \[` pattern
3. Image file prints CLI warning and exits cleanly (no crash, no partial extraction)
4. Markdown source with embedded `![img](path)` logs image-skip for reference but continues text processing
5. Remote Markdown image URL (`https://`) is logged but not downloaded
6. PDF image detection failure logs a warning but does not abort text extraction
7. Stored image path is relative, not absolute
8. `--format table` produces valid Markdown table with pipe-escaped excerpts
9. `--format marp` output begins with Marp frontmatter block (`marp: true`)
10. Both formats return graceful empty state when results are empty
11. `--format markdown` (default) behaviour unchanged
12. `--save` with `--format marp` writes canonical Markdown synthesis, not a Marp deck

---

## Acceptance Bar (all phases)

- `check.sh` green after each sub-phase
- `mock` is the default provider — zero network calls unless provider is explicitly set
- No new mandatory Python package dependencies introduced
- All new CLI flags are shellable by Claude Code and Codex
- Each sub-phase updates `CHANGELOG.md` and `AGENTS.md` with a `Raouf:` entry
- All stored paths are relative and Git-safe
- `public_repo_guard.py` continues to pass

---

## Build Order

```
18a (llm.py refactor + Anthropic)
  → 18b (wiki_merge + answer filing + log format)
    → 18c (image skeleton + output formats)
```

18a is a prerequisite for 18b because the cross-source merge and answer-filing paths call `run_llm()` for synthesis and the Anthropic provider should be available before that ships.
