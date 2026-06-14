import json

from scripts.trace_replay import replay_trace_file


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
