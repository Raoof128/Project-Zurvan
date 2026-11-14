# Z2 Claim Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-standing Markdown claim pages with a durable claim ledger (`data/claims.sqlite`) whose evidence re-resolves, whose corrections are typed revisions, whose belief state is reconstructable at any knowledge revision, and whose content is erasable — then migrate the 548 live claims into it.

**Architecture:** Six focused modules under `scripts/` (store, evidence, ledger, pages, visibility, migrate) behind the existing knowledge-ledger operation boundary. Record time is never stored in claims.sqlite; it is derived from a new `mutations.committed_revision` column in `data/registry.sqlite`. Claim pages in `wiki/claims/` become deterministic renders; non-current claims leave every derived view.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `fcntl`, `hashlib`, `secrets`, `json`), pytest, the existing `scripts/knowledge_revision.py` / `safe_write.py` / `measurement_snapshot.py`, Rust `rust/zurvan-mcp` (bridge only).

**Spec:** `docs/superpowers/specs/2026-11-14-z2-claim-ledger-design.md` (approved 14 Nov 2026, commit `7859f11`). Executors read both; § numbers below refer to the spec.

## Global Constraints

- No model call in any Z2 code path. No confidence decimal anywhere; confidence ∈ `low`/`medium`/`high`.
- `confidence_basis` vocabulary (closed): `primary_source`, `independent_confirmation`, `direct_measurement`, `agent_inference`, `user_assertion`, `legacy_unstated`.
- Status vocabulary: `asserted`, `supported`, `contested`, `superseded`, `retracted`, `unknown`, `orphaned`, `erased`. Current = `asserted`, `supported`, `contested`, `unknown`.
- Events: `assert`, `revise`, `supersede`, `retract`, `orphan`, `restore`, `erase`. Methods: `human`, `agent`, `model_extraction`, `migration`, `verifier`.
- IDs: claims `clm_` + 16 lowercase hex, content `cnt_` + 16 hex, both from `secrets.token_hex(8)`; never a hash of content, never from a model.
- claims.sqlite: `journal_mode=delete`, `foreign_keys=ON`, `secure_delete=ON` on EVERY connection, `PRAGMA user_version = 1`. No `-wal`/`-shm` ever.
- All claim text, reasons, quotes, selectors, source paths and source fingerprints live ONLY in table `content`. `row_hash` covers non-content fields + cids + `prev_hash`, never a body.
- Every write runs inside a `kr.operation` AND holds the claims lock until that operation ends. Record time is NEVER written into claims.sqlite.
- Evidence outcomes, exact strings: `intact`, `moved`, `moved_normalised`, `ambiguous`, `changed`, `gone`. Capsule context ≤ 64 characters each side; `TextQuoteSelector` prefix/suffix ≤ 32 characters each.
- Refusal codes (exception `.code`): `CLAIM_CONFLICT`, `CLAIMS_BUSY`, `REASON_REQUIRED`, `UNKNOWN_CLAIM`, `EVIDENCE_REJECTED`, `RESTORE_REFUSED`, `ExtractionConflict` (existing class).
- Tests use a tmp store root (`root=tmp_path`, mirror `tests/test_knowledge_revision.py` fixtures); `tests/conftest.py` fails any run that writes the live `wiki/`.
- Retrieval gate: `PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6` before/after; no previously-passing Top-3 query may fail after; changed queries named by INDEX only (never quote the instrument).
- Change protocol: each commit that changes behaviour adds to the session's `Raouf:` entry in AGENTS.md + CHANGELOG.md at the end (Task 15); never `git push`.

## Review Focus

1. **A claim whose source is outside the repo and unreadable at verify time (permission, unmounted volume)** — expect `gone` with the OS reason in the report, never an exception that aborts the whole verify run. Pinned in Task 3 (`test_resolve_unreadable_source_is_gone_not_raise`).
2. **An Obsidian edit to a page of a claim that is later superseded by command** — expect the supersede to REFUSE (`CLAIM_CONFLICT`, "page has unadopted edits") rather than regenerate over the edit. Pinned in Task 6 (`test_supersede_refuses_when_page_has_unadopted_edit`).
3. **Quotes containing Markdown/YAML-hostile text (`---`, `"`, newlines, `]]`)** — page renders round-trip and frontmatter still parses with `graph_build.parse_frontmatter`. Pinned in Task 6 (`test_render_hostile_text_round_trips`).
4. **Two processes writing claims at once (CLI + MCP server)** — second waits up to 30 s then `CLAIMS_BUSY`; never interleaves. Pinned in Task 4 (`test_second_writer_blocks_then_busy`).
5. **A source file that is not valid UTF-8 (binary, Latin-1)** — `add` refuses `EVIDENCE_REJECTED`; `verify` reports `changed` (decode with `errors="strict"` fails → treated as unreadable text), never crashes. Pinned in Task 3 (`test_non_utf8_source`).

