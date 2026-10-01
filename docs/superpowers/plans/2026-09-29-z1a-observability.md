# Z1a — Retrieval Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make retrieval observable without leaking: trace privacy modes with a
private-by-default `summary`, private trace files with retention, stage
timings, per-item eval runs with a timing-free result digest, rank movers
by index, a dataset registry that retires the burned instrument, and a
historical-reproducibility check on a coherent Z0 snapshot.

**Architecture:**
- **Traces.** `trace_schema` gains v2 and a `trace_mode`. `trace_writer` owns
  file privacy, the protected-eval guard and retention. `context_export`
  decides what each mode records.
- **Search.** `search_hybrid` fills an optional `timings` dict without changing
  its return value.
- **Eval.** `eval_search` is split into a pure `evaluate()` core plus the
  printing wrapper. New modules `eval_datasets.py` (registry, states,
  eligibility) and `eval_runs.py` (saved runs, movers, reproduce) sit beside
  it.
- **Ranking.** Z1a changes no ranking; Task 9 proves it against the BASE
  commit.

**Tech Stack:** Python 3 stdlib (`contextvars`, `hashlib`, `statistics`, `subprocess`), pytest, SQLite (existing).

**Spec:** `docs/superpowers/specs/2026-09-29-z1-observability-sealed-eval-design.md` (§3, §9 "Z1a closes when").

## Global Constraints

- Z1a changes **no ranking**. Any retrieval ranking or indexing change requires a before/after `eval_search` in CHANGELOG (AGENTS.md rule), and Task 9 does exactly that to prove equality.
- **Never quote the retrieval instrument verbatim.** Reference items by INDEX. No per-item digest may appear anywhere visible (confirmation oracle). Whole-file digests are allowed.
- **Frozen research artifacts must not be mutated:** `eval/provenance_real_*.jsonl`, their committed traces in `data/traces/`, and the 2C/1B metrics (86%/79%). The 35 git-tracked trace files are never modified, re-chmodded by a test, or pruned.
- Tests never touch the live `wiki/` or live traces except read-only or `--dry-run`. Use `tmp_path` roots (`tests/conftest.py` fails a run that writes the live wiki).
- `search --trace` defaults to `summary`, which contains **no raw query and no snippets**. Raw text only with explicit `--trace-mode full`. `protected_eval` is never selectable from the CLI.
- Trace dirs are `0700`, trace files `0600`.
- Retention: untracked traces older than 30 days or beyond the newest 500. Git-tracked traces are never removed. If tracked files cannot be listed, prune removes nothing.
- The historical reproducibility digest covers ranked ids and quality metrics only, never timings, timestamps or RSS.
- Commits: local only. Never `git push`.
- After any scripted edit: compare `git diff --stat` with this plan (memory: diff-size-is-a-check).

## Review Focus

1. **A trace written with the default flags on a real query.** A reasonable person expects no fragment of the query text anywhere in it: not in the title, the summary, `heading` fields or graph node titles. Task 3 test `test_summary_trace_contains_no_query_text_anywhere` greps the entire file bytes.
2. **Retention run inside the live repo.** Expect zero tracked files ever deleted, even with `days=0, keep=0`. Task 2 test `test_prune_dry_run_on_live_repo_never_selects_tracked`.
3. **A frozen v1 trace re-validated after the schema bump.** Expect exactly the same validity as before, so the RQ1 pilot numbers are unchanged. Task 1 test `test_committed_v1_traces_have_no_schema_issue` and the Task 9 live provenance run.
4. **`eval search --json` output.** Expect item indices, never query text, even for failures. Task 6 test `test_json_output_never_contains_query_text`.
5. **Movers or save-run on a HOLDOUT/RESERVE dataset.** Expect a refusal before any per-item row is written. Task 7 test `test_save_run_refuses_holdout_before_writing`.


## Gauntlet (2026-09-29, dry run in a throwaway worktree)

Every task was applied verbatim at `9dc181a`, and its tests ran red, then
green. Each task was then falsified by mutation. The full suite went from
**1333 → 1408 passed**, with the same 3 failures before and after. All 3 are
environmental: a worktree has no `wiki/` and no graph DB.

Defects found and fixed in this plan:

| # | Defect | Evidence | Fix |
|---|---|---|---|
| G0 | Committing this plan turned `main` red | `test_plan_integrity`: `_rec`/`test_unknown_mode_is_refused` declared with different signatures in different tasks | renamed per task (`9dc181a`) |
| G1 | Prune's "never delete a tracked Markdown twin" check untested | mutant survived | `test_prune_never_removes_a_tracked_markdown_twin` |
| G2 | `from tests.… import` cannot work: `tests/` is not a package | ModuleNotFoundError (Task 4 fixture, Task 9 matrix) | `from test_hybrid_search import tmp_index`; closure scans files like `test_z0_closure` |
| G3 | Result digest could ignore the rankings and still pass | mutant survived: the old test's reorder also changed top-1 | `test_result_digest_sees_a_ranking_change_the_metrics_cannot` |
| G4 | p95 wiring and formula untested (constant samples) | 2 mutants survived | nearest-rank `_percentile`; two percentile tests |
| G5 | `reproduce()` passed `components=` AND `required=`, which `capture()` refuses (`SnapshotComponentError`); fakes hid it | read of `measurement_snapshot.py:626` | `required=` only; test binds the call to the real signature |
| G6 | `eval reproduce` crashed printing its result: `cli.py`'s dispatcher binds `json` locally | UnboundLocalError after a 1m50s live run | local `import json`; CLI test (red, then green) |
| G7 | Exit condition checked `"COHERENT"`; the capture says `"VERIFIED"` | live run: equal digests, exit 1 | `build_snapshot.VERIFIED`; tests use the real vocabulary |
| — | Task 4 Step 3, Task 6 Step 3, Task 7 wiring were prose, not code | no-placeholder rule | replaced by the exact functions the dry run proved |
| G8 | Embedding unified diffs in the plan fails `git diff --check`: a blank context line is a single space | `git diff --check` flagged 28 lines; a whitespace-stripped diff no longer applies | full replacement functions instead of diffs |
| — | No test for `--explain`, the malformed-line print, or the movers CLI | — | added |

Mutation results (final): Task 1 4/4, Task 2 9/9, Task 3 6/6, Task 4 4/4,
Task 5 4/4, Task 6 9/9, Task 7 5/5, Task 8 3/3, closure 1/1 caught.

Measured on the live store (rev 262, CLEAN), using worktree code, read-only
except for released snapshots:

- **Ranking unchanged:** BASE and Z1a top-10 lists for all 50 items are byte-identical on one VERIFIED snapshot. Metrics are equal: top-1 0.72, top-3 0.74, MRR 0.739, 13 failures. 3m18s.
- **`eval reproduce`:** two digests equal (`f1406842…`), coherence VERIFIED at revision 262 MANAGED, snapshot released. About 2m09s.
- **RQ1 pilot unchanged:** `eval_provenance` on `provenance_real_gold.jsonl` is identical under BASE and Z1a (source recall 0.861, raw-leak 0, hash integrity 1.0).
- **Retention:** a prune costs about 7 ms per trace write. On the live repo it keeps 33 tracked traces and selects 0, even at `days=0, keep=0`.
- **Registry:** the live instrument resolves to `search_gold RETIRED` by whole-file digest.
- **Summary trace, end to end:** the query is absent from the file bytes, file 0600 / dir 0700, no Markdown twin.

A worktree cannot show:
- the post-write retention prune on a live trace directory that holds untracked traces (there are none today);
- `trace prune` without `--dry-run` on live;
- effects on the ledger. Z1a writes no knowledge, so none are expected. Task 9 checks `knowledge status` afterwards.

---

### Task 1: Trace schema v2 with `trace_mode`

**Files:**
- Modify: `scripts/trace_schema.py`
- Modify: `scripts/trace_validate.py`
- Modify: `tests/test_trace_schema.py:24` (`v1` → `v2`)
- Test: `tests/test_trace_modes.py` (create)

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION = "zurvan.trace.v2"`, `LEGACY_SCHEMA_VERSIONS = ("zurvan.trace.v1",)`, `SUPPORTED_SCHEMA_VERSIONS`, `TRACE_MODES = ("summary", "full", "protected_eval")`.
  - `TraceRecord.trace_mode: str = "full"`.
  - `record_from_dict` round-trips v1 unchanged.

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_modes.py`:

```python
import json
import subprocess

import pytest

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import (SCHEMA_VERSION, TRACE_MODES, TraceRecord,
                                  record_from_dict)
from scripts.trace_validate import validate_trace_file

V1 = "zurvan.trace.v1"


def _v2_rec(**kw):
    base = dict(trace_id="trace-20260929T000000Z-modes001", title="t", summary="s")
    base.update(kw)
    return TraceRecord(**base)


def test_new_records_are_v2_and_carry_their_mode():
    data = _v2_rec(trace_mode="summary").to_dict()
    assert data["schema_version"] == SCHEMA_VERSION == "zurvan.trace.v2"
    assert data["trace_mode"] == "summary"
    assert TRACE_MODES == ("summary", "full", "protected_eval")


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="trace_mode"):
        _v2_rec(trace_mode="verbose")


def test_v1_cannot_claim_a_mode():
    # v1 predates modes and stored raw payloads: calling one "summary" would lie.
    with pytest.raises(ValueError, match="trace_mode"):
        _v2_rec(schema_version=V1, trace_mode="summary")


def test_v1_dict_round_trips_byte_for_byte():
    original = {"schema_version": V1, "trace_id": "trace-20260614T000000Z-roundtr1",
                "created_at": "2026-06-14T00:00:00Z", "title": "t", "summary": "s",
                "events": []}
    assert record_from_dict(original).to_dict() == original


def test_validator_accepts_v2_and_rejects_a_bad_mode(tmp_path):
    good = _v2_rec(trace_mode="summary").to_dict()
    path = tmp_path / "good.json"
    path.write_text(json.dumps(good))
    assert validate_trace_file(path).valid

    bad = dict(good, trace_mode="verbose")
    path.write_text(json.dumps(bad))
    result = validate_trace_file(path)
    assert not result.valid and any("trace_mode" in i for i in result.issues)

    missing = {k: v for k, v in good.items() if k != "trace_mode"}
    path.write_text(json.dumps(missing))
    assert any("trace_mode" in i for i in validate_trace_file(path).issues)


def test_committed_v1_traces_have_no_schema_issue():
    # Frozen RQ1 artifacts: the bump must not change how they validate.
    out = subprocess.run(["git", "-C", str(PROJECT_ROOT), "ls-files", "data/traces/*.json"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert out, "expected committed traces"
    for rel in out:
        issues = validate_trace_file(PROJECT_ROOT / rel).issues
        assert not [i for i in issues if "schema_version" in i or "trace_mode" in i], rel
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_modes.py -q`
Expected: FAIL. `TRACE_MODES` cannot be imported (ImportError).

- [ ] **Step 3: Implement**

In `scripts/trace_schema.py`, replace `SCHEMA_VERSION = "zurvan.trace.v1"` with:

```python
SCHEMA_VERSION = "zurvan.trace.v2"
#: v1 predates trace modes and always stored raw payloads. Still read and
#: validated: the committed RQ1 traces are frozen research artifacts.
LEGACY_SCHEMA_VERSIONS = ("zurvan.trace.v1",)
SUPPORTED_SCHEMA_VERSIONS = (SCHEMA_VERSION,) + LEGACY_SCHEMA_VERSIONS
#: summary: no raw query/snippets (default); full: explicit local debugging;
#: protected_eval: forced inside a protected evaluation, aggregates only.
TRACE_MODES = ("summary", "full", "protected_eval")
```

In `TraceRecord`, add the field after `schema_version`:

```python
    schema_version: str = SCHEMA_VERSION
    trace_mode: str = "full"
    validate_id: bool = True
```

Replace the schema check in `__post_init__`:

```python
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"schema_version must be one of {SUPPORTED_SCHEMA_VERSIONS}")
        if self.schema_version == SCHEMA_VERSION:
            if self.trace_mode not in TRACE_MODES:
                raise ValueError(f"trace_mode must be one of {TRACE_MODES}")
        elif self.trace_mode != "full":
            raise ValueError("trace_mode: a v1 trace predates modes and is always 'full'")
```

