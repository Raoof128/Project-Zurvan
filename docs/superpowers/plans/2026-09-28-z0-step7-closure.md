# Z0 Step 7 — The implicit inventory is enforced, item 4 is proven end to end, and Z0 closes on a matrix that cannot rot

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Z0 honestly. Every one of §10's 20 gate items maps to a named test that exists and passes. The implicit-mutation inventory is a gated query, not a manual check. Item 4 is demonstrated with a real killed process.

**Architecture:** Step 7 is small because the fail-closed writes (amendment 7, 2026-09-28) already drove the live inventory to empty: 10 historical implicit rows, all resolved, and `zurvan knowledge callers` reports none. This step adds three things. `zurvan knowledge callers --strict` exits 1 while any unresolved implicit caller exists, and `check.sh` runs it. One end-to-end test covers gate item 4. A closure matrix goes in the spec, and `tests/test_z0_closure.py` fails if any test the matrix names stops existing. Z0 is then marked CLOSED.

**Tech Stack:** Python 3.12 stdlib, pytest. No new dependency.

**Spec:** [docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md](../specs/2026-09-18-z0-coherent-snapshots-design.md), §3.2 (the migration pipeline), §9.2 step 7, and §10 (all 20 items).

## Global Constraints

- **Inventory empty. No production multi-write operation relies on implicit mode at closure — a query over `mutations.implicit`, not an assertion.** (Design §10.1 item 3.)
- **Z0 closes when all of these are demonstrated, each by a named test.** (Design §10.)
- **The migration gate is "no unresolved implicit callers", not "no historical implicit rows": counting history made closure reachable only by deleting evidence, which this project does not do.** (`scripts/knowledge_revision.py`, `implicit_mutations`.)
- **A failed gate is reported as failed. Never redefine one retroactively so the work passes.** (CLAUDE.md.) If an item cannot be matched to a test that proves what §10 says, Z0 stays OPEN and the gap is named.
- **Production knowledge state is never a test fixture.** (AGENTS.md.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.**

## Audit (2026-09-28, before this plan)

Eleven items already had `test_gate_N` names. The other nine were matched by behaviour against §10's wording. Two real gaps and one missing record came out of it:

- **Item 3:** `knowledge callers` always exits 0, so nothing would notice a new implicit caller.
- **Item 4:** covered only in pieces. The real SIGKILL orphan is in `test_gate_5`, the refusal of a SUSPECT store in `test_gate_18` (which sets the flag with `_mark_suspect`, not a real orphan), and propagation in a test with an injected orphan. No test runs the sequence §10.1 item 4 describes.
- **The record:** no matrix, so nothing stops a renamed test from silently unproving an item.

## Review Focus

1. **Running `callers --strict` on a store with no ledger** (a fresh clone). Expected: exit 0, with a message saying nothing is recorded rather than "migration complete". Pinned in Task 1 (`test_strict_callers_on_a_store_with_no_ledger_passes_and_says_so`).
2. **A resolved caller writes implicitly again.** Expected: `--strict` fails again, because the inventory reopens. This is covered by the existing `test_a_new_implicit_write_from_a_resolved_caller_reopens_the_inventory` plus Task 1's strict test.
3. **A matrix row naming a test that was renamed.** Expected: `test_z0_closure.py` fails and names the row. Pinned in Task 3.
4. **Reconcile run while the SUSPECT owner is not provably dead.** Out of scope: `detect_orphans` orphans only DEAD owners (§3.3), which is unchanged here.
5. **`check.sh`'s own smoke steps writing implicitly.** Expected: none do. Verified by Task 4's `check.sh` run, which now fails if they do.

---

## Task 1 — `zurvan knowledge callers --strict`, gated in `check.sh`

**Files:**
- Modify: `scripts/cli.py`, `scripts/check.sh`
- Test: `tests/test_z0_closure.py` (create)

**Interfaces:**
- Produces: `zurvan knowledge callers [--strict]`. `--strict` exits 1 while any unresolved implicit caller exists. A store with no ledger exits 0 and prints `no ledger yet: nothing recorded`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_z0_closure.py`:

```python
"""Z0 closure (design §10): every gate item is demonstrated by a named test,
and the implicit inventory is a gated query, not an assertion (item 3)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


def _callers(root, *flags):
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"),
                           "knowledge", "callers", *flags],
                          capture_output=True, text=True, env=env, cwd=REPO)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    kr.ensure_schema(tmp_path)
    return tmp_path


def _implicit_write(root, name):
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration(reason="closure test"):
        write_file_safely(str(root / "wiki" / name), "x\n")


def test_gate_3_strict_callers_fails_while_the_inventory_is_open(store):
    _implicit_write(store, "a.md")
    out = _callers(store, "--strict")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "unresolved implicit caller" in out.stdout


def test_strict_callers_passes_once_every_caller_is_resolved(store):
    _implicit_write(store, "a.md")
    for caller in kr.implicit_callers(store):
        kr.resolve_implicit(caller, actor="test", root=store)
    out = _callers(store, "--strict")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "No unresolved implicit callers." in out.stdout


def test_strict_callers_on_a_store_with_no_ledger_passes_and_says_so(tmp_path):
    (tmp_path / "data").mkdir()
    out = _callers(tmp_path, "--strict")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nothing recorded" in out.stdout


def test_callers_without_strict_still_reports_and_exits_zero(store):
    _implicit_write(store, "a.md")
    out = _callers(store)
    assert out.returncode == 0
    assert out.stdout.strip()


def test_check_sh_gates_the_inventory():
    text = (REPO / "scripts" / "check.sh").read_text()
    assert "knowledge callers --strict" in text
```

- [ ] **Step 2: Run them and watch them fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_z0_closure.py
```

Expected: `test_gate_3…`, `…resolved`, `…no_ledger…` and `…check_sh…` FAIL. argparse rejects `--strict` with exit 2, and `check.sh` has no such line. `test_callers_without_strict_still_reports_and_exits_zero` passes: it pins today's behaviour.

- [ ] **Step 3: Implement.** In `scripts/cli.py`, replace `knowledge_sub.add_parser("callers", help="Unresolved implicit callers still to be migrated")` (the call spans two lines) with:

```python
    k_callers = knowledge_sub.add_parser(
        "callers", help="Unresolved implicit callers still to be migrated")
    k_callers.add_argument("--strict", action="store_true",
                           help="exit 1 while any unresolved implicit caller "
                                "exists (Z0 gate item 3)")
```

In the ledger check near the top of the `knowledge` branch, replace `print(f"no ledger yet: {exc}")` with:

```python
                print(f"no ledger yet: nothing recorded ({exc})")
```

and replace the `callers` action body with:

```python
        elif args.action == "callers":
            callers = kr.implicit_callers()
            if not callers:
                print("No unresolved implicit callers.")
            for caller, count in sorted(callers.items(), key=lambda kv: -kv[1]):
                print(f"{count:6}  {caller}")
            if callers and args.strict:
                print(f"{len(callers)} unresolved implicit caller(s): Z0 gate "
                      "item 3 requires none")
                sys.exit(1)
```

In `scripts/check.sh`, replace the `[13/23]` block's command line `PYTHONPATH=. python scripts/cli.py doctor` with:

```bash
PYTHONPATH=. python scripts/cli.py doctor
# Z0 gate item 3: the implicit-mutation inventory is a gated query, not an
# assertion. A production write outside an operation reopens it and fails here.
PYTHONPATH=. python scripts/cli.py knowledge callers --strict
```

- [ ] **Step 4: Run the tests and the live query**

```bash
PYTHONPATH=. python -m pytest -q tests/test_z0_closure.py tests/test_fail_closed_writes.py tests/test_knowledge_revision.py
PYTHONPATH=. python scripts/cli.py knowledge callers --strict; echo "exit=$?"
```

Expected: all pass. The live store prints `No unresolved implicit callers.` and `exit=0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/cli.py scripts/check.sh tests/test_z0_closure.py
git commit -m "zurvan: knowledge callers --strict, gated in check.sh (Z0 item 3)"
```

---

## Task 2 — Gate item 4, end to end with a real kill

**Files:**
- Test: `tests/test_coherent_capture.py`

**Interfaces:**
- Consumes: the existing `store` fixture and the `_write` helper in `tests/test_coherent_capture.py`, plus `bsn.capture_coherent` and `krec.reconcile(root=, accept=, actor=, reason=)`.

- [ ] **Step 1: Write the test** (append to `tests/test_coherent_capture.py`)

```python
def test_gate_4_a_killed_writer_blocks_snapshots_not_writes_until_reconciled(store):
    """§10.1 item 4 as one sequence, with a REAL kill: the revision becomes
    SUSPECT, snapshots of it are refused, live writes continue, SUSPECT
    propagates across a later unrelated operation, and only reconcile clears
    it. Before this, the parts were proven separately and the refusal only
    against a simulated SUSPECT (`_mark_suspect`)."""
    import signal
    from scripts import knowledge_reconcile as krec
    child = subprocess.run([sys.executable, "-c", (
        "import os, signal\n"
        "from scripts import knowledge_revision as kr\n"
        "from scripts.safe_write import write_file_safely\n"
        f"with kr.operation(writer='doomed', root={str(store)!r}):\n"
        f"    write_file_safely({str(store / 'wiki' / 'torn.md')!r}, 'x')\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n")],
        env={**os.environ, "PYTHONPATH": "."})
    assert child.returncode == -signal.SIGKILL
    kr.detect_orphans(store)
    suspect = kr.current(store)
    assert suspect.state == kr.SUSPECT
    with pytest.raises(bsn.BuildSnapshotRefused, match="SUSPECT"):
        bsn.capture_coherent(store, purpose="measurement", required=("wiki",))
    _write(store, "unrelated.md")                  # live writes continue
    later = kr.current(store)
    assert later.revision == suspect.revision + 1
    assert later.state == kr.SUSPECT               # advancing is not repair
    result = krec.reconcile(root=store, accept=True, actor="test", reason="gate 4")
    assert result["status"] == "CLEAN"
    with bsn.capture_coherent(store, purpose="measurement", required=("wiki",)) as bs:
        assert bs.revision == kr.current(store).revision
```

- [ ] **Step 2: Run it.** It pins behaviour that already exists, so it is expected to pass. Then falsify it, because a test that has never failed proves nothing:

```bash
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py -k gate_4
f=scripts/build_snapshot.py; cp $f /tmp/bs.keep
sed -i '' 's/^    if state.state != kr.CLEAN:$/    if False:/' $f
PYTHONPATH=. python -m pytest -q tests/test_coherent_capture.py -k gate_4 2>&1 | tail -1; cp /tmp/bs.keep $f
```

Expected: PASS, then `1 failed` with the refusal mutation (`_refuse_if_suspect` disabled), then the file restored. If the mutation leaves it green, the test does not prove the refusal: stop and fix the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_coherent_capture.py
git commit -m "zurvan: Z0 gate item 4 end to end with a real kill"
```

---

## Task 3 — The closure matrix, and the test that stops it rotting

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md` (a new §10.5 after the §10.4 block)
- Test: `tests/test_z0_closure.py`, `tests/test_coherent_capture.py` (item 7's real-kill test, added during execution: the audit's candidate used a fabricated orphan row)

**Interfaces:**
- Produces: a table in §10.5 with rows `| <item> | <what> | `<test name>`[, `<test name>`…] |`. `test_every_gate_item_names_a_test_that_exists` parses it.

- [ ] **Step 1: Write the failing test** (append to `tests/test_z0_closure.py`)

```python
SPEC = REPO / "docs" / "superpowers" / "specs" / "2026-09-18-z0-coherent-snapshots-design.md"


def _matrix():
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 10.5 Closure matrix", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|[^|]*\|(.*)\|\s*$", line)
        if m:
            rows[int(m.group(1))] = re.findall(r"`(test_[A-Za-z0-9_]+)`", m.group(2))
    return rows


def _defined_tests():
    names = set()
    for path in (REPO / "tests").glob("test_*.py"):
        names.update(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.M))
    return names


def test_every_gate_item_names_a_test_that_exists():
    """A renamed test must not silently unprove a gate item."""
    rows = _matrix()
    assert sorted(rows) == list(range(1, 21)), f"matrix covers {sorted(rows)}"
    defined = _defined_tests()
    missing = {item: [t for t in tests if t not in defined]
               for item, tests in rows.items()}
    missing = {k: v for k, v in missing.items() if v}
    empty = [item for item, tests in rows.items() if not tests and item != 20]
    assert not missing, f"matrix names tests that do not exist: {missing}"
    assert not empty, f"items with no named test: {empty}"
```

Item 20 is the regression floor, which is demonstrated by running pytest, `check.sh`, `public_repo_guard` and `git diff --check`, not by one test function. Its row names those commands and no `test_` symbol.

- [ ] **Step 2: Run it and watch it fail**

```bash
PYTHONPATH=. python -m pytest -q tests/test_z0_closure.py -k every_gate_item
```

Expected: FAIL with `IndexError: list index out of range`, because there is no `### 10.5 Closure matrix` section yet.

- [ ] **Step 3: Write §10.5.** Insert it directly before `## 11. Explicitly not built in Z0`:

```markdown
### 10.5 Closure matrix

Every item above, and the test that demonstrates it. `tests/test_z0_closure.py`
fails if any named test stops existing, so a rename cannot silently unprove an
item.

| Item | What | Named test(s) |
|---|---|---|
| 1 | Partial logical operation is BUSY | `test_gate_1_a_capture_between_the_two_writes_of_decision_add_is_busy` |
| 2 | Two implicit writes are detected and reported | `test_two_unwrapped_writes_are_two_implicit_mutations`, `test_a_permitted_write_outside_any_operation_is_one_implicit_mutation` |
| 3 | Inventory empty, as a gated query | `test_gate_3_strict_callers_fails_while_the_inventory_is_open`, `test_check_sh_gates_the_inventory` |
| 4 | Orphan: SUSPECT, snapshots refused, writes continue, propagates, only reconcile clears | `test_gate_4_a_killed_writer_blocks_snapshots_not_writes_until_reconciled` |
| 5 | Reconcile reaches CLEAN without a rebuild; a rebuild then makes stores CURRENT | `test_an_accepted_reconciliation_clears_an_incomplete_orphan`, `test_gate_5_reconcile_then_rebuild_is_current` |
| 6 | Reconcile stops for review when a planned path never completed | `test_an_incomplete_operation_stops_for_review`, `test_the_review_report_names_the_path_that_never_completed` |
| 7 | Planned vs completed: investigable after a kill before stamping | `test_gate_7_a_kill_between_the_bytes_and_the_stamp_is_investigable`, `test_a_crash_after_publishing_but_before_completing_still_orphans`, `test_an_orphan_names_the_path_that_never_completed` |
| 8 | Coherence refusal names the component and the gap | `test_gate_8_disagreeing_components_fail_closed_naming_the_gap` |
| 9 | No deadlock from a stale index | `test_gate_9_no_deadlock_from_a_stale_index` |
| 10 | A/B isolation | `test_gate_10_a_measurement_keeps_resolving_to_E_while_live_moves_to_E1` |
| 11 | A write during capture is retried, never mixed | `test_a_write_during_capture_is_retried_then_refused` |
| 12 | A completed unmanaged edit is minted EXTERNAL_OBSERVED | `test_gate_12_capture_mints_a_completed_unmanaged_edit` |
| 13 | Atomicity is not laundered | `test_gate_13_a_snapshot_of_an_external_revision_says_so`, `test_eval_reports_the_coherence_of_the_snapshot_it_scored` |
| 14 | An in-place write to the published index fails loudly | `test_gate_14_an_in_place_write_to_the_published_index_fails_loudly` |
| 15 | Published stores are self-contained | `test_the_published_graph_is_self_contained`, `test_a_build_with_a_sidecar_is_not_published` |
| 16 | Wiki immutability matrix, all five edits | `test_modifying_the_live_file_in_place_does_not_reach_the_snapshot`, `test_renaming_the_live_file_does_not_reach_the_snapshot`, `test_deleting_the_live_file_does_not_reach_the_snapshot`, `test_atomically_replacing_the_live_file_does_not_reach_the_snapshot`, `test_truncating_the_live_file_does_not_reach_the_snapshot` |
| 17 | Truthful stamp | `test_gate_17_a_rebuild_stamps_the_revision_it_read` |
| 18 | A rebuild refuses a SUSPECT store | `test_gate_18_a_suspect_store_is_refused` |
| 19 | Claims guard | `test_gate_19_an_unadapted_claims_ledger_fails_capture_closed` |
| 20 | Regression floor | full pytest 0 failed; `check.sh` all 24 gates; `public_repo_guard`; `git diff --check` (recorded in the closing change entry) |
```

Before trusting the table, confirm that each named test proves what its row says, not merely that it exists:

```bash
for t in test_two_unwrapped_writes_are_two_implicit_mutations test_an_incomplete_operation_stops_for_review test_a_crash_after_publishing_but_before_completing_still_orphans test_an_accepted_reconciliation_clears_an_incomplete_orphan test_the_published_graph_is_self_contained; do grep -rn "def $t" -A14 tests | head -16; echo; done
```

Expected: each body asserts the behaviour in its row. If one does not, do not relabel it: write the missing test into Task 3, or leave the item open and say so.

- [ ] **Step 4: Run it**

```bash
PYTHONPATH=. python -m pytest -q tests/test_z0_closure.py
sed -i '' 's/`test_gate_17_a_rebuild_stamps_the_revision_it_read`/`test_gate_17_renamed`/' docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md
PYTHONPATH=. python -m pytest -q tests/test_z0_closure.py -k every_gate_item 2>&1 | grep -E "17|failed"
git checkout docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md
```

Expected: all pass. The rename falsification then fails, naming item 17. The final `git checkout` discards only that falsification: §10.5 must already be committed. So commit the matrix before running the falsification line, or re-apply it afterwards.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md tests/test_z0_closure.py
git commit -m "zurvan: Z0 closure matrix - all 20 items named, and a test that fails if one rots"
```

---

## Task 4 — Verify, close Z0, and record

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md` (the header **Status** line), `docs/superpowers/specs/2026-09-18-zurvan-2-amendments.md` (the log row), `CLAUDE.md` (the Z0 bullet), `AGENTS.md`, `CHANGELOG.md`, `docs/agents-history.md`

**Interfaces:**
- Consumes: everything above. Produces no code.

- [ ] **Step 1: The regression floor (item 20)**

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider 2>&1 | tail -2
ls data/search.sqlite.building-* 2>/dev/null && echo "REBUILD IN FLIGHT - STOP" || bash scripts/check.sh > ~/.zurvan/measurements/z0-step7-check.txt 2>&1; echo "check.sh exit=$?"
PYTHONPATH=. python scripts/public_repo_guard.py && git diff --check main...HEAD && echo clean
```

Expected: pytest `0 failed`, and exactly 12 more passes than the step-6 close (1325): 6 in `test_z0_closure.py`, 1 for gate 4, and `test_plan_integrity`'s 5 parametrized cases for this plan. `check.sh exit=0`, with `No unresolved implicit callers.` in its `[13/23]` output. Clean. If anything fails, Z0 stays OPEN and the failure is reported as failed.

- [ ] **Step 2: Close Z0 in the record, only if Step 1 was green.** Change the spec header's `**Status:**` to `**Status:** Z0 CLOSED 2026-09-28 — all 20 §10 items demonstrated by named tests (§10.5).` Replace the CLAUDE.md bullet `- **Z0 is OPEN.** …` with a `- **Z0 is CLOSED (2026-09-28).**` bullet that keeps every operational rule it carries: `--require`, `SNAPSHOT_NOT_READY`, leases/GC, gate 6 is legacy and UNVERIFIED, and a rebuild refuses SUSPECT and waits. Add amendment log row 10: step 7 closure, with no design change. Add a dated `Raouf:` entry to AGENTS.md and CHANGELOG.md, and move AGENTS.md's oldest entry verbatim to `docs/agents-history.md`.

```bash
grep -n "Z0 CLOSED\|Z0 is CLOSED" docs/superpowers/specs/2026-09-18-z0-coherent-snapshots-design.md CLAUDE.md
PYTHONPATH=. python -m pytest -q tests/test_plan_integrity.py tests/test_z0_closure.py | tail -1
PYTHONPATH=. python scripts/public_repo_guard.py && git diff --check
git add CLAUDE.md AGENTS.md CHANGELOG.md docs/
git commit -m "zurvan: Z0 CLOSED - all 20 gate items demonstrated by named tests"
```

- [ ] **Step 3: Write-back** (after every measurement)

```bash
PYTHONPATH=. python scripts/cli.py decision add --title "Z0 closed: all 20 §10 gate items demonstrated by named tests; the implicit inventory is gated in check.sh" --reason "The closure matrix is enforced by tests/test_z0_closure.py so a rename cannot unprove an item; callers --strict makes item 3 a gated query rather than an assertion" --status accepted --tags zurvan z0
```