---

### Task 1: Registry records the committed revision and the scope version

**Files:**
- Modify: `scripts/knowledge_revision.py` (`ensure_schema` ~209, `commit_mutation` ~857, external-drift check ~709/~1119)
- Modify: `scripts/knowledge_scope.py` (`SCOPE_VERSION`, `is_authoritative`, `authoritative_files`)
- Test: `tests/test_z2_registry.py`

**Interfaces:**
- Produces: `mutations.committed_revision INTEGER NULL`; `knowledge_state.scope_version INTEGER NOT NULL DEFAULT 1`; `kr.belief_revisions(root=None) -> dict[str, int]` (mutation_id → `committed_revision` for COMMITTED, `resolution_revision` for RESOLVED); `kr.at_operation_end(fn: Callable[[], None]) -> None` (registers `fn` to run once after the OUTERMOST active operation commits or is abandoned, success or failure); `ks.SCOPE_VERSION == 2`; `ks.CLAIMS_DB_REL = ("data", "claims.sqlite")`.

- [ ] **Step 1: Write failing tests**
  - `test_commit_records_committed_revision`: open op, write one wiki page via `write_file_safely`, commit; row's `committed_revision == current().revision`.
  - `test_belief_revisions_maps_committed_and_resolved`: one COMMITTED, one ORPHANED then reconciled → map has both (resolved → `resolution_revision`), ABORTED absent.
  - `test_scope_version_change_is_not_drift`: store at scope 1 with stored digest; set `ks.SCOPE_VERSION=2` (monkeypatch); next op commits WITHOUT minting `EXTERNAL_OBSERVED`; `knowledge_state.scope_version == 2` after.
  - `test_claims_db_is_authoritative_when_present`: `is_authoritative(<root>/data/claims.sqlite) is True`; `authoritative_files` includes it iff it exists; `data/search.sqlite` still False.
  - `test_at_operation_end_runs_after_commit_and_after_abandon`.
- [ ] **Step 2: Run** `PYTHONPATH=. python -m pytest tests/test_z2_registry.py -q` → all FAIL.
- [ ] **Step 3: Implement.** Additive `ALTER TABLE` migrations in the style of `_migrate_attribution`; set `committed_revision = new_revision` inside the same `BEGIN IMMEDIATE` in `commit_mutation`; write `scope_version` at every commit; the external-drift comparison runs only when the stored `scope_version == ks.SCOPE_VERSION` and records nothing otherwise (spec §7.5: a scope change is not an edit). `at_operation_end` callbacks live on the `_CTX` operation object and run in `operation()`'s `finally`.
- [ ] **Step 4: Run** the new file plus `tests/test_knowledge_revision.py tests/test_knowledge_scope.py tests/test_knowledge_reconcile.py` → PASS (update any test that asserts `SCOPE_VERSION == 1` to 2 and say so in the commit).
- [ ] **Step 5: Commit** `z2: registry stores committed_revision and scope_version; claims.sqlite joins the authoritative scope`

### Task 2: Claim store — schema, content table, hash chain, lock

**Files:**
- Create: `scripts/claim_store.py`
- Test: `tests/test_z2_store.py`

**Interfaces:**
- Consumes: `ks.store_root`, `ks.CLAIMS_DB_REL`, `kr.at_operation_end`, `kr.active_operation`.
- Produces:
  - `db_path(root=None) -> Path`
  - `connect(root=None, *, readonly: bool = False) -> sqlite3.Connection` (rw creates schema; ro uses `mode=ro` URI and fails if absent)
  - `new_claim_id() -> str`, `put_content(conn, kind: str, body: str) -> str`, `get_content(conn, cid: str) -> str | None`
  - `append_revision(conn, row: dict) -> dict` — allocates `rev`, sets `prev_hash`/`row_hash`, inserts; returns the stored row
  - `chain_ok(conn) -> tuple[bool, str | None]` (problem description or None)
  - `acquire_for_operation(root=None, timeout_s: float = 30.0) -> None` — flock `data/claims.lock` once per outermost operation, release via `kr.at_operation_end`; raises `ClaimRefused("CLAIMS_BUSY")` on timeout; raises `kr.KnowledgeOperationRequired` with no active operation
  - `class ClaimRefused(Exception)` with `.code`
  - Tables per spec §7.1: `claims`, `revisions` (incl. `tags` JSON, `claim_type`, `extraction_key`, `observed_at_legacy`, `observed_at_legacy_source`, nullable `subject_id`/`predicate_id`/`object_json`), `evidence` (incl. `source_ref_cid`, `selector_cid`, `capsule_cid`, `location_hint`, `reader`), `relations`, `content`, `head`.
