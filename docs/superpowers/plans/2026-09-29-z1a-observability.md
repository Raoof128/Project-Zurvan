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


def _rec(**kw):
    base = dict(trace_id="trace-20260929T000000Z-modes001", title="t", summary="s")
    base.update(kw)
    return TraceRecord(**base)


def test_new_records_are_v2_and_carry_their_mode():
    data = _rec(trace_mode="summary").to_dict()
    assert data["schema_version"] == SCHEMA_VERSION == "zurvan.trace.v2"
    assert data["trace_mode"] == "summary"
    assert TRACE_MODES == ("summary", "full", "protected_eval")


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError, match="trace_mode"):
        _rec(trace_mode="verbose")


def test_v1_cannot_claim_a_mode():
    # v1 predates modes and stored raw payloads: calling one "summary" would lie.
    with pytest.raises(ValueError, match="trace_mode"):
        _rec(schema_version=V1, trace_mode="summary")


def test_v1_dict_round_trips_byte_for_byte():
    original = {"schema_version": V1, "trace_id": "trace-20260614T000000Z-roundtr1",
                "created_at": "2026-06-14T00:00:00Z", "title": "t", "summary": "s",
                "events": []}
    assert record_from_dict(original).to_dict() == original


def test_validator_accepts_v2_and_rejects_a_bad_mode(tmp_path):
    good = _rec(trace_mode="summary").to_dict()
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


def _rec(tid="trace-20260929T000000Z-priv0001", mode="summary"):
    return TraceRecord(trace_id=tid, title="t", summary="s", trace_mode=mode)


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_trace_files_and_dirs_are_private(tmp_path):
    paths = TraceStore(tmp_path).write(_rec(mode="full"))
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
    store.write(_rec())
    assert _mode(target) == 0o600


def test_summary_writes_no_markdown_twin_full_does(tmp_path):
    store = TraceStore(tmp_path)
    assert not store.write(_rec(mode="summary")).markdown_path.exists()
    assert store.write(_rec("trace-20260929T000000Z-priv0002", "full")).markdown_path.exists()


def test_protected_context_refuses_any_other_mode_before_writing(tmp_path):
    store = TraceStore(tmp_path)
    with protected_eval_context():
        assert in_protected_eval()
        for mode in ("summary", "full"):
            with pytest.raises(ProtectedTraceError) as exc:
                store.write(_rec(mode=mode))
            assert exc.value.code == "PROTECTED_TRACE"
        assert not store.trace_path("trace-20260929T000000Z-priv0001").exists()
        paths = store.write(_rec("trace-20260929T000000Z-priv0003", "protected_eval"))
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
    tracked = store.write(_rec("trace-20260929T000000Z-tracked1", "full"))
    subprocess.run(["git", "-C", str(tmp_path), "add", "-f", "data/traces", "wiki/traces"], check=True)
    old = store.write(_rec("trace-20260929T000000Z-oldtrace", "full"))
    new = store.write(_rec("trace-20260929T000000Z-newtrace"))
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
        _age(store.write(_rec(tid)).json_path, age, now)
    removed = store.prune(keep=1, now=now).removed
    assert sorted(removed) == sorted(str(store.trace_path(t)) for t in ids[:2])


def test_prune_dry_run_deletes_nothing(tmp_path):
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    p = store.write(_rec()).json_path
    result = store.prune(days=0, keep=0, dry_run=True)
    assert str(p) in result.removed and p.exists()


def test_prune_outside_git_refuses_and_removes_nothing(tmp_path):
    store = TraceStore(tmp_path)
    p = store.write(_rec()).json_path
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
- Modify: `tests/test_trace_retrieval_integration.py` (existing assertions expect the Markdown mirror and raw query, so pass `trace_mode="full"` there)
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


def test_unknown_mode_is_refused(tmp_path, monkeypatch):
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

In `tests/test_trace_retrieval_integration.py`, add `trace_mode="full",` to every `export_context(...)` and `search_memory(...)` call that asserts on the mirror or the raw query. That is a deliberate behaviour change: the default no longer writes them.

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
- Test: `tests/test_stage_timings.py` (create)

