# A public Zurvan: a single-commit mirror of a scanned tree, never the private history

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Zurvan's current code as a new public repository containing one commit of a tree that has passed every leak scan and whose test suite is green for a stranger, while `Raoof128/Project-Zurvan` and its full history stay private.

**Architecture:** `scripts/publish_mirror.py` exports `HEAD` with `git archive` into a directory outside the repo, runs the leak scans (private names, instrument items, credential shapes, home paths, client names) with inputs that exist only on the owner's machine, runs the suite there with an empty `HOME`, and only then initialises a one-commit repository. Publishing that directory is a separate, explicitly approved step. The private history — which carries a private project name in two blobs and every instrument item — is never pushed anywhere public.

**Tech Stack:** Python 3.12 stdlib, `git`, `gh` CLI.

**Spec:** this plan (no prior spec; the decision is option B of the 2026-09-28 public-readiness report, chosen by Raouf).

## Global Constraints

- **Do not `git push` to the public remote.** Only push when Raouf explicitly asks for that specific push. (CLAUDE.md.) The mirror's publication is one such push and is its own approval step (Task 4).
- **Never quote the retrieval instrument verbatim.** Reference items by index. (CLAUDE.md.)
- **Frozen research artifacts must not be mutated.** (AGENTS.md.) One instrument item lives in `eval/provenance_real_*.jsonl` and two `data/traces/` files; it is published with them, not edited out.
- **A tracked page must DECLARE its project.** `public_repo_guard` must pass on the exported tree. (AGENTS.md.)
- **A failed gate is reported as failed.** (CLAUDE.md.)
- **Plan documents are gated by `tests/test_plan_integrity.py`.**

## Measured inputs (2026-09-28 gauntlet — scans run, nothing pushed)

```text
private repo               PRIVATE since 2026-09-16; 217 commits, 1,310 blobs, 0 forks,
                           0 stars; Apache-2.0; 58 clones / 29 unique in the last 14 days
private names, history     13 of 14 never appear; 1 in two old AGENTS.md/CHANGELOG.md blobs
instrument, history        50 of 50 items appear; ALL introduced 2026-05-30 .. 2026-09-13,
                           i.e. while the repository was PUBLIC. Already exposed; this plan
                           does not change that and must not be described as protecting it.
Software Heritage          origin not archived (API: "not found")
exported HEAD (426 files)  private names 0; instrument items 1 (frozen eval + 2 frozen
                           traces); credential-shaped 1 (synthetic test key, 16 distinct
                           chars, sequential runs); home path 1 (step-3 plan); client name 1
                           (tests/conftest.py)
stranger suite             empty HOME: 16 failed + 1 error. Each re-run alone:
                           4 pass once the export is a git repository;
                           4 need the local-only instrument (InstrumentMissing) -
                             3 directly, 1 via `zurvan eval validate-gold` inside
                             test_cli_subcommands_work_from_foreign_cwd;
                           8 read the gitignored .private-projects (7 plan-integrity
                             parametrizations + the gauntlet self-leak test);
                           1 reads a LIVE data/graph.sqlite (latent isolation bug)
GitHub docs (2026)         push protection for users is ON by default for public repos and
                           BLOCKS a push containing a detected secret (bypass reasons incl.
                           "It's used in tests"); secret scanning covers entire history;
                           making a repo public exposes all history; after a rewrite,
                           removed data survives in clones, forks and cached SHA views
```

## Review Focus

1. **A stranger clones the mirror and runs `pytest`.** Expected: green, with skips that name the local-only input they need. (Task 1, `test_suite_is_green_for_a_stranger` via `publish_mirror --check`.)
2. **The owner runs the suite.** Expected: nothing that was checked before is now skipped — the skips fire only when the input is absent. (Task 1, `test_owner_runs_the_leak_checks`.)
3. **Push protection on publish.** Expected: no credential-shaped literal in the tree, so no bypass is needed. (Task 2.)
4. **A later re-publish.** Expected: a new commit on the mirror built the same way; the mirror never receives private history. (Task 3, `--refuse-if-remote-is-private-origin`.)
5. **The mirror is mistaken for the source of truth.** Expected: its README states it is a published snapshot. (Task 3.)

---

## Task 1 — The suite is green for a stranger

**Files:** Modify `tests/conftest.py`, `tests/test_plan_integrity.py`, `tests/test_gauntlet_completeness.py`, `tests/test_instruments.py`, `tests/test_eval_search.py`, `tests/test_check_gate_cli_smoke.py`, `tests/test_cli.py`, `tests/test_mcp_tools.py`

- [ ] **Step 1: Shared skip markers in `tests/conftest.py`**

```python
def _instrument_present() -> bool:
    try:
        from scripts.instruments import gold_path
        return gold_path().is_file()
    except Exception:
        return False


#: The retrieval instrument is local-only by design (CLAUDE.md). A public clone
#: cannot have it; tests that need it say so instead of failing.
needs_instrument = pytest.mark.skipif(
    not _instrument_present(),
    reason="needs the local-only retrieval instrument (~/.zurvan/instruments)")

#: `.private-projects` is gitignored: only the owner can check for these names.
needs_private_list = pytest.mark.skipif(
    not (Path(__file__).resolve().parents[1] / ".private-projects").is_file(),
    reason="needs the gitignored .private-projects list (owner-only check)")
```

