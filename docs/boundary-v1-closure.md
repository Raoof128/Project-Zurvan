# Zurvan Boundary v1 — closure record

**Closed 2026-09-18.** Design spec:
[docs/superpowers/specs/2026-09-15-zurvan-boundary-design.md](superpowers/specs/2026-09-15-zurvan-boundary-design.md).

This record states what v1 is, what it was measured at, and — at least as
importantly — what it does not do. It exists so that a later reader does not
have to reconstruct the bounds from a changelog, and so that the next phase
starts from a stated baseline rather than an assumed one.

Every number here was produced by a command that was run and read. Where a
measurement is bounded, the bound is next to the number, not in a footnote.

---

## 1. Threat model

v1 defends against the **careless authorised agent**: a human or an AI with
legitimate write access to this repository who is about to move protected bytes
into a place they should not go — usually by quoting, pasting, summarising with
verbatim excerpts, or committing a file they did not realise was derived from
protected material.

It does **not** defend against a **hostile** agent with write access. That is a
deliberate scope decision, not an oversight, and several mechanisms are
explicitly weaker than they could be because of it (see §6).

The wound is real and was measured before anything was built: seven verbatim
leaks in one session on 2026-09-15, including an evaluation item quoted into a
page that was never tracked and never pushed — contamination with zero
publication, which no confidentiality-shaped model describes. That last case is
why the model is organised around **sinks** rather than around "public".

---

## 2. What v1 protects

**Source classes:** `PRIVATE`, `INSTRUMENT`, `PUBLIC`.
**Sink classes:** `PUBLIC_ARTIFACT`, `RETRIEVAL_CORPUS`, `EVAL_OUTPUT`,
`PRIVATE_LOCAL`.

Blocked pairs — deny overrides allow, and unknown input fails closed (a
misspelled class raises rather than resolving to "allow"):

| source | sink | why |
| --- | --- | --- |
| `PRIVATE` | `PUBLIC_ARTIFACT` | confidentiality |
| `INSTRUMENT` | `PUBLIC_ARTIFACT` | the retrieval instrument must not leak |
| `INSTRUMENT` | `RETRIEVAL_CORPUS` | **contamination without publication** — the 2026-09-15 wound |

**Detectors.**

- **D1 — long-span verbatim matcher.** Winnowed k-gram fingerprints (k=32,
  w=33) locate candidates; the verdict always comes from a byte-exact
  comparison against the original source, so a hash collision can never produce
  a false block. `T = k + w − 1 = 64` bytes of **informative** content.
- **D2 — salient-literal registry.** Instrument items are short by nature
  (median 49 B against T=64, 49 of 50 below T), so D1 cannot reach 98% of the
  instrument by construction. D2 registration is the only thing protecting it.
- **D3 — known-public origin resolver.** Subtraction at the matched-span level,
  never by exempting a whole source, because exempting a source is a laundering
  primitive.

**Where it runs.** The commit gate scans the **complete staged post-image** of
every changed path — never diff hunks, because a protected span can be assembled
across a changed/unchanged boundary, split across two hunks, or moved by a
rename that produces no textual diff at all.

---

## 3. Provenance model (ingestion side)

Three classes, because "public" is not one thing:

`PRIVATE` → `PUBLIC_DECLARED` → `PUBLIC_VERIFIED`

Only `PUBLIC_VERIFIED` is acceded to D3 and can exempt a span.
`PUBLIC_DECLARED` is recorded for audit and **never honoured** — an unverified
claim that grants an exemption *is* the laundering primitive.

`PUBLIC_VERIFIED` means **reachable from an allowlisted ref in the repository
this gate protects**. Three weaker definitions were implemented, demonstrated
exploitable, and rejected:

| rejected definition | the exploit, measured |
| --- | --- |
| `git cat-file -e` (object exists) | `git hash-object -w` satisfies it with no commit, ref or intent |
| `git log --all` (reachable from any ref) | a stashed file and a scratch-branch file were both found — `git stash` would launder a private document |
| presence in the index | `git add` is local, unshared and reversible; stage, accede, unstage, exempt forever |