**Interfaces:**
- Produces:
  - `search_hybrid(query, limit=10, db_path=None, timings: dict | None = None)`. When `timings` is a dict it is filled with float ms for `STAGES = ("fts", "embed_query", "vector_scan", "hydrate", "fuse", "total")`. The return value is unchanged.
  - `_search_internal(..., timings=None)`, where keyword mode fills `{"keyword_scan", "total"}`.
  - `search_memory(..., explain=False)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage_timings.py`:

```python
import pytest

from scripts import hybrid_search as hs


@pytest.fixture
def index(tmp_path, monkeypatch):
    # Reuse the fixture builder the existing hybrid tests use; see Step 1b.
    from tests.test_hybrid_search import _build_tiny_index  # noqa: F401 (resolved in Step 1b)
    return _build_tiny_index(tmp_path, monkeypatch)


def test_timings_are_filled_and_ranking_unchanged(index):
    plain = hs.search_hybrid("alpha beta", limit=5, db_path=index)
    timings = {}
    timed = hs.search_hybrid("alpha beta", limit=5, db_path=index, timings=timings)
    assert timed == plain
    assert set(timings) == set(hs.STAGES)
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")


def test_missing_index_leaves_timings_empty(tmp_path):
    timings = {}
    assert hs.search_hybrid("x", db_path=str(tmp_path / "none.sqlite"), timings=timings) == []
    assert timings == {}
```

- [ ] **Step 1b: Resolve the fixture**

Read `tests/test_hybrid_search.py`. If it has a helper that builds a small index file, expose it as `_build_tiny_index(tmp_path, monkeypatch) -> str`, or import the existing one under that name. If it builds indexes inline, move that code into `_build_tiny_index` in that file without changing its tests. Ledger the ruling. The helper must use the mock embedding provider exactly as the existing tests do.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_stage_timings.py -q`
Expected: FAIL. `search_hybrid()` got an unexpected keyword argument `timings` (or `STAGES` is missing).

- [ ] **Step 3: Implement in `scripts/hybrid_search.py`**

```python
import time

#: Stages recorded by `search_hybrid(timings=...)` — observation only.
STAGES = ("fts", "embed_query", "vector_scan", "hydrate", "fuse", "total")


def _ms(start_ns: int) -> float:
    return round((time.perf_counter_ns() - start_ns) / 1e6, 3)
```

Change the signature to `def search_hybrid(query: str, limit: int = 10, db_path: str | None = None, timings: dict | None = None) -> List[Dict]:`. Then record spans without changing any computation:
- `t_total = time.perf_counter_ns()` after the missing-index early return;
- `t = time.perf_counter_ns()` before the FTS block, and after the normalisation loop `stage["fts"] = _ms(t)`;
- around `get_embedding(...)`: `stage["embed_query"]`;
- around `_score_semantic_top_k(...)`: `stage["vector_scan"]`;
- around `_hydrate(...)`: `stage["hydrate"]`;
- around the final `_fuse(scored)[:limit]`, computed into a local `fused` before `return`: `stage["fuse"]`, then `stage["total"] = _ms(t_total)`.

Use a local `stage = {}` and, just before returning, `if timings is not None: timings.update(stage)`. The missing-index path returns `[]` before any timing and leaves `timings` untouched.

In `scripts/context_export.py`:
- `_search_internal` gains `timings: Dict[str, float] | None = None`. In hybrid mode, pass `timings=timings`. In keyword mode, time the file scan: `t0 = time.perf_counter_ns()` at the start, and before `return` do `if timings is not None: timings.update({"keyword_scan": ms, "total": ms})`.
- `search_memory` gains `explain: bool = False`. Collect `timings = {}` and pass it to `_search_internal`, then pass `timings=timings` to `_write_retrieval_trace`.
  - In JSON mode add `"timings_ms": timings` when `explain`.
  - In text mode, when `explain`, print after the results:

```python
        if explain:
            print("\nStage timings (ms):")
            for name, value in timings.items():
                print(f"  {name:<12} {value:>10.3f}")
