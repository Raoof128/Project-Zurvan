# Zurvan Boundary: provenance-aware egress and contamination control

**Date:** 2026-09-15 (Australia/Sydney)
**Status:** Approved for planning — not implemented
**Scope:** A new local subsystem. Sub-project E, independent of A (retrieval core)
and D (provenance integrity), though it consumes D's byte-exact source store.

> **This document deliberately contains no gold query text, no private project
> name, and no private page path.** Leaks are referenced by number, literals by
> hash. `docs/` is indexed, so a spec that quoted its own evidence would be the
> first thing the system it describes would have to block. Dogfooding starts here.

---

## 1. The wound

On 2026-09-15 a single session leaked protected content **seven times**. Every
leak was written by an agent with legitimate read access, none involved malice,
and the existing defences — `.gitignore`, `public_repo_guard`, the `pre-push`
hook, GitHub push protection, and encryption at rest — caught **zero of them**.

| # | What moved | Span | Destination |
| --- | --- | --- | --- |
| 1 | Gold query #44 | 51 B | a wiki decision page |
| 2 | Gold query #44 | 51 B | `AGENTS.md` |
| 3 | A private project name | 5 B | `AGENTS.md` prose |
| 3b | The phrase carrying it | 60 B | `AGENTS.md` prose |
| 4 | A private project name | 6 B | `scripts/memory.py`, tests |
| 5 | A private page path | 79 B | an eval artifact (JSON) |
| 6 | 96 whole wiki pages | KB | a public git remote |

**Leak #1 is the one that reframes the problem.** That decision page was never
tracked by git and never reached the remote. It leaked nothing to anybody. It
was merely *indexed* — and it then outranked the real answer to the query it
quoted, moving Top-1 from 0.70 to 0.68. Measured repair: removing the quote from
the decision restored the answer to rank 2, removing it from `AGENTS.md` as well
restored rank 1.

That is a **contamination failure with zero publication**. No confidentiality
model describes it, and it is the reason this design is organised around sinks
rather than around "public".

## 2. Prior art, and the seam it concedes

> **REVISION 2026-09-15b — gap hunt across four fronts + this project's own
> archive. Three findings below change the design's standing: Boundary does not
> invent its core primitive (§2.1), the instrument problem has a world-first
> solution that structurally cannot apply here (§2.2), and the leak count is
> seven, not six (§2.3).**

### 2.1 This family already shipped the residue primitive — Boundary must bridge, not reinvent

Project Simurgh's **Stage 4X/4Y — VDR: Verifiable Document Residue** already
builds residue detection over a corpus: *"4X = residue over an authored corpus,
4Y = residue over submitted bytes."* That is D1's mechanism, shipped and proven.

Its signed limitation is the seam, in its own words:

> **"Coverage geometry, not leakage detection."** · **"The Map Is Not a Verdict"**

VDR maps where a gate's coverage reaches over a submitted document and
explicitly declines to render a verdict. Boundary needs precisely the verdict
VDR signs away, and points the same primitive the other way: VDR asks *does this
submission contain residue of our corpus?*; Boundary asks *does this outgoing
artefact contain residue of the protected corpus, and may it leave?*

**Consequence for this spec:** D1 is a REUSE with an inverted sink, not a new
invention. Claiming otherwise would be wrapping. Two VDR IOUs —
`irreducible_semantic_residue_deferred` and `cross_gate_residue_benchmark_deferred`
— are candidates for Boundary to pay rather than re-mint.

### 2.2 The instrument problem has a world-first solution that cannot apply here

*Double Blind Evals: Resolving the Dual Confidentiality Dilemma in AI Safety
Auditing* (Trask, Messing, Maham, Brundage, Isaac et al.; Google / Singapore AISI
/ OpenMined / ML Commons / AVERI) states the INSTRUMENT motive exactly:
evaluators keep prompts secret *"to prevent purposeful or incidental data
leakage, ensuring longer usefulness of their benchmark."* Their mechanism is
hardware secure enclaves — H100 confidential compute, PySyft — giving mutual
secrecy between a model owner and an evaluator.

**The seam: that architecture requires two mutually distrustful parties.** When
the evaluator IS the corpus owner — every local agent system, every RAG product
measuring its own retrieval, Zurvan — there is no second party to put on the
other side of the enclave. Their design **does not directly solve single-owner self-evaluation
contamination at a local corpus boundary** — same-org role separation or a
confidential-compute variant is conceivable, so the honest claim is "not solved by
this architecture", not "structurally impossible".

Adjacent work confirms the shape of the gap rather than closing it: DyePack
proves contamination by planting backdoors *in the benchmark*; Oren/Ni give
provable false-positive control via canonical-ordering tests; MemAudit audits
poisoned agent memory *post-hoc*. OWASP's Agent Memory Guard, by contrast, screens memory
reads and writes with *heuristic detectors* and no byte-exact provenance. All
enforce at runtime with allow/redact/quarantine/block dispositions, but on
heuristic detectors rather than byte-exact provenance.. None prevents the instrument
from entering the corpus, and none can be checked by a third party who is not
given the corpus.

Positioning sentence, scoped and falsifiable: **no matching prior pattern in this
project's current source map prevents self-evaluation contamination at the corpus
boundary, or proves its absence without disclosing the corpus.**

### 2.3 P1 provenance-integrity incident (NOT an eighth transfer leak)

`tests/test_wiki_merge.py` seeds an entity named after a real researcher. Before
the 2026-09-15 isolation fix it wrote five `merge | <entity> | 1 sources` entries
into the live `wiki/log.md` while the page itself went to a temp directory, so
`wiki/entities/` is empty. The audit log therefore asserts five ingests whose
content was never in the store.

