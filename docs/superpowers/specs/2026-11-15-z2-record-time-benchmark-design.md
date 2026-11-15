# IOU-Z2-5 — RTBench: a system-neutral record-time benchmark

**Date:** 15 November 2026 (Australia/Sydney) · **Status:** DRAFT for owner approval · **Closes:** IOU-Z2-5 of
`2026-11-14-z2-claim-ledger-design.md` §4 · **Scorecard:** belief-time travel 8 → 10 (§9 of the Z2 spec).

## 1. Intent and why

The Z2 gap hunt (Z2 spec §2.3) found that **no published memory benchmark tests record time**, meaning what a system
*believed* at an earlier point, as opposed to what was true in the world then (valid time). LongMemEval, BEAM,
MemoryAgentBench and Memora test valid time only. Z2 built belief-time travel and proved it internally (G3 property
test, G4 snapshot replay). IOU-Z2-5 turns that internal oracle into an **external, system-neutral benchmark**: any
memory system can be scored on it, not only Zurvan.

Raouf chose (15 Nov 2026): the **neutral** version (other systems and a paper can be scored on it, not only Zurvan's
own regression), and approach **C** (both a structured track and a natural-language track from one generator).
Built locally only; publishing the benchmark, or running paid third-party products against it, is Raouf's separate
call.

## 2. Goals, non-goals, anti-goals

**Goals**
- One deterministic generator produces *cases*. Each case is an event log plus questions with gold answers.
- **Track S (structured):** events and questions as JSON; exact scoring; no model anywhere.
- **Track N (natural language):** the same cases rendered as dated conversation sessions and plain-English questions;
  scored by normalised exact match on canonical answer tokens, so no LLM judge is needed for the primary score.
- A **reference oracle** computes gold answers from the event log alone, sharing no code with Zurvan.
- An **adapter interface** any system implements; shipped adapters: Zurvan (track S), two naive baselines (both
  tracks), and the oracle itself (both tracks, as a scorer self-check).
- A scorer and a report (JSON + Markdown) per run, broken down by question type and track.
- A **validity check**: the benchmark is only useful if it separates a correct record-time store from naive ones.

**Non-goals**
- Running Mem0, Zep, ChatGPT memory or any paid product. Adapters for them are not built here; the interface and a
  written adapter guide are.
- An LLM judge as the primary Track N score. An optional judge hook may exist, off by default, never in the headline.
- Publishing (repository, Hugging Face, paper). Separate call, after approval.
- Retrieval quality. RTBench tests the belief mechanism, not ranking.

**Anti-goals (a build that does these has failed even if tests pass)**
- The oracle imports, calls or copies logic from `scripts/claim_*` or `knowledge_*`. Zurvan would be grading itself.
- Gold answers are changed to match what Zurvan returns. A Zurvan miss is a **finding** about Zurvan (or about the
  oracle, proven with a hand-worked case), reported as such, never quietly reconciled.
- Any text from Raouf's real store, wiki, sources or instruments enters a case. Cases are synthetic from a fixed
  vocabulary.
- The holdout split's seeds or files appear in git, or a per-item digest of it appears anywhere (same rule as the
  retrieval instrument: whole-file digest only).
- A baseline is weakened to make the gap look bigger. Baselines are the most natural naive implementation, stated
  in one line each.
- Tests or runs write the live store. The Zurvan adapter runs every case in its own temporary `ZURVAN_ROOT`.

## 3. Constraints

- Lives in this repo: code under `scripts/rtbench/`, tests `tests/test_rtbench_*.py`, CLI `zurvan bench rtbench …`.
- Python standard library only for generator, oracle, renderer, scorer and baselines (the benchmark must be easy for
  others to run). The Zurvan adapter uses Zurvan.
- Deterministic: same generator version + seed → byte-identical case file (canonical JSON, sorted keys, `\n` endings).
- Private outputs (holdout files, reports) go to `~/.zurvan/`, never the repo. `eval/datasets.json` registers the
  splits by whole-file digest only.
- Change protocol, `public_repo_guard.py`, `git diff --check`, full suite green, as for any Zurvan change.

## 4. Approaches (decided)

| | Approach | Trade-off |
|---|---|---|
| A | Structured track only | Deterministic, exact, isolates the mechanism; chat products need an adapter |
| B | Natural-language track only (LongMemEval style) | Comparable to published memory benchmarks; needs a judge, noisy, mixes retrieval in |
| **C** | **Both from one generator (chosen by Raouf)** | Most complete; about twice the work; Track N scoring solved by canonical answer tokens (§6.4) |

## 5. The model the benchmark tests

