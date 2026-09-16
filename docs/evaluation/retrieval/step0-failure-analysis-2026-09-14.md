# Step 0 / Step 0B — failure anatomy: frozen experiment report

**Date:** 2026-09-14 (Australia/Sydney) · **Decision: BUILD 2B (at the threshold)**

Step 0 asked one question before any dependency was installed: **could a
reranker fix these failures at all?** A reranker only reorders what retrieval
already produced, so a document that never enters the candidate pool is
unreachable by it.

## Step 0 — on the post-P0 corpus (184,068 chunks)

```
RERANKABILITY RATE: 7/14
  fusion_ranking_miss    7
  gold_annotation_issue  4     <- see below; this label was WRONG
  candidate_miss         3
DECISION: defer_2b_until_after_2a   (5-7 band)
```

**Step 0 redirected the project.** The four "annotation" failures were not
annotation problems: the gold expected `README.md`, `AGENTS.md` and `CLAUDE.md`,
and all three had **zero chunks in the index**. Zurvan's own overview, rules and
quickstart had never been searchable. The indexer walked `wiki/` and `docs/`
only.

Those were **absence failures** — unreachable by reranking, fusion weights,
better embeddings, contextual chunks, `sqlite-vec` or KNN, because none of them
can rank a document that is not in the index. One missing line of scanner
coverage accounted for **4 of 14** observed failures.

**Classification corrected.** `gold_annotation_issue` blamed the evaluation set
for a corpus bug. The gold was right. A new class, `index_coverage_miss`, now
carries these; `gold_annotation_issue` remains for a gold entry that is
genuinely wrong.

## Phase 0A — root-document coverage

`ROOT_INDEX_FILES = ("README.md", "AGENTS.md", "CLAUDE.md")` — an explicit
allowlist, never a root glob, so a temporary report or future private note at
the repo root is never swept in.

| Gate | Result |
| --- | --- |
| Coverage | README 43 · AGENTS 23 · CLAUDE 6 chunks. Only those three root files indexed. **PASS** |
| Safety | `raw/` 0 chunks · `wiki/traces/` 0 chunks · guard passes. **PASS** |
| Retrieval | Top-1 0.68 -> **0.70** · Top-3 0.72 -> **0.74** · MRR 0.7057 -> **0.7297** · failures 14 -> 13 · **0 previously-passing queries broke** |

Acceptance did not require improvement — 0A is a correctness fix, as P0 was.

**Coverage was necessary but not sufficient: only 1 of the 4 recovered.**

| Coverage failure (by instrument INDEX; see the egress rule) | Outcome after 0A |
| --- | --- |
| rule about pushing to the public remote | **PASSES** |
| commands before saying a change is done | `fusion_ranking_miss`, fused rank **5** — just outside top-3 |
| why claim evidence must be quoted verbatim | `candidate_miss` — indexed, never retrieved |
| gold item #48 | `candidate_miss` — indexed, never retrieved |

Two documents are now in the corpus and still never reach the top-50. That is a
chunking/recall problem (Phase 2A), not a ranking one — and it was invisible
while they were absent entirely.

## Step 0B — recomputed fresh on the complete corpus (184,157 chunks)

Thresholds applied fresh to newly measured numbers. No `7/(14-4)` arithmetic: the
suggestive 7/10 was never measured and was not promoted.

```
RERANKABILITY RATE: 8/13
  fusion_ranking_miss    8
  candidate_miss         5
  index_coverage_miss    0
DECISION: build_2b
```

**Honest qualification: the rule fired exactly at its boundary.** The threshold is
`>= 8` and the measurement is 8. One query moving either way flips this decision.
It is a pass, not a mandate, and Phase 2B's own gate (improvement measured
against the ceiling stated here) remains the real test.

The **ceiling** Step 0B predicts: at best, reranking converts up to 8 failures to
passes, i.e. Top-3 from 0.74 toward ~0.90. The 5 `candidate_miss` queries are
untouched by any reranker and belong to 2A.

## Baseline chain

```
pre-P0     199,269 chunks · 0.68 / 0.72 / 0.7057 · 14 failures   (retired)
post-P0    184,068 chunks · 0.68 / 0.72 / 0.7057 · 14 failures   (retired)
post-0A    184,157 chunks · 0.70 / 0.74 / 0.7297 · 13 failures   <- CANONICAL
```

Phase 1's invariance gate measures against the post-0A row.