```

In `scripts/cli.py`, add to the search parser:
`search_parser.add_argument("--explain", action="store_true", help="Print per-stage timings")`. Pass `explain=getattr(args, "explain", False)` in the dispatch.

- [ ] **Step 4: Run to verify**

Run: `PYTHONPATH=. python -m pytest tests/test_stage_timings.py tests/test_hybrid_search.py tests/test_trace_modes_retrieval.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/hybrid_search.py scripts/context_export.py scripts/cli.py tests/test_stage_timings.py tests/test_hybrid_search.py
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
```

In `tests/test_eval_search.py`, rename `test_metrics_name_the_failing_queries` to `test_metrics_name_the_failing_items` and assert `metrics["failing_items"] == [0, 1, 2]` and `"failing_queries" not in metrics`. Keep its comment: indices serve the before/after need without quoting the instrument.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_eval_evaluate.py tests/test_eval_search.py -q`
Expected: FAIL. `es.evaluate` does not exist.

- [ ] **Step 3: Implement in `scripts/eval_search.py`**

Add `import hashlib, statistics` and:

```python
def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
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

Move the existing keyword branch (the `glob` scan) verbatim into `def _keyword_retrieve(query) -> list[str]`.

Import `get_rank` alongside the existing `scripts.metrics` imports. It is the matcher `calculate_top_k_hit` and `calculate_reciprocal_rank` already use (substring match, 1-based, 0 = miss), so `first_hit_rank` cannot disagree with the metrics.

In `run_search_evaluation`, replace the per-item loop and metric computation with:

```python
    core, rows = evaluate(dataset, index_path=index_path, hybrid=hybrid)
    metrics = dict(core)
    metrics.update({"min_top3": min_top3, "passed": core["top3_accuracy"] >= min_top3,
                    "index_path": index_path, "index_identity": _identity_of(index_path),
                    "snapshot_coherence": _coherence_of(index_path)})
    top1_accuracy, top3_accuracy, mrr = core["top1_accuracy"], core["top3_accuracy"], core["mrr"]
    failures, total_queries = core["failures"], core["queries"]
```

Keep the text report and the mock-embedding and min-top3 logic exactly as they are, with `metrics` from above. Store rows for Task 7 by returning them: make `run_search_evaluation` set `metrics["_rows"] = rows` only when a new keyword `keep_rows=True` is passed, and `pop` it before printing JSON.

The failing-query path of `load_gold_dataset` prints the raw line on a malformed record. Change `print(f"Warning: Skipping malformed line (missing required fields): {line}")` and the JSON-error print to `print(f"Warning: Skipping malformed line {lineno}")` with a line number, never the content.

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

Wire the CLI:
- `scripts/eval_search.py` argparse gains `--save-run` (store_true). When it is set, run with `keep_rows=True`, pop `_rows`, call `save_run(metrics, rows, dataset_for_file(gold))` and print `Run saved: <path>` (to stderr in `--json` mode).
- `scripts/cli.py`:
  - `eval_search_parser.add_argument("--save-run", action="store_true")`, with `["--save-run"]` appended in the `_run_script` call when it is set;
  - add the movers subcommand:

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
- Consumes: `measurement_snapshot.capture(components=..., purpose=..., required=...)` returning `Snapshot` (`.index_path`, `.release()`, context manager); `SnapshotNotReady` from `scripts.build_snapshot`; `evaluate`, `load_gold_dataset`.
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
            {"coherence": {"status": "COHERENT", "revision": 7, "atomicity": "MANAGED",
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
    assert out["coherence"]["status"] == "COHERENT"
    assert calls[0]["required"] == ("wiki", "search", "graph")
    assert calls[0]["purpose"] == "measurement"
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
    snap = capture_fn(components=REPRODUCE_REQUIRED, label="z1a-reproduce",
                      purpose="measurement", required=REPRODUCE_REQUIRED)
    try:
        digests = [evaluate_fn(dataset, index_path=str(snap.index_path), hybrid=True)[0]["result_digest"]
                   for _ in range(runs)]
        return {"equal": len(set(digests)) == 1, "digests": digests,
                "coherence": _coherence_of(str(snap.index_path)),
                "snapshot_index": str(snap.index_path)}
    finally:
        snap.release()
```

