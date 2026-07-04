import json

from scripts.trace_schema import hash_payload
from scripts.trace_replay import replay_trace_file
from scripts.trace_validate import validate_trace_file


def test_replay_trace_file_renders_deterministic_markdown(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-abcdef12",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Replay me",
                "summary": "Replay should not execute external operations.",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "tool_call",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "mcp",
                        "payload": {"tool_name": "zurvan_search"},
                        "payload_hash": "sha256:9d155e9d3fb72fb075e8ec86c3cf068b9176cbdf959e273168d2baa2be26f9da",
                    }
                ],
            }
        )
    )

    markdown = replay_trace_file(path)

    assert markdown.startswith("# Trace Replay: Replay me")
    assert "| evt-001 | tool_call | 2026-06-14T01:02:04Z | mcp |" in markdown
    assert "zurvan_search" in markdown


def test_replay_trace_file_rejects_invalid_trace_hash(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-abcdef12",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Do not replay",
                "summary": "Bad payload hashes should block replay.",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "tool_call",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "mcp",
                        "payload": {"tool_name": "zurvan_search"},
                        "payload_hash": "sha256:bad",
                    }
                ],
            }
        )
    )

    try:
        replay_trace_file(path)
    except ValueError as exc:
        assert "payload_hash mismatch" in str(exc)
    else:
        raise AssertionError("invalid trace replay should fail")


def test_replay_escapes_pipes_in_payload(tmp_path):
    # A payload value containing "|" must not break the Markdown table: the
    # generated cell escapes it as "\|" so the row keeps its 5 columns.
    payload = {"query": "cats | dogs", "mode": "hybrid"}
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-pipe0001",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Pipe payload",
                "summary": "Payload contains a pipe character.",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "retrieval.query",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "zurvan",
                        "payload": payload,
                        "payload_hash": hash_payload(payload),
                    }
                ],
            }
        )
    )

    markdown = replay_trace_file(path)
    row = next(l for l in markdown.splitlines() if l.startswith("| evt-001 "))
    assert "\\|" in row  # the payload pipe is escaped
    # Unescaped pipes = table columns; escaped "\|" pairs don't count. The row
    # must still have exactly the 5 structural columns (6 delimiters).
    assert row.replace("\\|", "").count("|") == 6


def test_replay_accepts_legacy_single_retrieval_event(tmp_path):
    path = tmp_path / "trace.json"
    payload = {
        "command": "context",
        "query": "legacy trace",
        "mode": "hybrid",
        "limit": 10,
        "result_count": 1,
        "results": [{"chunk_id": "chunk-legacy-001", "source_path": "wiki/legacy.md"}],
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-legacy1",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Legacy retrieval trace",
                "summary": "Old R2 trace with one coarse retrieval event.",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "retrieval",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "zurvan",
                        "payload": payload,
                        "payload_hash": hash_payload(payload),
                    }
                ],
            }
        )
    )

    validation = validate_trace_file(path)
    markdown = replay_trace_file(path)

    assert validation.valid is True
    assert "| evt-001 | retrieval |" in markdown
    assert "chunk-legacy-001" in markdown
