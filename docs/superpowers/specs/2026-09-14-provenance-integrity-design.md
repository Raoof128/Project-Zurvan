# Provenance integrity: make Zurvan's evidence claim re-verifiable by a stranger

**Date:** 2026-09-14 (Australia/Sydney)
**Status:** Draft — gap hunt complete, awaiting review
**Scope:** Sub-project D. Independent of A (retrieval core), B (capture), C (agent
ergonomics). Sequenced after A only because A is already specced.

---

## 1. The claim this project defends

Zurvan's distinguishing property is not search. It is that **a recorded claim
cannot be fabricated**: `claim add` rejects evidence that does not appear
byte-for-byte in an ingested source. Every other property (hybrid retrieval, the
graph, the privacy guard) exists in other systems. That one is rare.

This project makes that property (a) true everywhere it is asserted, and (b)
re-verifiable by someone who does not trust us.

## Orchestration (corrected 2026-09-14 — the specs' independence claim was false)

Both documents originally asserted that A and D are independent. **They are
not**: D's P0 mutates `wiki/` and therefore the corpus A measures. Measured
proof — P0 removed 15,201 chunks (7.63%) and 63% of the graph's nodes.

```
D-P0  (correctness cleanup)
  |
full reindex
  |
NEW eval baseline          <- 68/72/0.7057 on 199,269 chunks is RETIRED
  |
A-Step 0  failure anatomy on the CLEAN corpus
  |
A-Phase 1 sqlite-vec invariant storage swap
  |
  +-- D-P1 / P2 / P3 may proceed independently (all read-only w.r.t. the index)
```

Phase 1 may only demand identical ranking and failure sets *after* this point,
because only then is its baseline stable.

**Canonical baseline as of 2026-09-14 (post-P0):** 184,068 chunks · Top-1 0.68 ·
Top-3 0.72 · MRR 0.7057 · 14 failures. See
`docs/evaluation/retrieval-p0-stub-removal-2026-09-14.md`.

## 2. Gap hunt — four fronts, with receipts

### Front 1 — Regulation mandating with no mechanism

EU AI Act **Article 12** (automatic lifecycle event logging) became fully
applicable **2 August 2026**. Commentary is consistent that the text is
"precise on objectives and thin on operational detail", with open ambiguity over
**what events must be recorded, in what format, and who bears responsibility for
storage and integrity**.

*Source precision:* reported via secondary compliance commentary; the primary
Article 12 text is pinned at the EU AI Act service desk. Treat the "thin on
detail" characterisation as reported until read against the primary text and the
harmonised standards that follow it.

### Front 2 — Incidents: the wound is bleeding, with numbers

- **$145,000** in US court sanctions for fabricated citations in **Q1 2026**.
- One Oregon matter: **15 fabricated citations and 8 fabricated quotations**,
  ~**$109,700** in combined sanctions, fines and opposing costs.
- **$30,000** Sixth Circuit fine — reported as the steepest federal appellate
  sanction of its kind.
- Charlotin's public tracker: **~1,490 decisions worldwide**, >1,000 US, as of
  May 2026.

*All figures are reported via secondary legal commentary and are marked as such
until the underlying orders are pinned.*

The two punished behaviours map exactly onto two failure classes:

| Failure | Definition | Zurvan today |
| --- | --- | --- |
| `FABRICATED_CITATION` | The cited source does not exist | Prevented — a claim must name an ingested source |
| `FABRICATED_QUOTATION` | The source exists; the quote does not appear in it | Prevented — byte-exact evidence check |

Zurvan already prevents both **at write time**. It cannot yet **prove** it to a
third party, and it does not re-check them **over time**.

### Front 3 — What the incumbents concede, in their own words

A 2026 survey of evidence tracing and execution provenance in LLM agents states
that memory systems "optimize for recall, personalization, efficiency, or
coherence, with limited support for tracing origins, temporal validity,
conflicts, contamination, and privacy exposure", and that "provenance-specific
capabilities such as **source attribution, conflict handling, staleness
detection, contamination tracking, and evidence-aware verification** remain
limited".

Scored against that list:

| Capability the field concedes is limited | Zurvan |
| --- | --- |
| Source attribution | **has it** — claims anchor to a hashed source page |
| Evidence-aware verification | **has it** — byte-exact, enforced at write |
| Privacy exposure | **has it** — guard + routing (hardened 2026-09-14) |
| Staleness detection | **has it** — `zurvan_status` (added 2026-09-14) |
| Contamination tracking | **missing** |
| Conflict handling | **missing** |
| Temporal validity | **missing** (Zep has it) |

### Front 4 — Self-audit (measured, not asserted)

```
EVIDENCE DENSITY of the store
  evidence-bound  48.4%  (253 claims)
  judgment        20.3%  (106 decisions)
  unverified      31.4%  (164 notes)
```

And the finding that matters most, found by auditing our own store:

**3,811 auto-generated stubs in `wiki/claims/` assert evidence they do not
have.** `create_stub_concept_and_claim` writes one per ingest containing the
literal text *"Evidence missing? No, cited from \<source\>"* — with no `type:
claim`, no `confidence`, no `source:` field, and **no evidence section**. They
outnumber real claims **15:1** in the same directory.

This is the disease Zurvan exists to prevent, occurring inside Zurvan. Nothing
leaked (the directory is gitignored and guard-blocked), but any consumer reading
`wiki/claims/` is told 94% of the time that evidence exists when it does not.

### Front 5 — Zurvan's own corpus (searched, not assumed)

Searching the ingested corpus surfaced the positioning method already used in
this author's `llm-shield` prior-art map, which states its gap in the falsifiable
shape this project should copy:

> "Nothing in the list recomputes ... from evidence a hostile third party can
> re-derive offline."

Scoped, bold, and falsifiable — never "nothing in the world does X".

---

## 3. What to build, ranked

### P0 — DONE 2026-09-14 (ACCEPT on integrity; retrieval prediction unsupported)

Executed: 7,622 stub pages removed (3,811 claim + 3,811 concept — the original
"3,811" scope was wrong and covered only one family), generator deleted,
`extraction_status: unextracted` now carried by the source page. Integrity gate
passed on all four checks. The predicted retrieval improvement **did not occur**
and the evidence behind that prediction was retracted as self-fulfilling; see
the frozen report. Kept as ACCEPT on the correctness grounds it was approved on.

### P0 (original statement) — Stop asserting evidence that does not exist

Rewrite or remove the 3,811 stubs. A placeholder must state its own emptiness:
`status: unextracted`, no evidence section, no sentence claiming citation. An
anti-fabrication system must not ship 3,811 fabrications.

**Gate:** 0 files in `wiki/claims/` assert evidence without an evidence section;
a test asserts the invariant so the next ingest cannot reintroduce it.

### P1 — Contamination tracking (the strongest novel move)

Every source page carries a content hash; every claim carries a byte-exact quote.
**Nothing re-verifies a claim when its source changes.** A claim whose quote no
longer appears in its current source is *provably* contaminated — detectable
without a model, without a judgement call.

Zurvan is unusually able to close this precisely because it stores the quote
verbatim; a system storing only embeddings or summaries structurally cannot.

- **Wound:** a source edited, retracted or re-ingested silently poisons every
  downstream claim. Today nobody would know.
- **Species:** new kind for this project and, on the current source map, for the
  surveyed field.
- **Moat:** deeper guarantee.
- **Tomorrow:** the named actor is this repo — 253 claims, re-checkable today.
  Single blocker: no re-validation command exists.
- **Anti-gaming disclaimer:** detects *drift between a claim and its ingested
  text*. It cannot know a source was wrong when ingested, and it cannot detect a
  quote that is accurate but misleading in context.

### P2 — `zurvan verify` as a recomputable receipt (G2: name it after the wound)

Re-check every claim against its source and emit a receipt a third party can
recompute from pinned inputs: source path, content hash, quote offset, verdict.
Failure codes named after the incidents: `FABRICATED_CITATION`,
`FABRICATED_QUOTATION`, plus `CONTAMINATED` from P1.

**Gate:** running it twice on unchanged inputs produces byte-identical output;
mutating one character of one source flips exactly one claim to `CONTAMINATED`.

**Disclaimer:** the receipt proves quote-to-source correspondence. It is not a
statement that the claim is true, nor that the source is trustworthy.

### P3 — Evidence density as a published metric (G1)

Report the three-way split above as a first-class derived metric.

**Disclaimer, in the same breath:** density is not quality. A store can inflate
it with trivially quotable claims. It measures how much of what is recorded is
*bound to something*, not how much is worth knowing.

### P4 — Conflict detection (the ambition gap)

Two claims that contradict each other are currently invisible. This is the item
that moves the project from "audit found real bugs" to "built a kind of thing the
field concedes it lacks" — and it is the hardest, because contradiction is a
semantic judgement, not a hash comparison. Deferred, recorded as an IOU, not
silently dropped.

## 4. Honest scorecard

| Axis | Score | What moves it higher (named artifact) |
| --- | --- | --- |
| Ambition | 6/10 | P4 conflict detection — found by audit, not invention, until P4 exists |
| Falsifiability | 8/10 | P2's byte-identical-rerun gate; a hostile stranger can recompute |
| Wound evidence | 9/10 | Court sanctions with figures; primary orders still to be pinned |
| Honesty | 8/10 | Every mechanism ships its own disclaimer; secondary figures marked reported |
| Moat | 7/10 | P1 is structurally unavailable to embedding-only stores |

Re-score at closeout. Scores may go down; explaining why is a feature.

## 5. Out of scope

Temporal validity (Zep solves it; no wound here yet) · LLM-based contradiction
judgement · signing/PKI for receipts (recomputability first, signatures only if a
named external verifier appears) · anything in sub-projects A, B, C.