Before running, read `measurement_snapshot.capture`'s signature (`scripts/measurement_snapshot.py:611`). If `components=` must include `"search"` explicitly, or `required=` alone implies components, adjust the call to the real contract and ledger the ruling.

CLI (`scripts/cli.py`):

```python
    eval_repro_parser = eval_sub.add_parser("reproduce", help="Score the gold set twice on one coherent Z0 snapshot; compare result digests")
    eval_repro_parser.add_argument("--gold", default=None)
```

```python
    elif args.command == "eval" and args.action == "reproduce":
        from scripts.build_snapshot import SnapshotNotReady
        from scripts.eval_runs import reproduce
        try:
            out = reproduce(_resolved_gold(args.gold))
        except SnapshotNotReady as exc:
            print(f"SNAPSHOT_NOT_READY: {exc}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps(out, indent=2))
        sys.exit(0 if out["equal"] and out["coherence"].get("status") == "COHERENT" else 1)
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
import importlib

import pytest

MATRIX = {
    1: "tests.test_trace_modes_retrieval::test_summary_trace_contains_no_query_text_anywhere",
    2: "tests.test_trace_privacy::test_trace_files_and_dirs_are_private",
    3: "tests.test_trace_privacy::test_prune_dry_run_on_live_repo_never_selects_tracked",
    4: "tests.test_stage_timings::test_timings_are_filled_and_ranking_unchanged",
    5: "tests.test_eval_runs::test_save_run_refuses_holdout_before_writing",
    6: "tests.test_eval_datasets::test_only_holdout_can_gate_a_promotion",
    7: "tests.test_eval_reproduce::test_equal_digests_on_one_snapshot_and_release",
    8: "tests.test_trace_modes::test_committed_v1_traces_have_no_schema_issue",
}


@pytest.mark.parametrize("gate,ref", sorted(MATRIX.items()))
def test_every_z1a_gate_has_a_named_test(gate, ref):
    module, name = ref.split("::")
    assert callable(getattr(importlib.import_module(module), name, None)), f"gate {gate}: {ref}"
```

Run: `PYTHONPATH=. python -m pytest tests/test_z1a_closure.py -q`. Expected: 8 passed.
Falsify: rename one referenced test temporarily, confirm FAIL, then restore.

- [ ] **Step 2: Ranking-unchanged proof against BASE**

BASE is the commit before Task 1 (recorded by `task-start` for Task 1).
1. `git worktree add /tmp/z1a-base <BASE>` (scratchpad path allowed).
2. Capture one coherent snapshot: `PYTHONPATH=. python scripts/measurement_snapshot.py --require wiki,search,graph --json`. Record the snapshot's index path. If this refuses with `SNAPSHOT_NOT_READY`, stop and report; never work around it.
3. For both trees, run this script against the snapshot index and write ranked paths per index to a file:

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

Run it with `PYTHONPATH=<tree> python /scratch/rank_dump.py <tree> <snapshot-index> <tree>.json`.

4. `cmp base.json head.json`. Expected: identical. Any difference is a failed gate: stop and report it, never edit the proof.
5. Release the snapshot (`zurvan knowledge gc` or the snapshot's release) and remove the worktree.

Also run the retrieval eval before and after on the same snapshot:

`PYTHONPATH=. python scripts/eval_search.py --hybrid --index <snapshot-index> --json`

It runs once on HEAD. The same command runs in the BASE worktree without `result_digest`: compare `top1_accuracy`, `top3_accuracy` and `mrr`, which must be equal.

- [ ] **Step 3: Live gates**

Run each; record the output in the ledger.
- `PYTHONPATH=. python scripts/cli.py eval reproduce`. Expected: `"equal": true`, `coherence.status == "COHERENT"`, exit 0.
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