In `to_dict`, emit the mode for v2 only (a v1 dict round-trips unchanged):

```python
    def to_dict(self) -> Dict[str, Any]:
        data = {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "title": self.title,
            "summary": self.summary,
            "events": [event.to_dict() for event in self.events],
        }
        if self.schema_version == SCHEMA_VERSION:
            data["trace_mode"] = self.trace_mode
        return data
```

In `record_from_dict`, add `trace_mode=data.get("trace_mode", "full"),`.

In `scripts/trace_validate.py`, import `SUPPORTED_SCHEMA_VERSIONS, TRACE_MODES` (keep `SCHEMA_VERSION`) and replace the schema check:

```python
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(f"schema_version must be one of {SUPPORTED_SCHEMA_VERSIONS}")
    elif version == SCHEMA_VERSION and data.get("trace_mode") not in TRACE_MODES:
        issues.append(f"trace_mode must be one of {TRACE_MODES}")
```

In `tests/test_trace_schema.py:24`, change the expected `"zurvan.trace.v1"` to `"zurvan.trace.v2"`. This is a deliberate schema bump.

- [ ] **Step 4: Run to verify they pass, with the rest of the trace suite**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_modes.py tests/test_trace_schema.py tests/test_trace_validate.py tests/test_trace_replay.py tests/test_trace_cli.py tests/test_eval_provenance.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/trace_schema.py scripts/trace_validate.py tests/test_trace_modes.py tests/test_trace_schema.py
git commit -m "zurvan: trace schema v2 with trace_mode; v1 still read and validated unchanged"
```

---

### Task 2: Private trace files, the protected-eval guard, retention

**Files:**
- Modify: `scripts/trace_writer.py`
- Modify: `scripts/cli.py` (the `trace prune` subcommand)
- Modify: `tests/test_trace_writer.py:9` (its record becomes `trace_mode="full"`, which is the default, so no change is needed if the default is kept. Verify.)
- Test: `tests/test_trace_privacy.py` (create)

**Interfaces:**
- Consumes: `TraceRecord.trace_mode` (Task 1).
- Produces:
  - `protected_eval_context()` (context manager), `in_protected_eval() -> bool`, `ProtectedTraceError` (`.code == "PROTECTED_TRACE"`).
  - `TraceStore.prune(*, days=30, keep=500, now=None, dry_run=False) -> PruneResult(removed: list[str], kept_tracked: int, refused: str | None)`.
  - Constants `DIR_MODE=0o700`, `FILE_MODE=0o600`, `RETENTION_DAYS=30`, `RETENTION_KEEP=500`.
  - `TraceStore.write` writes the Markdown twin only for `full` and prunes after writing.

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_privacy.py`:

```python
import os
import stat
import subprocess
import time

import pytest

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import TraceRecord
from scripts.trace_writer import (ProtectedTraceError, TraceStore, in_protected_eval,
                                  protected_eval_context)


def _trace(tid="trace-20260929T000000Z-priv0001", mode="summary"):
    return TraceRecord(trace_id=tid, title="t", summary="s", trace_mode=mode)


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_trace_files_and_dirs_are_private(tmp_path):
    paths = TraceStore(tmp_path).write(_trace(mode="full"))
    assert _mode(paths.json_path) == 0o600
    assert _mode(paths.markdown_path) == 0o600
    assert _mode(paths.json_path.parent) == 0o700
    assert _mode(paths.markdown_path.parent) == 0o700


def test_an_existing_world_readable_trace_is_tightened(tmp_path):
    store = TraceStore(tmp_path)
    target = store.trace_path("trace-20260929T000000Z-priv0001")
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    os.chmod(target, 0o644)
    store.write(_trace())
    assert _mode(target) == 0o600


def test_summary_writes_no_markdown_twin_full_does(tmp_path):
    store = TraceStore(tmp_path)
    assert not store.write(_trace(mode="summary")).markdown_path.exists()
    assert store.write(_trace("trace-20260929T000000Z-priv0002", "full")).markdown_path.exists()


def test_protected_context_refuses_any_other_mode_before_writing(tmp_path):
    store = TraceStore(tmp_path)
    with protected_eval_context():
        assert in_protected_eval()
        for mode in ("summary", "full"):
            with pytest.raises(ProtectedTraceError) as exc:
                store.write(_trace(mode=mode))
            assert exc.value.code == "PROTECTED_TRACE"
        assert not store.trace_path("trace-20260929T000000Z-priv0001").exists()
        paths = store.write(_trace("trace-20260929T000000Z-priv0003", "protected_eval"))
        assert paths.json_path.exists() and not paths.markdown_path.exists()
    assert not in_protected_eval()


def _git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def _age(path, days, now):
    os.utime(path, (now - days * 86400, now - days * 86400))


def test_prune_removes_old_untracked_and_never_tracked(tmp_path):
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    now = time.time()
    tracked = store.write(_trace("trace-20260929T000000Z-tracked1", "full"))
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "data/traces", "wiki/traces"], check=True)
    old = store.write(_trace("trace-20260929T000000Z-oldtrace", "full"))
    new = store.write(_trace("trace-20260929T000000Z-newtrace"))
    for p in (tracked.json_path, tracked.markdown_path, old.json_path, old.markdown_path):
        _age(p, 40, now)

    result = store.prune(now=now)

    assert result.refused is None
    assert sorted(result.removed) == sorted([str(old.json_path), str(old.markdown_path)])
    assert tracked.json_path.exists() and tracked.markdown_path.exists()
    assert new.json_path.exists()
    assert result.kept_tracked == 1


def test_prune_keeps_only_the_newest_n_untracked(tmp_path):
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    now = time.time()
    ids = [f"trace-20260929T000000Z-keep000{i}" for i in range(3)]
    for age, tid in zip((3, 2, 1), ids):
        _age(store.write(_trace(tid)).json_path, age, now)
    removed = store.prune(keep=1, now=now).removed
    assert sorted(removed) == sorted(str(store.trace_path(t)) for t in ids[:2])


def test_prune_dry_run_deletes_nothing(tmp_path):
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    p = store.write(_trace()).json_path
    result = store.prune(days=0, keep=0, dry_run=True)
    assert str(p) in result.removed and p.exists()


def test_prune_outside_git_refuses_and_removes_nothing(tmp_path):
    store = TraceStore(tmp_path)
    p = store.write(_trace()).json_path
    result = store.prune(days=0, keep=0)
    assert result.refused and result.removed == [] and p.exists()


def test_prune_dry_run_on_live_repo_never_selects_tracked():
    tracked = set(subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files", "data/traces", "wiki/traces"],
        capture_output=True, text=True, check=True).stdout.split())
    result = TraceStore(PROJECT_ROOT).prune(days=0, keep=0, dry_run=True)
    assert result.refused is None
    selected = {os.path.relpath(p, PROJECT_ROOT) for p in result.removed}
    assert not (selected & tracked)
    tracked_json = [t for t in tracked if t.startswith("data/traces/trace-") and t.endswith(".json")]
    assert result.kept_tracked == len(tracked_json) > 0


def test_prune_never_removes_a_tracked_markdown_twin(tmp_path):
    # An untracked JSON whose Markdown twin IS tracked: the JSON may go,
    # the tracked twin may not.
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    now = time.time()
    paths = store.write(_trace("trace-20260929T000000Z-twin0001", "full"))
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "wiki/traces"], check=True)
    _age(paths.json_path, 40, now)
    result = store.prune(now=now)
    assert result.removed == [str(paths.json_path)]
    assert paths.markdown_path.exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_privacy.py -q`
Expected: FAIL. `ProtectedTraceError` cannot be imported.

- [ ] **Step 3: Implement in `scripts/trace_writer.py`**

Add these imports: `contextvars`, `os`, `subprocess`, `time`, `from contextlib import contextmanager`, `from dataclasses import dataclass, field`. Then add at module level:

```python
DIR_MODE = 0o700
FILE_MODE = 0o600
RETENTION_DAYS = 30
RETENTION_KEEP = 500

_PROTECTED = contextvars.ContextVar("zurvan_protected_eval", default=False)


class ProtectedTraceError(RuntimeError):
    """A non-protected trace was about to be written inside a protected
    evaluation (design §3.1). Raised before any byte lands."""
    code = "PROTECTED_TRACE"


@contextmanager
def protected_eval_context():
    """Everything written inside is forced to `protected_eval` or refused.
    A flag cannot downgrade it: the guard is in the writer, not the caller."""
    token = _PROTECTED.set(True)
    try:
        yield
    finally:
        _PROTECTED.reset(token)


def in_protected_eval() -> bool:
    return _PROTECTED.get()


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, FILE_MODE)  # O_CREAT's mode does not apply to an existing file


@dataclass
class PruneResult:
    removed: List[str] = field(default_factory=list)
    kept_tracked: int = 0
    refused: str | None = None
```

Replace `TraceStore.write`:

```python
    def write(self, record: TraceRecord) -> TracePaths:
        if in_protected_eval() and record.trace_mode != "protected_eval":
            raise ProtectedTraceError(
                f"PROTECTED_TRACE: a {record.trace_mode!r} trace cannot be written "
                "inside a protected evaluation")
        paths = self._paths_for(record.trace_id)
        _private_dir(paths.json_path.parent)
        _write_private(paths.json_path,
                       json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
        if record.trace_mode == "full":
            _private_dir(paths.markdown_path.parent)
            _write_private(paths.markdown_path, render_trace_markdown(record))
        self.prune()
        return paths
```

Add these methods to `TraceStore`:

```python
    def _tracked_names(self) -> dict | None:
        """Basenames git tracks under data/traces and wiki/traces, or None when
        they cannot be listed — and then nothing may be pruned."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.project_root), "ls-files", "-z", "--",
                 "data/traces", "wiki/traces"],
                capture_output=True, check=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        names = {"data": set(), "wiki": set()}
        for rel in filter(None, out.decode("utf-8", "replace").split("\0")):
            parts = rel.split("/")
            if parts[:2] == ["data", "traces"]:
                names["data"].add(parts[-1])
            elif parts[:2] == ["wiki", "traces"]:
                names["wiki"].add(parts[-1])
        return names

    def prune(self, *, days: int = RETENTION_DAYS, keep: int = RETENTION_KEEP,
              now: float | None = None, dry_run: bool = False) -> PruneResult:
        """Retention (design §3.2): remove untracked traces older than `days`
        or beyond the newest `keep`. A git-tracked trace is a frozen research
        artifact and is never removed."""
        tracked = self._tracked_names()
        if tracked is None:
            return PruneResult(refused="cannot list git-tracked traces; pruning nothing")
        now = time.time() if now is None else now
        cutoff = now - days * 86400
        candidates, kept_tracked = [], 0
        for path in (self.data_dir.glob("trace-*.json") if self.data_dir.exists() else []):
            if path.name in tracked["data"]:
                kept_tracked += 1
                continue
            candidates.append((path.stat().st_mtime, path.stem))
        candidates.sort(reverse=True)  # newest first
        removed = []
        for position, (mtime, trace_id) in enumerate(candidates):
            if position < keep and mtime >= cutoff:
                continue
            for path, kind in ((self.data_dir / f"{trace_id}.json", "data"),
                               (self.wiki_dir / f"{trace_id}.md", "wiki")):
                if path.name in tracked[kind] or not path.exists():
                    continue
                removed.append(str(path))
                if not dry_run:
                    path.unlink()
        return PruneResult(removed=removed, kept_tracked=kept_tracked)
```

In `scripts/cli.py`, after `trace_replay.add_argument("trace_id")`:

```python
    trace_prune = trace_sub.add_parser("prune", help="Apply trace retention (never touches git-tracked traces)")
    trace_prune.add_argument("--days", type=int, default=30)
    trace_prune.add_argument("--keep", type=int, default=500)
    trace_prune.add_argument("--dry-run", action="store_true")
```

and in the `trace` dispatch, before the final `else`:

```python
        elif args.trace_action == "prune":
            result = store.prune(days=args.days, keep=args.keep, dry_run=args.dry_run)
            if result.refused:
                print(f"Refused: {result.refused}", file=sys.stderr)
                sys.exit(1)
            verb = "Would remove" if args.dry_run else "Removed"
            print(f"{verb} {len(result.removed)} trace file(s); kept {result.kept_tracked} tracked.")
```

- [ ] **Step 4: Run to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_privacy.py tests/test_trace_writer.py tests/test_trace_cli.py tests/test_trace_retrieval_integration.py -q`
Expected: the privacy, writer and CLI tests pass. `test_trace_retrieval_integration` may still pass: its record is built by `context_export`, which does not set a mode yet, so it gets the default `full`. Task 3 changes that.

- [ ] **Step 5: Commit**

```bash
git add scripts/trace_writer.py scripts/cli.py tests/test_trace_privacy.py
git commit -m "zurvan: private trace files (0700/0600), protected-eval write guard, retention that never touches tracked traces"
```

---

### Task 3: Retrieval traces honour the mode; `--trace-mode`

**Files:**
- Modify: `scripts/context_export.py` (`_write_retrieval_trace`, `search_memory`, `export_context`, add `import hashlib` if absent)
- Modify: `scripts/cli.py` (search and context parsers and dispatch)
- Modify: `tests/test_trace_retrieval_integration.py` (one call: the mirror test passes `trace_mode="full"`)
- Test: `tests/test_trace_modes_retrieval.py` (create)

**Interfaces:**
- Consumes: `TraceRecord(trace_mode=...)`, `TraceStore.write`.
- Produces:
  - `_write_retrieval_trace(..., trace_mode: str = "summary", timings: dict | None = None) -> str`.
  - `search_memory(..., trace_mode="summary", explain=False)`, `export_context(..., trace_mode="summary")`.
  - CLI `--trace-mode {summary,full}`, which implies `--trace`.

- [ ] **Step 1: Write the failing tests**

`tests/test_trace_modes_retrieval.py`:

```python
import json

import pytest

import scripts.context_export as ce

QUERY = "unmistakable-query-marker zebra quartz"
RESULTS = [{"chunk_id": "c1", "source_path": "wiki/a.md", "heading": "Secret heading",
            "text": "snippet body text", "keyword_score": 0.5, "semantic_score": 0.5,
            "hybrid_score": 0.5}]
NODES = [{"path": "wiki/d.md", "node_type": "decision", "title": "Node title text",
          "depth": 1, "relation": "r", "source_id": "n1"}]


def _write(tmp_path, monkeypatch, mode, tid):
    monkeypatch.setattr(ce, "PROJECT_ROOT", tmp_path)
    return ce._write_retrieval_trace(command="context", query=QUERY, mode="hybrid", limit=5,
                                     results=RESULTS, graph_enabled=True, graph_depth=1,
                                     graph_nodes=NODES, trace_id=tid, trace_mode=mode,
                                     timings={"total": 1.5})


def test_summary_trace_contains_no_query_text_anywhere(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, "summary", "trace-20260929T000000Z-summ0001")
    raw = open(path, encoding="utf-8").read()
    for fragment in ("unmistakable", "zebra", "Secret heading", "snippet body", "Node title"):
        assert fragment not in raw
    data = json.loads(raw)
    assert data["trace_mode"] == "summary"
    q = data["events"][0]["payload"]
    assert len(q["query_sha256"]) == 64 and q["query_len"] == len(QUERY)
    assert data["events"][1]["payload"]["timings_ms"] == {"total": 1.5}
    assert not (tmp_path / "wiki" / "traces").exists()


def test_full_trace_keeps_raw_text_and_mirror(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, "full", "trace-20260929T000000Z-full0001")
    data = json.load(open(path))
    assert data["events"][0]["payload"]["query"] == QUERY
    assert (tmp_path / "wiki" / "traces" / "trace-20260929T000000Z-full0001.md").exists()


def test_protected_eval_trace_holds_aggregates_only(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch, "protected_eval", "trace-20260929T000000Z-prot0001")
    raw = open(path).read()
    for fragment in ("unmistakable", "wiki/a.md", "c1", "query_sha256"):
        assert fragment not in raw
    types = [e["event_type"] for e in json.loads(raw)["events"]]
    assert types == ["retrieval.query", "retrieval.result"]


