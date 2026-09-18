# Measurement snapshots — freeze the input, not the store

**Status:** IMPLEMENTED 2026-09-18 (see §8); §6 answered there
**Date:** 2026-09-17
**Scope:** `scripts/eval_search.py`, `scripts/check.sh`, `tests/conftest.py`, a new
`scripts/snapshot.py`. No change to retrieval ranking, and none to the frozen
artefacts.

## 1. The contradiction

Two designs in this repository are each correct and jointly impossible:

- **Zurvan is shared, live, cross-project memory.** The MCP server runs with
  `ZURVAN_MCP_READONLY=0` so a session in any repository can record a decision
  or a claim. That is the product.
- **`check.sh` measures retrieval against the live corpus** and `tests/conftest.py`
  fails the suite if `wiki/` changes while it runs, because the corpus is the
  instrument.

On 2026-09-17 at 14:57:12 a session working on an unrelated project wrote one
note through MCP. It landed inside `check.sh`'s pytest window, the guard fired,
and gate 2 aborted a 20-minute run. Nothing was wrong with any code under test.
The guard was right — the corpus did move — and the gate was still useless.

Waiting for a quiet store does not scale: it makes a local quality gate depend
on every other project's session, and on a human remembering.

## 2. The invariant

> **Shared knowledge may remain live and writable; measurements must not depend
> on the shared store remaining stationary.**

Freezing Zurvan to measure Zurvan is the wrong lever. Freeze the *input to the
measurement*.

## 3. What the measurement input actually is

Worth stating precisely, because it makes the fix much smaller than it looks:
`eval_search --hybrid` never reads `wiki/`. It reads **the index**, and compares
`source_path` strings against the gold set's `expected_paths`. The corpus
reaches the measurement only through `data/search.sqlite`.

(The `--hybrid`-less branch does glob `wiki/` and `docs/` directly. It is not on
any gate and is a different retriever; it is out of scope here and should be
snapshot-fed or deleted separately.)

So the measurement input is one SQLite file — and since 2026-09-17 that file is
replaced by a single atomic `os.replace()`, never mutated in place. That is what
makes a cheap snapshot possible.

## 4. Mechanism

**Capture.** At gate start, `snapshot.capture()` creates
`data/snapshots/<gate-id>/search.sqlite` as a **hard link** to the canonical
index. O(1), no copy, no extra disk. Because a rebuild promotes a *new inode*
with `os.replace()`, the linked inode is immune to any later promotion: the
snapshot keeps pointing at the bytes that existed at capture time even while
production moves on.

**Identity.** The capture records, and the evaluation reports:

```
snapshot_id        gate-20260917T150000Z
index_inode        191481762
chunks             184241
sources            4421
chunk_id_set_sha   b5475f32...
vector_bytes_sha   807fe54d...
embedding_space    sentence_transformers/all-MiniLM-L6-v2/384
corpus_manifest_sha <sha over (relpath, size, mtime_ns) for all indexable files>
```

Every retrieval number is then reported *with* the identity of what produced it.
A result that cannot name its input is not a measurement.

**Use.** `eval_search` takes `--index <path>` (defaulting to the canonical one,
so nothing changes for ad-hoc use); `check.sh` captures a snapshot after gate 5
and passes it to gate 6. External writes during the run change the live store
and the next rebuild, and change nothing about the number being computed.

**Release.** The snapshot directory is removed at gate end; a hard link holds the
inode alive until then, so a promoted-over index is still readable. Orphans are
swept the way abandoned build files are, and `data/snapshots/` is gitignored.

## 5. Concurrency regression (the test this design owes)

1. Build index state **A**; capture a snapshot; record its identity.
2. While the gate is "running", perform a real external write (a `note add`
   against the live store) and a full rebuild, producing state **B**.
3. Assert the evaluation still measures **A**: identical chunk-id-set hash,
   identical per-query ranks, snapshot identity unchanged.
4. Assert **B** survived in the live store: the note is present, the canonical
   index contains it after the rebuild.
5. Assert no mixing: no result carries B's chunk count with A's identity, and
   releasing the snapshot does not touch the canonical index.

## 6. The open question, which is Raouf's to answer

Once measurements read a snapshot, an external write during a gate no longer
invalidates any number. Should `tests/conftest.py` still **fail** the suite when
the live `wiki/` changes underneath it?

- **Keep failing (status quo).** Maximum safety, and it still catches a test
  writing into the live store — the failure class that put 48 `note-test-note-*`
  pages into every historical baseline. Cost: the suite stays hostage to other
  projects' sessions, which is the problem this design set out to remove.