- [ ] **Step 1: Failing tests**
  - `test_pragmas_on_every_connection`: `journal_mode == "delete"`, `secure_delete == 1`, `foreign_keys == 1`, `user_version == 1`; no `-wal` file after writes.
  - `test_ids_are_random_and_prefixed`: 1000 ids unique, regex `^clm_[0-9a-f]{16}$` / `^cnt_[0-9a-f]{16}$`.
  - `test_chain_detects_reordered_or_edited_row`: append 3 rows; `chain_ok` True; UPDATE one non-content field → False with the offending `(claim_id, rev)` in the message.
  - `test_chain_survives_content_erasure`: blank a referenced `content.body` → `chain_ok` still True.
  - `test_row_hash_never_contains_body`: body `"SECRET-XYZ"`; no `revisions` column value contains it; `row_hash` unchanged when body edited.
  - `test_lock_requires_operation` and `test_second_writer_blocks_then_busy` (child process holds lock; parent `timeout_s=0.5` → `CLAIMS_BUSY`).
- [ ] **Step 2: Run** `pytest tests/test_z2_store.py -q` → FAIL.
- [ ] **Step 3: Implement.** `row_hash = sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True))` over every `revisions` column except `row_hash` itself; `head` holds `last_rev_hash`. Never `VACUUM` here (Task 10 owns it).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `z2: claim store (schema, erasable content, hash chain, operation-scoped lock)`

### Task 3: Evidence — selectors, fingerprints, capsules, re-resolution

**Files:**
- Create: `scripts/claim_evidence.py`
- Test: `tests/test_z2_evidence.py`

**Interfaces:**
- Consumes: `scripts.evidence_admission.admit_quote`, `scripts.ingest.extract_text` (`scripts/ingest.py:99`; reader `extract_text@1`).
- Produces:
  - `READERS = ("utf8", "extract_text@1")`, `OUTCOMES = ("intact", "moved", "moved_normalised", "ambiguous", "changed", "gone")`
  - `read_source(source_ref: str, reader: str, root) -> tuple[bytes, str]` (raw file bytes, reader text; raises `SourceUnavailable(reason)`)
  - `anchor(source_ref: str, quote: str, reader: str, root, now: str) -> dict` → `{"selector": {...}, "capsule": str}`; refuses `ClaimRefused("EVIDENCE_REJECTED")` via `admit_quote`
  - `resolve(selector: dict, source_ref: str, reader: str, root) -> tuple[str, str | None]` → (outcome, detail)
- Selector JSON (exact keys): `{"TextQuoteSelector": {"exact", "prefix", "suffix"}, "TextPositionSelector": {"start", "end"}, "state": {"sha256", "bytes", "captured_at"}}`; positions are character offsets in reader text.
- [ ] **Step 1: Failing tests** — one per outcome on tmp files: unchanged file → `intact`; text inserted before quote → `moved`; quote's internal whitespace reflowed → `moved_normalised`; quote twice with identical context → `ambiguous`; quote deleted → `changed`; file removed → `gone`; plus `test_resolve_unreadable_source_is_gone_not_raise` (chmod 000) and `test_non_utf8_source` (Latin-1 bytes: `anchor` refuses `EVIDENCE_REJECTED`, `resolve` → `changed`); `test_prefix_suffix_disambiguate_duplicate_quote` → `moved` with distinct context; `test_capsule_bounds` (≤ 64 each side); `test_empty_quote_rejected` (F2).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `intact` iff `sha256(file bytes) == state.sha256`. Normalisation = collapse runs of Unicode whitespace to one space on both sides. Unique match is decided on exact first, then exact+prefix+suffix, then normalised.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `z2: W3C-shaped evidence anchors, source fingerprints, capsules and six re-resolution outcomes`

### Task 4: Ledger writes and views (add, supersede, retract, revise, current, as-of, history)

