import json

from scripts.trace_validate import validate_trace_file


def test_validate_trace_file_accepts_valid_trace(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-abcdef12",
                "created_at": "2026-06-14T01:02:03Z",
                "title": "Valid trace",
                "summary": "A minimal valid trace.",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "retrieval",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "zurvan",
                        "payload": {"query": "traceability"},
                        "payload_hash": "sha256:b1b1b6a65811ec7fb0406914304e78f7c758ded9f65eae45aed321aa92e700ac",
                    }
                ],
            }
        )
    )

    result = validate_trace_file(path)

    assert result.valid is True
    assert result.issues == []


def test_validate_trace_file_reports_missing_fields_and_hash_mismatch(tmp_path):
    path = tmp_path / "trace.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "zurvan.trace.v1",
                "trace_id": "trace-20260614T010203Z-abcdef12",
                "events": [
                    {
                        "event_id": "evt-001",
                        "event_type": "retrieval",
                        "timestamp": "2026-06-14T01:02:04Z",
                        "actor": "zurvan",
                        "payload": {"query": "traceability"},
                        "payload_hash": "sha256:not-the-real-hash",
                    }
                ],
            }
        )
    )

    result = validate_trace_file(path)

    assert result.valid is False
    assert "missing required field: title" in result.issues
    assert "event evt-001 payload_hash mismatch" in result.issues