- **Attribute by process ancestry, not by message text.** Have every Zurvan
  write stamp a sidecar (`data/writes.jsonl`) with pid and session id. The guard
  then fails for writes made by *its own process tree* — a test, unambiguously —
  and records writes from elsewhere as external, without a judgement call about
  what the text looks like. This is attribution as evidence, not as an excuse:
  it never declares a mutated measurement valid, because §4 already removed the
  dependency.

Recommendation: the second, **after** §4 ships and is proven by §5. Sequencing
matters — relaxing the guard before snapshots exist would trade a noisy failure
for a silent one.

Not in scope: a global write lease. It is the right tool for an operation that
genuinely needs a frozen live store (an atomic production rebuild, a promotion),
and the wrong tool for an ordinary test run — a 20-minute lock on a shared brain
every time someone runs the suite will be routed around within a week.

## 7. Effect on frozen artefacts

None. The frozen provenance golds and the 2026-09-15 rank baseline were measured
against the live index at a point in time and are not re-scored here. Going
forward a baseline gains a snapshot identity, which makes "the same measurement"
a checkable claim rather than an assumption — the 184,002 / 184,231 / 184,241
chunk counts attached to three different baselines are exactly the ambiguity
this removes.

---

## 8. Implemented — 2026-09-18

`scripts/measurement_snapshot.py`, `scripts/writelog.py`, `scripts/eval_search.py --index`,
`scripts/check.sh` gate 6, `tests/conftest.py`.

**Naming deviation, stated.** §4 proposed `scripts/snapshot.py`. That module
already exists and builds the `.tar.gz` export behind `zurvan snapshot`; writing
the new one there broke `zurvan snapshot` and was caught by the full suite as an
ImportError. The measurement snapshot lives in `measurement_snapshot.py` and the
export module is untouched.

**§3 was right and it made the fix small.** `eval_search --hybrid` reads only the
index, so freezing one file freezes the measurement. `capture()` hard-links it;
`os.replace()` promotion then gives production a new inode and the link keeps
pointing at the frozen bytes.

Measured on the production index (738 MB, 184,516 chunks):

| | |
| --- | --- |
| `os.link` alone | 0.26 ms |
| corpus manifest, 4,467 files (stat only) | 54.8 ms |
| index identity (chunk-id-set hash) | 386 ms |
| **`capture()` end to end** | **485 ms** |
| `deep=True`, hashes all 738 MB of vectors | +743 ms — opt-in for that reason |
| disk cost | none; `nlink` 1 → 2, same inode |
| peak traced memory | 1.20 MB |
| retrieval overhead | none measurable — identical ranked results |

Retrieval scored against canonical and against a snapshot gives the same
numbers: top-1 0.72, top-3 0.76, MRR 0.7423, 12 failures, same failing queries,
same `chunk_id_set_sha`. An early sequential comparison suggested +62% snapshot
overhead; interleaved it is −2.5% with an 84%-of-median spread within each
condition, so the honest statement is **no detectable difference**, which is
what the same inode predicts.

**§5's regression** passes through the real rebuild path, including repeated
rebuilds inside one open snapshot. Five mutants — snapshot pointing at the live
path, identity re-read live, copy instead of link, release deleting the
canonical index, sweep ignoring process liveness — five caught.

### §6 is answered: the guard stays, and asks a better question

The recommendation was "attribute by process ancestry, after §4 ships". §4 has
shipped, and the evidence changed the urgency: **both real aborts were in gate 2,
pytest** (2026-09-17 14:57, 2026-09-18 15:21), not in the retrieval evaluation.
Pytest is not scoring retrieval — it is asserting the suite did not contaminate
the corpus — so snapshots alone would have prevented neither.

Writes to `wiki/` are now stamped at the `safe_write` choke point with pid, ppid
and a session token inherited through the environment. The guard fails for
writes from its **own process tree**, including subprocesses, which is the
48-stray-pages defect unchanged. It records external writes and passes.

Three properties keep it honest:

- **Fails closed.** `all_external([])` is `False`. A change with no attribution
  is treated as ours, so a new write path that forgets to stamp makes the guard
  stricter, never weaker.
- **Not by text.** "Does this look like a test note?" gets it wrong in both
  directions; an ordinary decision whose title contains "test" walks through it.
- **Evidence, not an excuse.** It never declares a measurement valid — §4
  already removed that dependency.

The guard's behaviour is pinned by `tests/test_conftest_guard_behaviour.py`,
which runs a nested pytest over a **copy** of `conftest.py` in a temp directory
so `LIVE_ROOT` resolves there and production is never in scope: ours → fails,
external → passes with a visible notice, unattributed → fails.

That meta-test earned its place immediately. The external-write notice was
invisible in the first two attempts, because pytest captures **file descriptor
2** — neither `print` nor `sys.__stderr__` survives a passing run. It now goes
through the terminal reporter.

### Not done, deliberately

A write lease. Still the right tool for an operation that needs a stationary
store and still the wrong one for a test run.
