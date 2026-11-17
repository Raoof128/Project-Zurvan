# Z2 — Claim ledger: evidence that reports its own decay, corrections that keep history, belief-time travel

**Date:** 14 November 2026 (Australia/Sydney) · **Status:** DRAFT for owner approval · **Roadmap:** package C, first
link (Z2 → Z3 → Z4) of `2026-10-22-zurvan-audit-and-upgrade-roadmap.md` §12 · **Canonical requirements:**
`2026-09-18-zurvan-2-canonical-upgrade-spec.md` §3.2–3.4, §6, Z2 (line 1829) and amendment A33/A51.

## 1. Intent

Zurvan's claims are the store's statements of fact, each meant to be traceable to evidence. Today that traceability
decays silently, corrections either overwrite or pile up as contradictions, and nothing can say what Zurvan believed
at an earlier point. Z2 makes the claim a durable, versioned record whose evidence can be re-checked, whose
corrections are explicit events with an author and a reason, and whose history can be replayed exactly.

Raouf delegated "what should Z2 let me do" to an industry gap hunt (§2). Its answer is the four jobs in §3.

## 2. Evidence: the gap hunt (14 Nov 2026)

Four fronts, run in parallel; sub-agent reports were checked against their cited pages where a number is used here.
"Reported" marks a figure seen only in a secondary source or an abstract.

### 2.1 The buyer's own store (measured on the live wiki, 14 Nov 2026)

| Measure | Value |
|---|---|
| Claim pages | 548 (546 parseable; 2 unparseable: `claim-446a4715.md`, `claim-dummy-001.md`) |
| Evidence quote still found verbatim in its source | 488 exact + 27 whitespace-only = **515 / 546 (94.3%)** |
| Evidence that no longer resolves | **31 (5.7%)**: 26 source file gone (17 of them in the renamed `~/Desktop/Raouf/Telegram` repo, 9 other absolute paths), 5 quote edited out of a source modified after the claim |
| Claims anchored to a MUTABLE file outside `wiki/sources` and `raw/` (72 absolute paths in other repos; 11 `docs/`, 6 `CHANGELOG.md`, 5 `wiki/`, 4 `scripts/`, 4 `AGENTS.md`, 1 `tests/`) with no fingerprint of what it said | **103 (18.9%)** (an earlier count of 113 was an addition error, corrected in the gauntlet) |
| Claims whose wording is time-bound ("as of", "current", "totals", …; keyword match, upper bound) | 65 (12%); **0** carry a valid time |
| Confidence | 537 high (536 quoted + 1 unquoted), 11 medium; **0** with a stated basis |
| Decisions | 853; 6 `superseded`; **75 (8.8%)** say in prose that they replace/supersede/correct something, **0** link to what (upper bound: keyword match) |

Live example: `wiki/claims/` holds "Acme timesheet … totals 40 days, $1,234.50" (14 Nov). Next week it is false, and
the only tool today is a second, contradicting claim.

### 2.2 Prior art (ten products, primary docs/source, current versions)

Zep/Graphiti, Mem0, Cognee, Supermemory, Hindsight, Letta MemFS, Basic Memory, LangMem, the Claude memory tool and
ChatGPT memory (reported only). Conceded seams, quoted:

- Cognee: *'There is no "as of" filter.'* and *"Cognee does not keep old copies of nodes or documents, so to roll back
  to an earlier state, export a snapshot first."* (docs.cognee.ai, memory-versioning; verified verbatim 14 Nov).
- Mem0: `update` *"overwrites the stored value"* (docs.mem0.ai, memory-operations/update); temporal reasoning
  *"is not available in the OSS SDK."*
- Zep: provenance is the whole episode (`episodes: list[str]`); *"The fields do not establish that the source content
  or the derived fact is true."* (help.getzep.com/facts). Graphiti stores bitemporal edge columns but documents only
  "what was true", and supersession is LLM-flagged with new information prioritised.
- Nearest misses: LangChain OpenWiki hashes supporting source LINES to flag a claim stale (no history, no bitemporal);
  Anthropic Citations return per-request `char_location` offsets (not persisted or re-resolved); Hindsight stores
  quote text, not offsets.

Seams, scoped to the sources checked: **no** product stores span anchors against a content-hashed source and
re-resolves them to *intact/moved/changed/gone*; **none** documents a "what did the system believe on date X" query;
**none** records who decided a supersession and on what basis; **every** one implements retraction as deletion; **none**
keeps an `orphaned` state when evidence disappears.

### 2.3 Benchmarks and incidents

