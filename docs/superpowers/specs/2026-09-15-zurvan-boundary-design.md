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

**Open item (blocks the FP gate, design-gated — not implemented here).** Wire
provenance into ingestion: a page ingested from a tracked repo file or a public
URL is recorded with a PUBLIC origin class and acceded to D3 at ingest time.
Seeding D3 from "everything currently tracked" remains forbidden (Global
Constraint 7) — that is the laundering hole, and this is the opposite: recording
what was already public *at the moment it entered*, with its provenance.

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
