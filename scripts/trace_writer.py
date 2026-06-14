import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import TraceRecord, require_safe_trace_id


@dataclass(frozen=True)
class TracePaths:
    json_path: Path
    markdown_path: Path


class TraceStore:
    def __init__(self, project_root: Path | str = PROJECT_ROOT) -> None:
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data" / "traces"
        self.wiki_dir = self.project_root / "wiki" / "traces"

    def _paths_for(self, trace_id: str) -> TracePaths:
        require_safe_trace_id(trace_id)
        json_path = (self.data_dir / f"{trace_id}.json").resolve()
        markdown_path = (self.wiki_dir / f"{trace_id}.md").resolve()
        self._ensure_under(json_path, self.data_dir)
        self._ensure_under(markdown_path, self.wiki_dir)
        return TracePaths(json_path=json_path, markdown_path=markdown_path)

    @staticmethod
    def _ensure_under(path: Path, root: Path) -> None:
        root = root.resolve()
        if path != root and root not in path.parents:
            raise ValueError(f"unsafe trace path: {path}")

    def write(self, record: TraceRecord) -> TracePaths:
        paths = self._paths_for(record.trace_id)
        paths.json_path.parent.mkdir(parents=True, exist_ok=True)
        paths.markdown_path.parent.mkdir(parents=True, exist_ok=True)
        paths.json_path.write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths.markdown_path.write_text(render_trace_markdown(record), encoding="utf-8")
        return paths

    def trace_path(self, trace_id: str) -> Path:
        return self._paths_for(trace_id).json_path

    def read(self, trace_id: str) -> dict:
        path = self.trace_path(trace_id)
        if not path.exists():
            raise FileNotFoundError(f"trace not found: {trace_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> List[dict]:
        if not self.data_dir.exists():
            return []
        traces = []
        for path in sorted(self.data_dir.glob("trace-*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            traces.append(
                {
                    "trace_id": data.get("trace_id", path.stem),
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at", ""),
                    "event_count": len(data.get("events", [])),
                }
            )
        return traces


def render_trace_markdown(record: TraceRecord) -> str:
    lines = [
        f"# Trace: {record.title}",
        "",
        f"- **Trace ID:** `{record.trace_id}`",
        f"- **Schema:** `{record.schema_version}`",
        f"- **Created:** `{record.created_at}`",
        f"- **Events:** {len(record.events)}",
        "",
        "## Summary",
        record.summary,
        "",
        "## Events",
        "",
    ]
    if not record.events:
        lines.append("No events recorded.")
    for event in record.events:
        lines.extend(
            [
                f"### `{event.event_type}` {event.event_id}",
                "",
                f"- **Timestamp:** `{event.timestamp}`",
                f"- **Actor:** `{event.actor}`",
                f"- **Payload Hash:** `{event.payload_hash}`",
                "",
                "```json",
                json.dumps(event.payload, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
