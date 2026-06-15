import json

import pytest

from scripts.trace_schema import TraceEvent, TraceRecord
from scripts.trace_writer import TraceStore


def test_trace_store_writes_json_and_markdown_mirror(tmp_path):
    store = TraceStore(project_root=tmp_path)
    record = TraceRecord(
        trace_id="trace-20260614T010203Z-abcdef12",
        title="Answer provenance",
        summary="Records evidence and tool context.",
        events=[
            TraceEvent(
                event_id="evt-001",
                event_type="tool_call",
                timestamp="2026-06-14T01:02:04Z",
                actor="mcp",
                payload={"tool_name": "zurvan_search", "result_hash": "abc123"},
            )
        ],
    )

    paths = store.write(record)

    assert paths.json_path == tmp_path / "data" / "traces" / f"{record.trace_id}.json"
    assert paths.markdown_path == tmp_path / "wiki" / "traces" / f"{record.trace_id}.md"
    assert json.loads(paths.json_path.read_text())["trace_id"] == record.trace_id
    markdown = paths.markdown_path.read_text()
    assert "# Trace: Answer provenance" in markdown
    assert "`tool_call`" in markdown


def test_trace_store_refuses_unsafe_trace_id(tmp_path):
    store = TraceStore(project_root=tmp_path)
    record = TraceRecord(
        trace_id="../escape",
        title="Unsafe",
        summary="Should not write outside trace directories.",
        events=[],
        validate_id=False,
    )

    with pytest.raises(ValueError, match="unsafe trace_id"):
        store.write(record)