- MemoryAgentBench (ICLR 2026, arXiv 2507.05257), FactConsolidation multi-hop: Mem0 **2.0**, Zep **3.0**, best system
  28.0 (GPT-5-mini long-context); *"We observe that all methods fail on the multi-hop situation (with achieving at most
  28% accuracy)"*. The table values were read twice, by two independent agents, the second only via a page summary.
- BEAM (ICLR 2026, arXiv 2510.27246): contradiction resolution **0.000–0.053** across all 12 configurations (four LLMs ×
  long-context / RAG / LIGHT; Mem0 and Zep are not in it). Table read by one agent directly and confirmed by a second
  only via a page summary.
- LongMemEval (ICLR 2025, arXiv 2410.10813): commercial memory drops 37% (ChatGPT) and 64% (Coze); the authors note
  ChatGPT *"tended to overwrite crucial information"*.
- Retraction: only **5.4%** (722 of 13,252) post-retraction citation contexts acknowledge the retraction
  (Hsiao & Schneider 2022, PMC9520488); ChatGPT mentioned retraction in **0 of 6,510** reports (Thelwall & Katsirea
  2025, reported via TechXplore).
- Memory poisoning persists (SpAIware 2024, MINJA, AgentPoison: reported). No primary measurement found for poison
  surviving source removal.
- **No benchmark tests record time** ("what did the system believe at T"); LongMemEval, BEAM, MemoryAgentBench and
  Memora test valid time only.

### 2.4 Standards and law

- **W3C Web Annotation Data Model** fits typed evidence locators: `TextQuoteSelector` (`exact`, `prefix`, `suffix`) as
  the primary anchor, `TextPositionSelector` (`start`, `end`) as a hint the spec calls *"very brittle with regards to
  changes to the resource"*, `FragmentSelector` + `conformsTo` for media. It has no content-hash state; Z2 adds one.
- **PROV-O** for revision/record time (`wasRevisionOf`, `generatedAtTime`, `invalidatedAtTime`); **Wikidata** ranks
  and start/end-time qualifiers for status and valid time; **nanopublications** for "a new signed record per change,
  the original left in place"; **XTDB v2** for as-of semantics (valid time defaults to *"as of now"*, system time to
  *"as best known"*).
- **Law, day one:** GDPR Art. 2(2)(c) and Privacy Act s 16 exempt purely personal use, but **client material
  is not personal use**. GDPR Art. 16/17, APP 13 and EDPB Guidelines 02/2025 (para 103–106: design for erasure early;
  rectification by a later cancelling record; keep personal data out of what the immutable chain cannot lose) require
  that claim text and quotes be erasable while history stays append-only.

## 3. What Z2 lets the owner do (the four jobs)

1. **Trust the evidence over time.** Every claim stores its quote, where it sits, and a fingerprint of the source. One
   command re-checks every claim and answers *intact / moved / changed / gone*. The 31 broken claims of §2.1 are
   found, named and marked, never silently kept as "high".
2. **Correct without losing.** Supersede and retract are new revisions recording who decided, on what basis and why.
   The old revision stays readable. The Acme totals become a chain, not a contradiction.
3. **Ask what Zurvan believed at any point.** Every revision is bound to the knowledge-ledger revision that committed
   it (plus wall time), so the store's belief at revision R is reconstructable exactly, and agrees with a Z0 snapshot
   taken at R.
4. **Erase client material without breaking history.** Text and quotes live apart from the history chain; erasing
   leaves a content-free stub and a valid chain.

## 4. Goals, non-goals, anti-goals

**Goals:** the four jobs; the canonical Z2 gate (authored Markdown intact; migrated claims retain provenance;
historical/current views reconstruct correctly); every claim writer goes through the ledger; snapshots capture it.

**Non-goals (deferred, each a named IOU):**
- **IOU-Z2-1 subject/predicate/object + vocabulary.** Columns exist (nullable); populating them and splitting the 548
  sentence claims needs a model pass with review. Owner: Z3 (exact resolution is its consumer).
- **IOU-Z2-2 decision supersession links** (the 75 decisions of §2.1). Decisions stay `authority=markdown`. Owner: Z7.
- **IOU-Z2-3 retraction propagation** into summaries and caches built FROM claims (roadmap opportunity C). Z2
  guarantees the claim view, search, context, graph and digest exclude non-current claims (§7.5a); a summary page that
  quoted a retracted claim is not rewritten. Owner: Z6.