- [ ] **Step 2: Apply them** to exactly what the gauntlet found: `@needs_instrument` on `test_eval_search_forwards_threshold_and_hybrid_flag`, `test_no_instrument_item_falls_below_the_registration_floor`, `test_run_search_evaluation_json_output`; `@needs_private_list` on `test_a_plan_does_not_leak_what_its_subject_protects` and `test_the_gauntlet_does_not_itself_leak`. **Split** `test_cli_subcommands_work_from_foreign_cwd` — its `eval validate-gold` half gets `@needs_instrument`, its launcher (`scripts/zurvan version`) half stays unconditional, so a stranger still runs the part they can. Make `test_tool_zurvan_graph_stats_struct` build its own graph under `tmp_path` rather than read the live `data/graph.sqlite`.

```python
from tests.conftest import needs_instrument, needs_private_list
```

- [ ] **Step 3: Owner check** — the owner's run must skip nothing it ran before:

```bash
PYTHONPATH=. python -m pytest -q -p no:cacheprovider -rs 2>&1 | grep -c "local-only\|owner-only"   # expect 0
```

- [ ] **Step 4: Stranger check** — the exported tree, empty `HOME`, git-initialised: 0 failed.
- [ ] **Step 5: Commit** — `git commit -m "zurvan: the suite is green for a stranger, and says what it skipped"`

---

## Task 2 — Hygiene in the tracked tree

**Files:** Modify `tests/test_evidence_redact.py`, `tests/conftest.py:238`, `docs/superpowers/plans/2026-09-19-z0-step3-immutable-bytes.md:116,1735`

- [ ] **Step 1:** Build the synthetic AWS key at runtime so no credential-shaped literal is in the source (push protection matches literals; the redaction test still sees the full string):

```python
FAKE_AWS_KEY = "AKIA" + "0123456789" + "ABCDEF"      # synthetic; assembled so no literal key is committed
```

- [ ] **Step 2:** `tests/conftest.py:238` — the client name becomes `ClientX`. The step-3 plan's two `cd /Users/...` lines become `cd <repo>` (a privacy redaction of a committed record, the one kind of edit this project makes to one; recorded in the change entry).
- [ ] **Step 3:** Re-run the export scan: credential-shaped 0, home path 0, client name 0, private names 0, instrument items exactly the frozen five files.

```bash
PYTHONPATH=. python scripts/publish_mirror.py --scan-only
```

- [ ] **Step 4: Commit** — `git commit -m "zurvan: no credential-shaped literal, client name or home path in the tracked tree"`

---

## Task 3 — `publish_mirror.py`: export, scan, test, commit — never push

**Files:** Create `scripts/publish_mirror.py`, `tests/test_publish_mirror.py`

**Interfaces:** `export_head(dest) -> Path`, `scan(tree) -> dict`, `stranger_suite(tree) -> tuple`, `build(dest, allow_instrument_paths=FROZEN_INSTRUMENT_PATHS) -> Path` (exits non-zero on any finding outside the allow-list). `--scan-only` scans `HEAD` without building.

- [ ] **Step 1: Failing tests** — a planted private name, a planted credential literal, a planted home path, and an instrument item outside the frozen allow-list each make `scan` report it and `build` refuse; a clean tree builds a directory with exactly one commit and a `README` banner stating it is a published snapshot of a private source repository.

```python
def test_a_planted_credential_refuses_the_build(tmp_path):
    tree = tmp_path / "tree"; tree.mkdir()
    (tree / "x.py").write_text('K = "' + "AKIA" + "Q" * 16 + '"\n')
    assert pm.scan(tree)["credential-shaped"] == ["x.py"]
```

- [ ] **Step 2:** Implement with the scanner already proved in the gauntlet (whole-word, case-insensitive private names from `.private-projects`; instrument items read from `gold_path()` and reported by index only; the credential regex; `/Users/`; client names from a gitignored `.private-clients` list). The mirror commit uses the owner's author identity and message `Zurvan published snapshot <date> (source <short sha>)`.
- [ ] **Step 3:** `build` refuses if the destination already has a remote equal to the private origin.
- [ ] **Step 4: Commit.**

---

## Task 4 — Publish (outward-facing; Raouf approves the exact command)

Repository name chosen by Raouf 2026-10-01: `Raoof128/zurvan`. Naming it is not
the approval to publish; Step 2 still stops for an explicit yes.

- [ ] **Step 1:** `python scripts/publish_mirror.py --build ~/.zurvan/mirror` — prints the scan table and the stranger-suite result; exits non-zero on any finding.
- [ ] **Step 2: Stop and show Raouf** the table, the file count, the one commit, and the exact publish command, and wait for a yes:

```bash
gh repo create Raoof128/zurvan --public --source ~/.zurvan/mirror --push --description "Local-first knowledge engine"
```

- [ ] **Step 3: Verify what published** — `gh api repos/Raoof128/zurvan` (visibility public, 1 commit), `gh api repos/Raoof128/zurvan/secret-scanning/alerts` (0), and a fresh `git clone` + stranger suite green.
- [ ] **Step 4: Record** — AGENTS/CHANGELOG entry; CLAUDE.md: the private repo's remote stays private, the mirror is updated only through `publish_mirror.py`; a Zurvan decision; the instrument's prior public exposure (2026-05-30 .. 2026-09-16) filed as an open question for Z1's sealed holdout.

```text
this plan protects: private history (name leak, 49 history-only instrument items)
this plan does NOT protect: the instrument itself — every item was public before 2026-09-16
```
