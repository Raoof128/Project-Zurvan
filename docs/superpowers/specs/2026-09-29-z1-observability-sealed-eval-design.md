# Z1 — Retrieval observability and a sealed evaluation programme

**Date:** 2026-09-29
**Status:** Z1a CLOSED 2026-10-01 (gate 8: 1417 passed / 0 failed on merged `main`). Built (branch `z1a-observability`, plan
`docs/superpowers/plans/2026-09-29-z1a-observability.md`). Gates 1-7 are
demonstrated, live gates on revision 302. Gate 6 closes on the guard function only: the promotion
workflow that must call it is Z1b. Z1b APPROVED after revision 2, which applies
Raouf's review of 2026-09-29. Z1b is not built.
**Implements:** canonical spec §26 Z1, §9 (evaluation), §14 (observability).
**Builds on:** Z0 (CLOSED 2026-09-28) — coherent snapshots, leases, `stamp_verdict`.
**Amends:** splits Z1 into **Z1a** and **Z1b**, and replaces "sealed" as a
prompt-level promise with an OS-enforced boundary (amendments file §12).

---

## 0. Why two halves

Z1 in §26 lists six builds. They split cleanly by what they need from a human:

| | Z1a — observability | Z1b — sealed evaluation |
|---|---|---|
| Builds | trace privacy modes, stage timings, trace permissions + retention, rank-mover reports, retirement of the burned instrument | DEV/HOLDOUT/RESERVE sets, strata coverage, sealed authoring, the eval broker, promotion workflow, burn protocol, leak canaries |
| Needs Raouf | nothing | a `sudo` setup step, a CLI login as the eval identity, starting sealed authoring |
| Order | first | second; Z1a's `protected_eval` trace mode is a prerequisite |

Each half gets its own implementation plan. Z1 closes only when both gates in
§9 hold.

---

## 1. Understanding (what Raouf asked for, what is assumed)

**Said by Raouf (2026-09-29):**
- Z1a proceeds independently.
- The Z1b holdout is AI-authored — Raouf does not hand-write 150+ queries.
- The AI author is an *evaluation authority*, not an ordinary subagent: a clean
  context, no development history or memory, and a physical seal. "A Claude
  Code subagent by itself is not a sufficient security boundary."
- A second, independent reviewer; ~320 candidates → 160 HOLDOUT + ≥80 RESERVE.
- A narrow broker is the only door; development sees aggregates only.
- Burn/retire protocol, anti-hill-climbing, signed manifest, leak canaries,
  tests that *demonstrate* enforcement.
- Author and reviewer from different model families "when practical" —
  **then narrowed in review: Anthropic-only.** Model-family diversity is not
  worth widening private-corpus egress (§6.4).
- Review (revision 2): a third OS identity for candidate code; the broker
  root-owned and digest-pinned; backup separation for the key; the aggregate
  covert channel stated; stable evaluation targets; two consistency fixes.

**Confirmed in review:**
- A1. The corpus under evaluation is the private `wiki/`, pinned to a coherent
  Z0 snapshot. **Canonical targets are stable page/source locators**; chunk
  ids are only snapshot-bound aliases (§4.3), so a later chunker change
  (Z3/Z4) does not silently invalidate the instrument.
- A2. DEV (~200, visible) is authored by an ordinary agent run and **frozen
  before sealed authoring**. Its digest is part of the sealed suite version;
  changing DEV afterwards requires a new suite version and a new
  contamination pass.
- A3. "Development side" = **anything executing with the dev user's effective
  authority**: agents, MCP servers, hooks, IDE helpers, cron/launchd jobs,
  scripts, and every process they spawn.

---

## 2. Threat model

The adversary is **the development side itself** — not malicious, but
optimising. Every realistic leak so far in this repo was accidental (the
50-item instrument was public for 3.5 months; four leaks were found in the
report stating the rule). So the design assumes: whatever the development
side *can* read, it eventually *will*.

