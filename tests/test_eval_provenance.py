import json
import subprocess
import sys

import pytest

from scripts.trace_schema import hash_payload


def _event(event_id, event_type, payload):
    return {
        "event_id": event_id,
        "event_type": event_type,
        "timestamp": "2026-06-14T01:02:03Z",
        "actor": "zurvan",
        "payload": payload,
        "payload_hash": hash_payload(payload),
    }


def _write_trace(path, events):
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-prov001",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Provenance trace",
                "summary": "Trace used by provenance evaluation tests.",
                "events": events,
            }
        ),
        encoding="utf-8",
    )


def _write_gold(path, trace_path, **overrides):
    item = {
        "id": "case-001",
        "query": "provenance",
        "trace_path": str(trace_path),
        "expected_source_paths": ["wiki/source.md"],
        "expected_event_types": ["retrieval.query", "retrieval.result", "context.assembled"],
        "expected_chunk_ids": ["chunk-001"],
        "expect_graph_context": True,
    }
    item.update(overrides)
    path.write_text(json.dumps(item) + "\n", encoding="utf-8")


def test_provenance_gold_schema_accepts_future_chunk_ids(tmp_path):
    from scripts.eval_provenance import load_gold_dataset, validate_gold_dataset

    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "provenance"}),
            _event(
                "evt-002",
                "retrieval.result",
                {"results": [{"source_path": "wiki/source.md", "chunk_id": "chunk-001"}]},
            ),
            _event("evt-003", "context.assembled", {"included_chunk_ids": ["chunk-001"], "dropped": []}),
            _event("evt-004", "graph_context", {"nodes": [{"path": "wiki/related.md"}]}),
        ],
    )
    gold_path = tmp_path / "provenance_gold.jsonl"
    _write_gold(gold_path, trace_path)

    dataset = load_gold_dataset(str(gold_path))

    assert dataset[0]["expected_chunk_ids"] == ["chunk-001"]
    assert validate_gold_dataset(str(gold_path)) is True


def test_provenance_evaluation_gates_raw_leaks_before_scoring(tmp_path, capsys):
    from scripts.eval_provenance import run_provenance_evaluation

    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "provenance"}),
            _event("evt-002", "retrieval.result", {"results": [{"source_path": "raw/secret.md"}]}),
        ],
    )
    gold_path = tmp_path / "provenance_gold.jsonl"
    _write_gold(gold_path, trace_path, expected_source_paths=["raw/secret.md"])

    with pytest.raises(SystemExit):
        run_provenance_evaluation(str(gold_path))

    output = capsys.readouterr().out
    assert "Invariant Gate Failed" in output
    assert "raw_leak_rate: 100%" in output
    assert "Provenance Evaluation Results" not in output


def test_provenance_evaluation_gates_hash_mismatch_before_scoring(tmp_path, capsys):
    from scripts.eval_provenance import run_provenance_evaluation

    trace_path = tmp_path / "trace.json"
    event = _event("evt-001", "retrieval.query", {"query": "provenance"})
    event["payload_hash"] = "sha256:bad"
    _write_trace(trace_path, [event])
    gold_path = tmp_path / "provenance_gold.jsonl"
    _write_gold(gold_path, trace_path, expected_source_paths=[], expected_chunk_ids=[], expect_graph_context=False)

    with pytest.raises(SystemExit):
        run_provenance_evaluation(str(gold_path))

    output = capsys.readouterr().out
    assert "Invariant Gate Failed" in output
    assert "hash_integrity_rate: 0%" in output
    assert "Provenance Evaluation Results" not in output


def test_provenance_evaluation_scores_built_scope(tmp_path):
    from scripts.eval_provenance import run_provenance_evaluation

    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "provenance"}),
            _event(
                "evt-002",
                "retrieval.result",
                {
                    "results": [
                        {"source_path": "wiki/source.md", "chunk_id": "chunk-001"},
                        {"source_path": "wiki/other.md", "chunk_id": "chunk-002"},
                    ]
                },
            ),
            _event("evt-003", "context.assembled", {"included_chunk_ids": ["chunk-001"], "dropped": []}),
            _event("evt-004", "graph_context", {"nodes": [{"path": "wiki/related.md"}]}),
        ],
    )
    gold_path = tmp_path / "provenance_gold.jsonl"
    _write_gold(gold_path, trace_path)

    metrics = run_provenance_evaluation(
        str(gold_path),
        min_source_recall=1.0,
        min_provenance_completeness=1.0,
        min_graph_context_presence=1.0,
    )

    assert metrics["raw_leak_rate"] == 0.0
    assert metrics["hash_integrity_rate"] == 1.0
    assert metrics["expected_source_recall"] == 1.0
    assert metrics["provenance_completeness"] == 1.0
    assert metrics["graph_context_presence"] == 1.0


def test_cli_eval_provenance_prints_metrics_table(tmp_path):
    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "provenance"}),
            _event(
                "evt-002",
                "retrieval.result",
                {"results": [{"source_path": "wiki/source.md", "chunk_id": "chunk-001"}]},
            ),
            _event("evt-003", "context.assembled", {"included_chunk_ids": ["chunk-001"], "dropped": []}),
        ],
    )
    gold_path = tmp_path / "provenance_gold.jsonl"
    _write_gold(gold_path, trace_path, expect_graph_context=False)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/cli.py",
            "eval",
            "provenance",
            "--gold",
            str(gold_path),
            "--min-source-recall",
            "1.0",
            "--min-provenance-completeness",
            "1.0",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Provenance Evaluation Results" in result.stdout
    assert "expected_source_recall: 100%" in result.stdout


