import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from scripts.trace_schema import SCHEMA_VERSION, ALLOWED_EVENT_TYPES, TRACE_ID_RE, EVENT_ID_RE, hash_payload


REQUIRED_TRACE_FIELDS = ("schema_version", "trace_id", "created_at", "title", "summary", "events")
REQUIRED_EVENT_FIELDS = ("event_id", "event_type", "timestamp", "actor", "payload", "payload_hash")


@dataclass
class TraceValidationResult:
    valid: bool
    issues: List[str] = field(default_factory=list)


def validate_trace_file(path: Path | str) -> TraceValidationResult:
    trace_path = Path(path)
    issues: List[str] = []
    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return TraceValidationResult(False, [f"cannot read trace: {exc}"])
    except json.JSONDecodeError as exc:
        return TraceValidationResult(False, [f"invalid JSON: {exc}"])

    _validate_trace_dict(data, issues)
    return TraceValidationResult(valid=not issues, issues=issues)


def _validate_trace_dict(data: Dict[str, Any], issues: List[str]) -> None:
    for field_name in REQUIRED_TRACE_FIELDS:
        if field_name not in data:
            issues.append(f"missing required field: {field_name}")

    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION}")

    trace_id = data.get("trace_id")
    if trace_id is not None and not TRACE_ID_RE.fullmatch(str(trace_id)):
        issues.append(f"unsafe trace_id: {trace_id}")

    events = data.get("events")
    if events is None:
        return
    if not isinstance(events, list):
        issues.append("events must be a list")
        return

    seen = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"event {index} must be an object")
            continue
        _validate_event_dict(event, index, seen, issues)


def _validate_event_dict(event: Dict[str, Any], index: int, seen: set[str], issues: List[str]) -> None:
    label = str(event.get("event_id", index))
    for field_name in REQUIRED_EVENT_FIELDS:
        if field_name not in event:
            issues.append(f"event {label} missing required field: {field_name}")

    event_id = event.get("event_id")
    if event_id is not None:
        event_id = str(event_id)
        if not EVENT_ID_RE.fullmatch(event_id):
            issues.append(f"unsafe event_id: {event_id}")
        if event_id in seen:
            issues.append(f"duplicate event_id: {event_id}")
        seen.add(event_id)

    event_type = event.get("event_type")
    if event_type is not None and event_type not in ALLOWED_EVENT_TYPES:
        issues.append(f"event {label} has unsupported event_type: {event_type}")

    payload = event.get("payload")
    if payload is not None and not isinstance(payload, dict):
        issues.append(f"event {label} payload must be an object")
        return

    payload_hash = event.get("payload_hash")
    if isinstance(payload, dict) and payload_hash is not None:
        expected = hash_payload(payload)
        if payload_hash != expected:
            issues.append(f"event {label} payload_hash mismatch")