A **foreign repository** classes `DECLARED`, never `VERIFIED`: `git init && git
commit` in a scratch directory is not a publication event.

An accession is **historical evidence** and survives a later reset or rebase.
Stated cost: bytes acceded from a rewritten commit stay exempt until explicitly
revoked. Revocation belongs in the declassification ledger, not in `git rebase`.

**Global Constraint 7** holds: the public corpus is never seeded from
"everything currently tracked". The ~4,400 existing pages were **not**
retroactively classified.

---

## 4. Sink-baseline model (scan side)

A matched span is not a **new** disclosure when those exact bytes were already
in the accepted sink **before the candidate operation began**.

| property | guarantee |
| --- | --- |
| match-level | exemption applies span by span; appending a secret to a published file still blocks |
| candidate cannot prove itself | captured from tip trees of allowlisted refs — tree, index, candidate commit, stash, scratch refs all excluded |
| scoped to the protected repository | not the repo being scanned, or any scratch repo becomes sink evidence |
| frozen per invocation | one snapshot for the whole staged batch; candidates are never added to it |
| transient, never acceded | recomputed each time and never written to `public_origins` — delete the text from the canonical branch and the exemption vanishes with it |
| fails closed | no repo, no accepted refs, unreadable ⇒ empty baseline ⇒ no exemptions |

This satisfies Global Constraint 7 rather than amending it. An accession is
permanent, so seeding it from the tree would bless a leak forever; a
per-operation baseline has the opposite property.

**Restricted to `PRIVATE → PUBLIC_ARTIFACT`.** `INSTRUMENT` is never exempted
here for the same reason D3 never exempts it: an evaluation item whose words
also appear publicly still matches its own answer and still contaminates a
measurement.

Three alternative explanations for the false positives were measured and
rejected **before** this rule was written:

- **byte entropy** — inverted. The false positives scored 4.74 and 4.21
  bits/byte against **4.02** for genuine protected prose, so an entropy filter
  would suppress secrets and keep templates.
- **protected-origin frequency** — each span appeared in **1 of 300** origins,
  so "many origins, no information" never fires.
- **raising `T`** — fixes neither, and tunes a magic number against the exam.

---

## 5. Measured results

**`T = 64`, unchanged. No detector threshold or policy was tuned at any point.**

| gate | result | ceiling | path |
| --- | --- | --- | --- |
| **Recall** (11 fixtures, 10 leaks + 1 control) | **10/10, 100%**, control correct, **0 leaks suppressed by the sink baseline** | — | scanner **and** production gate |
| **Operational false positives** (200 candidate blobs) | **0/200** — 0/100 modified, 0/100 new | ≤ 1/200 | scanner **and** production gate |

Both frozen corpora were re-run through `boundary_guard` — the real commit gate
— via `--via-gate` on each harness, which exits non-zero if the gate and the
scanner disagree. The corpora were not regenerated, reseeded or tuned; only the
execution path was added.

**The decisive figure is not the zero.** Six candidates (`M079 M080 N022 N063
N065 N099`) block **without** the sink baseline and are clean with it. Six
exceeds the ceiling of one, so without scan-side sink provenance this gate
fails. That is the measurement showing the rule does real work.

**Numbers that are NOT results, recorded so they are not mistaken for one:**

| number | what it actually is |
| --- | --- |
| 11/200 | provenance-blind legacy; measured a provenance-inverted corpus |
| 1/200 | a 40-document pilot — **underpowered**; the same frozen 200 give 4/200 against 300 documents |
| 4/200 | diagnostic attempt A — all its artefacts were already-committed blobs, which a commit gate never sees |
| 33/200 | **invalid by construction** — the "new" half were siblings of the protected corpus and were largely blocked *correctly* |

**Commit-gate performance**, 150-document fixture, 11 reps, same fixture
throughout:

| staged files | pre-batching | scanner only | full gate (with baseline) |
| ---: | ---: | ---: | ---: |
| 1 | 0.071 s | 0.071 s | 0.156 s |
| 10 | 0.457 s | 0.220 s | 0.299 s |
| 50 | 1.980 s | 0.780 s | 0.884 s |
| 100 | 4.299 s | 1.495 s | **1.552 s (2.8×)** |

