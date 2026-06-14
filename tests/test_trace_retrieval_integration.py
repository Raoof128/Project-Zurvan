import json
import subprocess
import sys

import pytest

import scripts.context_export as context_export
from scripts.trace_replay import replay_trace_file


def _sample_results():
    return [
        {
            "chunk_id": "chunk-001",
            "source_path": "wiki/sources/mcp.md",
            "heading": "MCP",
            "text": "MCP provenance and retrieval tracing.",
            "keyword_score": 0.75,
            "semantic_score": 0.5,
            "hybrid_score": 0.65,
        }
    ]


def test_export_context_trace_writes_retrieval_and_graph_events(tmp_path, monkeypatch):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(context_export, "_search_internal", lambda *args, **kwargs: _sample_results())
    monkeypatch.setattr(
        context_export,
        "expand_graph_context",
        lambda seeds, depth=1: [
            {
                "path": "wiki/decisions/use-mcp.md",
                "node_type": "decision",
                "title": "Use MCP",
                "depth": depth,
                "relation": "outgoing:mentions",
                "source_id": "node-001",
            }
        ],
    )

    output = context_export.export_context(
        "MCP provenance",
        hybrid=True,
        graph=True,
        depth=2,
        trace=True,
        trace_id="trace-20260614T121314Z-r2000001",
    )

    trace_path = tmp_path / "data" / "traces" / "trace-20260614T121314Z-r2000001.json"
    mirror_path = tmp_path / "wiki" / "traces" / "trace-20260614T121314Z-r2000001.md"
    data = json.loads(trace_path.read_text())

    assert "Trace written:" in output
    assert mirror_path.exists()
    assert [event["event_type"] for event in data["events"]] == ["retrieval", "graph_context"]
    assert data["events"][0]["payload"]["mode"] == "hybrid"
    assert data["events"][0]["payload"]["results"][0]["keyword_score"] == 0.75
    assert data["events"][0]["payload"]["results"][0]["hybrid_score"] == 0.65
    assert data["events"][1]["payload"]["depth"] == 2
    assert data["events"][1]["payload"]["nodes"][0]["relation"] == "outgoing:mentions"
    assert "# Trace Replay: Retrieval context: MCP provenance" in replay_trace_file(trace_path)


def test_export_context_without_trace_preserves_output_and_writes_no_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(context_export, "_search_internal", lambda *args, **kwargs: _sample_results())

    baseline = context_export.export_context("MCP provenance", hybrid=True, trace=False)
    repeated = context_export.export_context("MCP provenance", hybrid=True)

    assert repeated == baseline
    assert "Trace written:" not in repeated
    assert not (tmp_path / "data" / "traces").exists()
    assert not (tmp_path / "wiki" / "traces").exists()


def test_search_memory_trace_writes_replayable_trace(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(context_export, "_search_internal", lambda *args, **kwargs: _sample_results())

    context_export.search_memory(
        "MCP provenance",
        hybrid=True,
        trace=True,
        trace_id="trace-20260614T121314Z-r2000002",
    )

    captured = capsys.readouterr()
    trace_path = tmp_path / "data" / "traces" / "trace-20260614T121314Z-r2000002.json"
    data = json.loads(trace_path.read_text())

    assert "Trace written:" in captured.out
    assert data["events"][0]["payload"]["command"] == "search"
    assert "Trace Replay" in replay_trace_file(trace_path)


def test_search_memory_without_trace_preserves_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(context_export, "_search_internal", lambda *args, **kwargs: _sample_results())

    context_export.search_memory("MCP provenance", hybrid=True, trace=False)
    first = capsys.readouterr().out
    context_export.search_memory("MCP provenance", hybrid=True)
    second = capsys.readouterr().out

    assert second == first
    assert "Trace written:" not in second
    assert not (tmp_path / "data" / "traces").exists()


def test_context_trace_rejects_unsafe_trace_id(tmp_path, monkeypatch):
    monkeypatch.setattr(context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(context_export, "_search_internal", lambda *args, **kwargs: _sample_results())

    with pytest.raises(ValueError, match="unsafe trace_id"):
        context_export.export_context("MCP provenance", trace=True, trace_id="../raw/secret")


def test_cli_context_trace_uses_project_root_override(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "source.md").write_text("cli_context_trace_unique")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/cli.py",
            "--project-root",
            str(tmp_path),
            "context",
            "--topic",
            "cli_context_trace_unique",
            "--trace",
            "--trace-id",
            "trace-20260614T121314Z-r2000003",
        ],
        capture_output=True,
        text=True,
    )

    trace_path = tmp_path / "data" / "traces" / "trace-20260614T121314Z-r2000003.json"
    assert result.returncode == 0
    assert "Trace written:" in result.stdout
    assert trace_path.exists()


def test_cli_search_trace_uses_project_root_override(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "source.md").write_text("cli_search_trace_unique")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/cli.py",
            "--project-root",
            str(tmp_path),
            "search",
            "cli_search_trace_unique",
            "--trace",
            "--trace-id",
            "trace-20260614T121314Z-r2000004",
        ],
        capture_output=True,
        text=True,
    )

    trace_path = tmp_path / "data" / "traces" / "trace-20260614T121314Z-r2000004.json"
    assert result.returncode == 0
    assert "Trace written:" in result.stdout
    assert trace_path.exists()


def test_cli_context_trace_rejects_unsafe_trace_id_without_traceback(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "source.md").write_text("cli_unsafe_trace_unique")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/cli.py",
            "--project-root",
            str(tmp_path),
            "context",
            "--topic",
            "cli_unsafe_trace_unique",
            "--trace",
            "--trace-id",
            "../raw/secret",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unsafe trace_id" in result.stdout
    assert "Traceback" not in result.stderr