def test_unknown_retrieval_trace_mode_is_refused(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        _write(tmp_path, monkeypatch, "verbose", "trace-20260929T000000Z-bad00001")


def test_cli_trace_mode_implies_trace_and_refuses_protected(monkeypatch, capsys):
    import scripts.cli as cli
    seen = {}
    # cli.py binds `from scripts.context_export import search_memory` at import
    monkeypatch.setattr(cli, "search_memory", lambda *a, **k: seen.update(k))
    monkeypatch.setattr("sys.argv", ["zurvan", "search", "x", "--trace-mode", "full"])
    cli.main()
    assert seen["trace"] is True and seen["trace_mode"] == "full"
    monkeypatch.setattr("sys.argv", ["zurvan", "search", "x", "--trace-mode", "protected_eval"])
    with pytest.raises(SystemExit):
        cli.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_modes_retrieval.py -q`
Expected: FAIL. `_write_retrieval_trace()` got an unexpected keyword argument `trace_mode`.

- [ ] **Step 3: Implement**

In `scripts/context_export.py`, change the signature of `_write_retrieval_trace` to add `trace_mode: str = "summary", timings: Dict[str, float] | None = None,` after `trace_id`. Replace its body up to `record = TraceRecord(` with:

```python
    if trace_mode not in ("summary", "full", "protected_eval"):
        raise ValueError(f"unknown trace_mode: {trace_mode!r}")
    final_trace_id = create_trace_id(trace_id)
    full = trace_mode == "full"
    protected = trace_mode == "protected_eval"

    query_payload: Dict[str, Any] = {"command": command, "mode": mode, "limit": limit}
    if full:
        query_payload["query"] = query
    elif not protected:
        query_payload["query_sha256"] = hashlib.sha256(query.encode("utf-8")).hexdigest()
        query_payload["query_len"] = len(query)

    result_payload: Dict[str, Any] = {"command": command, "result_count": len(results)}
    if not protected:
        result_payload["results"] = [
            _result_trace_payload(r) if full else _summary_result_payload(r) for r in results]
    if timings is not None:
        result_payload["timings_ms"] = dict(timings)

    events = [
        TraceEvent(event_id="evt-001", event_type="retrieval.query", timestamp=utc_now(),
                   actor="zurvan", payload=query_payload),
        TraceEvent(event_id="evt-002", event_type="retrieval.result", timestamp=utc_now(),
                   actor="zurvan", payload=result_payload),
    ]
    next_event_number = 3
    if mode == "hybrid" and not protected:
        events.append(TraceEvent(event_id=f"evt-{next_event_number:03d}",
                                 event_type="retrieval.fusion", timestamp=utc_now(),
                                 actor="zurvan", payload=_fusion_payload(results)))
        next_event_number += 1
    if command == "context" and not protected:
        events.append(TraceEvent(event_id=f"evt-{next_event_number:03d}",
                                 event_type="context.assembled", timestamp=utc_now(),
                                 actor="zurvan",
                                 payload=_assembled_context_payload(results, dropped=dropped)))
        next_event_number += 1
    if graph_enabled and not protected:
        nodes = graph_nodes or []
        events.append(TraceEvent(
            event_id=f"evt-{next_event_number:03d}", event_type="graph_context",
            timestamp=utc_now(), actor="zurvan",
            payload={"enabled": True, "depth": graph_depth, "node_count": len(nodes),
                     "nodes": [_graph_trace_payload(n) if full else
                               {k: v for k, v in _graph_trace_payload(n).items() if k != "title"}
                               for n in nodes]}))
```

and the record:

```python
    record = TraceRecord(
        trace_id=final_trace_id,
        title=f"Retrieval context: {query}" if full else f"Retrieval trace: {command}",
        summary=f"Opt-in {trace_mode} retrieval trace for zurvan {command}.",
        events=events,
        trace_mode=trace_mode,
    )
```

Add next to `_result_trace_payload`:

```python
def _summary_result_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Ids, ranks and scores; no heading or text (design §3.1 summary)."""
    return {k: v for k, v in _result_trace_payload(result).items() if k != "heading"}
```

Add `trace_mode: str = "summary"` to `search_memory` and `export_context`, and pass `trace_mode=trace_mode` to both `_write_retrieval_trace` calls.

In `scripts/cli.py`, add to both the search and context parsers:

```python
    search_parser.add_argument("--trace-mode", choices=["summary", "full"], default=None,
                               help="summary (default): no raw query/snippets; full: raw text (local debugging). Implies --trace")
```

(Use `context_parser.add_argument(...)` for context.) In both dispatches, pass:

```python
                trace=getattr(args, "trace", False) or bool(getattr(args, "trace_mode", None)),
                trace_mode=getattr(args, "trace_mode", None) or "summary",
```

In `tests/test_trace_retrieval_integration.py`, `test_export_context_trace_writes_retrieval_and_graph_events` is the only test that breaks (measured in the gauntlet: 1 failed, 7 passed). It asserts on the Markdown mirror. Add `trace_mode="full",` to its `export_context(...)` call, directly after `trace=True,`. That is a deliberate behaviour change: the default no longer writes a mirror.

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_trace_modes_retrieval.py tests/test_trace_retrieval_integration.py tests/test_trace_privacy.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/context_export.py scripts/cli.py tests/test_trace_modes_retrieval.py tests/test_trace_retrieval_integration.py
git commit -m "zurvan: --trace defaults to summary (no raw query, no snippets); --trace-mode full is explicit; protected_eval never selectable"
```

---

### Task 4: Stage timings and `search --explain`

**Files:**
- Modify: `scripts/hybrid_search.py` (`search_hybrid`)
- Modify: `scripts/context_export.py` (`_search_internal`, `search_memory`)
- Modify: `scripts/cli.py` (`--explain` on search)
- Test: `tests/test_stage_timings.py` (create; reuses the `tmp_index` fixture of `tests/test_hybrid_search.py`, which is not modified)

**Interfaces:**
- Produces:
  - `search_hybrid(query, limit=10, db_path=None, timings: dict | None = None)`. When `timings` is a dict it is filled with float ms for `STAGES = ("fts", "embed_query", "vector_scan", "hydrate", "fuse", "total")`. The return value is unchanged.
  - `_search_internal(..., timings=None)`, where keyword mode fills `{"keyword_scan", "total"}`.
  - `search_memory(..., explain=False)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage_timings.py`:

```python
from scripts import hybrid_search as hs
from test_hybrid_search import tmp_index  # noqa: F401 — pytest fixture; tests/ is on sys.path (prepend mode, no package)


def test_timings_are_filled_and_ranking_unchanged(tmp_index):
    plain = hs.search_hybrid("hybrid search citation source", limit=5, db_path=tmp_index)
    assert len(plain) >= 2, "a vacuous result would make the equality below prove nothing"
    timings = {}
    timed = hs.search_hybrid("hybrid search citation source", limit=5, db_path=tmp_index,
                             timings=timings)
    assert timed == plain
    assert set(timings) == set(hs.STAGES)
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")


def test_missing_index_leaves_timings_empty(tmp_path):
    timings = {}
    assert hs.search_hybrid("x", db_path=str(tmp_path / "none.sqlite"), timings=timings) == []
    assert timings == {}


def test_search_explain_prints_stage_table_and_traces_timings(tmp_path, monkeypatch, capsys):
    import json

    import scripts.context_export as ce
    monkeypatch.setattr(ce, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("alpha beta")
    ce.search_memory("alpha", hybrid=False, explain=True, trace=True,
                     trace_id="trace-20260929T000000Z-explain1")
    out = capsys.readouterr().out
    assert "Stage timings (ms):" in out and "keyword_scan" in out
    trace = json.loads((tmp_path / "data" / "traces" / "trace-20260929T000000Z-explain1.json").read_text())
    assert set(trace["events"][1]["payload"]["timings_ms"]) == {"keyword_scan", "total"}
```

`tests/` is not a package. pytest's default `prepend` import mode puts `tests/` itself on `sys.path`, so the existing module-scoped fixture is importable as `from test_hybrid_search import tmp_index`. `from tests.test_hybrid_search …` fails with ModuleNotFoundError (gauntlet G2). The query matches the fixture corpus, and `len(plain) >= 2` stops the equality from passing vacuously on empty results.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_stage_timings.py -q`
Expected: FAIL. `search_hybrid()` got an unexpected keyword argument `timings`.

- [ ] **Step 3: Implement**

`scripts/hybrid_search.py` (Task 4 is the only task that touches it). Add `import time` after `import math`. After the imports add:

```python
#: Stages recorded by `search_hybrid(timings=...)` — observation only.
STAGES = ("fts", "embed_query", "vector_scan", "hydrate", "fuse", "total")


def _ms(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1e6, 3)
```

Replace `search_hybrid` in full with the gauntlet-proven version. Only the `stage`/`t`/`_ms` lines and the `timings` parameter are new; every computation is unchanged:

```python
def search_hybrid(query: str, limit: int = 10, db_path: str | None = None,
                  timings: dict | None = None) -> List[Dict]:
    from scripts.config import get_search_db_path
    if db_path is None:
        db_path = str(get_search_db_path())
    if not os.path.exists(db_path):
        # stderr, not stdout: callers run with --json and parse stdout, so a
        # diagnostic printed there corrupts the payload into unparseable text.
        print(
            f"Error: Search index not found at {db_path}. Run 'zurvan index rebuild' first.",
            file=sys.stderr,
        )
        return []

    stage = {}
    t_total = time.perf_counter_ns()
    # read-only: the query path must never create or modify the index
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    from scripts import vec_storage
    vec_storage.load_extension(conn)
    cursor = conn.cursor()

    # Keyword search using FTS5 (BM25 rank)
    t = time.perf_counter_ns()
    import re
    terms = [t for t in re.split(r'\W+', query) if t]
    if terms:
        # Each term is quoted as an FTS5 string: identical matching for plain
        # tokens, but bareword keywords (AND/OR/NOT/NEAR) no longer raise
        # "fts5: syntax error".
        fts_query = " OR ".join(f'"{t}"' for t in terms)
        cursor.execute("""
            SELECT chunk_id, bm25(chunks_fts) as bm25_score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
        """, (fts_query,))
        fts_results = cursor.fetchall()
    else:
        fts_results = []

    # Normalize BM25 scores (lower bm25 value is better in SQLite FTS5)
    # Actually SQLite bm25 returns negative values. Let's invert and normalize.
    fts_scores = {}
    if fts_results:
        min_score = min(row[1] for row in fts_results)
        max_score = max(row[1] for row in fts_results)
        for chunk_id, score in fts_results:
            # Normalize between 0 and 1
            if max_score == min_score:
                fts_scores[chunk_id] = 1.0
            else:
                # since scores are typically negative, smaller is better.
                # mapping max_score (worst) -> 0, min_score (best) -> 1
                norm_score = (max_score - score) / (max_score - min_score)
                fts_scores[chunk_id] = norm_score
    stage["fts"] = _ms(t)

    # Semantic search — embed the query with the SAME provider/model the
    # index was built with (never the env), so scores stay meaningful.
    index_provider, index_model = _index_embedding_config(cursor)
    t = time.perf_counter_ns()
    query_emb = get_embedding(query, provider=index_provider, model=index_model)['vector']
    stage["embed_query"] = _ms(t)

    t = time.perf_counter_ns()
    top = _score_semantic_top_k(cursor, query_emb, fts_scores, limit)
    stage["vector_scan"] = _ms(t)
    t = time.perf_counter_ns()
    scored = _hydrate(cursor, top)
    stage["hydrate"] = _ms(t)
    conn.close()

    t = time.perf_counter_ns()
    fused = _fuse(scored)[:limit]
    stage["fuse"] = _ms(t)
    stage["total"] = _ms(t_total)
    if timings is not None:
        timings.update(stage)
    return fused
```

`scripts/context_export.py` (on top of Task 3):

1. Add `import time` after `import hashlib`.
2. `_search_internal`: add the parameter and time both modes:

```python
def _search_internal(
    query: str, hybrid: bool = False, limit: int = 10, root: str | Path | None = None,
    timings: Dict[str, float] | None = None,
) -> List[Dict[str, Any]]:
```

```python
        return search_hybrid(query, limit, db_path=str(get_search_db_path()), timings=timings)

    t0 = time.perf_counter_ns()
```

```python
    matches.sort(key=lambda x: x['hybrid_score'], reverse=True)
    if timings is not None:
        elapsed = round((time.perf_counter_ns() - t0) / 1e6, 3)
        timings.update({"keyword_scan": elapsed, "total": elapsed})
    return matches[:limit]
```

3. `search_memory`: add `explain: bool = False` after `trace_mode`. Replace `results = _search_internal(query, hybrid, limit=10)` with:

```python
    timings: Dict[str, float] = {}
    results = _search_internal(query, hybrid, limit=10, timings=timings)
```

Pass `timings=timings,` to its `_write_retrieval_trace(...)` call, after `trace_mode=trace_mode,`. In the JSON branch, before `print(json.dumps(payload, indent=2))`:

```python
        if explain:
            payload["timings_ms"] = timings
```

In the text branch, before `if trace_path: print(f"Trace written: ...")`:

```python
        if explain:
            print("\nStage timings (ms):")
            for name, value in timings.items():
                print(f"  {name:<12} {value:>10.3f}")
```

`scripts/cli.py`: before the search parser's `--json` argument, add
`search_parser.add_argument("--explain", action="store_true", help="Print per-stage timings")`.
In the search dispatch, after `as_json=getattr(args, "as_json", False),`, add `explain=getattr(args, "explain", False),`.

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_stage_timings.py tests/test_hybrid_search.py tests/test_trace_modes_retrieval.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/hybrid_search.py scripts/context_export.py scripts/cli.py tests/test_stage_timings.py
git commit -m "zurvan: per-stage search timings (observation only) and search --explain"
```

---

### Task 5: Dataset registry — the burned instrument is RETIRED

**Files:**
- Create: `eval/datasets.json`
- Create: `scripts/eval_datasets.py`
- Test: `tests/test_eval_datasets.py` (create)

**Interfaces:**
- Produces:
  - `STATES = ("DEV", "HOLDOUT", "RESERVE", "RETIRED")`, `DatasetInfo(name: str, state: str, sha256: str)`.
  - `dataset_for_file(path) -> DatasetInfo`. A file whose digest is unregistered becomes `DatasetInfo("unregistered", "DEV", digest)`.
  - `DatasetNotEligible(ValueError)` with `.code`.
  - `require_promotion_eligible(info)`, which raises unless the state is `HOLDOUT`.
  - `require_itemised_allowed(info)`, which raises for `HOLDOUT`/`RESERVE` with code `SEALED_DATASET`.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_datasets.py`:

```python
import hashlib
import json

import pytest

from scripts import eval_datasets as ed
from scripts.config import PROJECT_ROOT


def test_registry_retires_the_burned_instrument():
    reg = json.loads((PROJECT_ROOT / "eval" / "datasets.json").read_text())
    manifest = json.loads((PROJECT_ROOT / "eval" / "search_gold.manifest.json").read_text())
    entry = reg["datasets"]["search_gold"]
    assert entry["state"] == "RETIRED"
    assert entry["sha256"] == manifest["sha256"]
    # whole-file digests only: nothing per item (confirmation-oracle rule)
    assert set(entry) <= {"state", "sha256", "n_items", "retired_on", "reason"}


def _file(tmp_path, body=b'{"query":"q","expected_paths":["p"]}\n'):
    p = tmp_path / "g.jsonl"
    p.write_bytes(body)
    return p, hashlib.sha256(body).hexdigest()


def test_dataset_for_file_matches_by_whole_file_digest(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry",
                        lambda: {"datasets": {"x": {"state": "RETIRED", "sha256": digest}}})
    assert ed.dataset_for_file(p) == ed.DatasetInfo("x", "RETIRED", digest)


def test_unregistered_file_is_dev(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry", lambda: {"datasets": {}})
    assert ed.dataset_for_file(p) == ed.DatasetInfo("unregistered", "DEV", digest)


@pytest.mark.parametrize("state", ["DEV", "RETIRED", "RESERVE"])
def test_only_holdout_can_gate_a_promotion(state):
    with pytest.raises(ed.DatasetNotEligible) as exc:
        ed.require_promotion_eligible(ed.DatasetInfo("x", state, "0" * 64))
    assert exc.value.code == "NOT_PROMOTION_ELIGIBLE"
    ed.require_promotion_eligible(ed.DatasetInfo("x", "HOLDOUT", "0" * 64))


@pytest.mark.parametrize("state", ["HOLDOUT", "RESERVE"])
def test_sealed_states_never_allow_item_rows(state):
    with pytest.raises(ed.DatasetNotEligible) as exc:
        ed.require_itemised_allowed(ed.DatasetInfo("x", state, "0" * 64))
    assert exc.value.code == "SEALED_DATASET"


def test_unknown_state_in_registry_fails_closed(tmp_path, monkeypatch):
    p, digest = _file(tmp_path)
    monkeypatch.setattr(ed, "_load_registry",
                        lambda: {"datasets": {"x": {"state": "SORT_OF", "sha256": digest}}})
    with pytest.raises(ValueError):
        ed.dataset_for_file(p)
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_datasets.py -q`
Expected: FAIL. `eval_datasets` cannot be imported.

- [ ] **Step 3: Implement**

`eval/datasets.json` (copy the digest from `eval/search_gold.manifest.json`, a whole-file digest that is already public):

```json
{
  "schema": "zurvan.eval.datasets.v1",
  "note": "Whole-file digests only. No per-item digest may ever appear here: it is a confirmation oracle. Reference items by index.",
  "datasets": {
    "search_gold": {
      "state": "RETIRED",
      "sha256": "34a533f2d74fc3994d4cf0a80e21a558986609bb5ce09d5224d2e781d5571018",
      "n_items": 50,
      "retired_on": "2026-09-29",
      "reason": "public in the repository 2026-05-30..2026-09-16; kept as historical regression and diagnostics (spec §9.1), never a promotion gate"
    }
  }
}
```

`scripts/eval_datasets.py`:

```python
"""Evaluation dataset registry (Z1 design §3.5, §4.1).

A dataset is identified by the SHA-256 of its whole file. Its state decides
what it may be used for: only HOLDOUT gates a promotion, and HOLDOUT/RESERVE
never produce per-item rows on the development side.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from scripts.config import PROJECT_ROOT

STATES = ("DEV", "HOLDOUT", "RESERVE", "RETIRED")
REGISTRY = PROJECT_ROOT / "eval" / "datasets.json"


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    state: str
    sha256: str


class DatasetNotEligible(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dataset_for_file(path) -> DatasetInfo:
    digest = file_sha256(path)
    for name, entry in _load_registry().get("datasets", {}).items():
        if entry.get("sha256") == digest:
            state = entry.get("state")
            if state not in STATES:
                raise ValueError(f"dataset {name!r} has unknown state {state!r}")
            return DatasetInfo(name, state, digest)
    return DatasetInfo("unregistered", "DEV", digest)


def require_promotion_eligible(info: DatasetInfo) -> None:
    if info.state != "HOLDOUT":
        raise DatasetNotEligible(
            "NOT_PROMOTION_ELIGIBLE",
            f"dataset {info.name!r} is {info.state}; only a sealed HOLDOUT gates a promotion")


def require_itemised_allowed(info: DatasetInfo) -> None:
    if info.state in ("HOLDOUT", "RESERVE"):
        raise DatasetNotEligible(
            "SEALED_DATASET",
            f"dataset {info.name!r} is {info.state}; per-item rows never leave the seal")
```

- [ ] **Step 4: Run to verify, then run the public-repo guard (`eval/datasets.json` is new and tracked)**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_datasets.py tests/test_instruments.py -q && PYTHONPATH=. python scripts/public_repo_guard.py`
Expected: tests pass; "Public repo guard passed."

- [ ] **Step 5: Commit**

```bash
git add eval/datasets.json scripts/eval_datasets.py tests/test_eval_datasets.py
git commit -m "zurvan: eval dataset registry - the burned 50-item instrument is RETIRED and cannot gate a promotion"
```

---

### Task 6: `evaluate()` core — per-item rows, result digest, stage percentiles, no query text

**Files:**
- Modify: `scripts/eval_search.py`
- Modify: `tests/test_eval_search.py:166-180` (`failing_queries` → `failing_items`)
- Test: `tests/test_eval_evaluate.py` (create)

**Interfaces:**
- Consumes: `search_hybrid(..., timings=)`, `hs.STAGES` (Task 4).
- Produces:
  - `evaluate(dataset: list[dict], *, index_path: str, hybrid: bool = True) -> tuple[dict, list[dict]]`, returning `(metrics, rows)`.
  - Each row is `{"index": int, "retrieved": [str], "first_hit_rank": int | None}`, with ranks 1-based.
  - `metrics` gains `failing_items: [int]`, `result_digest: str` and `stage_latency_ms: {stage: {"p50", "p95"}}`. It loses `failing_queries`.
  - `result_digest(rows, metrics) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_evaluate.py`:

```python
import json

from scripts import eval_search as es

DATA = [{"query": "secret-query-text-one", "expected_paths": ["wiki/a.md"]},
        {"query": "secret-query-text-two", "expected_paths": ["wiki/z.md"]}]


def _fake(query, limit=10, db_path=None, timings=None):
    if timings is not None:
        timings.update({s: 1.0 for s in ("fts", "embed_query", "vector_scan", "hydrate", "fuse")}
                       | {"total": 5.0})
    return [{"source_path": "wiki/b.md"}, {"source_path": "wiki/a.md"}]


def test_rows_ranks_and_failing_items(monkeypatch):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    metrics, rows = es.evaluate(DATA, index_path="x")
    assert rows == [{"index": 0, "retrieved": ["wiki/b.md", "wiki/a.md"], "first_hit_rank": 2},
                    {"index": 1, "retrieved": ["wiki/b.md", "wiki/a.md"], "first_hit_rank": None}]
    assert metrics["failing_items"] == [1]
    assert "failing_queries" not in metrics
    assert metrics["stage_latency_ms"]["total"] == {"p50": 5.0, "p95": 5.0}


def test_result_digest_ignores_timings_and_tracks_ranking(monkeypatch):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    m1, r1 = es.evaluate(DATA, index_path="x")

    def slower(*a, timings=None, **k):
        out = _fake(*a, timings=timings, **k)
        if timings is not None:
            timings["total"] = 999.0
        return out
    monkeypatch.setattr(es, "search_hybrid", slower)
    m2, _ = es.evaluate(DATA, index_path="x")
    assert m1["result_digest"] == m2["result_digest"]

    monkeypatch.setattr(es, "search_hybrid",
                        lambda *a, **k: [{"source_path": "wiki/a.md"}, {"source_path": "wiki/b.md"}])
    m3, _ = es.evaluate(DATA, index_path="x")
    assert m3["result_digest"] != m1["result_digest"]


def test_json_output_never_contains_query_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("x")
    (tmp_path / "wiki" / "z.md").write_text("x")
    gold = tmp_path / "g.jsonl"
    gold.write_text("".join(json.dumps({"query": d["query"],
                                        "expected_paths": [str(tmp_path / p) for p in d["expected_paths"]]})
                            + "\n" for d in DATA))
    es.run_search_evaluation(str(gold), hybrid=True, as_json=True, index_path="x")
    out = capsys.readouterr().out
    assert "secret-query-text" not in out
    assert json.loads(out)["failing_items"] == [0, 1]


def test_a_malformed_gold_line_is_reported_by_number_not_content(tmp_path, capsys):
    gold = tmp_path / "g.jsonl"
    gold.write_text('{"query": "secret-query-text-three"}\n{not json secret-query-text-four\n')
    assert es.load_gold_dataset(str(gold)) == []
    out = capsys.readouterr().out
    assert "secret-query-text" not in out
    assert "line 1" in out and "line 2" in out


def test_result_digest_sees_a_ranking_change_the_metrics_cannot(monkeypatch):
    # Same hits at the same ranks, different results below them: every quality
    # metric is equal, so only the per-item ranked ids can tell the runs apart.
    monkeypatch.setattr(es, "search_hybrid", lambda *a, **k: [
        {"source_path": "wiki/a.md"}, {"source_path": "wiki/c.md"}])
    m1, _ = es.evaluate(DATA, index_path="x")
    monkeypatch.setattr(es, "search_hybrid", lambda *a, **k: [
        {"source_path": "wiki/a.md"}, {"source_path": "wiki/d.md"}])
    m2, _ = es.evaluate(DATA, index_path="x")
    assert (m1["top1_accuracy"], m1["top3_accuracy"], m1["mrr"]) == \
           (m2["top1_accuracy"], m2["top3_accuracy"], m2["mrr"])
    assert m1["result_digest"] != m2["result_digest"]


def test_percentiles_are_nearest_rank():
    samples = [float(v) for v in range(1, 21)]
    assert es._percentile(samples, 50) == 10.0
    assert es._percentile(samples, 95) == 19.0
    assert es._percentile([5.0], 95) == 5.0
    assert es._percentile([], 50) is None


def test_evaluate_reports_p50_and_p95_of_each_stage(monkeypatch):
    totals = iter([1.0, 9.0])

    def varying(query, limit=10, db_path=None, timings=None):
        timings.update({"total": next(totals)})
        return []
    monkeypatch.setattr(es, "search_hybrid", varying)
    metrics, _ = es.evaluate(DATA, index_path="x")
    assert metrics["stage_latency_ms"]["total"] == {"p50": 1.0, "p95": 9.0}
```

In `tests/test_eval_search.py`, rename `test_metrics_name_the_failing_queries` to `test_metrics_name_the_failing_items` and assert `metrics["failing_items"] == [0, 1, 2]` and `"failing_queries" not in metrics`. Keep its comment: indices serve the before/after need without quoting the instrument.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_evaluate.py tests/test_eval_search.py -q`
Expected: FAIL. `es.evaluate` does not exist.

- [ ] **Step 3: Implement in `scripts/eval_search.py`**

Proven in the gauntlet. Make the edits below; the result is the file the dry run tested.

1. Imports: change the `scripts.metrics` import to also import `get_rank`, and add `import hashlib` and `import math` above it.
2. Replace `load_gold_dataset` in full. A malformed line is reported by number, never by content:

```python
def load_gold_dataset(filepath: str) -> List[Dict[str, Any]]:
    filepath = _resolve(filepath)
    if not os.path.exists(filepath):
        print(f"Error: Gold dataset '{filepath}' not found.")
        sys.exit(1)

    dataset = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if 'query' not in data or 'expected_paths' not in data:
                    print(f"Warning: Skipping malformed line {lineno} (missing required fields)")
                    continue
                dataset.append(data)
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed JSON line {lineno}")
    return dataset
```

3. Insert these before `def run_search_evaluation`. `_keyword_retrieve` is the old keyword branch, moved verbatim:

```python
def _keyword_retrieve(query: str) -> List[str]:
    """The non-hybrid baseline: a substring scan over wiki/ + docs/."""
    import glob
    wiki_files = glob.glob(str(PROJECT_ROOT / "wiki" / "**" / "*.md"), recursive=True) + \
        glob.glob(str(PROJECT_ROOT / "docs" / "**" / "*.md"), recursive=True)
    keywords = query.lower().split()
    matches = []
    for filepath in wiki_files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            score = sum(1 for k in keywords if k in content.lower())
            if score > 0:
                matches.append((score, filepath))
        except Exception:
            continue
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m[1] for m in matches[:10]]


def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    k = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))  # nearest rank
    return round(ordered[k], 3)