**Files:**
- Create: `scripts/claim_ledger.py`
- Test: `tests/test_z2_ledger.py`, `tests/test_z2_belief_property.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `@dataclass ClaimState: claim_id, rev, status, text, confidence, basis: list[str], tags: list[str], valid_from, valid_until, evidence: list[dict], legacy_id, scope_project, claim_type`
  - `@dataclass BeliefState: revision: int | None, pre_ledger: bool, claims: dict[str, ClaimState]`
  - `add(text, source, evidence, *, confidence, basis=(), tags=(), valid_from=None, valid_until=None, project=None, actor, method, reader="utf8", claim_type=None, extraction_key=None, legacy_id=None, observed_at_legacy=None, observed_at_legacy_source=None, root=None) -> str`
  - `supersede(old_id, *, text, source, evidence, reason, actor, method, valid_from=None, expected_rev=None, confidence="high", basis=(), tags=None, root=None) -> str`
  - `retract(claim_id, *, reason, basis, actor, method, expected_rev=None, root=None) -> int`
  - `revise(claim_id, *, reason, actor, method, expected_rev=None, text=None, confidence=None, basis=None, tags=None, valid_from=None, valid_until=None, source=None, evidence=None, root=None) -> int`
  - `current(root=None) -> dict[str, ClaimState]`, `as_of_revision(R: int, root=None) -> BeliefState`, `as_of_date(iso: str, root=None) -> BeliefState`, `valid_at(state: dict[str, ClaimState], iso: str) -> dict[str, ClaimState]`, `history(claim_id, root=None) -> list[dict]`
  - Every write: `acquire_for_operation`; refuses `REASON_REQUIRED` for supersede/retract/revise without reason; `CLAIM_CONFLICT` when `expected_rev` ≠ latest visible rev; `UNKNOWN_CLAIM`.
- Visibility rule: a row is visible iff its `mutation_id` is in `kr.belief_revisions()` (rows written by the CURRENT active operation are visible to that operation only). `as_of_revision(R)`: per claim, the visible row with the greatest belief revision ≤ R, ties broken by `rev`. `pre_ledger=True` when R < the smallest belief revision of any claims mutation.
- [ ] **Step 1: Failing tests** (`test_z2_ledger.py`)
  - `test_add_then_current`; `test_supersede_links_and_ends_old` (old status `superseded`, `valid_until == new valid_from`, relation `supersedes`); `test_retract_keeps_history`; `test_reason_required`; `test_expected_rev_conflict_writes_nothing`; `test_aborted_operation_rows_invisible` (raise inside op after `add`; `current()` lacks it; `history` lacks it; doctor-visible via store); `test_as_of_before_ledger_is_pre_ledger`; `test_valid_at_filters_by_valid_time`; `test_mcp_cannot_record_human` is Task 8's.
- [ ] **Step 2: Failing property test** (`test_z2_belief_property.py`): seeded random sequences (≥ 200 seeds × ≤ 30 ops) of add/supersede/retract/revise, some inside operations that abort, interleaved with unrelated wiki page writes; after each committed op record `(R, current())`; at the end assert `as_of_revision(R).claims == recorded[R]` for every R. (Spec gate G3.)
- [ ] **Step 3: Run** both → FAIL.
- [ ] **Step 4: Implement.** Supersede is ONE operation: new claim `assert` + old claim `supersede` rev + relation row.
- [ ] **Step 5: Run** → PASS. **Step 6: Commit** `z2: claim ledger writes and bitemporal views; belief replay property test`

### Task 5: Non-current claims leave every derived view (§7.5a)

**Files:**
- Create: `scripts/claim_visibility.py`
- Modify: `scripts/chunk.py` (`scan_markdown_files` ~148), `scripts/graph_build.py` (file walk ~167), `scripts/agent_workflow.py` (~164), `scripts/claim_federation.py` (~38 discovery loop), `scripts/context_export.py` (claim collection)
- Test: `tests/test_z2_visibility.py`

**Interfaces:**
- Produces: `NONCURRENT = ("superseded", "retracted", "orphaned", "erased")`; `is_hidden_claim_page(rel_path: str, frontmatter: dict) -> bool` — True iff `rel_path` starts with `wiki/claims/`, `frontmatter.get("authority") == "structured"` and `frontmatter.get("status") in NONCURRENT`. Legacy pages (no `authority`) are never hidden.
- [ ] **Step 1: Failing tests:** a tmp wiki with one current and one `status: retracted, authority: structured` page → excluded from `scan_markdown_files` output, `build_graph` nodes, the recall digest, federation discovery and context export; the current page present in all five.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — each reader parses frontmatter with `graph_build.parse_frontmatter` (chunk.py reads only the first 4 KiB to decide). **Step 4: Run** → PASS plus `tests/test_chunk*.py tests/test_graph*.py tests/test_claim_federation.py tests/test_context_export*.py`.
- [ ] **Step 5: Commit** `z2: non-current claim pages leave search, graph, context, federation and the recall digest`

### Task 6: Generated pages, hand-edit conflicts, adopt and regenerate

**Files:**
- Create: `scripts/claim_pages.py`
- Modify: `scripts/claim_ledger.py` (every write ends by rendering affected pages)
- Test: `tests/test_z2_pages.py`

**Interfaces:**
- Consumes: Task 4 views; `scripts.safe_write.write_file_safely`.
- Produces: `render(state: ClaimState, history: list[dict], superseded_texts: list[str], generated_from_revision: int | None) -> str`; `page_path(state, root) -> Path` (`wiki/claims/<legacy_id>.md` if legacy else `<claim_id>.md`); `write_page(claim_id, root=None) -> Path`; `has_unadopted_edit(claim_id, root=None) -> bool`; `conflicts(root=None) -> list[str]`; `adopt(claim_id, *, actor, reason, root=None) -> int`; `regenerate(claim_id: str | None = None, root=None) -> list[Path]`.
- Frontmatter keys exactly as spec §7.5; `generated_content_hash` = sha256 of the body below the closing `---`. Body sections `# Claim`, `## Evidence` (`> quote`), `**Source**: [[...]]`, status banner line `> **STATUS: <STATUS>** — <reason>` when non-current, `## History` (rev · event · date · actor), and for a superseding claim one `Supersedes: <old text>` line per old claim.
- [ ] **Step 1: Failing tests:** `test_render_is_byte_stable` (render twice, identical); `test_render_hostile_text_round_trips` (quote with `---`, `"`, newline, `]]`; `parse_frontmatter` returns the same status/claim_id); `test_hand_edit_listed_in_conflicts`; `test_supersede_refuses_when_page_has_unadopted_edit` (`CLAIM_CONFLICT`); `test_adopt_turns_edit_into_revise` (body text change → new rev with that text; unparseable edit → refuse, page untouched); `test_regenerate_discards_edit`; `test_only_renderer_writes_wiki_claims` (AST/grep scan of `scripts/`: no other module opens a `wiki/claims` path for write — spec G9).
- [ ] **Step 2–4:** run FAIL → implement → run PASS.
- [ ] **Step 5: Commit** `z2: deterministic claim pages; hand edits become conflicts, never overwritten`