def test_scorer_does_not_demand_fusion_for_keyword_only_query(tmp_path):
    """Negative branch: retrieval.fusion must NOT be demanded for a keyword-only
    query. The same fusion-less trace scores 100% completeness against a gold that
    does not list fusion, and is incomplete against a gold that does — proving the
    scorer's per-item conditional discriminates rather than always requiring it."""
    from scripts.eval_provenance import run_provenance_evaluation

    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "keyword only"}),
            _event(
                "evt-002",
                "retrieval.result",
                {"results": [{"source_path": "wiki/source.md", "chunk_id": "chunk-001"}]},
            ),
        ],
    )

    keyword_gold = tmp_path / "keyword_only.jsonl"
    _write_gold(
        keyword_gold,
        trace_path,
        expected_event_types=["retrieval.query", "retrieval.result"],
        expected_chunk_ids=[],
        expect_graph_context=False,
    )
    metrics = run_provenance_evaluation(str(keyword_gold), min_provenance_completeness=1.0)
    assert metrics["provenance_completeness"] == 1.0

    hybrid_gold = tmp_path / "hybrid.jsonl"
    _write_gold(
        hybrid_gold,
        trace_path,
        expected_event_types=["retrieval.query", "retrieval.result", "retrieval.fusion"],
        expected_chunk_ids=[],
        expect_graph_context=False,
    )
    with pytest.raises(SystemExit):
        run_provenance_evaluation(str(hybrid_gold), min_provenance_completeness=1.0)


def test_scorer_does_not_score_graph_for_non_graph_query(tmp_path):
    """Negative branch: graph_context_presence must NOT be dragged down by a
    non-graph query (expect_graph_context False is skipped, so presence stays
    1.0), while a graph query against the same graphless trace fails the graph
    gate — proving graph scoring is conditional, not universal."""
    from scripts.eval_provenance import run_provenance_evaluation

    trace_path = tmp_path / "trace.json"
    _write_trace(
        trace_path,
        [
            _event("evt-001", "retrieval.query", {"query": "non graph"}),
            _event(
                "evt-002",
                "retrieval.result",
                {"results": [{"source_path": "wiki/source.md", "chunk_id": "chunk-001"}]},
            ),
            _event("evt-003", "context.assembled", {"included_chunk_ids": ["chunk-001"], "dropped": []}),
        ],
    )

    non_graph_gold = tmp_path / "non_graph.jsonl"
    _write_gold(
        non_graph_gold,
        trace_path,
        expected_event_types=["retrieval.query", "retrieval.result", "context.assembled"],
        expected_chunk_ids=["chunk-001"],
        expect_graph_context=False,
    )
    metrics = run_provenance_evaluation(
        str(non_graph_gold),
        min_provenance_completeness=1.0,
        min_graph_context_presence=1.0,
    )
    assert metrics["graph_context_presence"] == 1.0

    graph_gold = tmp_path / "graph.jsonl"
    _write_gold(
        graph_gold,
        trace_path,
        expected_event_types=["retrieval.query", "retrieval.result", "context.assembled"],
        expected_chunk_ids=["chunk-001"],
        expect_graph_context=True,
    )
    with pytest.raises(SystemExit):
        run_provenance_evaluation(str(graph_gold), min_graph_context_presence=1.0)


def test_checked_in_provenance_gold_has_realistic_positive_cases():
    from scripts.eval_provenance import load_gold_dataset, run_provenance_evaluation, validate_gold_dataset

    dataset = load_gold_dataset("eval/provenance_gold.jsonl")
    case_ids = {item["id"] for item in dataset}

    assert len(dataset) >= 6
    assert "search-trace-happy-path" in case_ids
    assert "context-trace-happy-path" in case_ids
    assert "context-graph-trace-happy-path" in case_ids
    assert "legacy-retrieval-trace-compatibility" in case_ids
    assert "stale-superseded-note-labelled-later" in case_ids
    assert validate_gold_dataset("eval/provenance_gold.jsonl") is True

    metrics = run_provenance_evaluation(
        "eval/provenance_gold.jsonl",
        min_source_recall=1.0,
        min_provenance_completeness=1.0,
        min_graph_context_presence=1.0,
    )

    assert metrics["expected_source_recall"] == 1.0
    assert metrics["provenance_completeness"] == 1.0
    assert metrics["graph_context_presence"] == 1.0


def test_checked_in_negative_gold_catches_raw_leak_and_incomplete_trace():
    from scripts.eval_provenance import load_gold_dataset, run_provenance_evaluation

    dataset = load_gold_dataset("eval/provenance_gold_negative.jsonl")
    case_ids = {item["id"] for item in dataset}

    assert {"raw-path-invariant-failure", "incomplete-trace-completeness-failure"} <= case_ids

    with pytest.raises(SystemExit):
        run_provenance_evaluation("eval/provenance_gold_negative.jsonl")

    incomplete_path = "eval/provenance_gold_incomplete.jsonl"
    with pytest.raises(SystemExit):
        run_provenance_evaluation(incomplete_path, min_provenance_completeness=1.0)

    low_recall_path = "eval/provenance_gold_low_recall.jsonl"
    with pytest.raises(SystemExit):
        run_provenance_evaluation(low_recall_path, min_source_recall=1.0)


def test_run_provenance_evaluation_json_output(capsys):
    import json as _json
    from scripts.eval_provenance import run_provenance_evaluation

    metrics = run_provenance_evaluation(as_json=True)
    payload = _json.loads(capsys.readouterr().out)

    # Output format only — scores must equal the returned (unchanged) metrics.
    assert payload == metrics
    assert payload["raw_leak_rate"] == 0.0
    assert payload["hash_integrity_rate"] == 1.0