def result_digest(rows, metrics) -> str:
    """SHA-256 over what the ranking produced: per-item ranked ids and the
    quality metrics. Timings, timestamps and resources are excluded — they
    vary by nature (design §3.5)."""
    body = {"rows": [{"index": r["index"], "retrieved": r["retrieved"]} for r in rows],
            "top1": metrics["top1_accuracy"], "top3": metrics["top3_accuracy"],
            "mrr": metrics["mrr"]}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate(dataset, *, index_path, hybrid=True):
    """Pure core: no printing, no exit. Returns (metrics, rows). Rows name
    items by INDEX; no query text leaves this function."""
    rows, rrs, stage_samples = [], [], {}
    top1 = top3 = 0
    for index, item in enumerate(dataset):
        expected = item["expected_paths"]
        if hybrid:
            timings = {}
            results = search_hybrid(item["query"], limit=10, db_path=index_path, timings=timings)
            for name, value in timings.items():
                stage_samples.setdefault(name, []).append(value)
            retrieved = [r["source_path"] for r in results]
        else:
            retrieved = _keyword_retrieve(item["query"])
        first = get_rank(expected, retrieved) or None  # the same matcher the metrics use
        rows.append({"index": index, "retrieved": retrieved, "first_hit_rank": first})
        top1 += calculate_top_k_hit(expected, retrieved, 1)
        top3 += calculate_top_k_hit(expected, retrieved, 3)
        rrs.append(calculate_reciprocal_rank(expected, retrieved))
    n = len(dataset)
    metrics = {
        "queries": n,
        "top1_accuracy": calculate_accuracy(top1, n),
        "top3_accuracy": calculate_accuracy(top3, n),
        "mrr": calculate_mean_reciprocal_rank(rrs),
        "failing_items": [r["index"] for r in rows
                          if r["first_hit_rank"] is None or r["first_hit_rank"] > 3],
        "stage_latency_ms": {name: {"p50": _percentile(v, 50), "p95": _percentile(v, 95)}
                             for name, v in stage_samples.items()},
    }
    metrics["failures"] = len(metrics["failing_items"])
    metrics["result_digest"] = result_digest(rows, metrics)
    return metrics, rows
```

4. Replace `run_search_evaluation` in full. It is built on the core, and its text report, mock-embedding skip and min-top3 exit are unchanged:

```python
def run_search_evaluation(gold_file: str = None, hybrid: bool = False, min_top3: float = 0.0,
                          as_json: bool = False, max_queries: int | None = None,
                          index_path: str | None = None, keep_rows: bool = False):
    """Score retrieval against the gold set.

    ``index_path`` names the index to score. It defaults to the canonical one,
    so ad-hoc use is unchanged, and it exists so a gate can hand this function a
    MEASUREMENT SNAPSHOT — a hard link to the index as it was when the gate
    started. Another project writing to the shared store mid-run then changes
    the live store and the next rebuild, and changes nothing about the number
    being computed here. The index is reported alongside the metrics, because a
    result that cannot name its input is not a measurement.

    ``max_queries`` runs only the first N queries. It exists for LIVENESS
    checks, not for measurement: the MCP e2e smoke called this tool with the
    full 50-query set, which at 199k chunks takes minutes and dominated the
    smoke's runtime — a smoke test should assert the tool responds, not run an
    evaluation. Quality numbers must always come from the whole set, so a
    truncated run labels itself as one in its output.
    """
    gold_file = gold_file or _default_gold()
    if index_path is None:
        from scripts.config import get_search_db_path
        index_path = str(get_search_db_path())
    validate_gold_dataset(gold_file, quiet=as_json)
    dataset = load_gold_dataset(gold_file)
    if not dataset:
        print("Error: No valid queries found in gold dataset.")
        sys.exit(1)

    full_size = len(dataset)
    if max_queries is not None:
        dataset = dataset[:max(1, int(max_queries))]

    if not as_json:
        print(f"Running Evaluation on {len(dataset)} queries (Hybrid: {hybrid})...")

    core, rows = evaluate(dataset, index_path=index_path, hybrid=hybrid)
    total_queries, failures = core["queries"], core["failures"]
    top1_accuracy, top3_accuracy, mrr = core["top1_accuracy"], core["top3_accuracy"], core["mrr"]
    metrics = dict(core)
    metrics.update({
        "min_top3": min_top3,
        "passed": top3_accuracy >= min_top3,
        "index_path": index_path,
        "index_identity": _identity_of(index_path),
        "snapshot_coherence": _coherence_of(index_path),
    })
    if keep_rows:
        metrics["_rows"] = rows

    if as_json:
        print(json.dumps({k: v for k, v in metrics.items() if not k.startswith("_")}, indent=2))
    else:
        print("\nSearch Evaluation Results")
        if total_queries < full_size:
            print(f"(LIVENESS RUN — first {total_queries} of {full_size} queries; not a quality measurement)")
        print("=========================")
        print(f"Queries: {total_queries}")
        print(f"Top-1 accuracy: {top1_accuracy * 100:.0f}%")
        print(f"Top-3 accuracy: {top3_accuracy * 100:.0f}%")
        print(f"Mean reciprocal rank: {mrr:.2f}")
        print(f"Failures: {failures}")
        ident = metrics["index_identity"]
        if ident:
            print(f"Index: {index_path}")
            print(f"  {ident.get('chunks')} chunks / {ident.get('sources')} sources"
                  f"  chunk-set {str(ident.get('chunk_id_set_sha'))[:12]}"
                  f"  space {','.join(ident.get('embedding_space') or [])}")
        c = metrics["snapshot_coherence"]
        detail = (f"revision {c['revision']} ({c['atomicity']})"
                  if c.get("revision") is not None else c.get("reason"))
        print(f"Snapshot coherence: {c['status']} — {detail}")
        print("=========================")

    if top3_accuracy < min_top3:
        if _index_uses_mock_embeddings(index_path):
            # Retrieval quality cannot be judged against deterministic-hash
            # vectors: the semantic half of the score is noise, so the number
            # measures nothing. The smoke test runs under the mock provider,
            # and this gate previously "passed" there only because that run had
            # overwritten the real index with mock embeddings — the very bug
            # this skip exists to stop masking.
            if not as_json:
                print(
                    f"Skipping the retrieval quality gate: the index was built with mock "
                    f"embeddings, so top-3 ({top3_accuracy:.2f}) is not a meaningful "
                    f"measurement. Rebuild with a real provider to enforce it."
                )
            metrics["skipped_reason"] = "mock embeddings"
            return metrics
        if not as_json:
            print(f"Error: Top-3 accuracy ({top3_accuracy}) is below required minimum ({min_top3})")
        sys.exit(1)

    return metrics