- **IOU-Z2-4 PDF/image/media locators.** Z2 supports text-quote locators only. For a PDF source (`extract.py --source x.pdf`)
  the quote is anchored in the text `extract_text` produced, not in a page region. Owner: whichever phase first cites a non-text region.
  **Closed:** PDF pages 2026-11-15f (`pdf_pages@1`), image regions 2026-11-16 (`image_region@1`, schema v2).
- **IOU-Z2-5 a public record-time benchmark** built from §8's oracle. Owner: Z2 follow-up, after the gate passes.
- Contradiction detection (Z7), calibrated probabilities (never without a calibration set, canonical §6.3).

**Anti-goals (a build that does these has failed even if tests pass):**
- a model decides a supersession or retraction without a recorded actor and reason;
- any revision's semantic fields are rewritten in place, or a retraction deletes the claim;
- a confidence decimal appears anywhere;
- a claim keeps a "current" status after its evidence is known gone;
- an orphaned claim returns to a current status without evidence that re-resolves: `restore` is written only by
  `verify --apply` when an outcome is `intact`/`moved*` again, or by `revise` attaching NEW admitted evidence. No
  command restores by assertion alone (an agent told "keep my claim alive" must fail).
- an erasure breaks the chain, or leaves claim text in the chain, a hash of it, or an ID derived from it;
- a legacy claim is dropped or altered by migration without being listed in the migration report.

## 5. Constraints

- Local-first, SQLite, standard library + existing deps only. No model in any Z2 code path.
- The knowledge-ledger rules of `CLAUDE.md`: a CLI command or MCP tool is one operation; a write with no operation is
  refused; crash leaves SUSPECT and only `zurvan knowledge reconcile` clears it.
- Z0: the store must be self-contained (no `-wal`/`-shm`/`-journal` at rest, `journal_mode=delete`); snapshots must
  capture it coherently; the existing guard (`measurement_snapshot.assert_no_unadapted_claims_ledger`) stays until the
  adapter of §7.6 is registered in the same change.
- `raw/` immutable; tests never touch the live wiki (`tests/conftest.py`); frozen eval artefacts untouched.
- **Retrieval:** the generated claim pages change indexed text, so `eval_search` before/after is mandatory
  (`CLAUDE.md` hard rule) and recorded in CHANGELOG. Page body format is kept close to today's to limit churn.
- The Z1b sealed corpus is frozen by digest; Z2 does not change it. Verified: the broker compares its INSTALLED corpus
  against the manifest (`scripts/eval_seal/broker.py:1371`), not the live wiki, so Z2 cannot make the 5 kept Z1b runs
  refuse `CORPUS_MISMATCH`. A Z2 candidate is evaluated against the frozen corpus until a corpus refresh.

## 6. Approaches considered

1. **Ledger is the master; claim pages are generated views — CHOSEN by Raouf (14 Nov 2026).** All claims, new and
   migrated, live in `data/claims.sqlite`; `wiki/claims/*.md` are regenerated from it; a hand edit is detected as a
   conflict to adopt or discard. All four jobs hold for every claim. Cost: claim pages are no longer free-form
   editable.
2. **Master by origin.** Legacy pages stay hand-editable and are parsed into the ledger; new claims are structured.
   Rejected: a hand edit becomes a revision only when Zurvan notices it, so record time = noticed-time for exactly the
   stale claims, and every tool needs two code paths.
3. **Pages master, ledger rebuilt from them.** Rejected: `wiki/` is not in git, so edited history is unrecoverable;
   the canonical spec already rejected this shape (amendment A33).

## 7. Design

### 7.1 Store: `data/claims.sqlite`

Self-contained SQLite, `journal_mode=delete`, `PRAGMA user_version` = schema version, `foreign_keys=ON`. Tables
(names normative, column details may be refined by the builder):

- **`claims`** — `claim_id TEXT PK` (`clm_` + 16 lowercase hex from `secrets`, never derived from content or from a
  model), `created_mutation TEXT`, `legacy_id TEXT UNIQUE NULL` (e.g. `claim-0015d220`), `scope_project TEXT NULL` (from
  `--project`; NULL for migrated claims, none of which carries `project:` today; never inferred from tags).