A *fact slot* is (entity, attribute), e.g. (`Orrin Mill`, `annual output`). A *statement* asserts a value for a slot,
e.g. "Orrin Mill's annual output is 4,210 tonnes". The canonical answer token is the value (`4210 tonnes`).

**Two time axes.** *Record time* `t` is a logical tick, the position in the event log at which a change became
belief (1, 2, 3, …; aborted events mint no tick). *Valid time* `v` is an optional ISO date interval
`[valid_from, valid_until)` on a statement, meaning when the fact held in the world.

**Events** (every event carries `t` except `abort`):

| Event | Meaning |
|---|---|
| `assert(sid, slot, value, valid?)` | new statement becomes believed |
| `supersede(old_sid, new_sid, value, valid?, reason)` | old stops being current; new is believed |
| `retract(sid, reason)` | statement stops being believed; no replacement |
| `revise(sid, confidence)` | same statement, metadata changes; still believed |
| `erase(sid)` | content removed for every time; the fact that a statement existed remains |
| `noise(note)` | unrelated write; advances `t`, changes no belief |
| `abort(inner_event)` | an attempted change that failed; never believed at any `t` |

**Generator invariants.** A slot has at most one believed statement at any tick: a new value for a slot that
already has one arrives as `supersede`, never as a second `assert`. No event targets a statement that is already
superseded, retracted or erased. *Ticks are the oracle's logical clock*; each adapter maps them to its own (the
Zurvan adapter maps tick → the knowledge revision its operation minted, or the reconcile revision after an abort,
exactly as the G3 test does). An `abort` mints no tick and is delivered to adapters as `{"type": "abort", "inner":
…}`; a system that stores the inner change is wrong.

**Query time is the end of the log.** Every question is asked after all events. So erasure is retroactive for
content: asked at the end, "what did you believe at t=3?" returns `[erased]` for an erased statement even if it was
believed and readable at t=3. This is the GDPR-vs-history tension the benchmark is meant to expose.

## 6. Questions, gold and scoring

### 6.1 Question types

| Code | Question | Gold |
|---|---|---|
| Q1 `believed_set@t` | which statements were believed at `t`? | set of `sid` (Track S) / set of value tokens, an erased one as `[erased]` (Track N) |
| Q2 `status@t` | status of statement `sid` at `t` | one of `believed`, `superseded`, `retracted`, `erased`, `not_yet` |
| Q3 `value@t` | value believed for slot at `t` | value token, `[erased]`, or `none` |
| Q4 `bitemporal@t,v` | value believed at `t` as holding in the world at `v` | value token, `[erased]`, or `none` |
| Q5 `ever_believed` | was value X for this slot believed at any tick? | `yes` / `no`; at least 40% each across a split; in a case with an abort, at least one `no` item is the aborted value; other `no` items are values never asserted; `yes` items favour values later superseded or retracted; never asked about an erased statement's value |
| Q6 `current` (control) | value believed now for slot | value token, or `none` (an erased statement is not believed after its erase tick) |

**Erasure in gold** follows the Z2 design's job 4 ("a content-free stub and a valid chain"): the *history* of an
erased statement survives, its *content* does not. So at a `t` before the erase tick, Q1 includes its `sid`, Q2 says
`believed` (or `superseded`/`retracted` if that happened first), and Q3/Q4 answer `[erased]` rather than the value.
From the erase tick on, Q2 says `erased`, Q1 excludes it, and Q3/Q4 answer `none` (an `erase` only targets a believed statement, so the slot is empty afterwards). An erased statement is never `not_yet` once it had been asserted.

**Open valid time.** A statement with no valid interval holds at every `v` (Z2 spec `valid_at`: absence of a stated
interval is not evidence it was false). This is the benchmark's own stated rule, not a copy of Zurvan's code.

`revise` changes no answer: it is a distractor that a system must not mistake for a new value (Q3 after a revise
returns the same token). `noise` is a distractor that advances `t`.

Questions are sampled to stress transitions. At least half of Q1–Q4 sit at a change point: the tick at which a
statement of the asked slot ended (retract and erase endings weighted twice), or a tick while a later-erased
statement was believed. A system that only knows "now", or that keeps every value without corrections, passes
questions far from those points. (Revised prospectively after the plan's gauntlet, 15 Nov 2026: sampling at
"before or after any change" let the append-only baseline reach 0.667.)

### 6.2 Reference oracle

`scripts/rtbench/oracle.py`: a pure fold over the event log. State after tick `t` = apply events with tick ≤ `t` in
order, skip `abort`, then apply every `erase` in the log regardless of tick (query time is the end). It shares no code
with Zurvan (anti-goal, enforced by a test that greps its imports). It answers every question type for both tracks.

### 6.3 Track S scoring