```

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_evaluate.py tests/test_eval_search.py tests/test_mcp_tools.py -q`
Expected: all pass. If `tests/test_mcp_tools.py` does not exist, run `PYTHONPATH=. python -m pytest -q -k "eval"` instead.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_search.py tests/test_eval_evaluate.py tests/test_eval_search.py
git commit -m "zurvan: eval core returns per-item rows by index and a timing-free result digest; no query text in eval output"
```

---

### Task 7: Saved runs and rank movers

**Files:**
- Create: `scripts/eval_runs.py`
- Modify: `scripts/cli.py` (`eval search --save-run`, `eval movers A B`)
- Modify: `scripts/eval_search.py` (`--save-run` flag on its own CLI)
- Test: `tests/test_eval_runs.py` (create)

**Interfaces:**
- Consumes: `evaluate`, rows (Task 6); `dataset_for_file`, `require_itemised_allowed`, `DatasetNotEligible` (Task 5).
- Produces:
  - `RUNS_DIR = Path.home() / ".zurvan" / "eval-runs"`.
  - `save_run(metrics, rows, dataset: DatasetInfo, runs_dir=RUNS_DIR) -> Path`, which calls `require_itemised_allowed` first and writes 0600.
  - `load_run(path) -> dict`.
  - `movers(a: dict, b: dict) -> list[dict]` of `{"index", "rank_a", "rank_b"}` for changed ranks. It refuses different datasets (`DATASET_MISMATCH`) and sealed states.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_runs.py`:

```python
import os
import stat

import pytest

from scripts import eval_runs as er
from scripts.eval_datasets import DatasetInfo, DatasetNotEligible

DEV = DatasetInfo("unregistered", "DEV", "a" * 64)
METRICS = {"result_digest": "d", "top1_accuracy": 0.5}


def _rows(*ranks):
    return [{"index": i, "retrieved": [], "first_hit_rank": r} for i, r in enumerate(ranks)]


def test_save_run_is_private_and_records_dataset(tmp_path):
    path = er.save_run(METRICS, _rows(1, None), DEV, runs_dir=tmp_path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    run = er.load_run(path)
    assert run["dataset"] == {"name": "unregistered", "state": "DEV", "sha256": "a" * 64}
    assert run["rows"][1]["first_hit_rank"] is None


@pytest.mark.parametrize("state", ["HOLDOUT", "RESERVE"])
def test_save_run_refuses_holdout_before_writing(tmp_path, state):
    with pytest.raises(DatasetNotEligible):
        er.save_run(METRICS, _rows(1), DatasetInfo("h", state, "b" * 64), runs_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_movers_lists_changed_ranks_by_index_only(tmp_path):
    a = er.load_run(er.save_run(METRICS, _rows(1, 2, None, 4), DEV, runs_dir=tmp_path / "a"))
    b = er.load_run(er.save_run(METRICS, _rows(1, 5, 3, 4), DEV, runs_dir=tmp_path / "b"))
    assert er.movers(a, b) == [{"index": 1, "rank_a": 2, "rank_b": 5},
                               {"index": 2, "rank_a": None, "rank_b": 3}]


def test_movers_refuses_different_datasets(tmp_path):
    other = DatasetInfo("unregistered", "DEV", "c" * 64)
    a = er.load_run(er.save_run(METRICS, _rows(1), DEV, runs_dir=tmp_path / "a"))
    b = er.load_run(er.save_run(METRICS, _rows(1), other, runs_dir=tmp_path / "b"))
    with pytest.raises(DatasetNotEligible) as exc:
        er.movers(a, b)
    assert exc.value.code == "DATASET_MISMATCH"


def test_movers_refuses_a_run_file_claiming_a_sealed_state(tmp_path):
    a = er.load_run(er.save_run(METRICS, _rows(1), DEV, runs_dir=tmp_path))
    forged = dict(a, dataset=dict(a["dataset"], state="HOLDOUT"))
    with pytest.raises(DatasetNotEligible):
        er.movers(forged, forged)


def test_cli_movers_prints_indices_and_refuses_sealed(tmp_path, monkeypatch, capsys):
    import json

    import scripts.cli as cli
    a = er.save_run(METRICS, _rows(1, 2), DEV, runs_dir=tmp_path / "a")
    b = er.save_run(METRICS, _rows(1, 4), DEV, runs_dir=tmp_path / "b")
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "movers", str(a), str(b)])
    cli.main()
    assert "item    1: rank 2 -> 4" in capsys.readouterr().out
    forged = tmp_path / "forged.json"
    body = json.loads(a.read_text())
    body["dataset"]["state"] = "HOLDOUT"
    forged.write_text(json.dumps(body))
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "movers", str(forged), str(forged)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_runs.py -q`
Expected: FAIL. `eval_runs` cannot be imported.

- [ ] **Step 3: Implement `scripts/eval_runs.py`**

```python
"""Saved per-item eval runs and rank movers (Z1 design §3.4).

Runs are private local files under ~/.zurvan/eval-runs (never the repo,
never indexed). Items are named by INDEX. HOLDOUT/RESERVE runs are refused
before anything is written: their per-item rows never leave the seal.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval_datasets import DatasetInfo, DatasetNotEligible, require_itemised_allowed

RUNS_DIR = Path.home() / ".zurvan" / "eval-runs"


def save_run(metrics: dict, rows: list, dataset: DatasetInfo, runs_dir: Path = RUNS_DIR) -> Path:
    require_itemised_allowed(dataset)
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runs_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = runs_dir / f"run-{stamp}-{metrics.get('result_digest', 'nodigest')[:12]}.json"
    body = {"schema": "zurvan.eval.run.v1",
            "dataset": {"name": dataset.name, "state": dataset.state, "sha256": dataset.sha256},
            "metrics": {k: v for k, v in metrics.items() if not k.startswith("_")},
            "rows": [{"index": r["index"], "first_hit_rank": r["first_hit_rank"],
                      "retrieved": r["retrieved"]} for r in rows]}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2, sort_keys=True)
    return path


def load_run(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _info(run: dict) -> DatasetInfo:
    d = run["dataset"]
    return DatasetInfo(d["name"], d["state"], d["sha256"])


def movers(a: dict, b: dict) -> list:
    info_a, info_b = _info(a), _info(b)
    require_itemised_allowed(info_a)
    require_itemised_allowed(info_b)
    if info_a.sha256 != info_b.sha256:
        raise DatasetNotEligible("DATASET_MISMATCH", "runs were scored on different datasets")
    rank_b = {r["index"]: r["first_hit_rank"] for r in b["rows"]}
    return [{"index": r["index"], "rank_a": r["first_hit_rank"], "rank_b": rank_b.get(r["index"])}
            for r in a["rows"] if rank_b.get(r["index"]) != r["first_hit_rank"]]
```

Wire the CLI. In `scripts/eval_search.py`'s `__main__` block, add after the `--validate` argument:

```python
    parser.add_argument("--save-run", action="store_true",
                        help="Save per-item rows (by index) to ~/.zurvan/eval-runs for `zurvan eval movers`")
```

and replace the final `else:` branch with:

```python
    else:
        gold = args.gold or _default_gold()
        metrics = run_search_evaluation(gold, args.hybrid, args.min_top3, as_json=args.as_json,
                                        max_queries=args.max_queries, index_path=args.index_path,
                                        keep_rows=args.save_run)
        if args.save_run:
            from scripts.eval_datasets import dataset_for_file
            from scripts.eval_runs import save_run
            path = save_run(metrics, metrics.pop("_rows"), dataset_for_file(_resolve(gold)))
            print(f"Run saved: {path}", file=sys.stderr if args.as_json else sys.stdout)
```

`scripts/cli.py`: after `eval_search_parser.add_argument("--json", ...)`, add `eval_search_parser.add_argument("--save-run", action="store_true", help="Save per-item rows (by index) for `zurvan eval movers`")`, and in the `_run_script` call append `+ (["--save-run"] if getattr(args, "save_run", False) else [])`. Then add the movers subcommand:

```python
    eval_movers_parser = eval_sub.add_parser("movers", help="Items whose rank moved between two saved runs (by index)")
    eval_movers_parser.add_argument("run_a")
    eval_movers_parser.add_argument("run_b")
```

dispatch:

```python
    elif args.command == "eval" and args.action == "movers":
        from scripts.eval_datasets import DatasetNotEligible
        from scripts.eval_runs import load_run, movers
        try:
            moved = movers(load_run(args.run_a), load_run(args.run_b))
        except DatasetNotEligible as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(2)
        for m in moved:
            print(f"item {m['index']:>4}: rank {m['rank_a']} -> {m['rank_b']}")
        print(f"{len(moved)} item(s) moved.")
```

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_runs.py tests/test_eval_evaluate.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_runs.py scripts/eval_search.py scripts/cli.py tests/test_eval_runs.py
git commit -m "zurvan: saved eval runs (private, by index) and eval movers; sealed datasets refused before any row is written"
```

---

### Task 8: Historical reproducibility on a coherent Z0 snapshot

**Files:**
- Modify: `scripts/eval_runs.py` (add `reproduce`)
- Modify: `scripts/cli.py` (`eval reproduce`)
- Test: `tests/test_eval_reproduce.py` (create)

**Interfaces:**
- Consumes: `measurement_snapshot.capture(label=..., purpose=..., required=...)` (never with `components=`) returning `Snapshot` (`.index_path`, `.release()`, context manager); `SnapshotNotReady` from `scripts.build_snapshot`; `evaluate`, `load_gold_dataset`.
- Produces:
  - `reproduce(gold_path, *, runs=2, capture_fn=None, evaluate_fn=None) -> dict` returning `{"equal": bool, "digests": [str], "coherence": dict, "snapshot_index": str}`.
  - The snapshot is always released.
  - `SnapshotNotReady` propagates, and the CLI exits 2 on it.

- [ ] **Step 1: Write the failing tests**

`tests/test_eval_reproduce.py`:

```python
import json

import pytest

from scripts import eval_runs as er