| # | Channel | Mitigation |
|---|---|---|
| T1 | Dev reads sealed files | Separate OS identity, 0700 home; `sudo` needs a password (verified 2026-09-29: `sudo -n true` → "a password is required"). Claude Code deny rules are a second wall. |
| T2 | Dev (or the eval identity) edits the broker to print items | Broker and its runtime are installed **root-owned, mode 0555/0444**, from a reviewed SHA by Raouf — not writable by dev, eval or candidate users (a file's owner can chmod it, so the eval identity must not own it). The sudoers rule pins the broker by **SHA-256 digest**. Repo edits have no effect until reinstall. |
| T3 | Candidate code reads the sealed sets or exfiltrates queries | **Privilege separation first:** candidate code runs as a third identity, `zurvancandidate`, which cannot read eval sets, keys, runs or incidents, and owns only its scratch dir. It gets one query at a time over IPC. `sandbox-exec` (deny network, deny writes outside scratch) is **defence in depth** only — Apple lists it as deprecated, so confidentiality never rests on it. |
| T4 | Broker output leaks items | Output is built by an **allowlist serializer** from a closed schema; no free-text field exists to leak into. Errors are opaque codes. |
| T5 | Hill-climbing via repeated runs | One HOLDOUT run per frozen `(commit, corpus, suite, evaluator)`; repeats return the cached result. A per-suite-version run budget; exhaustion forces renewal. |
| T6 | Small-stratum aggregates leak items | Per-stratum metrics only on declared promotion runs; routine runs get overall metrics + counts. Budget (T5) bounds cumulative bits. |
| T7 | Backups (Time Machine) of the eval home | HOLDOUT/RESERVE encrypted at rest with `age`. The `age` identity lives in `keys/`, which setup **excludes from Time Machine** (`tmutil addexclusion -p`), and `seal-check` proves it (`tmutil isexcluded` → `[Excluded]`) or fails. Backups therefore hold ciphertext without its key. Consequence, accepted: losing the disk loses the key, and the suite must be renewed (re-authored), never restored. |
| T8 | Hidden text reaches dev logs/transcripts anyway | Leak canaries + a fingerprint scan run **as the eval identity** over dev artifacts (read-only ACL). Hit → integrity incident → burn. |
| T9 | Confirmation oracle | No per-item digests anywhere visible (existing rule, `eval/search_gold.manifest.json`). Opaque item ids are random, not hashes. The scan is never callable with dev-supplied input. |
| T10 | Author/reviewer inherit dev context | They run as the eval identity: separate `$HOME`, so no `~/.claude` memory, no Zurvan recall hook, no transcripts. |
| T11 | Covert channels from candidate code through allowed outputs | **Accepted residual risk, bounded, documented — not solved.** Two channels: (a) *timing*: latency is reported per run, rounded to 10 ms; (b) *the aggregate result itself*: a candidate can deliberately shape which items it gets right, encoding bits into Recall/MRR. This is inherent in black-box evaluation. Bounds: HOLDOUT metrics are reported at **fixed precision** (quality metrics as integer counts over `n_items`, or rounded to 0.001; bootstrap CIs rounded to 0.01), repeats are cached (T5), and the per-suite run budget caps the cumulative bits. |

**Out of scope:** an adversary with Raouf's password or root, and macOS kernel
vulnerabilities. Raouf, as
operator, can read everything; the seal protects against the development
side, not against the owner.

**Docker is not a boundary here.** `<dev-user>` controls the Docker daemon,
which is root-equivalent. A container would look sealed and not be.

---

## 3. Z1a — observability

### 3.1 Trace modes (§14.1)

`TraceMode = summary | full | protected_eval`, recorded in every trace.

| Field | summary (default) | full | protected_eval |
|---|---|---|---|
| raw query text | **no** — `query_sha256` + length | yes | **never** |
| result snippets | no | yes | never |
| result ids + ranks + scores | yes | yes | never (aggregate only) |
| stage timings | yes | yes | yes |
| snapshot/revision ids | yes | yes | yes |
| Markdown twin in `wiki/traces/` | no | yes | never |

- `--trace` gives `summary`; `--trace-mode full` is explicit.
- `protected_eval` is **forced** by the eval path and cannot be downgraded by a
  flag — the writer refuses `full` when a protected context is active
  (tested).
- **Behaviour change:** today `--trace` writes the raw query
  (`data/traces/*.json`, `retrieval.query.payload.query`). After Z1a it does
  not unless `full` is asked for. Schema bumps to `zurvan.trace.v2`; v1
  traces stay readable and valid.

### 3.2 Permissions and retention

- Trace dirs `0700`, files `0600` (today `0755`/`0644`).
- Retention: untracked traces older than 30 days or beyond the newest 500 are
  removed by `zurvan trace prune` and after every trace write
  (`TraceStore.write`); check.sh has no maintenance step, and a quality gate
  should not delete files. A prune failure never fails the write it follows.
- **The git-tracked traces in `data/traces/` (33 `.json`, counted from git at
  run time; "35" here earlier was a miscount) are frozen research artifacts
  and are never pruned** — prune skips any path `git ls-files` reports, and a
  test proves it.

### 3.3 Stage timings

`search_hybrid` records `perf_counter_ns` spans for: `embed_query`, `fts`,
`vector_scan`, `fuse`, `hydrate`, `total`. Exposed three ways:

- trace field `timings_ms` (all modes);
- `zurvan search ... --explain` prints a stage table;
- `eval_search` reports p50/p95 per stage.

Ranking must be unchanged: timing is observation only. Proven by a
before/after `eval_search` run with identical metrics (required by the
retrieval-change rule in AGENTS.md anyway).

### 3.4 Rank-mover report

`zurvan eval movers <run-a> <run-b>` compares two stored per-item eval runs and
lists items whose target rank moved, by item **index**, never text.

- Allowed datasets: `dev`, `retired`. Refused for `holdout`/`reserve` at the
  type level: those runs never store per-item rows on the dev side, so there
  is nothing to compare (tested).

### 3.5 Retire the burned instrument

The 50-item instrument was public 2026-05-30 → 2026-09-16. Per §9.1 it is
kept, not rewritten:

- state `RETIRED` (diagnostic + historical regression) in a tracked
  `eval/datasets.json` registry;
- it can no longer gate a promotion (the promotion workflow refuses a
  `RETIRED` dataset);
- **historical reproducibility gate:** `eval_search` on one Z0 snapshot
  captured with `--require wiki,search,graph`, run twice, gives an identical
  **result digest**: SHA-256 over the canonical per-item ranked ids and the
  quality metrics (Recall@k, MRR), **excluding** timings, timestamps, RSS and
  other resource observations, which vary by nature. The metrics JSON records
  the snapshot's coherence and the digest;
- existing anti-leak rules are unchanged: index-only references, no per-item
  digest, file stays in `~/.zurvan/instruments/`.

---

## 4. Z1b — dataset model

### 4.1 Pools and states

```
DEV        visible, permanent, separate pool (never feeds HOLDOUT)

RESERVE ──promote──► HOLDOUT ──burn/expiry──► RETIRED
   │                                             ▲
   └──────────────burn──────────────────────────┘
```

- **RETIRED is terminal.** A retired item never returns.
- A RESERVE item promoted to HOLDOUT stops being RESERVE.

The candidate *software* lifecycle is separate: `DEV gate → frozen at SHA →
HOLDOUT gate (declared promotion) → PROMOTED`. The RESERVE pool is for renewal
and replacement, not a routine gate.

### 4.2 Strata

The 16 primary strata of §9.3, with ids and target counts, in a tracked,
machine-readable `eval/z1b/spec.json`:

- the ids;
- minimum HOLDOUT per stratum: 10;
- RESERVE per stratum: 5;
- grading rule per stratum;
- difficulty targets;
- the run policy (§5.4);
- the promotion predeclaration schema.

`spec.json` contains **no evaluation content**.

**Temporal and contradiction strata in Z1** are *document-retrieval*
questions: "which page records what X was before Y", "which pages
disagree on Z", graded by retrieving existing pages. Claim-ledger temporal
semantics (bitemporal validity, supersession) are deferred to Z2, which will
add its own strata. All 16 strata exist in Z1; none is left empty for Z2.

### 4.3 Item schema (sealed side only)

```json
{"id": "opaque random", "stratum": "…", "query": "…",
 "targets": [{"page": "wiki/…", "source_id": "… or null",
              "locator": {"kind": "page|section|line_range", "value": "…"}}],
 "snapshot_aliases": {"snapshot": "<corpus sha256>", "chunk_ids": ["…"]},
 "grading": "rank|abstain|set",
 "difficulty": "easy|medium|hard", "provenance": {"author": "…", "reviewer": "…"}}
```

`targets` are the truth; they survive re-chunking and re-indexing.
`snapshot_aliases` are a convenience bound to one corpus snapshot. The grader
resolves a returned chunk to its page/section and matches on `targets`. If a
target page is missing from a future snapshot, the item is reported as
`TARGET_MISSING` (counted, not silently failed or dropped); a suite whose
corpus moves far enough to lose targets is renewed.

### 4.4 Grading: deterministic first

| Grading | Used by | Rule |
|---|---|---|
| `rank` | most strata | Recall@1/3/10 and MRR, a hit being a returned chunk that resolves to a `targets` page/locator |
| `set` | multi-hop, synthesis | fraction of `targets` in top-10 |
| `abstain` | not-found / insufficient evidence | pass if top score < the threshold declared in `spec.json` |

`abstain` is **provisional**: today's retriever has no abstention signal. It is
reported, but excluded from promotion criteria until Z3/Z4 gives it meaning.

No model grader is used in Z1b.

---

## 5. Z1b — the seal

### 5.1 Identity and layout

Two standard, non-admin macOS users, created by Raouf running
`scripts/eval_seal/setup.sh` under `sudo`:

| Identity | Owns | Can read | Cannot read |
|---|---|---|---|
| `zurvaneval` | sets, keys, runs, incidents | everything under its home; the pinned corpus | — |
| `zurvancandidate` | its scratch dir only | the pinned corpus/index (group `zurvancorpus`, read-only); the root-owned runtime | sets, keys, runs, incidents, the dev user's home |
| dev user | the repo | nothing under either home | everything sealed |

The layout:

```
/Library/ZurvanEval/                       root:wheel 0755
  broker/        pinned broker + candidate-runner + venv   root-owned, 0555/0444
/Users/zurvaneval/                         0700 zurvaneval
  keys/          age identity, ed25519 signing key 0600    Time Machine-excluded
  sets/          holdout.age  reserve.age  dev-ref.jsonl
  runs/          run ledger, cached results, per-item rows (never exported)
  incidents/     canary/scan hits
/Library/ZurvanEval/corpus/                root:zurvancorpus 0750, files 0440
               Z0 snapshot copy (wiki, search, graph) bound by digest
/Users/zurvancandidate/scratch/            0700 zurvancandidate, wiped per run
/Users/Shared/zurvan-eval-drop/            1733 (sticky)  candidate bundles in
```

It also adds `/etc/sudoers.d/zurvan-eval`, with exactly two rules, each
pinned by digest:

```
<dev-user>  ALL=(zurvaneval)      NOPASSWD: sha256:<digest> /Library/ZurvanEval/broker/bin/zurvan-broker
zurvaneval  ALL=(zurvancandidate) NOPASSWD: sha256:<digest> /Library/ZurvanEval/broker/bin/zurvan-candidate-run
```

- The digests are computed at install time, and `visudo -c` must accept the
  file or the install aborts. A digest mismatch after any change to the
  binary makes sudo refuse: a tampered broker cannot run at all.
- With no argument list in the rule, sudo allows any arguments. So the broker
  treats its argv as untrusted: a fixed subcommand set, strict parsing, and no
  path arguments except a drop-dir bundle name matching a strict pattern.
- `env_reset` is on, so `PYTHONPATH` and `PATH` injection are scrubbed.
- The wrappers use absolute paths and the root-owned venv.
- Setup also grants `zurvaneval` a **read-only ACL** on the development
  transcript and trace directories, for the scan in §5.6.

### 5.2 Pinned broker

- `scripts/eval_seal/install.sh <sha>` (run by Raouf) exports the broker
  sources at a reviewed SHA into `/Library/ZurvanEval/broker/` as
  `root:wheel`, mode 0555/0444, builds its venv there, records the SHA,
  recomputes both sudoers digests, and runs `visudo -c`. No sealed-side
  identity owns any file in it.
- The broker reports its installed SHA in every response.
- A repo broker newer than the installed one is surfaced as `BROKER_STALE`;
  it does not install itself.

### 5.3 Broker interface: the only door

```
zurvan eval z1b status
zurvan eval z1b run --commit <sha> --suite holdout [--declare <file>]
zurvan eval z1b seal-check
```

The dev-side `zurvan` builds a `git bundle` of `<sha>` into the drop dir, then
`sudo -u zurvaneval zurvan-broker …`. The broker then:

1. verifies the bundle contains `<sha>`, and the corpus, suite manifest and
   evaluator digests;
2. checks the run policy (§5.4);
3. starts `zurvan-candidate-run` as `zurvancandidate`, which clones the bundle
   into its own scratch dir and runs the candidate retriever under
   `sandbox-exec` (deny network; write only to scratch). The candidate process
   **cannot open the sets or keys under any sandbox state**, because the OS
   denies `zurvancandidate` access;
4. decrypts HOLDOUT in its own memory and sends **one query at a time** over
   the candidate's stdin, reading ranked ids from its stdout. The candidate
   never sees targets, grading or other items' results;
5. grades;
6. writes per-item rows to `runs/` (sealed);
7. emits the allowlisted aggregate JSON;
8. has `zurvan-candidate-run` wipe the scratch dir.

**Aggregate output (closed schema):**

- identity: suite id, `n_items`, `corpus_sha256`, `suite_manifest_sha256`,
  `evaluator_version`, `broker_sha`, `candidate_commit`;
- metrics: overall Recall@1/3/10 and MRR at fixed precision (0.001), and
  paired-bootstrap CIs rounded to 0.01 (T11);
- per-stratum metrics, on declared promotion runs only;
- `latency_ms` p50/p95 (10 ms resolution) and peak RSS;
- `seal_valid`, `cached`, `budget_remaining`.

Every value is typed, and there is no string field that carries item content.
A test fuzzes the serializer with sealed rows and asserts none of their
strings appear in the output.

### 5.4 Run policy (anti-hill-climbing)

- Key = `(candidate_commit, corpus_sha256, suite_manifest_sha256,
  evaluator_version)`. A repeat run returns the cached result with
  `cached: true`, so no new information comes out.
- A HOLDOUT run needs a **predeclaration** (§9.5 of the canonical spec)
  committed at `<sha>`: primary metric, critical strata, non-inferiority
  margins, improvement target and resource budgets. Without one, only overall
  metrics are returned.
- Budget: 8 non-cached HOLDOUT runs per suite version. At zero the broker
  refuses with `SUITE_RENEWAL_REQUIRED`.
- A working tree with uncommitted changes, or an unknown SHA, is refused
  (`CANDIDATE_NOT_FROZEN`).
- RESERVE is runnable only with `--suite reserve --final-acceptance`,
  against a candidate that already passed HOLDOUT under the same suite
  version.

### 5.5 Manifest

`eval/z1b/manifest.json` is tracked, public, and signed with the eval
identity's ed25519 key. The public key is tracked; signing uses
`ssh-keygen -Y sign`. It holds:

- schema and seal version, creation time;
- author and reviewer model identifiers, and the authoring-prompt SHA-256;
- corpus snapshot SHA-256;
- whole-file SHA-256 of `holdout.age` and `reserve.age`;
- stratum counts, accepted/rejected counts;
- the evaluator version;
- the burn ledger: opaque id, reason and date.

**No per-item digest.** A tampered or unsigned manifest fails closed on both
sides.

### 5.6 Canaries and leak scan

Each sealed set carries random canary tokens, known only to the eval identity.
`zurvan-broker scan` runs as `zurvaneval` on a schedule and at each `run`,
over the dev artifacts it has ACL read on:

- `~/.claude/projects/*Zurvan*/`;
- the repo's `data/traces`, `wiki/traces` and logs;
- `~/.zurvan/reports`.

It looks for canaries, and for Boundary-style k-gram fingerprints of every
HOLDOUT/RESERVE query (reusing `scripts/boundary/`).

A hit writes an incident, burns the matched items, and makes the next `run`
refuse with `INTEGRITY_INCIDENT` until Raouf acknowledges it. The scan takes
**no input from the development side**, so it cannot be used as a
does-this-string-exist oracle.

---

## 6. Z1b — sealed authoring pipeline

Everything in this section runs as `zurvaneval`, started by Raouf with
`sudo -u zurvaneval …/broker/bin/zurvan-author`. It is never started from a
dev session.

### 6.1 Inputs

- `spec.json`;
- the authoring rubric (tracked, public);
- the item schema;
- the corpus snapshot copy;
- `dev-ref.jsonl`, the DEV set. DEV is public, so handing it over leaks
  nothing, and it is needed for the contamination checks.

Nothing else.

### 6.2 Pipeline

1. **Author** drafts about 20 candidates per stratum (about 320), each
   grounded in cited corpus pages.
2. **Deterministic filters:**
   - schema validation;
   - every target exists in the snapshot;
   - exact duplicates and normalised lexical overlap;
   - MinHash n-gram near-duplicates, candidate↔candidate and candidate↔DEV;
   - query↔target answer leakage (the target's distinctive strings appearing
     in the query).
3. **Reviewer** (a separate clean session) rejects items that are ambiguous,
   unsolvable, misclassified, trivial, artificial or ungrounded.
4. **Independent re-solve:** a third clean session is given only the query and
   the corpus and must find a target. Items it cannot re-solve are dropped.
5. **Select** 10 per stratum for HOLDOUT and 5 per stratum for RESERVE,
   balancing difficulty. A stratum that falls short is reported as short
   (count only), **not padded**.
6. Encrypt, sign the manifest, and emit **aggregate counts only**.

### 6.3 What comes back to the development side

Only the manifest: counts per stratum, accepted/rejected totals, digests and
signature. No query, target, rejection reason or sample.

### 6.4 Model families: Anthropic-only (decided 2026-09-29)

Author, reviewer and re-solver are separate clean Claude sessions
(`claude -p` as `zurvaneval`, its own login), each on a different Claude model
where practical. The manifest records which model played which role.

**No second provider.** `codex` and `gemini` are installed, but using them
would send private corpus excerpts to OpenAI or Google; today wiki content
reaches only Anthropic. Raouf's ruling: model-family diversity is not worth
widening private-corpus egress. The seal exists to reduce doors, not to add
them. The accepted cost is correlated author/reviewer blind spots; the
independent re-solve and the deterministic filters are the mitigation.

### 6.5 DEV set

About 200 items across the same strata, authored by an ordinary agent run on
the dev side. It is stored at `~/.zurvan/instruments/z1-dev.jsonl`: untracked,
never indexed, and referenced by index only, under the same rules as the old
instrument. Its whole-file digest is in the manifest. It is frozen before
sealed authoring starts.

---

## 7. Integration with what exists

| Existing | Used by Z1 as |
|---|---|
| Z0 `capture_coherent(purpose="measurement", required=("wiki","search","graph"))` | the corpus snapshot bound into the manifest; historical reproducibility |
| `derived_health.stamp_verdict` | proves the snapshot's stores are CURRENT at its revision |
| `scripts/boundary/` k-gram fingerprints | the leak scan (§5.6) |
| `eval_search.py` | DEV/RETIRED runs; grows stage percentiles and per-item rows for movers |
| `trace_writer.py` / `trace_schema.py` | modes, v2 schema, permissions |
| `public_repo_guard.py`, instrument tests | must stay green; extended to refuse any `eval/z1b/*` file that is not on an allowlist |

---

## 8. Refusals (fail closed)

| Code | When |
|---|---|
| `SEAL_NOT_CONFIGURED` | no `zurvaneval` identity or sudoers rule. Z1b commands exit non-zero, never silently pass. |
| `BROKER_STALE` | installed broker SHA ≠ repo broker SHA (warning on `status`, refusal on `run`) |
| `MANIFEST_INVALID` | bad signature, digest mismatch, or schema version |
| `CORPUS_MISMATCH` | snapshot digest ≠ manifest |
| `CANDIDATE_NOT_FROZEN` | dirty tree or unknown SHA |
| `SUITE_RENEWAL_REQUIRED` | budget exhausted |
| `INTEGRITY_INCIDENT` | unacknowledged canary/scan hit |
| `PROTECTED_TRACE` | an attempt to write a non-`protected_eval` trace inside an eval run |

---

## 9. Gates

### Z1a closes when:

1. `summary` traces contain no raw query (test and live run).
2. Trace files are 0600 and dirs 0700.
3. Prune never touches the 35 tracked traces.
4. Stage timings are present, and ranking is unchanged: before/after
   `eval_search` metrics are identical, documented in CHANGELOG.
5. Movers refuse holdout/reserve.
6. The burned instrument is `RETIRED` and refused by the promotion workflow.
7. The historical result digest (ranked ids + quality metrics, no timings or
   resources) is identical across two runs on one coherent Z0 snapshot.
8. The instrument leak guard and the full suite are green.

### Z1b closes when (on the real `zurvaneval` identity, not a simulation):

1. `seal-check` from the dev session gets `EACCES` on every sealed path, and
   Claude Code deny rules block the same paths. **Candidate code running as
   `zurvancandidate` with the sandbox disabled** still gets `EACCES` on the
   sets, keys, runs and incidents, which proves the sandbox is not the vault
   door.
1b. The broker and runner are root-owned and not writable by any of the three
   users; a changed binary is refused by sudo's digest check. `keys/` reports
   `[Excluded]` from Time Machine.
2. The serializer fuzz shows no sealed string in any output, including errors.
3. Tampered manifest, corpus, broker or bundle each fail closed with their
   code.
4. A dirty or unknown commit is refused; a repeat run is cached; an exhausted
   budget is refused.
5. A candidate that tries network access or a write outside its scratch dir is
   killed by the sandbox, and the run is marked failed.
6. A planted canary in a dev trace raises `INTEGRITY_INCIDENT` and burns the
   item; burned items never re-enter.
7. RESERVE is unreachable without `--final-acceptance`.
8. The manifest shows HOLDOUT ≥ 150 and RESERVE ≥ 80, or reports each short
   stratum honestly. A short stratum is a failed gate item, not a pass.
9. **Red-team:** a fresh agent is told to extract any holdout item from the dev
   session, and fails. Its transcript is kept.

Tests that need the real identity are in `tests/test_z1b_seal_live.py`. They
**skip with a loud reason** when the identity is absent, and check.sh reports
`Z1b: NOT CONFIGURED`, never PASS. Everything else (serializer, policy,
manifest verification, sandbox profile) is unit-tested against a tmp-root
simulation.

---

## 10. Steps only Raouf can do

1. Review this spec, including the §6.4 egress decision.
2. After the Z1b plan is built: run `sudo scripts/eval_seal/setup.sh` and
   `sudo scripts/eval_seal/install.sh <sha>`.
3. Log in `claude` as `zurvaneval`. Never as `zurvancandidate`, which has
   no network and no model access.
4. Start sealed authoring: `sudo -u zurvaneval …/zurvan-author`. Authoring
   ~320 items plus review plus re-solve costs real model usage.

---

## 11. Non-goals

- No retrieval ranking change in Z1. Z1 only observes and measures.
- No model grader.
- No claim-ledger temporal semantics (bitemporal validity, supersession) until
  Z2. Z1's temporal and contradiction strata are document-retrieval questions
  over existing pages (§4.2).
- No second model provider (§6.4).
- No remote or CI execution of the broker.
