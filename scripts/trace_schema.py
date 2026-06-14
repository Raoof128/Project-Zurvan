import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List


SCHEMA_VERSION = "zurvan.trace.v1"
TRACE_ID_RE = re.compile(r"^trace-[A-Za-z0-9][A-Za-z0-9_.-]{7,127}$")
EVENT_ID_RE = re.compile(r"^evt-[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
ALLOWED_EVENT_TYPES = {
    "retrieval",
    "retrieval.query",
    "retrieval.result",
    "retrieval.fusion",
    "context.assembled",
    "graph_context",
    "tool_call",
    "resource_read",
    "memory_write",
    "safety_event",
    "citation",
    "final_claim",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hash_payload(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def create_trace_id(seed: str | None = None) -> str:
    if seed is not None:
        if not TRACE_ID_RE.fullmatch(seed):
            raise ValueError(f"unsafe trace_id: {seed}")
        return seed

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = uuid.uuid4().hex[:8]
    return f"trace-{timestamp}-{digest}"


def require_safe_trace_id(trace_id: str) -> None:
    if not TRACE_ID_RE.fullmatch(trace_id):
        raise ValueError(f"unsafe trace_id: {trace_id}")


@dataclass
class TraceEvent:
    event_id: str
    event_type: str
    timestamp: str
    actor: str
    payload: Dict[str, Any] = field(default_factory=dict)
    payload_hash: str | None = None

    def __post_init__(self) -> None:
        if not EVENT_ID_RE.fullmatch(self.event_id):
            raise ValueError(f"unsafe event_id: {self.event_id}")
        if self.event_type not in ALLOWED_EVENT_TYPES:
            allowed = ", ".join(sorted(ALLOWED_EVENT_TYPES))
            raise ValueError(f"event_type must be one of: {allowed}")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dictionary")
        if self.payload_hash is None:
            self.payload_hash = hash_payload(self.payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
        }


@dataclass
class TraceRecord:
    trace_id: str
    title: str
    summary: str
    events: List[TraceEvent] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    validate_id: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        if self.validate_id:
            require_safe_trace_id(self.trace_id)
        if not self.title.strip():
            raise ValueError("title is required")
        if not self.summary.strip():
            raise ValueError("summary is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "created_at": self.created_at,
            "title": self.title,
            "summary": self.summary,
            "events": [event.to_dict() for event in self.events],
        }


def event_from_dict(data: Dict[str, Any]) -> TraceEvent:
    return TraceEvent(
        event_id=data["event_id"],
        event_type=data["event_type"],
        timestamp=data["timestamp"],
        actor=data["actor"],
        payload=data.get("payload", {}),
        payload_hash=data.get("payload_hash"),
    )


def record_from_dict(data: Dict[str, Any]) -> TraceRecord:
    return TraceRecord(
        schema_version=data["schema_version"],
        trace_id=data["trace_id"],
        created_at=data["created_at"],
        title=data["title"],
        summary=data["summary"],
        events=[event_from_dict(item) for item in data.get("events", [])],
    )
