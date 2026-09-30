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


def test_a_prune_failure_never_fails_a_write_that_landed(tmp_path, monkeypatch):
    store = TraceStore(tmp_path)

    def boom(*a, **k):
        raise OSError("prune blew up")

    monkeypatch.setattr(TraceStore, "prune", boom)
    paths = store.write(_trace())
    assert paths.json_path.exists()


def test_prune_tolerates_a_trace_that_vanishes_mid_scan(tmp_path, monkeypatch):
    _git_repo(tmp_path)
    store = TraceStore(tmp_path)
    store.data_dir.mkdir(parents=True)
    ghost = store.data_dir / "trace-20260929T000000Z-ghost001.json"
    monkeypatch.setattr(type(ghost), "glob", lambda self, pat: iter([ghost]))
    result = store.prune(days=0, keep=0)
    assert result.refused is None and result.removed == []
