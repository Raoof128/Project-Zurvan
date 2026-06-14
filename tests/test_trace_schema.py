import pytest

from scripts.trace_schema import TraceEvent, TraceRecord, create_trace_id, hash_payload


def test_trace_record_requires_minimum_audit_fields():
    event = TraceEvent(
        event_id="evt-001",
        event_type="retrieval",
        timestamp="2026-06-14T00:00:00Z",
        actor="zurvan",
        payload={"query": "MCP security", "result_count": 2},
    )

    record = TraceRecord(
        trace_id="trace-20260614T000000Z-a1b2c3d4",
        title="MCP safety lookup",
        summary="Captured retrieval context for an MCP safety answer.",
        events=[event],
    )

    data = record.to_dict()

    assert data["schema_version"] == "zurvan.trace.v1"
    assert data["trace_id"] == "trace-20260614T000000Z-a1b2c3d4"
    assert data["events"][0]["payload_hash"] == hash_payload(event.payload)


def test_trace_id_rejects_path_like_or_unsafe_values():
    with pytest.raises(ValueError, match="unsafe trace_id"):
        create_trace_id("../raw/secret")

    with pytest.raises(ValueError, match="unsafe trace_id"):
        create_trace_id("trace with spaces")


def test_trace_event_rejects_unknown_event_type():
    with pytest.raises(ValueError, match="event_type"):
        TraceEvent(
            event_id="evt-001",
            event_type="network_call",
            timestamp="2026-06-14T00:00:00Z",
            actor="zurvan",
            payload={},
        )


def test_generated_trace_ids_are_unique():
    trace_ids = {create_trace_id() for _ in range(3)}

    assert len(trace_ids) == 3
    for trace_id in trace_ids:
        assert trace_id.startswith("trace-")
