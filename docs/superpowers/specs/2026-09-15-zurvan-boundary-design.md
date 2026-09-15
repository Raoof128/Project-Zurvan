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
other side of the enclave. Self-evaluation contamination is structurally outside
their design, and it is the only kind Zurvan can suffer.

Adjacent work confirms the shape of the gap rather than closing it: DyePack
proves contamination by planting backdoors *in the benchmark*; Oren/Ni give
provable false-positive control via canonical-ordering tests; MemAudit audits
poisoned agent memory *post-hoc*; OWASP's Agent Memory Guard screens memory
reads and writes with *heuristic detectors* and no byte-exact provenance. All
detect after the fact, in the model or the memory. None prevents the instrument
from entering the corpus, and none can be checked by a third party who is not
given the corpus.

Positioning sentence, scoped and falsifiable: **no matching prior pattern in this
project's current source map prevents self-evaluation contamination at the corpus
boundary, or proves its absence without disclosing the corpus.**

### 2.3 Leak #7: the log asserting merges that never happened

`tests/test_wiki_merge.py` seeds an entity named after a real researcher. Before
the 2026-09-15 isolation fix it wrote five `merge | <entity> | 1 sources` entries
into the live `wiki/log.md` while the page itself went to a temp directory, so
`wiki/entities/` is empty. The audit log therefore asserts five ingests whose
content was never in the store.

This is a distinct failure class from the other six. Those put protected content
where it did not belong; this one made the provenance record **state something
false**. A store whose log can assert a merge that never happened cannot be the
evidence base for any attestation built on it — which makes it a prerequisite,
not a footnote.

### 2.4 Prior art at rest and in flight (unchanged)

Encrypted agent-memory vaults (Perseus Vault, AgentVault, CtxVault) and
capability-sealed secret mediation (CapSeal) protect data **at rest** and gate
**credentials**. Information-flow work for agents — APPA's permissions algebra,
*Ghost in the Agent*, AgentFlow's per-object taint — tracks influence **inside an
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

**The v1 policy matrix.** Only the two BLOCK cells are formally guaranteed:

| source → sink | PUBLIC_ARTIFACT | RETRIEVAL_CORPUS | EVAL_OUTPUT | PRIVATE_LOCAL |
| --- | --- | --- | --- | --- |
| `PRIVATE` | **BLOCK** | allow by policy | warn | allow |
| `INSTRUMENT` | **BLOCK** | **BLOCK** | warn | allow |
| `PUBLIC` | allow | allow | allow | allow |

Leak #1 is the `INSTRUMENT → RETRIEVAL_CORPUS` cell. Leaks #2–#6 are
`→ PUBLIC_ARTIFACT`.

## 5. The invariant

> **No sink receives an undeclassified verbatim span of ≥ T bytes whose matched
> span has a source class forbidden for that sink and no approved public origin,
> nor any salient literal forbidden for that sink.**

"Protected sink" would read two ways — the sink is not what is protected. The
forbidden-ness is a property of the (source class, sink) pair in §4's matrix, so
the invariant names the pair.

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

D3 is first-class because without it D1 is unusable. Measured on real corpora
(§8), the private store contains ingested copies of the project's **own public
documents**, so a naive gate blocks a commit for quoting the repo's README.

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

Acceptance for v1: **7/7 recall**, a stated FP rate, and p95 scan latency under
the threshold that keeps the gate from being disabled — measured, not asserted.

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
opaque id, the span hash, the scope, a reason and a timestamp — and **never** the
private source path, title, or the quoted text.

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

An honest evaluator satisfies it for free by running the check; a contaminated
one cannot forge it, because the verifier recomputes the intersection over keyed
k-gram fingerprints. Once the seal is cheap, publishing retrieval metrics
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

## 16. Open questions (IOUs)

1. **Default `T`** — empirical, from the frozen benchmark after D3 lands.
2. **What populates the trusted public corpus** — repo docs and skills are
   obvious; published papers ingested as sources are not, and mis-marking one is
   a laundering vector.
3. **`RETRIEVAL_CORPUS` enforcement point** — the indexer's write path or a
   pre-index scan; the first is stronger, the second is cheaper.
4. **Migration** — `wiki/` currently lives in the repo; moving the store to
   `~/.zurvan/boundary/` touches ~184k indexed chunks and every `wiki/...` path
   in the gold set and the frozen reports.