### Task 7: CLI commands

**Files:**
- Modify: `scripts/cli.py` (parser ~382, dispatch ~1034)
- Test: `tests/test_z2_cli.py`

**Interfaces:**
- Consumes: Tasks 4, 6, 9–11.
- Produces subcommands of `zurvan claim`: `add` (existing flags + `--basis`, `--valid-from`, `--valid-until`, `--project`, `--actor`, `--method {human,agent}`), `supersede`, `retract`, `revise`, `verify [--apply]`, `show [--as-of-revision|--as-of-date] [--valid-at]`, `history`, `list [--status] [--as-of-*]`, `conflicts`, `adopt`, `regenerate [--all]`, `erase --reason-class`, `doctor`, `migrate [--dry-run]`. Exit codes: 0 ok, 1 refused (prints `Error: <CODE>: <detail>` to stderr), 2 usage. `--actor` default `local:<getpass.getuser()>`; `--method` default `human`.
- [ ] **Step 1: Failing tests** (subprocess against a tmp root via `ZURVAN_ROOT`): `test_claim_add_cli_unchanged_contract` (old flags still work, exit 0, page created); `test_supersede_cli_requires_reason` (exit 1, `REASON_REQUIRED`); `test_show_as_of_revision_cli`; `test_read_commands_open_no_operation` (registry revision unchanged after `show/history/list/verify`).
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5: Commit** `z2: zurvan claim CLI (supersede, retract, revise, verify, show/history/list, conflicts, erase, doctor, migrate)`

### Task 8: Writers routed through the ledger (memory, extract, MCP Python + Rust)

**Files:**
- Modify: `scripts/memory.py:259-303` (`add_claim`), `scripts/extract.py` (`plan_claim_writes` ~38, page writes ~193+), `scripts/mcp_tools.py` (~432), `scripts/mcp_server.py` (~267), `rust/zurvan-mcp/src/bridge.rs` (`bridge_tool` ~44, `is_write` ~59, allow-list ~111/~282), `rust/zurvan-mcp/fixtures/tools_list.json`, `scripts/mcp_parity.py` (~300-340)
- Test: `tests/test_z2_writers.py`; Rust: `cargo test` in `rust/zurvan-mcp`