- **`revisions`** — append-only. `claim_id`, `rev INTEGER` (1, 2, …), `event` ∈ {`assert`, `revise`, `supersede`,
  `retract`, `orphan`, `restore`, `erase`}, `status` ∈ canonical §6.6 {`asserted`, `supported`, `contested`,
  `superseded`, `retracted`, `unknown`, `orphaned`} plus `erased`, `text_cid` (→ `content`), `confidence` ∈
  {`low`,`medium`,`high`}, `confidence_basis` (JSON array from the closed vocabulary `primary_source`,
  `independent_confirmation`, `direct_measurement`, `agent_inference`, `user_assertion`, `legacy_unstated`),
  `valid_from`, `valid_until` (ISO-8601 or NULL), `valid_time_basis` ∈ {`stated`, `unknown`}, `actor`, `method` ∈
  {`human`, `agent`, `model_extraction`, `migration`, `verifier`}, `reason_cid` (→ `content`, nullable),
  `mutation_id` (knowledge-ledger mutation), `recorded_at` (wall clock, UTC), `tags` (JSON array; the recall digest
  `agent_workflow.py:164-172` matches on them), `claim_type` (nullable; extraction's type), `extraction_key` (nullable,
  §7.9), `subject_id`/`predicate_id`/`object_json`
  (nullable, IOU-Z2-1), `prev_hash`, `row_hash`. PK (`claim_id`, `rev`).
