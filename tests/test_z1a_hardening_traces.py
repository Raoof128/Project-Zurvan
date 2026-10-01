"""Z1a hardening, traces: the review minors deferred during Z1a (plan,
"Execution record (2026-10-01)")."""
import json
import os
import stat
import subprocess

import pytest

import scripts.context_export as ce
import scripts.trace_writer as tw
from scripts.trace_schema import TraceRecord
from scripts.trace_validate import validate_trace_file

QUERY = "hardening-marker quokka basalt"
RESULTS = [{"chunk_id": "c1", "source_path": "wiki/a.md", "heading": "h",
            "text": "snippet body", "keyword_score": 0.5, "semantic_score": 0.5,
            "hybrid_score": 0.5}]


def _trace(tid, mode):
    return TraceRecord(trace_id=tid, title="t", summary="s", trace_mode=mode)


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_rewriting_a_full_trace_as_summary_removes_its_markdown_twin(tmp_path):
    store = tw.TraceStore(tmp_path)
    tid = "trace-20261001T000000Z-twin0001"
    full = store.write(_trace(tid, "full"))
    assert full.markdown_path.exists()
    store.write(_trace(tid, "summary"))
    assert not full.markdown_path.exists()


def test_an_existing_file_is_private_before_new_bytes_land(tmp_path, monkeypatch):
    store = tw.TraceStore(tmp_path)
    target = store.trace_path("trace-20261001T000000Z-mode0001")
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    os.chmod(target, 0o644)
    seen = []
    real_fdopen = os.fdopen

    def spy(fd, *a, **k):
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        return real_fdopen(fd, *a, **k)

    monkeypatch.setattr(tw.os, "fdopen", spy)
    store.write(_trace("trace-20261001T000000Z-mode0001", "summary"))
    assert seen and all(m == 0o600 for m in seen)


@pytest.mark.parametrize("kw", [{"days": -1}, {"keep": -1}])
def test_prune_refuses_negative_retention(tmp_path, kw):
    with pytest.raises(ValueError, match="non-negative"):
        tw.TraceStore(tmp_path).prune(**kw)


@pytest.mark.parametrize("flag", ["--days", "--keep"])
def test_cli_trace_prune_refuses_negative_retention(monkeypatch, flag):
    import scripts.cli as cli
    monkeypatch.setattr("sys.argv", ["zurvan", "trace", "prune", flag, "-1", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cli_trace_prune_dry_run_lists_an_old_untracked_trace(monkeypatch, tmp_path, capsys):
    import scripts.cli as cli
    import scripts.wiki_merge as wm
    # --project-root rebinds these module globals; monkeypatch restores them
    monkeypatch.setattr(ce, "PROJECT_ROOT", ce.PROJECT_ROOT)
    monkeypatch.setattr(wm, "PROJECT_ROOT", wm.PROJECT_ROOT)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    old = tw.TraceStore(tmp_path).write(_trace("trace-20261001T000000Z-cliold01", "summary"))
    os.utime(old.json_path, (0, 0))
    monkeypatch.setattr("sys.argv", ["zurvan", "--project-root", str(tmp_path),
                                     "trace", "prune", "--dry-run"])
    cli.main()
    assert "Would remove 1 trace file(s)" in capsys.readouterr().out
    assert old.json_path.exists()


def test_prune_removes_nothing_when_git_times_out_inside_a_repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    store = tw.TraceStore(tmp_path)
    store.write(_trace("trace-20261001T000000Z-gito0001", "summary"))

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(tw.subprocess, "run", boom)
    result = store.prune(days=0, keep=0)
    assert result.removed == [] and result.refused
    assert store.trace_path("trace-20261001T000000Z-gito0001").exists()


def test_validator_rejects_a_v1_trace_claiming_a_mode(tmp_path):
    v1 = {"schema_version": "zurvan.trace.v1", "trace_id": "trace-20261001T000000Z-v1mode01",
          "created_at": "2026-10-01T00:00:00Z", "title": "t", "summary": "s",
          "events": [], "trace_mode": "summary"}
    path = tmp_path / "v1.json"
    path.write_text(json.dumps(v1))
    assert any("trace_mode" in i for i in validate_trace_file(path).issues)
    v1["trace_mode"] = "full"  # the loader accepts this, so the validator must too
    path.write_text(json.dumps(v1))
    assert not [i for i in validate_trace_file(path).issues if "trace_mode" in i]


def test_search_memory_default_trace_is_summary_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ce, "_search_internal", lambda *a, **k: RESULTS)
    ce.search_memory(QUERY, trace=True, trace_id="trace-20261001T000000Z-e2es0001")
    raw = (tmp_path / "data" / "traces" / "trace-20261001T000000Z-e2es0001.json").read_text()
    assert json.loads(raw)["trace_mode"] == "summary"
    assert "quokka" not in raw and "snippet body" not in raw


def test_export_context_default_trace_is_summary_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(ce, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ce, "_search_internal", lambda *a, **k: RESULTS)
    ce.export_context(QUERY, trace=True, trace_id="trace-20261001T000000Z-e2ec0001")
    raw = (tmp_path / "data" / "traces" / "trace-20261001T000000Z-e2ec0001.json").read_text()
    assert json.loads(raw)["trace_mode"] == "summary"
    assert "quokka" not in raw and "snippet body" not in raw


def test_cli_context_trace_mode_dispatch(monkeypatch):
    import scripts.cli as cli
    seen = {}
    monkeypatch.setattr(ce, "export_context", lambda *a, **k: seen.update(k) or "")
    monkeypatch.setattr("sys.argv", ["zurvan", "context", "--topic", "x", "--trace-mode", "full"])
    cli.main()
    assert seen["trace"] is True and seen["trace_mode"] == "full"
    seen.clear()
    monkeypatch.setattr("sys.argv", ["zurvan", "context", "--topic", "x", "--trace"])
    cli.main()
    assert seen["trace"] is True and seen["trace_mode"] == "summary"
    monkeypatch.setattr("sys.argv", ["zurvan", "context", "--topic", "x",
                                     "--trace-mode", "protected_eval"])
    with pytest.raises(SystemExit):
        cli.main()