**Interfaces:**
- `add_claim(text, source, evidence, confidence, tags) -> bool` keeps its signature and printed messages; now calls `claim_ledger.add(..., actor=<op caller or "local:unknown">, method="human" for CLI | "agent" for MCP)`.
- Extraction: `extraction_key = sha256(source file bytes).hexdigest() + ":" + <model claim id>`; identical key+text → no-op; same key, different text → `ExtractionConflict` unless `--supersede`. Model ids never become filenames.
- New MCP tools (Python and Rust, identical names/args): `zurvan_claim_supersede(old_claim_id, text, source, evidence, reason, valid_from=None, expected_rev=None)`, `zurvan_claim_retract(claim_id, reason, basis, expected_rev=None)` (writes; read-only mode refuses), `zurvan_claim_show(claim_id, as_of_revision=None, as_of_date=None, valid_at=None)`, `zurvan_claim_history(claim_id)` (reads). MCP writes always `method="agent"`, `actor="mcp:<client name or 'unknown'>"`. Rust allow-list check applies to `claim_supersede.source` with the existing refusal text.
- [ ] **Step 1: Failing tests:** `test_add_claim_writes_ledger_and_page`; `test_f1_two_sources_same_model_id_two_claims` (spec G9); `test_reextraction_identical_is_noop`; `test_reextraction_changed_text_conflicts`; `test_mcp_cannot_record_human`; `test_mcp_supersede_respects_readonly`; parity cases for the four tools in `scripts/mcp_parity.py`; Rust unit tests for `bridge_tool`/`is_write` mappings and the supersede allow-list.
- [ ] **Step 2–4:** FAIL → implement → PASS (`PYTHONPATH=. python -m pytest tests/test_z2_writers.py tests/test_extract*.py tests/test_mcp*.py -q`; `cd rust/zurvan-mcp && cargo test`; `PYTHONPATH=. python scripts/mcp_parity.py` all match).
- [ ] **Step 5: Commit** `z2: every claim writer goes through the ledger; MCP supersede/retract/show/history in Python and Rust`

### Task 9: Reconcile and doctor understand claims.sqlite

**Files:**
- Modify: `scripts/knowledge_reconcile.py` (`_parses` ~58)
- Modify: `scripts/claim_store.py` (add `doctor`)
- Test: `tests/test_z2_reconcile.py`

**Interfaces:**
- Produces: `claim_store.reconcile_ok(path: Path, planned_pages: list[str]) -> bool` (`PRAGMA integrity_check == "ok"`, `chain_ok`, every planned claim page exists); `claim_store.doctor(root=None) -> dict` with keys `chain_ok`, `integrity`, `invisible_rows` (list of `(claim_id, rev, mutation_state)`), `dangling_cids`, `page_mismatches`.
- [ ] **Step 1: Failing tests:** `test_reconcile_does_not_crash_on_sqlite` (today: `UnicodeDecodeError`); `test_crash_mid_claim_write_then_reconcile` (kill child after the claims txn commits, before op commit → store SUSPECT; `reconcile` → RESOLVED; rows join belief at `resolution_revision`, not before — spec G8); `test_doctor_reports_invisible_rows`.
- [ ] **Step 2–4:** FAIL → implement (`_parses` dispatches on `path.name == "claims.sqlite"`) → PASS with `tests/test_knowledge_reconcile.py`.
- [ ] **Step 5: Commit** `z2: reconcile validates claims.sqlite instead of crashing; claim doctor`

### Task 10: Erasure with a reported residue

**Files:**
- Modify: `scripts/claim_ledger.py` (add `erase`)
- Test: `tests/test_z2_erase.py`

**Interfaces:**
- Produces: `ERASE_REASONS = ("client_request", "personal_data", "legal", "owner_request")`; `erase(claim_id, *, reason_class, actor, root=None, legacy_dir: Path | None = None) -> dict` → `{"rev", "residue": [{"kind", "where", "action"}]}`; residue kinds `search_index`, `snapshot`, `legacy_copy`, `extraction_json`, `time_machine`.
- [ ] **Step 1: Failing tests:** `test_erase_blanks_every_body_and_keeps_chain`; `test_erased_text_absent_from_db_bytes` (raw file bytes after `VACUUM` contain no fragment of text, quote, capsule, source path; spec G7); `test_erase_stubs_legacy_copy_and_records_it` (the legacy file becomes a stub, its manifest gains an `erased` entry); `test_erase_lists_snapshots_holding_copies`; `test_erased_claim_hidden_from_views`.
- [ ] **Step 2–4:** FAIL → implement (`UPDATE content SET body=NULL…`, `VACUUM` after the txn, page stub via renderer) → PASS.
- [ ] **Step 5: Commit** `z2: claim erase keeps the chain, zeroes content, reports every residual copy`

