# RTBench: a record-time benchmark for memory systems

RTBench asks a question that no published memory benchmark asks: **what did the system believe at an earlier
point?** LongMemEval, BEAM, MemoryAgentBench and Memora test *valid time*, i.e. what was true in the world at T.
RTBench tests *record time*: what the system had on record at T, after corrections, retractions, erasures and
failed writes, asked only once the whole history has been ingested.

Design: `docs/superpowers/specs/2026-11-15-z2-record-time-benchmark-design.md` (IOU-Z2-5 of the Z2 claim ledger).
Code: `scripts/rtbench/`. Everything but the Zurvan adapter is Python standard library only.

## The model

A *slot* is (entity, attribute), e.g. *Orrin Mill's annual output*. A *statement* gives a slot a value. Each case is an
event log:

| Event | Meaning |
|---|---|
| `assert` | a new statement is believed |
| `supersede` | a statement is replaced by a new one |
| `retract` | a statement stops being believed, with no replacement |
| `revise` | metadata (confidence) changes; the value does not |
| `erase` | content is removed for every time; the fact that a statement existed remains |
| `noise` | an unrelated write |
| `abort` | a change that failed; it is never believed |

Record time is a logical tick: the position at which a change became belief (aborts mint none). Statements may carry
a valid-time interval `[valid_from, valid_until)`. **Every question is asked at the end of the log**, so an erase is
retroactive for content: asked afterwards about an earlier tick, the right answer is `[erased]`, not the old value.

## Questions

| Type | Question | Answer |
|---|---|---|
| Q1 | which statements were believed at t? | a set |
| Q2 | status of a statement at t | `believed`, `superseded`, `retracted`, `erased`, `not_yet` |
| Q3 | value of a slot at t | value, `[erased]` or `none` |
| Q4 | value of a slot at t, as holding in the world at date v | value, `[erased]` or `none` |
| Q5 | was this value ever believed for this slot? | `yes` / `no` |
| Q6 | value of a slot now (control) | value or `none` |

The **headline** is the macro accuracy over Q1-Q5. Q6 is a control reported apart: every reasonable system passes it,
so counting it would let a "now-only" memory look half right. Questions cluster at *change points* (the tick a
statement ended, and spans where a later-erased statement was believed), because a system that only knows the
present, or keeps every value without corrections, passes questions far from them.

## Two tracks, one generator

- **Track S (structured):** events and questions as JSON. Exact match, no model.
- **Track N (natural language):** the same cases as dated conversation sessions, one event per session ("[Session 7,
  2026-01-12] Correction: Orrin Mill's annual output is now 4,350 tonnes, replacing the record made in session 3.
  Reason: audit correction."). The questions are the Track S questions in English, with the same gold. Answers are
  distinctive tokens, so a normalised exact match decides them without an LLM judge. A statement is named by the
  session that recorded it, never by its value, so no question leaks erased content.

## Splits

| Split | Cases | Where | Registered |
|---|---|---|---|
| `dev` | 200, seeds 1000-1199 | regenerate anywhere: `zurvan bench rtbench generate --split dev` | `eval/datasets.json` `rtbench_dev` (DEV), `eval/rtbench_dev.manifest.json` |
| `holdout` | 200, private seeds | `~/.zurvan/instruments/` only (0700 / 0600), never in git | `eval/datasets.json` `rtbench_holdout` (HOLDOUT), whole-file digests only |

A holdout run reports scores only, never an item, in the report or on stdout.

## Running

```bash
zurvan bench rtbench generate --split dev
zurvan bench rtbench run --adapter oracle|overwrite|append_only|zurvan|zurvan_abort_after_claim \
                         --track S|N --split dev|holdout [--limit N]
zurvan bench rtbench report ~/.zurvan/reports/rtbench-<adapter>-<track>-<split>-<stamp>.json
zurvan bench rtbench validity --split dev     # spec §6.5: oracle 1.000, both baselines <= 0.60, no errors
```

A run refuses a split whose cases **or gold** file does not match its registered digest; the holdout runs only as
a registered split, and never with `--limit` (differencing two limits would expose one case's results). A Zurvan run
refuses a non-empty `--work-dir`.

## Writing an adapter for another system

Implement `scripts.rtbench.adapters.Adapter`:

```python
class MySystem(Adapter):
    name = "my_system"
    tracks = {"N"}              # or {"S"}, or both
    def reset(self): ...        # a fresh, empty memory for one case
    def ingest(self, item): ... # Track S: one event dict; Track N: one {"session", "date", "text"} turn
    def answer(self, q): ...    # Track S: a question dict; Track N: {"qid", "text"}; return the answer
```

The runner calls `reset`, feeds every item in order, then asks every question. A chat-memory product belongs on
Track N: send each turn as a message, ask each question, return the reply. An exception from `ingest` or `answer` is
counted as an error and a wrong answer, never a crash. A Track N set answer (Q1) may come back as prose: `none`
scores as the empty set and items split on a comma that is not a thousands separator (`4,210 tonnes, 100 staff`).

## Recorded results (dev, 15 Nov 2026, generator `rtbench-1`)

| Adapter | Track | Headline (Q1-Q5) | Q1 | Q2 | Q3 | Q4 | Q5 | Control Q6 |
|---|---|---|---|---|---|---|---|---|
| oracle (scorer self-check) | S, N | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| overwrite ("latest value wins") | S, N | 0.327 | 0.059 | 0.351 | 0.352 | 0.380 | 0.490 | 1.000 |
| append_only ("keep everything") | S | 0.540 | 0.043 | 0.603 | 0.511 | 0.543 | 1.000 | 0.633 |
| append_only | N | 0.539 | 0.036 | 0.603 | 0.511 | 0.543 | 1.000 | 0.633 |
| zurvan (Z2 claim ledger) | S | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| zurvan, failed writes adopted | S | 0.903 | 0.794 | 0.999 | 0.899 | 0.908 | 0.915 | 0.872 |

0 adapter errors in every run. The validity bar (both baselines at or under 0.60 headline on both tracks) holds.

**Reading Zurvan's 1.000 honestly.** Z2 was built against the same fold this oracle implements (its G3 property
test), so a perfect score shows the ledger keeps its own contract under a new, independent oracle; it is not a
comparison with other products, none of which has been run. The second Zurvan row is the informative one. When an
operation fails *after* its claim write has landed, Zurvan's `reconcile` adopts the claim (Z2 spec §7.2: a resolved
mutation's rows join belief at its resolution revision). RTBench counts that as believing a failed change. Zurvan's
default adapter fails before the claim write; both rows are reported so the difference is visible.

**Found by this benchmark:** `claim_ledger.supersede` refuses an erased claim but, without `expected_rev`, accepts
superseding a claim that is already superseded. That forks the chain into two current claims for one fact, and
`retract` likewise accepts a retracted claim. In the adopted-abort run this is why there were no refusals: the
benchmark's next supersede of the original claim was accepted. Recorded as a Z2 follow-up, not changed here.

## Not done here

Running Mem0, Zep, ChatGPT memory or any other product (Track N adapters for them need accounts and spend);
publishing the benchmark; an LLM judge (optional, never the headline).
