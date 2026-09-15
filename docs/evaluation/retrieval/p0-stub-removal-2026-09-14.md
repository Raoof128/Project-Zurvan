# P0 — machine stub removal: frozen experiment report

**Date:** 2026-09-14 (Australia/Sydney)
**Commit (pre-change):** acb252c
**Decision: ACCEPT on integrity grounds. Retrieval prediction UNSUPPORTED.**

## What changed

Removed 7,622 machine-generated placeholder pages — 3,811 `Claim-*` stubs whose
body read *"Evidence missing? No, cited from \<source\>"*, and 3,811
`AutoConcept-*` stubs reading *"Generated from \<source\>"*. Deleted the
generator (`create_stub_concept_and_claim`) so ingest cannot recreate them, and
replaced the signal honestly with `extraction_status: unextracted` in the source
page's own frontmatter.

Selection was by **structure, never filename**: a page with a `type:` field, an
`## Evidence` section, or more than one body line is AUTHORED and out of scope.
This mattered — 5 hand-authored concepts (`corrigibility`, `hard-constraints`,
`honesty-standards`, `the-1000-users-heuristic`, and one more) live in the same
directory as 3,811 stubs and a filename rule would have destroyed them.

## Measurements

| | Before | After | Delta |
| --- | --- | --- | --- |
| Indexed chunks | 199,269 | 184,068 | **−15,201 (−7.63%)** |
| Graph nodes | 12,090 | 4,482 | **−63%** |
| Graph edges | 11,455 | 3,917 | **−66%** |
| Top-1 | 0.6800 | 0.6800 | **+0.0000** |
| Top-3 | 0.7200 | 0.7200 | **+0.0000** |
| MRR | 0.7057 | 0.7057 | **+0.0000** |
| Failures | 14 | 14 | identical set; 0 fixed, 0 newly broken |

Dependency versions unchanged. Embeddings: `sentence_transformers` /
`all-MiniLM-L6-v2`. Rebuild reused 183,999 vectors, computed 69.

## The prediction, and why it failed

The stated prediction was: *"removing 15,244 information-free chunks should
improve the 68/72/0.71 baseline, not just leave it unchanged."*

**Unsupported.** Retrieval quality did not move by any amount.

The supporting evidence was **methodologically bad and is retracted**. The claim
that stubs "displace real evidence" rested on a search for *"evidence cited from
source"* returning ten stubs in the top ten — but that query is nearly verbatim
the stub's own body text. It was constructed from the artifact it was indicting,
so the match proved only that string matching works. The 50-query gold set,
written for other purposes, shows the stubs never reached the top-3 of any real
query.

**Lesson, recorded so it is not repeated:** a query built from the text of the
thing under test is not evidence about that thing. Demonstrations must use
queries that existed before the hypothesis.

## Why this is still ACCEPT

The justification approved beforehand was correctness, not retrieval: a system
whose distinguishing property is that claims cannot be fabricated was shipping
3,811 assertions of evidence it did not have, outnumbering real claims 15:1 in
the same directory. That stands independently of any retrieval effect.

The unpredicted 63% graph reduction is a real secondary gain — the graph was
mostly scaffolding nodes — but it was not foreseen and is not claimed as a win
that was designed for.

## Integrity gate — all pass

```
claim pages asserting evidence without an Evidence section : 0
machine stubs masquerading as extracted objects            : 0
ingest still has a stub generator                          : False
new files in claims/concepts after a fresh ingest          : 0
source page declares                                       : extraction_status: unextracted
```

## Consequence for sub-project A

**68/72/0.7057 is retired as A's working baseline.** The post-P0 numbers are
numerically identical but were measured on a different corpus (184,068 chunks),
and that is the corpus A's Step 0 and Phase 1 invariance gate must use.

New canonical baseline: **184,068 chunks · Top-1 0.68 · Top-3 0.72 ·
MRR 0.7057 · 14 failures** (IDs recorded in `eval_after_p0.json`).