class FakeSnap:
    def __init__(self, tmp_path):
        self.index_path = tmp_path / "search.sqlite"
        self.index_path.write_bytes(b"")
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"coherence": {"status": "VERIFIED", "revision": 7, "atomicity": "MANAGED",
                           "purpose": "measurement", "reason": None}}))
        self.released = False

    def release(self):
        self.released = True


def _gold(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"query":"q","expected_paths":["wiki/a.md"]}\n')
    return p


def test_equal_digests_on_one_snapshot_and_release(tmp_path):
    snap = FakeSnap(tmp_path)
    calls = []

    def fake_capture(**kw):
        calls.append(kw)
        return snap
    out = er.reproduce(_gold(tmp_path), capture_fn=fake_capture,
                       evaluate_fn=lambda ds, index_path, hybrid: ({"result_digest": "same"}, []))
    assert out["equal"] is True and out["digests"] == ["same", "same"]
    assert out["coherence"]["status"] == "VERIFIED"
    assert calls[0]["required"] == ("wiki", "search", "graph")
    assert calls[0]["purpose"] == "measurement"
    # capture() refuses components= together with required= (SnapshotComponentError)
    assert "components" not in calls[0]
    assert snap.released


def test_differing_digests_are_reported_not_hidden(tmp_path):
    snap = FakeSnap(tmp_path)
    digests = iter(["one", "two"])
    out = er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: snap,
                       evaluate_fn=lambda *a, **k: ({"result_digest": next(digests)}, []))
    assert out["equal"] is False and snap.released


def test_snapshot_released_even_when_evaluation_raises(tmp_path):
    snap = FakeSnap(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: snap, evaluate_fn=boom)
    assert snap.released


def test_reproduce_call_is_accepted_by_the_real_capture_contract(tmp_path):
    # The fakes above accept any keywords; this pins the call against the real
    # signature, which refuses `components=` together with `required=`.
    import inspect

    from scripts import measurement_snapshot as ms
    seen = {}
    er.reproduce(_gold(tmp_path), capture_fn=lambda **kw: seen.update(kw) or FakeSnap(tmp_path),
                 evaluate_fn=lambda *a, **k: ({"result_digest": "d"}, []))
    inspect.signature(ms.capture).bind(**seen)
    assert not ({"components", "required"} <= set(seen))


@pytest.mark.parametrize("equal,status,code", [(True, "VERIFIED", 0), (False, "VERIFIED", 1),
                                               (True, "UNVERIFIED", 1)])