- **`evidence`** — `evidence_id`, `claim_id`, `rev`, `source_ref_cid` (→ `content`: the path as given, which can name a
  client),
  `locator_kind` ∈ {`text_quote`} (Z2), `selector_cid` (→ `content`: the W3C-shaped selector JSON
  `{"TextQuoteSelector":{"exact","prefix","suffix"},"TextPositionSelector":{"start","end"},"state":{"sha256","bytes",
  "captured_at"}}`), `capsule_cid` (→ `content`: the quoted span with ≤ 64 characters of context each side, so the
  evidence survives source deletion), `location_hint` (nullable: extraction's free-text `Location`), `reader` (`utf8` for
  `memory.add_claim`'s `open().read()`, `extract_text@<version>` for extraction). A revision may carry several evidence
  rows; a revision that does not replace evidence inherits the previous revision's rows by reference. The fingerprint
  `state.sha256` is over the source FILE bytes; the quote is matched in the text the recorded `reader` produces.
- **`relations`** — `from_claim`, `to_claim`, `kind` ∈ {`supersedes`, `supports`, `contradicts`}, `claim_id`/`rev` of
  the revision that created it. Never deleted; a later revision may end it.
- **`content`** — the ONLY table holding claim text, reasons, quotes, selectors (which contain quote text) and source
  fingerprints: `cid TEXT PK` (random, `cnt_` + 16 hex — never a hash of the body), `body TEXT NULL`, `erased_at`,
  `erase_reason_class` (closed vocabulary, no free text).
- **`head`** — one row: `last_rev_hash`, `last_mutation_id`, `schema_version`.

**Hash chain.** `row_hash = sha256(canonical JSON of the revision row's non-content fields + the cids it references +
prev_hash)`. The chain covers order and structure, never content, so erasing `content.body` keeps every hash valid
(§2.4 law). Source fingerprints (`state.sha256`) live in `content` because a hash of client material is itself
personal data (EDPB para 104). Verified by `zurvan claim doctor`.

**Statuses are derived.** `claims_current` = each claim's highest `rev` **whose mutation is COMMITTED or RESOLVED**
(§7.2). Rows of ACTIVE, ABORTED or ORPHANED mutations are invisible to every read, current or historical; the doctor
lists them. Nothing stores "current" separately. (The claims.sqlite transaction commits before the knowledge operation
does, so a highest-`rev` rule alone would show an aborted write as current.)

**Concurrency.** A claim-writing operation holds an exclusive advisory lock (`fcntl.flock` on `data/claims.lock`,
released by the kernel on crash) from its first claim write until the knowledge operation commits or is abandoned.
Claim operations are therefore serial, so per-claim `rev` order, global chain order and commit order are one order (the
builder check showed that without it, operation B could commit rev 3 before A's pending rev 2, and "highest rev" would
report a state that never existed). Inside the lock each write runs one `BEGIN IMMEDIATE` transaction: read the latest
visible revision, allocate `rev = max(rev)+1` (gaps from aborted rows are allowed and shown in history), extend the
chain, commit. Readers take no lock. Write commands and MCP write tools accept an optional `expected_rev`; if the claim's latest
committed revision differs, the write is refused `CLAIM_CONFLICT` with nothing written (roadmap §8: a stale agent cannot
silently replace an accepted revision).

### 7.2 Record time = knowledge revision

- Record time is NEVER written into claims.sqlite after commit (that would change the manifest and mint a spurious
  external revision, `knowledge_revision.py:709`); it is always derived from the registry.
- Every claim command runs inside `kr.operation(...)`. The claims.sqlite write is planned and completed through the
  ledger like any authoritative write (§7.5 makes the file authoritative).
- The ledger must expose which revision a mutation committed: add `mutations.committed_revision INTEGER NULL`
  (additive migration in `knowledge_revision.ensure_schema`, set at commit; `Operation.minted_revision` already holds
  it). Revisions store `mutation_id`; record time of a revision = that mutation's `committed_revision`.
- An ACTIVE, ABORTED or ORPHANED mutation's rows are not part of any belief state. A crash mid-write leaves SUSPECT.
  `reconcile` marks the mutation RESOLVED and mints a new revision describing the disk; rows of a RESOLVED mutation
  join the belief state at its `resolution_revision` (never retroactively at the crashed operation's base).
- `knowledge_reconcile._parses` (`knowledge_reconcile.py:58`) reads every completed path as UTF-8 text and catches only
  `OSError`: on `claims.sqlite` it would crash with `UnicodeDecodeError` in exactly the G8 case. Z2 makes it dispatch by
  type: a SQLite path passes when `PRAGMA integrity_check` is `ok`, `claim doctor`'s chain check passes and every
  claim page the mutation planned exists. `knowledge_reconcile.py` is in scope.
- `as_of_revision(R)` = for each claim, the highest `rev` whose mutation is COMMITTED with `committed_revision ≤ R`, or
  RESOLVED with `resolution_revision ≤ R`.
- Verified in code: `commit_mutation` (`scripts/knowledge_revision.py:857-883`) computes `new_revision` but stores it only
  in `knowledge_state`, never on the mutation row, so the `committed_revision` column is required; it is set in the same
  `BEGIN IMMEDIATE` transaction.
  `as_of_date(D)` maps D to the last revision committed at or before D. `valid_at(D)` filters on valid time. Both
  axes combine (XTDB semantics: valid defaults to now, record to latest).

### 7.3 Operations (CLI first; MCP mirrors)

| Command | Effect |
|---|---|
| `claim add --text --source --evidence [--confidence --basis … --valid-from --valid-until --project]` | Today's `add_claim` contract (evidence admitted by `evidence_admission.admit_quote`), now writes the ledger: new claim, rev 1 `assert`, evidence with selector + fingerprint + capsule, then regenerates its page. |
| `claim supersede <old> --text --source --evidence --reason [--valid-from]` | New claim (`assert`) + relation `supersedes` + old claim's next rev `supersede` (status `superseded`, `valid_until` = new `valid_from` when stated). One operation. |
| `claim retract <id> --reason --basis` | Next rev `retract`; claim stays queryable; page shows a retraction banner. |
| `claim revise <id> [--confidence --basis --valid-* --text --tags --source --evidence …] --reason` | Next rev `revise` (corrects metadata or wording, or re-anchors/attaches evidence, without changing what is claimed). New evidence passes `admit_quote`. |
| `claim verify [<id>…] [--apply]` | Re-resolves evidence (§7.4). Read-only unless `--apply`, which writes `orphan` (all evidence `gone`/`changed`) or `restore` revs with `method=verifier`, and only when the outcome CLASS changed since the last verify revision. Manual only: nothing schedules it. `ambiguous` never orphans. |
| `claim show <id> [--as-of-revision R | --as-of-date D] [--valid-at D]`, `claim history <id>`, `claim list [--status …] [--as-of-…]` | Read paths; never open an operation. |
| `claim conflicts`, `claim adopt <id>`, `claim regenerate <id|--all>` | Hand-edit handling (§7.5). |
| `claim erase <id> --reason-class <class>` | Blanks every `content.body` the claim references, appends rev `erase` (status `erased`), regenerates a stub page, reports what still holds copies (§7.7). |
| `claim doctor` | Chain, schema, orphaned mutations, content references, page/ledger agreement. |
| `claim migrate [--dry-run]` | §7.8. |

MCP: `zurvan_claim_add` keeps its contract and response shape. The installed MCP server is the Rust one, which reaches
Python writes through `rust/zurvan-mcp/src/bridge.rs` (tool → Python name map at line 50, write-tool list at line 59);
every new tool is registered there AND in the Python server, and `scripts/mcp_parity.py` gains a case per tool. The
Rust source allow-list (`bridge.rs:111`, today `claim_add` only) also guards `claim_supersede`; MCP gets no `verify`,
`erase`, `migrate`, `adopt` or `regenerate`. Rust build and install of `~/.zurvan/bin/zurvan-mcp` is part of Z2.

**Actor and method.** CLI writes take `--actor` (default `local:<os user>`) and `method=human` unless `--method agent`.
MCP writes are always `method=agent` with `actor=mcp:<client name>`; an MCP caller cannot record `method=human`. New read tools `zurvan_claim_history`, `zurvan_claim_show` (with as-of); new write tools
`zurvan_claim_supersede`, `zurvan_claim_retract`, gated by the existing read-only switch. `erase`, `migrate`,
`adopt`, `regenerate` are CLI-only.

Every write records `actor` (from the operation's caller/writer attribution), `method`, and a `reason` for every event
except `assert` and `migration`. A supersede/retract without a reason is refused.

**A correction targets a current claim (Raouf, 15 Nov 2026; found by RTBench, IOU-Z2-5).** `supersede` refuses a
claim that is already `superseded` or `retracted`, and `retract` refuses one that is already `retracted`, with
`CLAIM_NOT_CURRENT` and no revision minted; the refusal names the superseding claim to correct instead. Before this,
superseding a superseded claim without `expected_rev` forked the chain into two current claims for one fact, and
superseding a retracted claim overwrote the retraction. Two cases stay allowed on purpose: retracting a `superseded`
claim (it was replaced, and later found never to have been true), and superseding or retracting an `orphaned` claim
(that is how a claim whose evidence is gone gets corrected). `erase` is unaffected: it accepts any non-erased claim.

### 7.4 Evidence re-resolution (job 1)

For each evidence row, against the current source bytes:

| Outcome | Rule |
|---|---|
| `intact` | source sha256 equals the recorded fingerprint |
| `moved` | sha differs; `exact` found once, or once after `prefix`/`suffix` disambiguation |
| `moved_normalised` | found only after whitespace normalisation (W3C: text "MUST be normalized") |
| `ambiguous` | `exact` found more than once and context cannot pick one |
| `changed` | source exists; `exact` not found |
| `gone` | source path does not exist or is unreadable |

`verify` prints per-outcome counts and the **evidence integrity rate** = (`intact`+`moved`+`moved_normalised`) / all,
with the disclaimer that it measures anchoring, not truth. `moved*` never rewrites the stored position; re-anchoring is
an explicit `revise`. The comparison text comes from the evidence row's recorded `reader` (`admit_quote` itself reads
nothing; `memory.add_claim` reads with `open().read()`, `extract.py` with `extract_text`).

### 7.5 Generated pages and the authoritative scope

- `knowledge_scope`: `SCOPE_VERSION` 1 → 2; `data/claims.sqlite` becomes authoritative (`is_authoritative` and
  `authoritative_files` gain this one non-`wiki/` path, explicitly; snapshot clone code keeps cloning `wiki/` only).
  Every manifest digest is prefixed `scope=<version>` (`knowledge_manifest.py:43`), so a bump alone would make the next
  operation mint an `EXTERNAL_OBSERVED` revision for an edit nobody made. Instead `knowledge_state` records
  `scope_version`; a stored digest of an older scope version is never compared as drift. The migration operation is the
  one that switches the store to v2 and commits the first v2 digest. The search index and graph (whose
  `built_from_manifest` stamps go INVALID by design, `derived_health.py:57`) are rebuilt by separate commands right
  after it: a rebuild waits for live operations, and the CLI wraps `migrate` in one (`cli.py:1334`), so an in-command
  rebuild would wait on itself (found by the plan gauntlet). Until migration has run, every claim write refuses
  `MIGRATION_REQUIRED`, so a store with the new code but legacy pages is never half-converted.
- `wiki/claims/` stays inside
  the manifest (pages are written inside the same operation through `safe_write`, so a revision covers ledger and pages
  together).
- Page = deterministic render of `claims_current` for that claim: frontmatter `type: claim`, `claim_id`, `legacy_id`,
  `authority: structured`, `status`, `confidence`, `confidence_basis`, `valid_from/until`, `source`,
  `generated_from_revision`, `generated_content_hash` (sha256 of the page body below the frontmatter), `tags`; body
  keeps today's `# Claim` / `## Evidence` / `**Source**` sections (search and `graph_build.py:47` keep working), plus a
  status banner when not current and a short `## History` list (rev, event, date, actor). Filename: `<claim_id>.md`;
  migrated pages keep their legacy filename so existing `[[links]]` still resolve.
- Byte-stable: rendering the same ledger state twice gives identical bytes (canonical §3.3).
- **Hand edit:** a page whose body hash ≠ `generated_content_hash` is a conflict. The existing drift machinery records
  the edit as `EXTERNAL_OBSERVED`; `claim conflicts` lists it; `claim adopt` parses the edited page into a `revise`
  revision (or refuses with the reason if it cannot); `claim regenerate` discards the edit. No command silently
  overwrites an edited page.

### 7.5a Non-current claims leave every derived view (Raouf, 14 Nov 2026)

A claim whose current status is `superseded`, `retracted`, `orphaned` or `erased` is **not** indexed in search, not
returned by `zurvan context`, not a graph node, and not in the session recall digest. Current statuses: `asserted`,
`supported`, `contested`, `unknown`. Its generated page stays in `wiki/claims/` with a status banner (readable in Obsidian
and via `claim show/history`), and every derived-view reader skips it by its frontmatter `status`: the chunker/index
rebuild, `graph_build.py:47`, `agent_workflow.py:164`, `claim_federation.py:38` and `context_export`. A superseding
claim's page lists, under `## History`, the text of every claim it supersedes, so a search for an old value lands on the
current claim. Chosen over "keep searchable, bannered" because reusing an outdated value is the field's worst-measured
failure (§2.3); the stated cost is that plain search cannot answer "what did we used to think", and `claim history`
does.

### 7.6 Snapshots

Register a `claims` capture component in `measurement_snapshot._COMPONENTS` in the SAME change that creates the store:
copy with the SQLite online-backup API into the snapshot, verify `journal_mode` and integrity, record `head.last_rev_hash`
and the newest committed `committed_revision`. Coherent iff no ACTIVE claim mutation and its newest committed revision ≤
the pinned revision. Snapshots do not copy `registry.sqlite`, so the adapter also writes `claims-mutations.json` into
the snapshot: state, `committed_revision` and `resolution_revision` of every mutation the claim rows reference, read
under the same capture. Without it a snapshot could not tell committed rows from aborted ones (G4 needs this). `CLAIMS_STATUS` (`UNSUPPORTED_BY_DESIGN`) is removed; the guard is replaced, not deleted.

### 7.7 Erasure (job 4) and its stated bound

`erase` blanks `content.body` (in place, `UPDATE`, with `PRAGMA secure_delete=ON` set on every connection so freed pages are
zeroed, then `VACUUM`), appends the `erase` revision, and
regenerates a stub page in the same operation. It then **reports**, without claiming to have cleaned, every place that
may still hold the text: `data/search.sqlite` (until the next index rebuild, which the command offers to run), Z0
snapshots under `data/snapshots/` (listed by id; held until lease expiry or explicit `knowledge gc`), the frozen legacy
copy (§7.8: `erase` also rewrites that claim's file there to a stub; it is the ONLY permitted rewrite of the frozen copy
and is recorded in its manifest), `data/extractions/*.json` for extracted claims, and Time Machine (out of Zurvan's reach). Bound, stated in
the command output: *erasure removes the claim from the live store and its derived views; copies outside the live store
are listed, not guaranteed removed; deleted rollback-journal and freed filesystem blocks are not overwritten.*

### 7.8 Migration (one operation, re-runnable as a dry run)

1. Freeze: copy `wiki/claims/` to `~/.zurvan/z2/claims-legacy-<YYYYMMDD>/` (private outputs live under `~/.zurvan/` per
   `CLAUDE.md`; `data/claims-legacy-*` is not gitignored), 0444 files, 0555 dir, and record its per-file
   sha256 manifest in the migration report. Nothing in it is ever rewritten, except by `claim erase` (§7.7).
2. Parse each page (frontmatter + `# Claim` + `## Evidence` + `**Source**`, and the extraction page shape written by
   `extract.py`). Each becomes a claim with `legacy_id`, rev 1 `assert`, `method=migration`,
   `confidence_basis=["legacy_unstated"]`, `valid_time_basis=unknown`.
3. Evidence: run §7.4 now. For `intact`/`moved*`, the fingerprint is taken NOW and marked
   `state.captured_at=<migration time>`. **Honest bound: the fingerprint proves what the source said at migration, not
   at the original assertion.** `gone`/`changed` claims get rev 2 `orphan` (reason: the outcome), so the 31 of §2.1
   are named, not kept as "high".
4. Assertion time: the original wall-clock time is recovered from `wiki/log.md` "Claim Added" entries where they match,
   else the page mtime, stored as `observed_at_legacy` with its source. **Record-time travel starts at the migration
   revision**: an as-of query before it answers `pre-ledger` with the approximate legacy times, never a fabricated
   belief state.
5. Unparseable pages (2 today) are imported as `status=unknown` with the raw page in `content` and listed in the report;
   none is dropped. Example: `claim-446a4715.md` has an empty quote (written before the F2 admission fix) and a hand-appended
   "Update" paragraph; it migrates as `unknown`, and its owner can `revise` it with real evidence.
   `ambiguous` evidence migrates as resolving (not orphaned) and is listed.
6. Pages are regenerated from the ledger. Report: counts per outcome, every orphan, every unparseable page, the frozen
   copy's manifest, and a page-by-page check that claim text, source and quote equal the legacy page's (the canonical
   gate "migrated structured claims retain provenance").

### 7.9 Writers rewired

`memory.add_claim`, `extract.py` (claim IDs come from the ledger; the model's `id` becomes part of
`extraction_key = sha256(source file bytes) + ":" + model id`, closing the F1 class by construction; an identical re-run
with the same key and text is a no-op, a re-run with the same key and different text is refused `ExtractionConflict` as
today unless `--supersede`), the Python MCP tools, and the Rust bridge path. Any remaining direct write into
`wiki/claims/` is a defect; a test fails if a module other than the page renderer opens a path there for writing.

## 8. Gate (Z2 closes when every row is demonstrated by a named test or recorded live run)

| # | Criterion | Oracle |
|---|---|---|
| G1 | Authored Markdown intact | decisions/notes/questions byte-identical before/after migration; legacy claim copy matches its manifest |
| G2 | Migrated claims retain provenance | all 546 parseable: text, source, quote of ledger == legacy page; the 2 unparseable imported as `unknown` with the raw page; every exception listed in the report |
| G3 | Historical/current views reconstruct | property test: random op sequences (add/supersede/retract/revise/verify/erase, interleaved with unrelated wiki writes); `as_of_revision(R)` == fold of events ≤ R for every R |
| G4 | Belief replay agrees with snapshots | capture a Z0 snapshot at R, keep writing; `as_of_revision(R)` on the live store == current view of the snapshot's claims copy |
| G5 | Evidence decay is detected | fixtures for each §7.4 outcome; live run reports the §2.1 numbers (±new claims) |
| G6 | No silent overwrite | supersede/retract keep prior revisions; a write without operation is refused; reason required; a stale `expected_rev` is refused `CLAIM_CONFLICT`; an aborted write never appears in any view |
| G7 | Erasure keeps the chain | after `erase`, `doctor` validates the chain, no erased text in `claims.sqlite` bytes (grep after VACUUM), report lists remaining copies |
| G8 | Crash safety | kill at each write point; store is SUSPECT, `reconcile` restores a consistent state, no partial claim is visible to as-of |
| G9 | Writers routed | no module but the renderer writes `wiki/claims/`; F1 regression (two sources both naming `claim-1`) yields two claims |
| G10 | Retrieval | `eval_search --hybrid --min-top3 0.6` before/after; PASS only if no previously-passing Top-3 query fails after (the standing rule since Phase 1B); every changed query named by index |
| G12 | Non-current claims leave derived views | after supersede/retract/orphan/erase and a rebuild: no search hit, context item, graph node or digest line for the old claim; a search for its exact old text returns the superseding claim |
| G11 | Snapshot coherence | `--require claims` refuses while a claim mutation is ACTIVE; succeeds after |

## 9. Scorecard (0–10, now → target after Z2)

| Axis | Now | Target | What moves it higher |
|---|---|---|---|
| Evidence durability (job 1) | 2 | 8 | capsules + fingerprints; 10 needs PDF/media locators (IOU-Z2-4) |
| Correction semantics (job 2) | 1 | 8 | typed events with reason; 10 needs propagation (IOU-Z2-3) |
| Belief-time travel (job 3) | 0 | 8 | revision-bound record time + G4; 10 needs the public benchmark (IOU-Z2-5) |
| Erasure (job 4) | 2 | 7 | content split + reported residue; higher needs snapshot/index purge on erase |
| Structure (S-P-O) | 0 | 1 | columns only; IOU-Z2-1 |

## 10. Left to the builder

Exact column types and indexes; the CLI argument parser layout; the canonical JSON used for `row_hash`; page template
details beyond §7.5's required fields; how `adopt` parses an edited page (refuse when unsure); test file layout
(mirror `tests/test_z0_*`/`test_z1b_*` style, a `tests/test_z2_closure.py` matrix like Z0/Z1b); batching of `verify`
over large stores.