Exact match per question; Q1 also reports set precision, recall and F1. Headline = macro-average accuracy over the
five record-time types Q1–Q5. Q6 is a *control*, reported separately: every reasonable system should pass it, so
counting it would let a now-only memory look half-right. Per-type accuracy and 95% Wilson intervals are always reported.

### 6.4 Track N rendering and scoring

**One event per session**, so every tick is a session boundary, and the Track N questions are exactly the Track S
questions rendered in English (one-to-one, same gold). Per-track scores are therefore comparable. Track N adapters
receive the prose turns only, no hidden structured fields. In this build the shipped adapters read Track N through
one shared template parser (the same one R4 tests). What Track N proves here is that the prose alone carries
every answer (the oracle scores 100% reading it) and still discriminates (baselines stay under the bar). Scoring LLM
memory products on it is what it is for later.

The renderer turns each event into a dated session turn from fixed templates, e.g. *"[Session 7, 2026-03-04] Update:
Orrin Mill's annual output is now 4,350 tonnes, not 4,210; the earlier figure double-counted a shift."* `noise`
becomes an unrelated turn; `abort` becomes a turn the user immediately withdraws in the same session ("scratch that,
don't record it"). A statement is named by the session that recorded it ("the record made in session 4"), never by its value, so a question cannot leak erased content. Questions are plain English and pinned to a session, e.g. *"As of the end of session 5, what had
you recorded as Orrin Mill's annual output? Answer with the value only, or 'none'."*

Values are generated to be **distinctive tokens** (numbers with units, coined names), so a normalised exact match
(case, whitespace, thousands separators, unit spelling from a fixed table) decides correctness without a judge. Set
questions ask for a comma-separated list. Erased content renders as `[erased]` in gold; the user turn that erases
says "forget everything about X's Y, permanently".

### 6.5 Validity check (a release criterion, not a nice-to-have)

On the dev split: the oracle adapter scores 100% on both tracks (scorer self-check). The overwrite baseline and the
append-only baseline each score ≤ 60% headline (Q1–Q5 macro) on both tracks. Question sampling caps questions at the
final tick at 20% of Q1–Q4, so "now" knowledge alone cannot carry a type. If either baseline beats 60%, the question
mix does not discriminate: fix the generator's question sampling (prospectively, documented), never the threshold.

### 6.6 Baselines

- **Overwrite** ("latest value wins"): one value per slot, replaced on every assert/supersede, deleted on
  retract/erase; ignores `t` and valid time. The typical key-value memory.
- **Append-only** ("keep everything"): every asserted value kept forever, no retract or supersede handling; answers a
  value question with the most recently asserted value at or before `t`. The typical vector-memory log.
- Both baselines drop `abort` events (a failed write is not stored), which is the natural behaviour and gives them Q5's
  aborted-value negatives for free; that is accepted, not engineered away.
- **Oracle**: the reference fold wrapped as an adapter. Not a baseline; a scorer check.

## 7. Splits, files and registration

- `dev`: 200 cases from public seeds stated in code. Its files can be regenerated by anyone; the manifest digest is
  committed in `eval/rtbench_dev.manifest.json` and `eval/datasets.json` (`state: DEV`).
- `holdout`: 200 cases from seeds held in `~/.zurvan/instruments/rtbench_holdout.seeds` (0600), files in the same
  directory, registered by whole-file digest only (`state: HOLDOUT`). Never in git, never quoted.
- Case file: JSONL, one case per line: `{"case_id", "generator_version", "events": [...], "questions": [...]}`. Gold
  lives in a separate `*.gold.jsonl` so an adapter never sees it.
- Case sizes: 8–60 events, 2–8 slots, 10–30 questions per case. About 20% of cases include an erase, about 30% an
  abort, about 30% valid-time intervals.

## 8. Adapter interface

```python
class Adapter:
    name: str
    tracks: set            # {"S"}, {"N"} or {"S", "N"}
    def reset(self) -> None: ...                  # fresh, empty memory for one case
    def ingest(self, item) -> None: ...           # Track S: one event dict; Track N: one session turn dict
    def answer(self, question) -> object: ...     # returns the answer in the track's answer format
```

The runner calls `reset`, feeds every item of a case in order, then asks every question. **Questions are only asked
after the whole log is ingested.** A system cannot answer from the state it happened to be in at `t`; it must
remember history. The Zurvan adapter maps each tick to the knowledge revision its operation minted and answers
through `as_of_revision`, `valid_at` and `history`. It writes one source file per case holding each statement's text
on its own line (the verbatim evidence quote), stores the statement as claim text, and records the slot in a tag
(`rtslot:<id>`). The baselines read the same structured slot field from the Track S event, so no adapter gets
information another lacks. A miss caused by the adapter's mapping is an adapter defect and is named as one. Aborts raise inside `kr.operation` before the claim write by default; a second adapter raises after it, because
Zurvan's reconcile adopts a failed operation's landed claim (Z2 §7.2) and that cost is measured, not hidden; `noise` uses
`write_file_safely`; each case gets its own temporary `ZURVAN_ROOT`.

## 9. CLI and outputs

```
zurvan bench rtbench generate --split dev [--out DIR]
zurvan bench rtbench run --adapter zurvan|overwrite|append_only|oracle --track S|N --split dev|holdout
zurvan bench rtbench report RUN.json
```

Reports go to `~/.zurvan/reports/rtbench-<adapter>-<track>-<split>-<date>.{json,md}` and record generator version,
split digest, adapter name and per-type scores. A holdout run prints scores only, never items.

## 10. Acceptance (the build is done when each row is shown by a named test or a recorded run)

| # | Criterion |
|---|---|
| R1 | Same version + seed gives byte-identical case and gold files (test) |
| R2 | Oracle imports nothing from Zurvan's claim or knowledge modules (test greps imports) |
| R3 | Oracle gold matches hand-worked answers on at least 6 hand-written cases, one per question type, including erase-after-belief and abort (test) |
| R4 | Track N rendering round-trips: parsing the canonical tokens out of the rendered sessions recovers the Track S values (test) |
| R5 | Oracle adapter scores 100% on dev, both tracks (recorded run) |
| R6 | Validity check §6.5 holds on dev (recorded run) |
| R7 | Zurvan adapter on dev Track S: score recorded. Every miss is named by case index and question type and classified as a Zurvan defect or an oracle defect, with the hand-worked proof. No gold edits to match Zurvan |
| R8 | No test or run writes the live store (conftest guard plus temp roots) |
| R9 | Holdout seeds and files exist only under `~/.zurvan/instruments/`; `public_repo_guard` and boundary scan stay clean |

## 11. Calls made after the builder check (Claude, 15 Nov 2026; correct any)

- Track N: one event per session, the same questions as Track S, prose-only input, read by a shared template parser.
- Erasure gold keeps history and drops content (§6.1), per the Z2 design's job 4.
- Q5 has positive and negative items; Q6 is a control outside the headline; final-tick questions capped at 20%.
- Generator invariants (§5): one believed statement per slot; no events on dead statements; aborts carry their inner
  event and mint no tick.
- Holdout seeds come from `secrets` (CSPRNG) at first generation; a new `bench` group is added to `cli.py`;
  datasets are registered as `rtbench_dev` and `rtbench_holdout`.

## 12. Left to the builder

Template wording for Track N (fixed, versioned with the generator); the vocabulary of entities, attributes and value
generators; the exact sampling weights within the §7 ranges; the normalisation table for answer tokens; JSON field
names beyond §7–8; module split inside `scripts/rtbench/`.

## 13. Generator rtbench-2: erasing dead history (Raouf, 15 Nov 2026)

rtbench-1 only ever erased a statement that was still believed. The common erasure request (GDPR art. 17) is the
opposite: forget a value that was superseded or retracted long ago. rtbench-2 adds that case and changes nothing
else; rtbench-1 stays frozen and its registered dev split regenerates byte for byte (a test checks it).

- **Events.** An erase may target a superseded or retracted statement that is not yet erased. When dead history
  exists, an erase picks it with probability 0.6 (always, when no statement is believed). The share of cases with
  an erase rises from 0.20 to 0.35. Every other invariant of §5 holds; a statement is erased at most once.
- **Oracle.** A statement keeps how and when it ended (`superseded` or `retracted`) and gains `erased_at`. Q2 reads
  `erased` from `erased_at` on and its earlier status before it; content is `[erased]` at every tick (Q1 values,
  Q3, Q4); Q5 never asks about an erased value. A live erase is unchanged (`erased_at` equals the end tick).
- **Change points.** The tick of a dead-history erase counts twice, alongside the ticks the statement was believed.
- **Splits.** `dev2` (DEV2_SEEDS = range(2000, 2200)) and `holdout2` (200 private `secrets` seeds in
  `~/.zurvan/instruments/rtbench_holdout2.seeds`, files in `rtbench_holdout2/`, 0700/0600), registered as
  `rtbench_dev2` and `rtbench_holdout2` in `eval/datasets.json` by whole-file digest only. Holdout seeds avoid both
  dev ranges. `zurvan bench rtbench generate|run|validity --split dev2|holdout2`; every split whose name starts with
  `holdout` is sealed (registered only, no `--limit`, no items in reports).
- **Validity** (§6.5) was measured on `dev2` before registration and holds: oracle 1.000 on both tracks, overwrite
  0.323 (S and N), append_only 0.552 (S) / 0.548 (N).