### Task 11: Verify with --apply, and the restore rule

**Files:**
- Modify: `scripts/claim_ledger.py` (add `verify`)
- Test: `tests/test_z2_verify.py`

**Interfaces:**
- Produces: `verify(claim_ids: list[str] | None = None, *, apply: bool = False, root=None) -> dict` → `{"counts": {outcome: n}, "integrity_rate": float, "claims": {claim_id: [outcome…]}, "written": [(claim_id, rev, event)]}`; printed disclaimer `evidence integrity measures anchoring, not truth`.
- Rules: `--apply` writes `orphan` when ALL evidence is `gone`/`changed`, `restore` when an orphaned claim's evidence is `intact`/`moved*` again, and only when the outcome class differs from the last verifier revision; `ambiguous` never orphans; no other path writes `restore` (`RESTORE_REFUSED` from `revise` with no new evidence on an orphaned claim).
- [ ] **Step 1: Failing tests:** `test_verify_read_only_by_default`; `test_apply_orphans_then_restores`; `test_apply_is_idempotent` (second run writes nothing); `test_restore_by_assertion_refused`; `test_orphaned_claim_hidden_from_views`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5: Commit** `z2: claim verify reports decay; --apply orphans and restores only on evidence`

### Task 12: Snapshot adapter for claims (replaces the fail-closed guard)

**Files:**
- Modify: `scripts/measurement_snapshot.py` (`CLAIMS_STATUS` ~471, `_COMPONENTS` ~453, `assert_no_unadapted_claims_ledger` ~481, the per-component currency check used by `capture(required=...)`)
- Test: `tests/test_z2_snapshot.py`