**Taxonomy, corrected.** §1 lists **seven transfer leaks** (#1, #2, #3, #3b, #4,
#5, #6). This event is **not** number eight — it is a different kind, recorded as
**P1, a provenance-integrity incident**. The detector benchmark denominator stays
at **7 transfer cases**; P1 is undetectable by D1/D2/D3 and must never be counted
in their recall.

Those seven put protected content
where it did not belong; this one made the provenance record **state something
false**. A store whose log can assert a merge that never happened cannot be the
evidence base for any attestation built on it — which makes it a prerequisite,
not a footnote.

### 2.4 Prior art at rest and in flight (unchanged)

Encrypted agent-memory vaults (Perseus Vault, AgentVault, CtxVault) and
capability-sealed secret mediation (CapSeal) protect data **at rest** and gate
**credentials**. Information-flow work for agents — APPA's permissions algebra,
*Ghost in the Agent* — and AgentFlow, which does define flow/path rules, controlled
release and a runtime reference monitor constraining which sinks receive sensitive
fields — track influence **inside an
agent's execution**, and NeuroTaint reconstructs provenance **offline, after the
fact**.

None of them stop an *authorised* agent from copying protected text into a
forbidden destination, because in every one of them the read was permitted and
the write looks ordinary. In leak #4 the cryptography, the capability check and
the policy engine would all have performed perfectly.

**The unfair advantage this design exploits:** Zurvan stores source text
byte-exact rather than reducing it to embeddings. An embedding-only store
structurally cannot answer *"does this outgoing artefact contain text that came
from here?"* Zurvan can.

## 3. Threat model (v1)

**Defended:** an *authorised but provenance-unreliable* agent — one that may
legitimately read protected material and later write those bytes into a
forbidden destination, having forgotten, confused, or never recorded where the
text came from.

**Trusted computing base:** the Boundary service, its corpora, its fingerprint
index, its policy file, and its scanner. All local, all outside the git tree.

**Explicitly out of scope for v1:** a compromised or adversarial agent
deliberately evading the scanner (encoding, fragmentation below threshold,
character substitution, screenshots). That is V5, where APPA/AgentFlow/CapSeal
become neighbours rather than competitors. Human-operator mistakes benefit from
the same gate but are **not formally claimed** until V2.

## 4. The core abstraction: source class × sink class

Protection is a property of a span's **origin**, not of its directory. Two
protected classes, with different security goals:

| Source class | Property protected | Failure mode |
| --- | --- | --- |
| `PRIVATE` | Confidentiality | Disclosure |
| `INSTRUMENT` | Measurement integrity | The system under evaluation can see its own exam |
| `PUBLIC` | None beyond existing policy | — |

And four sinks, because an INSTRUMENT leak needs no publication to do damage:

```
PUBLIC_ARTIFACT    anything tracked, pushed, exported or published
RETRIEVAL_CORPUS   anything the indexer will read
EVAL_OUTPUT        frozen reports and rank artifacts
PRIVATE_LOCAL      the local store itself
```

**The v1 policy matrix.** Formally guaranteed: **three blocked (source class, sink) pairs across two protected source classes.** An earlier draft said "two", which an implementer could satisfy by omitting a policy entirely.

| source → sink | PUBLIC_ARTIFACT | RETRIEVAL_CORPUS | EVAL_OUTPUT | PRIVATE_LOCAL |
| --- | --- | --- | --- | --- |
| `PRIVATE` | **BLOCK** | allow by policy | warn | allow |
| `INSTRUMENT` | **BLOCK** | **BLOCK** | warn | allow |
| `PUBLIC` | allow | allow | allow | allow |

Leak #1 is the `INSTRUMENT → RETRIEVAL_CORPUS` cell. Leaks #2–#6 are
`→ PUBLIC_ARTIFACT`.

**Sink labels are NON-EXCLUSIVE, and deny overrides allow.** `docs/foo.md` is
simultaneously a `PUBLIC_ARTIFACT` and a `RETRIEVAL_CORPUS` member; an eval
artifact may be `EVAL_OUTPUT` and tracked. Without a precedence rule,
`PRIVATE → RETRIEVAL_CORPUS` (allow) and `PRIVATE → PUBLIC_ARTIFACT` (block)
give contradictory verdicts on the same file. **Every applicable sink label is
evaluated and any BLOCK wins.**

## 5. The invariant

> **No sink receives an undeclassified verbatim span of ≥ T bytes whose matched
> span has a source class forbidden for that sink and no approved public origin,
> nor any salient literal forbidden for that sink.**

"Protected sink" would read two ways — the sink is not what is protected. The
forbidden-ness is a property of the (source class, sink) pair in §4's matrix, so
the invariant names the pair.

**Scope of the guarantee (corrected).** The invariant holds only for sinks
Boundary actually mediates. V1 integrates `scan-diff` (commit) and the indexer
write path; `EVAL_OUTPUT` and publication export are V2. Until a sink is
integrated, **Boundary makes no claim about it** — an earlier draft asserted a
universal invariant while wiring one sink, which would have been a guarantee
stronger than the enforcement behind it.

`T` is a **parameter, not a constant**, and its default is determined
empirically from the frozen benchmark (§9) once D3 exists. Freezing 64 today
would be a magic number defended by a measurement taken before the detector that
changes it.

## 6. Detectors

### D1 — Long-span matcher
Winnowed k-gram fingerprints (rolling hash, minimiser selection) over protected
sources, keyed and stored in SQLite. A fingerprint hit is a **candidate only**;
the verdict always comes from a **byte-exact comparison against the original
source**. Hashes never decide, so collisions cannot produce a false block and
the claim stays defensible.

**Detection guarantee, parameterised.** "Winnowed fingerprints" alone does not
guarantee detection of every identical span ≥ T. The spec fixes the domain (bytes
vs tokens), the k-gram size `k`, and the window `w`, and states the resulting
guarantee — for standard winnowing every shared substring of length ≥ `k + w - 1`
is detected. `T` is defined in terms of `k` and `w`, not chosen independently.
Tests exercise exactly `T-1`, `T`, `T+1`.

**Postings are one-to-many.** A fingerprint maps to a LIST of `(source_id,
offset)`: common k-grams and hash collisions legitimately have many origins, and
keeping one produces false negatives or misattributed provenance. All postings
resolve, then byte-compare.

### D2 — Salient-literal registry
Exact matching for short, high-salience strings that no span rule can reach —
project names (5–6 B), private paths, complete gold queries. Named *literal*,
not *identifier*, because a full gold query is neither short nor an identifier.
Entries carry metadata; **cleartext never leaves the local store**:

```yaml
class: INSTRUMENT
value_hash: <sha256>
match: exact
case_sensitive: false
forbidden_sinks: [RETRIEVAL_CORPUS, PUBLIC_ARTIFACT]
```

**Storage semantics.** The canonical literal lives **only** in local
`literals.sqlite` — a SHA-256 cannot locate an arbitrary case-insensitive
substring, so a searchable representation must exist locally. Tracked or exported
metadata carries the hash / opaque id only. Matching uses Unicode **casefold**
(not `lower()`), and the casefold version is recorded in each release so a
normalisation change cannot silently re-open one.

Case-insensitivity is mandatory and load-bearing: on 2026-09-15 a redaction pass
covering lower and upper case missed a title-cased occurrence, which survived
into a further commit.

### D3 — Known-public origin resolver
Subtraction happens at the **matched-span level**, never by exempting a whole
source. Exempting a source would permit laundering: drop protected text into a
source marked public, and everything it contains becomes quotable.

```
candidate span
   ├── matches a PROTECTED source?           no  → clean
   │        yes
   └── identical span in the trusted PUBLIC corpus?
            yes → exempt this span
            no  → protected match → BLOCK
```

**D3 is source-class and sink aware.** Public-origin subtraction is sound for
`PRIVATE` (confidentiality): if the exact span is genuinely public, disclosing it
again discloses nothing. It is **not** sound for `INSTRUMENT → RETRIEVAL_CORPUS`
(measurement integrity): a gold query whose words also occur publicly still
matches its own answer and still contaminates. Instrument spans require explicit
benchmark declassification or retirement, never generic public origin.

**The trusted public corpus must never be seeded from "everything currently
tracked."** If the tree already contains an accidental leak and `public.sqlite` is
rebuilt from it, D3 blesses that leak permanently — yesterday's mistake becomes
tomorrow's known-public origin. Seed only from approved, reviewed, known-clean
provenance, recording accession provenance and digest per entry.

D3 is first-class because without it D1 is unusable. Measured on real corpora
(§8), the private store contains ingested copies of the project's **own public
documents**, so a naive gate blocks a commit for quoting the repo's README.

## 6b. Fail-closed state validation

Every scan verifies, before rendering any verdict: protected-source digest,
classification-policy digest, literal-registry digest, fingerprint-index digest.
**Stale, missing, partial or mismatched Boundary state is a hard failure, never a
"clean" result.** Index replacement is atomic. Without this, editing a protected
source after the last `build` yields silent false negatives — which destroys the
guarantee more quietly than any detector bug could.

## 7. Architecture

```
~/.zurvan/boundary/          ← outside the git tree. git cannot stage what it cannot see.
  sources.sqlite             protected sources, byte-exact, class-tagged
  public.sqlite              trusted public-origin corpus (D3)
  fingerprints.sqlite        winnowed index → (source_id, offset)
  literals.sqlite            D2 registry; cleartext local-only
  releases.sqlite            declassification ledger
  policy.yaml                the source × sink matrix
```

The tracked repo holds **only** the scanner code and opaque release IDs. This is
the structural half of the design: removing the data from the working tree makes
`git add -A` incapable of capturing it, with no rule to remember or misconfigure.

## 8. Measured baseline (2026-09-15, pre-implementation)

Public corpus 1.69 MB / 321 tracked code+doc files. Private sample 11.16 MB /
400 real source pages. Coincidental spans, D3 **not** applied:

| T (bytes) | 24 | 32 | 48 | 64 | 96 | 128 |
| --- | --- | --- | --- | --- | --- | --- |
| coincidental spans | 1118 | 645 | 318 | 173 | 122 | 98 |

Samples at T=48 are the project's own plan and skill documentation, ingested as
sources. **This is the measurement that makes D3 mandatory** and the reason T is
not yet frozen.

Detector recall against the seven real leaks, measured:

```
D1 (T=64) alone   2/7      leaks #5, #6
D2 alone          5/7      leaks #1, #2, #3, #3b, #4
D1 + D2           7/7
```

## 9. Benchmark protocol

Two frozen sets:

```
eval/boundary_redteam.jsonl     the 7 real leaks (hashed spans, never cleartext)
eval/boundary_clean.jsonl       legitimate public/private overlap
```

Every run reports **each detector independently** — a single accuracy number
hides which component earns its keep:

```
recall on real leaks        n/7
false positives             n / total clean artefacts
FP match count              n
median / p95 scan latency   ms
index build time            s
index size                  MB
```

**Acceptance for v1 — predeclared BEFORE testing, not chosen after:**

| Gate | Threshold |
| --- | --- |
| Recall on the 7 transfer leaks | **7/7**, no exceptions |
| False-positive artefact rate | **≤ 1 blocked artefact per 200 clean** (predeclare; a "stated FP rate" is not a gate — 60% FP satisfies that prose) |
| Added scan latency, p95 | **≤ 400 ms** on the clean corpus |
| Index build | completes and validates on the full protected corpus |

### First measurement against real material — 2026-09-16

Run: 300 real `wiki/` pages as the protected corpus (10.1 MB, 591,191
fingerprints), 200 real tracked files as the scan set. The two sets are disjoint
by construction — `wiki/` is gitignored, the scan set is tracked.

| Gate | Result | |
| --- | --- | --- |
| Added scan latency, p95 | **234 ms** and **344 ms** over two runs (p50 186/238, max 376/646) | **MET, thin margin** |
| Index build | 8.7 s, validates | **MET** |
| Recall on the 7 transfer leaks | not yet run — the leak corpus does not exist | **NOT MEASURED** |
| False-positive artefact rate | 11/200 | **INVALID AS MEASURED — not failed, not passed** |

The latency figure is the fix, not the starting point: the first run measured
p50 14,957 ms / p95 34,501 ms. The cause is closed below (§D1 degenerate
content): one file produced **63,975 candidate anchors** from a horizontal rule
it shared with a protected page.

Both runs are reported because the margin is thin and the spread is real. The
profile explains why — **every scan pays a ~200 ms constant floor**, whatever
the artefact:

```
open_db + validate            179 ms   <- constant, independent of input
  orphan LEFT JOIN             74 ms
  count(*) fingerprints        11 ms
  stat_digest (300 files)      10 ms
winnow + candidate SQL       0-212 ms  <- scales with the artefact
blob fetch + confirm          0-17 ms
```

A 548 ms scan of a 200-byte `.claude/settings.json` is 208 ms of validation and
almost nothing else. The 74 ms orphan join could move to `deep=True` and would
buy back most of the floor — **it was deliberately left alone.** The budget is
already met, and moving a live integrity check out of the per-scan path to
improve a number that passes is loosening a checker, which this project does not
do. It is recorded here so the option is visible rather than rediscovered.

**Known cost, not a breach:** the commit gate scans every staged blob
separately, so a 50-file commit pays ~10 s of revalidation. A scan session that
validates once and holds the connection for a batch would remove it, at the
price of a TOCTOU window across the batch that the current design closes on
purpose. That trade is a design change, not an optimisation, and is not made
here.

**Why the FP number is invalid as a measurement, rather than a failure.** Every
one of the 11 was read. None is a coincidental collision:

| Class | Example | What it is |
| --- | --- | --- |
| Ingestion round-trip | `docs/superpowers/plans/*.md` ↔ `wiki/sources/docs__superpowers__plans__*.md` | the tracked file's own ingested copy |
| Generator boilerplate | `scripts/ingest.py`'s template ↔ the pages it wrote | flow is code → wiki, never wiki → code |
| Code idiom | `parser = argparse.ArgumentParser()\n    parser.add_argument("--` (65–68 B) | shared idiom just over T=64 |

The detector is right in each case: that text really is in the protected corpus.
The corpus is what is wrong. **`ingest.py` records `title`, `hash` and
`ingested_at` — and no source class.** A public document ingested into
`wiki/sources/` becomes PRIVATE by location alone, after which the original
public file can never be committed. That is a **provenance inversion**, and it
makes "false positive" unmeasurable on this corpus: the metric cannot separate a
coincidence from a document that was public before Zurvan ever saw it.

Per the project's measurement rule, this is recorded as *invalid as a metric,
not passed*, and replaced **prospectively**:

> **v2 criterion.** ≤ 1 blocked artefact per 200, measured on a scan set with no
> ingestion relationship to the protected corpus — i.e. after ingestion records
> a source class and D3 exempts material acceded as public.

**Open item — IMPLEMENTED 2026-09-17, see "Provenance at ingestion" below.**
The FP gate remains NOT YET MEASURED under the v2 criterion: the mechanism
exists and is validated, but no qualifying post-provenance evaluation corpus
does. The original text of the item follows.

Wire provenance into ingestion: a page ingested from a tracked repo file or a public
URL is recorded with a PUBLIC origin class and acceded to D3 at ingest time.
Seeding D3 from "everything currently tracked" remains forbidden (Global
Constraint 7) — that is the laundering hole, and this is the opposite: recording
what was already public *at the moment it entered*, with its provenance.

### Provenance at ingestion — added 2026-09-17

The open item above is implemented. `scripts/provenance.py` decides an origin
class when a document is acceded, from evidence available at that moment, and
`ingest.py` writes it onto the source page as `origin_class` / `origin_provenance`.

**Three classes, because "public" is not one thing.**

    PRIVATE  ->  PUBLIC_DECLARED  ->  PUBLIC_VERIFIED

Only `PUBLIC_VERIFIED` is acceded to D3, so only it can exempt a span.
`PUBLIC_DECLARED` is recorded for audit and never honoured — an unverified claim
that grants an exemption is itself the laundering primitive.

**`PUBLIC_VERIFIED` = these exact bytes are reachable from an ALLOWLISTED ref in
the repository this gate protects.** Three weaker predicates were implemented,
demonstrated to be exploitable, and rejected:

| Predicate | Why it fails | Evidence |
| --- | --- | --- |
| `git cat-file -e` (object exists) | `git hash-object -w` satisfies it with no commit, ref or intent | dangling blob reports EXISTS; `log --find-object` finds nothing |
| `git log --all` (any ref) | `--all` covers `refs/stash`, `refs/original/*`, scratch branches | a stashed file and a temp-branch file were both found |
| index / "tracked" | `git add` is local, unshared, reversible | staging then unstaging would leave the text exempt forever |

Default allowlist: the canonical branch as git reports it (`origin/HEAD`, else
`main`/`master`). A repository with neither verifies **nothing** — fail closed.
`ZURVAN_PROTECTED_REFS` declares release refs where a project has them.

Staged-only, foreign-repository commits and declared URLs all class
`PUBLIC_DECLARED`. A foreign repository matters: `git init && git add && git
commit` in a scratch directory is not a publication event, and treating it as
one would mean anyone who can run git can cross the boundary.

**An accession is historical evidence.** It survives a later reset, rebase or
branch deletion: the disclosure already happened, re-validating would make a
routine rebase silently re-block clean content, and revocation belongs in the
declassification ledger rather than in `git rebase`. Each accession records the
blob OID and a repository identity (its root-commit OID — a filesystem path
would name a private project inside a stored page) so an audit can challenge it.

**FP gate status after this change — unchanged, and deliberately so.**

| | |
| --- | --- |
| Mechanism | IMPLEMENTED, experimentally validated (round-trip blocks without provenance, clears with it, private material still blocks) |
| Legacy corpus | INVALID for the v2 criterion — none of the ~4,400 existing pages carries a class, and Global Constraint 7 forbids upgrading them retroactively |
| Prior 11/200 | remains historically valid for the provenance-blind setup it measured |
| v2 FP gate | **NOT YET MEASURED** — a qualifying post-provenance evaluation corpus does not exist yet |

### Scan-side sink provenance — added 2026-09-17

**The sequence, because the conclusion depends on it:**

1. Provenance-blind legacy measurement: **11/200**, every one an ingestion
   round-trip. Invalid as measured.
2. Provenance-aware diagnostic attempt A: **4/200**, **3/199** independent.
   FAIL. The round-trip class is gone.
3. Characterisation **rejected** the two obvious explanations. Byte entropy is
   inverted — the surviving false positives scored 4.74 and 4.21 bits/byte
   against 4.02 for genuine protected prose, so an entropy filter would suppress
   secrets and keep templates. Protected-origin frequency never fires — each
   span appeared in 1 of 300 origins.
4. Every remaining genuine-looking match was a span **already committed to this
   repository's canonical branch**.
5. Diagnosis changed: this is missing scan-side provenance, **not** evidence
   that `T = 64` is wrong. `T` is unchanged.

**The invariant.** A matched span is not a NEW disclosure when those exact bytes
were present in the accepted protected-repository sink **before the candidate
operation began**.

- **Match-level, not file-level.** A changed file carries old public spans and
  new private ones; exempting the whole candidate would make "append a secret to
  a public file" a bypass.
- **The candidate cannot prove itself.** Evidence comes from a baseline captured
  beforehand from accepted refs only. The working tree, index, candidate commit,
  `refs/stash`, `refs/original/*`, scratch refs and foreign repositories are all
  excluded — the ingestion allowlist, applied to the other side.
- **Restricted to `PRIVATE -> PUBLIC_ARTIFACT`**, the same pair D3 may exempt.
  INSTRUMENT is never exempted: a gold item whose words appear in the sink still
  matches its own answer and still contaminates.

**Relationship to Global Constraint 7 — a real tension, resolved by transience.**
GC7 forbids seeding the trusted public corpus from "everything currently
tracked", because a leak already in the tree would be blessed **permanently**.
This baseline is never acceded: it is recomputed per operation and never written
to `public_origins`. Remove the text from the canonical branch and the exemption
disappears with it, which is the opposite of the permanence GC7 exists to
prevent. The two rules therefore diverge on a deleted public file — a recorded
accession survives (it is evidence of what was true when made), a baseline does
not (it is a question asked fresh). Both behaviours are pinned by tests.

**Stated bounds.**

- The baseline is the **tip trees** of accepted refs, not all history. Content
  deleted from the canonical branch stops being evidence. Conservative in the
  safe direction, and bounded.
- A span **straddling** the public/new boundary by even one byte is not exempt,
  so a benign reformat can still block. The principled fix is residual
  subtraction — strip the public parts and block only if what remains reaches
  `T` — which D3 promises at span level and has the same edge. Deliberately not
  bundled with this change.

**Attempt A is preserved, and is not the operational gate.** All 200 of its
artefacts were already-committed blobs, so the corpus does not model candidate
commit blobs. The operational corpus must contain blobs that are NOT reachable
from the baseline: modified existing files and genuinely new files. Attempt A is
kept as a regression corpus for sink-publicity behaviour — its already-public
matches are expected to disappear, and that result must not be reported as the
v2 FP score.

### Verdicts — recall and the operational FP gate, 2026-09-17

Both gates are now measured against frozen corpora. **`T = 64` is unchanged, and
no detector policy was tuned.**

| Gate | Result | Verdict |
| --- | --- | --- |
| **Recall** (11 fixtures, 10 leaks + 1 control) | **10/10 detected, 100%**; control correct; **0 leaks suppressed** by sink provenance | **PASS** |
| **Operational FP** (200 candidate blobs) | **0/200 blocked** — 0/100 modified, 0/100 new | **PASS** (ceiling ≤ 1) |

**Recall — `~/.zurvan/instruments/boundary-recall-v1/`.** Eleven shapes: long
prose, exactly `T`, `T−1` as a control, embedded in benign text, multiple
separated spans, code/config, prose, and four built specifically to attack
match-level sink provenance — public bytes before the private span, after it, on
both sides, and a private span appended to a wholly public file. All four block.
The sink rule suppressed **no** genuine disclosure, which is the property that
makes the FP result below safe to accept.

**Operational FP — `~/.zurvan/instruments/boundary-fp-operational-v1/`.** 100
deterministic benign edits to baseline-public files, plus 100 superseded
historical blobs. **Six candidates (M079, M080, N022, N063, N065, N099) block
WITHOUT the sink baseline and are clean with it** — six exceeds the ceiling of
one, so without scan-side sink provenance this gate fails. That is the
measurement establishing the rule does real work rather than merely looking
principled.

**The first operational attempt returned 33/200 and is recorded as INVALID BY
CONSTRUCTION, not as a detector failure.** Its "new" half was sourced from
`raw/` — the same tree as the protected corpus, which held 254 documents from
one export family — so all 100 candidates were siblings of protected documents,
genuinely shared content with them, and were largely blocked *correctly*. The
corrected half comes from the repository's own git history, independent by
construction because `raw/` has never been committed.

**Stated bound on the 0/200.** The "new" half is superseded versions of tracked
files: real candidate blobs a gate sees on a revert or cherry-pick, but largely
sink-public and therefore the easier case. A corpus of genuinely novel documents
unrelated to both the sink and the protected corpus would be harder and is not
available locally.

**Residual subtraction is NOT implemented, on evidence.** The straddling-span
limitation is real and pinned by a test, but neither frozen corpus produced a
case: 0 false positives, 0 suppressed leaks. Building it now would be designing
against a predicted failure rather than an observed one.

**FP gate status, superseding the earlier lines.**

| | |
| --- | --- |
| Provenance-blind legacy (11/200) | historical; measured a provenance-inverted corpus |
| Diagnostic attempt A (4/200, 3/199) | preserved; **not** the operational gate — all its artefacts were already-committed blobs |
| **Operational v2 (0/200)** | **MEASURED, PASS**, with the bound above |
| **Recall (10/10)** | **MEASURED, PASS** |

### Production and evaluation now share one sink policy — 2026-09-18

**Before.** Scan-side sink provenance existed in scanner semantics but the
production commit gate did not supply the baseline. `boundary_guard.main` called
`gitscan.scan_staged(root, repo)`, the parameter defaulted to `None`, and the
scanner therefore ran with sink provenance disabled on every real commit. The
0/200 operational result was evidence about the designed scanner, not about the
gate that shipped.

**After.** `boundary_guard` captures an accepted-sink baseline once per
invocation and hands it to the same batch session the scanner uses. Production
and evaluation use the same frozen pre-candidate sink semantics, and that
equality is asserted rather than assumed.

**Why the 13 sink contracts did not catch it.** Every one of them called
`scan_bytes` directly. A test that reaches past the caller cannot detect a
caller that fails to pass an argument. The new suite
(`tests/test_boundary_gate_policy.py`) enters through `boundary_guard.main` —
the function a pre-commit hook calls — and one of its tests asserts on the
argument itself, at the seam that actually broke.

**The baseline the gate uses.**

| property | how |
| --- | --- |
| scoped to the protected repository | `provenance.protected_repo_root()`, never the repo being scanned — otherwise `git init && git commit` in a scratch directory manufactures sink evidence |
| accepted refs only | the same allowlist ingestion uses; `refs/stash`, `refs/original/*` and scratch branches excluded |
| captured before any candidate | once per invocation, in `boundary_guard.main`, before `scan_staged` |
| immutable for the batch | one `ScanSession`; candidates are never added to it |
| transient | handed to the scanner and discarded; never written to `public_origins` |
| unavailable from tree/index/candidate | tip trees of refs only, so a staged blob is not evidence about itself |
| fails closed | no protected repo, no accepted refs, or an unreadable one ⇒ empty baseline ⇒ no exemptions |

**Policy equivalence, asserted.** For the same candidates and the same frozen
baseline, `production_commit_gate_verdicts == evaluation_scanner_verdicts`. This
is the invariant whose absence let the two drift while every lower-level suite
stayed green. It is now pinned in three places: a unit test over a mixed staged
set, and a `--via-gate` mode on **both** frozen harnesses which re-runs the
frozen corpus through `boundary_guard` and exits non-zero on disagreement.

**Both frozen gates re-measured through the production path** — the corpora were
not regenerated, reseeded or tuned; only the execution path was added:

| gate | scanner path | production gate path |
| --- | --- | --- |
| Operational FP (200 candidates) | 0/200, 6 suppressed by sink publicity | **0/200, exit code 0** |
| Recall (11 fixtures) | 10/10, controls correct | **10/10, controls correct, 0 missed** |

Recall is the measurement that had to survive this change. A slightly too
generous exemption — or a baseline reader that mis-framed a blob and returned
one giant object — would make every span look already-public, suppress every
leak, and make the false-positive gate look *better*. 10/10 through the shipped
path is what rules that out.

**A limitation this exposed, pinned rather than hidden.** `d1.confirm` expands a
match maximally in both directions. If appended bytes begin with the same byte
the protected source continues with — a trailing blank line, ordinary in
Markdown — the confirmed span reaches one byte past the public blob,
`contains_span` is false, and a benign edit still blocks. Measured here as a
**224-byte span against a 223-byte public blob**. The first draft of the new
test suite hit this by accident and read as a wiring failure; it is now a
deliberate test. Residual subtraction remains unbuilt for the same reason as
before: neither frozen corpus produces a straddling case, so building it would
be designing against a predicted failure. Note that the operational corpus's
edit recipe *does* use a leading newline and still scored 0/200 — so that corpus
does not exercise the straddle either, and the bound is stated rather than
measured away.

**Cost of the baseline, measured separately from the scanner.** Capture reads
every blob in the tip trees. One `git cat-file` per object cost **0.92 s for 100
sink blobs and 3.16 s for this repository's 399** — a fixed per-commit cost that
dwarfed the 0.075 s it takes to scan a single staged file, and would have erased
the batching gain. `git cat-file --batch` reads the whole sink in one process,
framed by declared length so blobs containing NULs, CRLF or no trailing newline
survive intact:

| | per-object | batched |
| --- | ---: | ---: |
| this repository, 399 blobs | 3.16 s | **0.075 s** (42×) |
| benchmark fixture, 100 blobs | 0.92 s | **0.071 s** (13×) |

Gate latency with the baseline wired, against the scanner alone and against the
pre-batching model (11 reps, same fixture):

| staged files | pre-batching | scanner only | **full gate** | gate vs pre-batching |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.071 s | 0.071 s | **0.156 s** | 0.46× |
| 10 | 0.457 s | 0.220 s | **0.299 s** | **1.5×** |
| 50 | 1.980 s | 0.780 s | **0.884 s** | **2.2×** |
| 100 | 4.299 s | 1.495 s | **1.552 s** | **2.8×** |

The 2.6× batching gain is preserved: at 100 files the baseline adds ~4% to the
scanner and the gate still beats the pre-batching path by 2.8× **while
implementing strictly more policy**. The honest exception is the single-file
commit, which goes from 0.071 s to 0.156 s — the baseline is a fixed cost and at
n=1 there is nothing to amortise it against. Peak traced memory rises 2.19 MB →
2.90 MB at 100 files; the extra is the held sink (2.88 MB on this repository).

### Commit-gate batching — one scan session per invocation, 2026-09-18

Performance only. `T = 64`, the source×sink matrix, D1/D2/D3 semantics, the sink
baseline rule and both frozen evaluation corpora are untouched. The measurements
below were taken before and after on the same generated fixture, and every
verdict was compared rather than assumed.

**The old cost model.** `gitscan.scan_staged` called `scan_bytes` once per staged
post-image, and `scan_bytes` opens the store, validates it, reads the config,
loads every origin row and builds a fresh blob cache. None of that depends on
which candidate is being scanned, so the gate paid for it N times. Measured on a
150-document fixture at 100 staged files:

| repeated per staged file | 100 files |
| --- | --- |
| store opens (`sqlite3.connect` + `quick_check` + schema + version check) | 100 |
| `store.validate` (3 aggregates + orphan join + `stat_digest` tree walk) | 100 |
| origin table loads | 100 |
| literal registry loads | 100 |
| HMAC registry-key reads | 100 |
| release-table queries | one per (match, sink) |
| **`store.get_blob` — read + full SHA-256 re-verification** | **4,056** |

The blob reads dominate. `get_blob` re-hashes every blob it returns, deliberately
— a blob store that trusts its own filenames is a filesystem, not an integrity
mechanism — and a per-scan cache threw that proof away between files, so the same
150 protected blobs were read and re-hashed 4,056 times.

**The session invariant.** A `ScanSession` reads protected state, public
accessions, the literal registry, the release table and the accepted sink
baseline **once, before the first candidate**, and never re-reads them. Every
candidate in the batch is answered against exactly the same view.

That is stronger than the per-file path, not weaker. With isolated scans a
mid-commit edit to the protected tree made the gate raise `StaleStateError`
partway down the staged list, so the same staged set got different answers
depending on where the edit landed. Staleness is now deferred to the **next**
gate invocation, which still refuses until a rebuild — never swallowed.

**The intra-batch anti-laundering invariant.** The baseline is frozen and
candidates are never added to it. The obvious optimisation — accumulate what you
have already scanned — would create a laundering primitive that does not
otherwise exist: stage the leak twice, the first copy enters the "already
public" set, the second reads clean, and one `git add` declassifies protected
material. For the same reason match de-duplication stays **per candidate**;
hoisting the `seen` set into the session would drop a genuine leak from the
second file that happens to share a span with the first.

Both were verified by mutation rather than by reading: each bug was written into
`session.py`, the named regression was run, and the test failed. Five mutants,
five caught — hoisted `seen`, accumulated baseline, unshared blob cache,
per-candidate store open, and fail-fast on first block.

**Benchmark methodology.** `scripts/bench_boundary_gate.py`. A disposable fixture
— 150 generated protected documents, one instrument file, a throwaway git repo
of generated candidates, two of which carry a real protected span — built from
fixed seeds so the same bytes are scanned in both modes. It refuses to run
against `~/.zurvan/boundary` or the working tree. Two modes over **one** fixture:
`isolated` (one independent scan per staged blob, the old model, preserved
because `scan_bytes` is now a one-candidate session) and `batch`. Timings are
p50/p95 over 11 repetitions after a discarded warm-up; counters and peak memory
are collected in separate passes so instrumentation never enters a timed one.

Staging must produce a real diff: the first draft re-staged byte-identical
content, `diff --cached` was empty, and it reported 8 ms for 100 files.

**Before / after** (11 reps; the 5-rep run agrees to within 3%):

| staged files | isolated p50 | batch p50 | speed-up | isolated p95 | batch p95 | per file |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 0.074 s | 0.074 s | **1.0×** | 0.082 s | 0.076 s | 74 ms → 74 ms |
| 10 | 0.486 s | 0.231 s | **2.1×** | 0.544 s | 0.259 s | 48.6 ms → 23.1 ms |
| 50 | 2.092 s | 0.811 s | **2.6×** | 2.175 s | 1.022 s | 41.8 ms → 16.2 ms |
| 100 | 4.236 s | 1.604 s | **2.6×** | 4.360 s | 1.958 s | 42.4 ms → 16.0 ms |

Expensive initialisations, at 100 files: store opens **100 → 1**, validations
**100 → 1**, `stat_digest` tree walks **100 → 1**, registry-key reads
**100 → 1**, blob reads **4,056 → 150**. Blob reads saturate at 150 — the number
of protected blobs — so each is read and verified at most once per batch, which
is the floor.

There is no speed-up at one staged file, and the table says so. The work is
identical; only its amortisation changed.

**Stated cost.** Peak traced memory at 100 files rises 1.29 MB → 2.19 MB. The
shared blob cache is bounded by the size of the protected corpus, not by the
candidate count — the same 150 blobs at 50 files as at 100 — so on the real
11.2 MB corpus the ceiling is roughly that, once, for the life of one commit.

**Correctness results.** Blocked counts were identical in all three runs
(pre-change isolated, post-change isolated, post-change batch) at every size:
1/1, 2/10, 3/50, 3/100. Batching tests **17/17**; all Boundary suites **95/95**;
`boundary_redteam` **27/27**; frozen recall **10/10** with controls correct and
zero leaks suppressed; frozen operational FP **0/200** with the same **6**
candidates suppressed by sink publicity; `pytest` **900 passed / 4 skipped /
0 failed**.

**Open item this exposed, not fixed here.** The commit gate never passes a
baseline: `boundary_guard.main` → `gitscan.scan_staged` supplies `baseline=None`,
so scan-side sink provenance — implemented, tested and measured at 0/200 — is
**not active in the real gate**. `scan_staged` now accepts a `baseline`
parameter, so wiring it is a one-line caller change, but doing so would change
live verdicts and is a policy decision, not a performance one.

### D1 degenerate content — added 2026-09-16

T counts **content**, not bytes. A 270-byte rule of box-drawing characters is
three bytes repeated ninety times; it identifies no origin.

- k-grams that are one unit of period ≤ 8 repeated are not fingerprinted, on
  both the index and the query side. Peak anchors for one file: 63,975 → 809;
  2.4% of fingerprints dropped.
- A confirmed span is accepted only if its **informative length** — length with
  every run of ≥ 16 bytes at period ≤ 8 collapsed to one unit — reaches T.

Stated bound: a "secret" that is itself a short unit repeated (`AAAA…`) is not
protected by D1. It carries at most 8 bytes of information and cannot identify
an origin; D2 literal registration covers such a string if it matters.

The rule must cut both ways, and gauntlet rows 18–20 pin both directions:
padding cannot **manufacture** a match, padding cannot **conceal** one, and
protected prose buried between two long runs stays reachable. `D1_VERSION` is
bumped to `d1-winnow-v2-informative`, so a store or a release from before this
change fails closed instead of being silently reinterpreted.

**`scan-diff` semantics.** Scanning diff hunks alone is unsafe: a protected span
can be assembled across a changed/unchanged boundary, across two hunks, or moved
by a rename with no textual diff. Commit gating therefore scans the **complete
staged post-image blob of every changed file**, and pre-push scans the resulting
**commit range trees**, never the working tree.

## 10. CLI and gate integration

```
zurvan boundary build         fingerprint protected sources
zurvan boundary status        corpus/index health
zurvan boundary scan FILE     test one candidate artefact
zurvan boundary scan-diff     scan staged/working changes
zurvan boundary explain HIT   local provenance explanation
zurvan boundary release       declassify an exact span
zurvan boundary audit         re-scan everything currently public
```

`scan-diff` joins the existing fail-closed gate alongside `pytest`,
`public_repo_guard.py` and `git diff --check`. Later: `publish export`, MCP
writes, and the indexer's write path (the `RETRIEVAL_CORPUS` sink).

## 11. Declassification

Controlled release exists from day one, because a scanner that cannot be
overridden legitimately gets disabled illegitimately. A release record stores an
opaque id, the span digest, **the source class and origin id**, **the permitted
sink(s)**, **the detector normalisation version**, a reason and a timestamp — and
**never** the private source path, title, or the quoted text. Binding all five is
what stops a release for sink A from declassifying sink B, or the same bytes from
a different protected origin.

## 12. What v1 does NOT detect

Stated plainly so the guarantee means something: paraphrase, summarisation that
reveals a private fact without copying, encoding (base64/ROT), character
substitution, screenshots, deliberate fragmentation below `T`, and causal
influence without textual transfer.

> **Boundary v1 provides deterministic prevention of undeclared verbatim
> protected-to-forbidden-sink text transfer.** Narrow claim, hard guarantee.

## 13. Ladder

```
V1  exact verbatim, two policies               ← this spec
V2  normalisation (whitespace, punctuation, Unicode); human-operator claim
V3  near-copy (token shingles, edit distance)
V4  semantic (paraphrase, extracted facts)
V5  hostile-agent containment (capabilities, runtime IFC, isolated sinks)
```

## 14. Risks

| Risk | Mitigation |
| --- | --- |
| FP fatigue → gate disabled | D3 + declassification + measured FP rate as an acceptance gate |
| `T` overfitted to 7 leaks | `T` parameterised; benchmark reports the whole curve |
| Laundering via a public-marked source | D3 subtracts spans, never sources |
| The registry becomes the leak | Cleartext local-only; tracked side sees hashes and opaque ids |
| Scanner trusted but untested | Red-team set frozen; both outcomes recorded |

## 15. Scorecard (v1, honest)

**v1 is closed as of 2026-09-18.** The threat model, the stated
bounds and every measured number live in
[docs/boundary-v1-closure.md](../../boundary-v1-closure.md).

| Axis | Score | What moves it higher |
| --- | --- | --- |
| Falsifiability | 9/10 | Already attackable: hand it a corpus and an artefact |
| Real-wound fit | 8/10 | Seven measured leaks, one session |
| Coverage of leak kinds | 4/10 | V2 normalisation; paraphrase is out of reach by construction |
| FP discipline | 6/10 | A real public-origin registry, not a hand-maintained list |
| Novelty vs prior art | 7/10 | Egress-time content lineage; the INSTRUMENT class is unclaimed |

## 15b. Sub-project F — Instrument Seal (proposed, not specced)

Boundary prevents the flow; it cannot prove to anyone else that the flow did not
happen. Simurgh's doctrine supplies the missing half — **VCA: machine-checkable,
offline-reproducible, "without trusting any vendor, model, or lab."**

> **Instrument Seal.** A signed, offline-recomputable attestation that corpus C
> at digest `D_c` contained ZERO verbatim echoes of instrument I at digest `D_i`
> at time T under parameters `(T_bytes, normalisation)` — checkable by a third
> party given neither the corpus nor the instrument.

**Status: RESEARCH IOU, not a V1 deliverable.** The claim "a contaminated
evaluator cannot forge it" is currently **too strong**. A third party can
recompute an intersection over supplied commitments, but nothing yet binds those
commitments to the *complete real corpus* — an evaluator could attest over a
subset that excludes the contamination. Closing this needs a completeness/binding
construction (authenticated precommitment, TEE, ZK, or a trusted witness), not
merely signed keyed k-gram sets. Private-set-intersection cardinality is
established territory, but truthful-input commitment is a separate, unsolved
requirement here. Until that exists, Instrument Seal is a research direction. Once the seal is cheap, publishing retrieval metrics
*without* one becomes the signal (make the absence auditable).

**The number it publishes — Instrument Exposure.** Fraction of eval items with a
verbatim echo in the corpus under test. Measured on this project:

| | |
| --- | --- |
| Exposure now | **0/50** over 184,002 chunks |
| Exposure on the morning of 2026-09-15 | **1/50** (gold #44, in two indexed documents) |
| Measured cost of that one item | Top-1 **0.70 → 0.68**; expected answer rank 1 → 3 |

**Anti-gaming disclaimer, stated in the same breath.** A seal proves *verbatim*
separation only. It says nothing about paraphrase, about semantic leakage, or
about whether a model saw the instrument in pretraining. "Blind" is bounded: keyed
k-gram fingerprints resist casual recovery but leak under dictionary attack on
short, low-entropy spans, so a seal discloses no more than a hashed k-gram set
discloses — which is not nothing. Exposure is a corpus property, never a claim
about a model.

**Dependency:** shares D1's fingerprint index with Boundary, and depends on §2.3
being fixed first — an attestation over a store whose log can assert a merge that
never happened is an attestation over a lie.

## 15c. Required migration — Boundary currently fails against this repository

**`eval/search_gold.jsonl` is tracked in git today.** Under §4, that is an
`INSTRUMENT → PUBLIC_ARTIFACT` transfer, which Boundary blocks. The spec is
therefore not implementable against the current tree without this migration, and
shipping the scanner first would mean its first honest run blocks the repository
it lives in.

| Move | From | To |
| --- | --- | --- |
| Instrument cleartext | `eval/search_gold.jsonl` (tracked) | `~/.zurvan/instruments/` (local) |
| Tracked remnant | — | schema + gold-set digest + opaque per-query ids |
| Protected sources, literals, releases, fingerprints | — | `~/.zurvan/boundary/` (local) |

**What does NOT move: `wiki/`.** An earlier IOU floated relocating the whole
knowledge base. That would rewrite a foundational property of this project —
`wiki/` is the tracked Markdown knowledge layer and the Obsidian vault, with
SQLite rebuildable from it — to implement a sub-project. Boundary moves the
*protected corpora and its own state* out of git, then enforces classification at
the transitions into tracked or indexed Markdown. Sub-project E does not get to
eat the architecture it is protecting.

## 16. Open questions (IOUs)

1. **Default `T`** — empirical, from the frozen benchmark after D3 lands.
2. **What populates the trusted public corpus** — repo docs and skills are
   obvious; published papers ingested as sources are not, and mis-marking one is
   a laundering vector.
3. **`RETRIEVAL_CORPUS` enforcement point** — the indexer's write path or a
   pre-index scan; the first is stronger, the second is cheaper.
4. ~~**Migration** — move `wiki/` out of the repo.~~ **RETIRED 2026-09-16:**
   superseded by §15c. Moving `wiki/` would rewrite the project's foundational
   architecture to serve a sub-project. Only the protected corpora, the instrument
   cleartext and Boundary's own state leave git.
