import json
from pathlib import Path

from scripts.trace_schema import record_from_dict
from scripts.trace_validate import validate_trace_file


def replay_trace_file(path: Path | str) -> str:
    trace_path = Path(path)
    validation = validate_trace_file(trace_path)
    if not validation.valid:
        raise ValueError("; ".join(validation.issues))

    data = json.loads(trace_path.read_text(encoding="utf-8"))
    record = record_from_dict(data)
    lines = [
        f"# Trace Replay: {record.title}",
        "",
        f"- **Trace ID:** `{record.trace_id}`",
        f"- **Created:** `{record.created_at}`",
        f"- **Summary:** {record.summary}",
        "",
        "| Event | Type | Timestamp | Actor | Payload |",
        "| --- | --- | --- | --- | --- |",
    ]
    for event in record.events:
        # Escape pipes so a payload value containing "|" doesn't break the
        # Markdown table (GFM un-escapes "\|" back to "|" inside the cell).
        payload = json.dumps(event.payload, sort_keys=True, ensure_ascii=True).replace("|", "\\|")
        lines.append(
            f"| {event.event_id} | {event.event_type} | {event.timestamp} | {event.actor} | `{payload}` |"
        )
    if not record.events:
        lines.append("| No events |  |  |  |  |")
    return "\n".join(lines).rstrip() + "\n"
