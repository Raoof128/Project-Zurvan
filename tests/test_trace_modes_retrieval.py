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