Expensive per-invocation work at 100 files: store opens **100 → 1**, validations
**100 → 1**, blob reads **4,056 → 150** (the floor — one verified read per
protected blob). Baseline capture **3.16 s → 0.075 s** for this repository's 399
sink blobs. Peak traced memory 1.29 MB → 2.90 MB.

---

## 6. What v1 explicitly does NOT protect

Stated bounds, each one tested or measured rather than asserted:

1. **A hostile agent.** A total-preserving delete-and-insert in the fingerprint
   index passes the cheap per-scan validation and is caught only by deep
   verification. The staleness probe uses size + mtime, which a careless agent
   cannot defeat and a deliberate one can.
2. **Anything below `T = 64` informative bytes** that is not registered as a D2
   literal. This is a cliff, not a gradient.
3. **A "secret" that is a short unit repeated** (`AAAA…`). It carries at most 8
   bytes of information and cannot identify an origin.
4. **Paraphrase, translation, summary.** Out of reach by construction — D1 is a
   verbatim matcher.
5. **Encoding, fragmentation, substitution, screenshots, transformed leaks.**
   Not attempted in v1. This is the deferred adversarial-evasion work (§7).
6. **Straddling spans.** If appended bytes begin with the byte the protected
   source continues with, the confirmed span reaches past the public blob and a
   benign edit still blocks. Measured: 224-byte span against a 223-byte public
   blob. Residual subtraction is the principled fix and is deliberately unbuilt
   — neither frozen corpus produces the case, and the operational corpus's edit
   recipe does not exercise it either.
7. **Content deleted from the canonical branch** stops being sink evidence. The
   baseline is tip trees, not history. Conservative in the safe direction.
8. **Accessions survive history rewrites** until explicitly revoked.
9. **The "new file" half of the FP corpus** is superseded versions of tracked
   files — real candidate blobs a gate sees on a revert or cherry-pick, but
   largely sink-public and therefore the easier case. A corpus of genuinely
   novel documents independent of both the sink and the protected corpus would
   be harder and was not available locally.
10. **`PUBLIC_DECLARED` has no promotion path.** An audit that verifies a
    declared URL cannot record that finding.
11. **R3 (MCP/tool-call provenance events) is not built.** Do not claim complete
    provenance.
12. **Boundary is opt-in.** With no store it reports `NOT CONFIGURED` and exits
    0. A gate that blocks every commit before setup gets bypassed, then removed.

---

## 7. Deferred: adversarial evasion (the next phase)

Everything above assumes the careless agent. The question v1 does not answer is:

> Can an agent that *wants* to get protected bytes past the gate do so?

Known-unhandled classes, none of which v1 attempts: encoding and re-encoding
(base64, URL, HTML entities, homoglyphs), fragmentation across commits or files,
whitespace and punctuation substitution, case and Unicode normalisation attacks
beyond D2's casefold, re-ordering, screenshots and other non-text carriers, and
generation-time paraphrase.

This is a **new design phase with its own threat model**, not an extension of
v1. It should start by measuring how far the current detectors actually fall —
a red-team corpus of transformed leaks — before any mechanism is chosen, for the
same reason the recall corpus had to exist before any detector policy was
selected.

---

## 8. Reproducing these numbers

```bash
PYTHONPATH=. python scripts/eval_boundary_recall.py     --eval-root <tmp> --via-gate
PYTHONPATH=. python scripts/eval_boundary_candidates.py --eval-root <tmp> --via-gate
PYTHONPATH=. python scripts/boundary_redteam.py                  # 27/27
PYTHONPATH=. python scripts/bench_boundary_gate.py --mode all --reps 11
PYTHONPATH=. python -m pytest -q                                 # 916 passed / 4 skipped
bash scripts/check.sh                                            # 24 gates
```

Frozen corpora live in `~/.zurvan/instruments/boundary-{recall-v1,fp-operational-v1}/`
and never in git; `eval/boundary_*.manifest.json` carries digests only.

**Do not regenerate, reseed, or tune against a frozen corpus.** If a criterion
turns out to measure the wrong thing, mark it invalid as a metric — never
passed — and replace it *prospectively*.