def test_cli_reproduce_prints_json_and_sets_exit_code(monkeypatch, capsys, equal, status, code):
    # cli.py's dispatcher has a function-local `import json`, which makes `json`
    # unbound in any branch that did not execute it (measured: a 2-minute
    # live run died printing its result).
    import scripts.cli as cli
    monkeypatch.setattr(er, "reproduce", lambda gold: {"equal": equal, "digests": ["d", "d"],
                                                        "coherence": {"status": status},
                                                        "snapshot_index": "x"})
    monkeypatch.setattr(cli, "_resolved_gold", lambda g: "g")
    monkeypatch.setattr("sys.argv", ["zurvan", "eval", "reproduce"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    assert json.loads(capsys.readouterr().out)["equal"] is equal
```

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_reproduce.py -q`
Expected: FAIL. `eval_runs` has no attribute `reproduce`.

- [ ] **Step 3: Implement**

Append to `scripts/eval_runs.py`:

```python
REPRODUCE_REQUIRED = ("wiki", "search", "graph")


def reproduce(gold_path, *, runs: int = 2, capture_fn=None, evaluate_fn=None) -> dict:
    """Design §3.5 gate: score the gold set `runs` times on ONE coherent Z0
    snapshot and compare result digests (ranked ids + quality metrics only)."""
    from scripts.eval_search import _coherence_of, evaluate, load_gold_dataset
    if capture_fn is None:
        from scripts.measurement_snapshot import capture as capture_fn
    evaluate_fn = evaluate_fn or evaluate
    dataset = load_gold_dataset(str(gold_path))
    # `required=` alone: capture() refuses it together with `components=`.
    snap = capture_fn(label="z1a-reproduce", purpose="measurement",
                      required=REPRODUCE_REQUIRED)
    try:
        digests = [evaluate_fn(dataset, index_path=str(snap.index_path), hybrid=True)[0]["result_digest"]
                   for _ in range(runs)]
        return {"equal": len(set(digests)) == 1, "digests": digests,
                "coherence": _coherence_of(str(snap.index_path)),
                "snapshot_index": str(snap.index_path)}
    finally:
        snap.release()
```

`capture()` raises `SnapshotComponentError` when given `components=` and `required=` together (gauntlet G5). `test_reproduce_call_is_accepted_by_the_real_capture_contract` binds the call against the real signature, because the fakes accept anything.

CLI (`scripts/cli.py`):

```python
    eval_repro_parser = eval_sub.add_parser("reproduce", help="Score the gold set twice on one coherent Z0 snapshot; compare result digests")
    eval_repro_parser.add_argument("--gold", default=None)
```

```python
    elif args.command == "eval" and args.action == "reproduce":
        import json  # this function binds `json` locally elsewhere (line ~853)
        from scripts.build_snapshot import VERIFIED, SnapshotNotReady
        from scripts.eval_runs import reproduce
        try:
            out = reproduce(_resolved_gold(args.gold))
        except SnapshotNotReady as exc:
            print(f"SNAPSHOT_NOT_READY: {exc}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(out, indent=2))
        sys.exit(0 if out["equal"] and out["coherence"].get("status") == VERIFIED else 1)
```

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_reproduce.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_runs.py scripts/cli.py tests/test_eval_reproduce.py
git commit -m "zurvan: eval reproduce - result digests compared across two runs on one coherent Z0 snapshot"
```

---

### Task 9: Live gates, ranking-unchanged proof, closure matrix, docs

**Files:**
- Test: `tests/test_z1a_closure.py` (create)
- Modify: `docs/superpowers/specs/2026-09-29-z1-observability-sealed-eval-design.md` (§3.2 retention trigger; Z1a status)
- Modify: `CHANGELOG.md`, `AGENTS.md` (rotation), `docs/agents-history.md`, `CLAUDE.md` (Z1a bullet)

- [ ] **Step 1: Closure-matrix test (fails if any gate's proof disappears)**

`tests/test_z1a_closure.py`:

```python
"""Z1a closure matrix (design §9, "Z1a closes when"): each gate names the test
that demonstrates it. `tests/` is not a package, so tests are found by
scanning files (as test_z0_closure does), never by importing `tests.*`."""
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

MATRIX = {
    1: ("test_trace_modes_retrieval.py", "test_summary_trace_contains_no_query_text_anywhere"),
    2: ("test_trace_privacy.py", "test_trace_files_and_dirs_are_private"),
    3: ("test_trace_privacy.py", "test_prune_dry_run_on_live_repo_never_selects_tracked"),
    4: ("test_stage_timings.py", "test_timings_are_filled_and_ranking_unchanged"),
    5: ("test_eval_runs.py", "test_save_run_refuses_holdout_before_writing"),
    6: ("test_eval_datasets.py", "test_only_holdout_can_gate_a_promotion"),
    7: ("test_eval_reproduce.py", "test_equal_digests_on_one_snapshot_and_release"),
    8: ("test_trace_modes.py", "test_committed_v1_traces_have_no_schema_issue"),
}


@pytest.mark.parametrize("gate", sorted(MATRIX))
def test_every_z1a_gate_names_a_test_that_exists(gate):
    filename, name = MATRIX[gate]
    source = (TESTS / filename).read_text(encoding="utf-8")
    assert re.search(rf"^def {name}\(", source, re.M), f"gate {gate}: {filename}::{name}"
```

Run: `PYTHONPATH=. python -m pytest tests/test_z1a_closure.py -q`. Expected: 8 passed.
Falsify: rename one referenced test temporarily, confirm FAIL, then restore.

- [ ] **Step 2: Ranking-unchanged proof against BASE**

BASE is the commit before Task 1. Run from a checkout of BASE's scripts: `git worktree add <scratch>/z1a-base <BASE>`. The proof takes ONE coherent snapshot, dumps every gold item's top-10 with BASE code and with HEAD code into scratch files, compares the bytes, prints only numbers, and always releases the snapshot. It needs about 3.3 min; the gauntlet measured 3m18s.

Neither script may print query text. BASE's `eval_search --json` still emits `failing_queries`, so only numeric keys are printed.

`<scratch>/rank_dump.py`:

```python
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.hybrid_search import search_hybrid
from scripts.eval_search import load_gold_dataset
from scripts.instruments import gold_path
out = [[r["source_path"] for r in search_hybrid(i["query"], limit=10, db_path=sys.argv[2])]
       for i in load_gold_dataset(str(gold_path()))]
json.dump(out, open(sys.argv[3], "w"))
```

`<scratch>/ranking_proof.py`:

```python
import json, os, subprocess, sys, hashlib
live, wt, S = sys.argv[1:4]
sys.path.insert(0, live)
from scripts.measurement_snapshot import capture
snap = capture(root=live, label="z1a-rankproof", purpose="measurement", required=("wiki", "search", "graph"))
try:
    idx = str(snap.index_path)
    res = {}
    for name, tree in (("base", live), ("head", wt)):
        out = f"{S}/{name}.json"
        subprocess.run([sys.executable, f"{S}/rank_dump.py", tree, idx, out], check=True,
                       env=dict(os.environ, PYTHONPATH=tree, ZURVAN_ROOT=live))
        res[name] = open(out, "rb").read()
        # eval metrics per tree (drop any text field; print numbers only)
        m = subprocess.run([sys.executable, f"{tree}/scripts/eval_search.py", "--hybrid", "--index", idx, "--json"],
                           capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=tree, ZURVAN_ROOT=live))
        d = json.loads(m.stdout)
        print(name, {k: d[k] for k in ("queries", "top1_accuracy", "top3_accuracy", "mrr", "failures")},
              "digest" if "result_digest" in d else "no-digest")
    print("ranked lists identical:", res["base"] == res["head"], hashlib.sha256(res["base"]).hexdigest()[:12])
    print("coherence:", json.load(open(os.path.join(os.path.dirname(idx), "manifest.json")))["coherence"]["status"])
finally:
    snap.release()
    for n in ("base", "head"):
        try: os.remove(f"{S}/{n}.json")
        except OSError: pass
```

Run: `PYTHONPATH=<BASE-tree> python <scratch>/ranking_proof.py <BASE-tree> <HEAD-tree> <scratch>`
Expected (the gauntlet's own measurement, rev 262): `ranked lists identical: True`; base and head both `{'queries': 50, 'top1_accuracy': 0.72, 'top3_accuracy': 0.74, 'mrr': 0.739…, 'failures': 13}`; `coherence: VERIFIED`; `data/snapshots` empty afterwards.

If the store has moved since the gauntlet, the numbers may differ, but base and head must still be EQUAL. Any inequality is a failed gate: stop and report it, never edit the proof. A `SNAPSHOT_NOT_READY` refusal means wait or report; never work around it.

- [ ] **Step 3: Live gates**

Run each; record the output in the ledger.
- `PYTHONPATH=. python scripts/cli.py eval reproduce`. Expected: `"equal": true`, `coherence.status == "VERIFIED"` (the capture vocabulary; `COHERENT` is `knowledge status`'s word; gauntlet G7), exit 0, about 2 min.
- `PYTHONPATH=. python scripts/cli.py search "trace privacy" --hybrid --trace`, then read the written JSON. Expected: `trace_mode` is `summary`, no query text, no Markdown twin, file mode 0600. Delete that one trace afterwards (it is untracked).
- `PYTHONPATH=. python scripts/cli.py trace prune --dry-run`. Expected: it lists no tracked file.
- `PYTHONPATH=. python scripts/eval_provenance.py --gold eval/provenance_real_gold.jsonl --json`. Expected: identical metrics to the frozen RQ1 values (86%/79% pair as recorded in AGENTS.md).

- [ ] **Step 4: Docs**

Spec §3.2: replace "are removed by `zurvan trace prune`, run from check.sh's maintenance step" with "are removed by `zurvan trace prune` and after every trace write (`TraceStore.write`); check.sh has no maintenance step, and a quality gate should not delete files". Spec status: "Z1a BUILT <date>".

Add a CHANGELOG entry and an AGENTS entry (rotate: keep the two newest, move the older one verbatim to `docs/agents-history.md`). Include the ranking-unchanged before/after numbers; the retrieval rule requires them.

In CLAUDE.md, add one bullet under Hard rules: `--trace` is `summary` by default (no raw query); `eval search` output names items by index; the 50-item instrument is `RETIRED` in `eval/datasets.json`.

- [ ] **Step 5: Verify and commit**

Run: `PYTHONPATH=. python -m pytest -q > /tmp/z1a-suite.txt 2>&1; tail -3 /tmp/z1a-suite.txt; PYTHONPATH=. python scripts/public_repo_guard.py; git diff --check; git diff --stat`
Expected: 0 failed; guard passed; no whitespace errors; the diff stat is in proportion to the tasks.

```bash
git add tests/test_z1a_closure.py docs/superpowers/specs/2026-09-29-z1-observability-sealed-eval-design.md CHANGELOG.md AGENTS.md docs/agents-history.md CLAUDE.md
git commit -m "zurvan: Z1a closed - observability gates demonstrated; ranking unchanged vs BASE"
```

Then write back to Zurvan (after all measurement): `decision add` (summary-default traces; index-only eval output), `agent postedit`, and `knowledge status` must be CLEAN with no drift.

---

## Execution record (2026-10-01)

Executed on branch `z1a-observability` from `a79b26b`. Each task had an implementer and a task review (subagents on Sonnet); Task 9 ran inline. Commits are in git. This section keeps what the gitignored SDD ledger held and git does not: every ruling made on Raouf's behalf, the live measurements, and the review minors deferred to a later pass.

### Rulings

- Ruling: the "35 tracked traces" figure in plan Global Constraints and spec §9 gate 3 is a prose miscount; the invariant is "every git-tracked file under data/traces", counted from git at run time (33 json today) — why: tests already derive the set from git ls-files, and the gate's intent is "no tracked trace pruned" — cost if wrong: none to code; Task 9 docs should state 33 (or "all tracked") instead of 35.
- Ruling: fix it — write() must not fail because best-effort retention failed; tolerate vanished files in prune (stat/unlink missing_ok) and never let a prune OSError escape write(); tracked-file safety unchanged — spec §3.2 frames retention as housekeeping, and gate 3 (never prune tracked) is untouched — cost if wrong: a swallowed prune error hides retention not running (visible via `trace prune --dry-run`).
- Ruling: `explain=False` on search_memory is Task 4 scope (plan Task 4 step 3, l.978 adds it), not a Task 3 omission — the brief's Interfaces line anticipates — cost if wrong: none, Task 4 adds it.
- Ruling: summary traces keeping source_path/chunk_id is correct — spec §3.1 table: summary keeps "result ids + ranks + scores", drops raw query (sha256+length) and snippets — cost if wrong: result ids reveal which pages matched, which the spec accepts for summary.
- Ruling: Z1a gate 6 ("RETIRED and refused by the promotion workflow") closes in Z1a on the guard `require_promotion_eligible` + test_only_holdout_can_gate_a_promotion (plan closure matrix l.1984); no Z1a task calls it because the promotion workflow is Z1b scope (spec §0 table l.20). Task 9 docs must state "guard in place; the Z1b promotion workflow must call it", not claim a workflow refuses today — cost if wrong: a gate reported as passed on a caller that doesn't exist (CLAUDE.md: never redefine a gate to pass).
- Ruling: I1 fix — movers re-resolves each run's state from the CURRENT registry by the run's whole-file sha256 and refuses if either the stored state or the registry state is sealed (HOLDOUT/RESERVE); stored-state check kept for forged files — spec gate 5 "Movers refuse holdout/reserve" is absolute and sealed data is never itemised — cost if wrong: one registry read per movers call.
- Ruling: I2 fix — movers refuses (new code ITEMS_MISMATCH) when the two runs' row index sets differ; save_run still saves truncated runs (a --max-queries liveness run is legitimate) — spec gate 5/§3.4 movers must not report fabricated rank changes — cost if wrong: comparing a truncated to a full run needs two same-size runs.
- Ruling: split Task 9 — Step 1 (closure-matrix test, worktree only) dispatched now; Steps 2-3 (live measurement) and Step 4-5 (docs carrying the measured numbers) HELD until Raouf decides how to reconcile the live store (record drift, rebuild search+graph, gc the 2 dead snapshots, and whether another session is writing) — spec/CLAUDE.md: measure only on reconciled inputs, never overlap a rebuild with other writers — cost if wrong: a delay only.
- Ruling: fix the matrix (fix round 1): each gate maps to a LIST of tests that genuinely demonstrate it (5 -> movers tests; 6 -> test_registry_retires_the_burned_instrument + test_only_holdout_can_gate_a_promotion; 8 -> test_instruments.py leak tests + test_committed_v1_traces_have_no_schema_issue), and a LIVE_PROOF dict names gates 1,4,7,8 whose closure additionally needs the Task 9 live run (live trace check; BASE-vs-HEAD ranking proof; real `eval reproduce`; full suite 0 failed on merged main where the 15 worktree env failures cannot occur) — CLAUDE.md "never redefine a gate so work passes"; a closure matrix that reads as full closure while live proofs are pending is that — cost if wrong: a few lines of test.

### Live measurements (revision 302)

- GATE 4 RESULT: BASE vs HEAD top-10 lists for all 50 items byte-identical on one VERIFIED-captured rev-302 snapshot: cmp equal, sha256 6cce77720b64 both; 50 items, all non-empty, all <=10. (Metrics half pending: hang diagnosis.)
- LIVE: `trace prune --dry-run` (HEAD code, live root): "Would remove 0 trace file(s); kept 33 tracked."
- LIVE: eval_provenance on provenance_real_gold.jsonl: BASE and HEAD JSON byte-identical; cases 12, expected_source_recall 0.8611 (86%), raw_leak_rate 0.0, hash_integrity_rate 1.0, provenance_completeness 1.0, graph_context_presence 1.0.
- GATE 4 METRICS (one VERIFIED rev-302 snapshot, 12m28s, released): base {queries 50, top1 0.72, top3 0.74, mrr 0.74, failures 13} == head {same}; head carries result_digest, base none. With the byte-identical top-10 lists => ranking unchanged vs BASE. GATE 4 PASSED.
- GATE 7 LIVE: `zurvan eval reproduce` (HEAD code, live root) exit 0, 9m45s: equal true, digests 0982995bc307…72ab x2, coherence VERIFIED rev 302 purpose measurement atomicity EXTERNAL_OBSERVED; snapshot released (data/snapshots empty). Gold = the 50-item instrument (same path as metrics run, queries 50). GATE 7 PASSED.
- GATE 1 LIVE: `search "<synthetic probe>" --hybrid --trace` (HEAD, live root) exit 0 -> trace v2, trace_mode summary, file 0600, data/traces dir now 0700 (tightened by the write; git does not track dir modes), probe string occurrences in file bytes 0, no wiki/traces twin, untracked; deleted afterwards (33 tracked remain, git status clean). GATE 1 PASSED.

### Deferred review minors

- Task 1: minor (deferred): test_committed_v1_traces_have_no_schema_issue filters only schema_version/trace_mode issues and does not assert files are v1 (plan-mandated test text) — **fixed (Z1a hardening, 2026-10-01)**
- Task 1: minor (deferred): trace_validate accepts a v1 file carrying trace_mode != "full" while record_from_dict raises — validator/loader disagree on that edge — **fixed (Z1a hardening, 2026-10-01)**
- Task 1: minor (deferred): trace_validate.py:6 import line ~150 chars — **fixed (Z1a hardening, 2026-10-01)**
- Task 2: minor (deferred): stale 'full' Markdown twin survives if the same trace id is rewritten in summary/protected_eval mode (privacy-relevant; ids are normally unique) — **fixed (Z1a hardening, 2026-10-01)**
- Task 2: minor (deferred): _write_private truncates an existing 0644 file before chmod (brief readable window; dir is 0700) — **fixed (Z1a hardening, 2026-10-01)**
- Task 2: minor (deferred): `trace prune` accepts negative --days/--keep (tracked still safe) — **fixed (Z1a hardening, 2026-10-01)**
- Task 2: minor (deferred): write() spawns git ls-files every call, 30s timeout, no cache; refused reason discarded — **not fixed: ~7 ms per write, measured**
- Task 2: minor (deferred): no tests for ls-files failure inside a repo, trace prune CLI — **fixed (Z1a hardening, 2026-10-01)**
- Task 2: minor (deferred): dry_run prune may list a vanished file in `removed` (cosmetic) — **not fixed: cosmetic**
- Task 3: minor (deferred): no end-to-end test that search_memory/export_context(trace=True) with no trace_mode writes summary (default only covered at writer + CLI) — **fixed (Z1a hardening, 2026-10-01)**
- Task 3: minor (deferred): CLI test stubs search_memory; `context --trace-mode` dispatch untested — **fixed (Z1a hardening, 2026-10-01)**
- Task 3: minor (deferred): summary graph nodes keep `path` (page filename may echo a title) — spec-allowed result id — **not fixed: spec-allowed result id**
- Task 4: minor (deferred): timing capture runs even when timings=None (~11 perf_counter_ns; plan-mandated, negligible) — **not fixed: negligible, plan-mandated**
- Task 4: minor (deferred): --explain with missing index prints empty stage header — **not fixed: cosmetic**
- Task 4: minor (deferred): no test for --explain --json timings_ms or hybrid-mode trace timings — **fixed (Z1a hardening, 2026-10-01)**
- Task 5: minor (deferred): require_itemised_allowed is a deny-list (HOLDOUT/RESERVE); a hand-built DatasetInfo with bogus state passes (plan-mandated; dataset_for_file validates states) — **fixed (Z1a hardening, 2026-10-01)**
- Task 5: minor (deferred): duplicate-digest registry entries resolved by order, not refused; no sha256 format check; DatasetInfo not validated on construction — **fixed (Z1a hardening, 2026-10-01)**
- Task 6: minor (deferred): keyword-mode (hybrid=False) rows hold absolute glob paths, so result_digest differs across checkouts (hybrid uses index-relative source_path) — **not fixed: would change keyword-mode ranking; needs its own before/after eval**
- Task 6: minor (deferred): keep_rows leaves `_rows` in the returned metrics dict; stripped from JSON output but a future caller serialising metrics would emit retrieved ids — **fixed (Z1a hardening, 2026-10-01)**
- Task 7: minor (deferred): eval_search.py --save-run on sealed dataset refuses only after full evaluation, as a traceback (no row written); should pre-check and exit 2 — **fixed (Z1a hardening, 2026-10-01)**
- Task 7: minor (deferred): run_search_evaluation JSON prints failing_items indices for any dataset incl. sealed (Task 6 code) — Z1b broker scope, cross-check vs gate 5 in final review — **fixed (Z1a hardening, 2026-10-01)**
- Task 7: minor (deferred): failed --min-top3 exits before save; runs dir mkdir then chmod (empty-dir window), ~/.zurvan parent default perms — **partly: runs dir now created 0700 (fixed); save-on-failed-gate not changed (gate exit behaviour)**
- Task 7: minor (deferred): `eval movers` CLI catches only DatasetNotEligible; an invalid registry state surfaces as a traceback (still fails closed); untested — **fixed (Z1a hardening, 2026-10-01)**
- Task 8: minor (deferred): reproduce/CLI report equal=true on an empty gold set or runs=1 (vacuous pass) — Task 9 live gate must confirm n_items=50 — **fixed (Z1a hardening, 2026-10-01)**
- Task 8: minor (deferred): snapshot_index in output names an already-released path; no test for CLI SnapshotNotReady->exit 2 or KeyboardInterrupt release — **fixed (Z1a hardening, 2026-10-01)**
- Task 9a: minor (deferred): test_the_live_instrument_is_not_tracked_by_git runs git ls-files from cwd, vacuous if run elsewhere (pre-existing) — **fixed (Z1a hardening, 2026-10-01)**