**Interfaces:**
- Produces: `capture_claims(root, into) -> Component` (SQLite online-backup API into `into/claims.sqlite`, `assert_self_contained`, `integrity_check`; writes `into/claims-mutations.json` = `{mutation_id: {"state", "committed_revision", "resolution_revision"}}` for every mutation referenced by a revision row, read in the same capture); `claims` registered in `_COMPONENTS`; `--require claims` works; `snapshot_belief(snapshot_dir) -> dict[str, ClaimState]` in `claim_ledger` (current view computed from the snapshot's copy + sidecar only).
- [ ] **Step 1: Failing tests:** `test_require_claims_refuses_while_claim_mutation_active` (spec G11); `test_snapshot_belief_matches_as_of_revision` (capture at R, keep writing claims, `snapshot_belief(snap) == as_of_revision(R).claims` — spec G4); `test_snapshot_ignores_aborted_rows_via_sidecar`; existing `tests/test_snapshot*.py` updated where they asserted `UNSUPPORTED_BY_DESIGN`.
- [ ] **Step 2–4:** FAIL → implement → PASS (`pytest tests/test_z2_snapshot.py tests/test_snapshot*.py tests/test_measurement_snapshot*.py -q`).
- [ ] **Step 5: Commit** `z2: claims snapshot component with mutation sidecar; belief replay agrees with snapshots`

### Task 13: Migration (dry run, freeze, import, orphan, regenerate, report)

**Files:**
- Create: `scripts/claim_migrate.py`
- Test: `tests/test_z2_migrate.py`

**Interfaces:**
- Produces: `@dataclass LegacyClaim: legacy_id, text, source, quote, confidence, tags, claim_type, location, raw`; `parse_legacy_page(name: str, text: str) -> LegacyClaim | None` (both shapes: `memory.add_claim` pages and `extract.py` pages with `claim_type`/multiple `>` quotes/`Location:`); `migrate(root=None, *, dry_run: bool = False, legacy_dir: Path | None = None, report_path: Path | None = None, now: str | None = None) -> dict`. Defaults: `legacy_dir = ~/.zurvan/z2/claims-legacy-<YYYYMMDD>/`, `report_path = ~/.zurvan/z2/migration-report-<YYYYMMDD>.json`.
- Report keys: `counts` (parsed, unparseable, per outcome), `orphaned` (legacy_id, outcome, source), `unparseable` (legacy_id), `ambiguous`, `legacy_manifest` (name → sha256), `provenance_mismatches` (must be `[]`), `observed_at_sources` (`log.md` | `mtime`).
- Behaviour per spec §7.8: one operation holding the claims lock; freeze FIRST (0444 files, 0555 dir) and verify the manifest; each parsed page → `add(..., method="migration", actor="migration", basis=["legacy_unstated"], legacy_id=...)`; then `verify` outcomes; `gone`/`changed` → `orphan` rev with the outcome as reason; unparseable → status `unknown` with the raw page as text; pages regenerated (legacy filenames kept); after commit, rebuild search index and graph (`zurvan index search`, `zurvan graph rebuild` functions) and include their exit status in the report. `--dry-run` writes nothing (no freeze, no db) and prints the would-be counts.
- [ ] **Step 1: Failing tests** on a tmp wiki seeded with: two normal pages, one extraction-shape page, one page whose source is missing, one whose quote was edited out, one with an empty quote and a hand-appended `**Update…**` paragraph, one unparseable page, and one decision page:
  - `test_dry_run_writes_nothing`; `test_freeze_first_and_read_only`; `test_provenance_retained_for_every_parsed_page` (text/source/quote equal; spec G2); `test_broken_evidence_orphaned_with_reason`; `test_unparseable_imported_as_unknown_not_dropped`; `test_authored_markdown_untouched` (decision page bytes identical; spec G1); `test_rerun_is_refused_or_noop` (second `migrate` → no duplicates); `test_pre_ledger_as_of_before_migration`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5: Commit** `z2: claim migration with frozen legacy copy, orphaning and a provenance report`

### Task 14: Closure matrix

**Files:**
- Create: `tests/test_z2_closure.py`

**Interfaces:** mirrors `tests/test_z1b_closure.py`: `MATRIX = {"G1": [...], …, "G12": [...]}` mapping each spec §8 gate to the named tests above; `LIVE_PROOF = {"G2": "live migration report: provenance_mismatches == []", "G5": "live verify reproduces the §2.1 numbers", "G10": "eval_search before/after recorded"}`; tests `test_matrix_covers_g1_to_g12_exactly`, `test_every_listed_test_exists`, `test_no_duplicates`.
- [ ] **Step 1:** write the matrix + its three tests. **Step 2:** run → PASS (every referenced test exists by now; a FAIL names the missing one). **Step 3: Commit** `z2: closure matrix maps G1-G12 to their tests`

### Task 15: Live migration, retrieval gate, Rust install, logs (operator-visible; run ONLY after Raouf says go)

**Files:**
- Modify: AGENTS.md, CHANGELOG.md (one `Raouf:` entry; rotate per protocol), `docs/agents-history.md`
- Outputs: `~/.zurvan/z2/claims-legacy-<date>/`, `~/.zurvan/z2/migration-report-<date>.json`, `~/.zurvan/reports/z2-eval-before-<date>.json`, `…-after-<date>.json`

- [ ] **Step 1:** Full suite green: `PYTHONPATH=. python -m pytest -q` → 0 failed (live seal tests may SKIP without a sudo ticket); `bash scripts/check.sh`; `public_repo_guard.py`; `git diff --check`.
- [ ] **Step 2:** Retrieval BEFORE: `PYTHONPATH=. python scripts/eval_search.py --hybrid --min-top3 0.6 --json > ~/.zurvan/reports/z2-eval-before-<date>.json` (no index rebuild in flight; `check.sh` not running).
- [ ] **Step 3:** `scripts/zurvan claim migrate --dry-run` → counts must match spec §2.1 (546 parsed, 2 unparseable, 31 broken ± claims added since 14 Nov; any other difference is investigated before Step 4).
- [ ] **Step 4:** `scripts/zurvan claim migrate` → report `provenance_mismatches == []`; `claim doctor` all ok; `knowledge status` CLEAN.
- [ ] **Step 5:** Retrieval AFTER (same command, `-after-`); PASS iff no previously-passing Top-3 query fails; name every changed query by index. A FAIL is reported as FAIL (spec G10), not re-run until it passes.
- [ ] **Step 6:** `cd rust/zurvan-mcp && cargo build --release`, install to `~/.zurvan/bin/zurvan-mcp` per the existing install procedure in AGENTS.md; `scripts/zurvan` MCP smoke (`scripts/e2e_mcp_smoke.py`).
- [ ] **Step 7:** `scripts/zurvan claim verify` → record counts and the integrity rate (spec G5 live proof).
- [ ] **Step 8:** Log entry (scope, numbers, gate table G1–G12 with evidence, IOUs Z2-1…5 restated), Zurvan write-back (`decision add --project zurvan`), commit `z2: live migration of 548 claims; gates G1-G12`.
