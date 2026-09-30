import contextvars
import json
import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import TraceRecord, require_safe_trace_id


DIR_MODE = 0o700
FILE_MODE = 0o600
RETENTION_DAYS = 30
RETENTION_KEEP = 500

_PROTECTED = contextvars.ContextVar("zurvan_protected_eval", default=False)


class ProtectedTraceError(RuntimeError):
    """A non-protected trace was about to be written inside a protected
    evaluation (design §3.1). Raised before any byte lands."""
    code = "PROTECTED_TRACE"


@contextmanager
def protected_eval_context():
    """Everything written inside is forced to `protected_eval` or refused.
    A flag cannot downgrade it: the guard is in the writer, not the caller."""
    token = _PROTECTED.set(True)
    try:
        yield
    finally:
        _PROTECTED.reset(token)


def in_protected_eval() -> bool:
    return _PROTECTED.get()


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)


def _write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, FILE_MODE)  # O_CREAT's mode does not apply to an existing file


@dataclass
class PruneResult:
    removed: List[str] = field(default_factory=list)
    kept_tracked: int = 0
    refused: str | None = None


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
        if in_protected_eval() and record.trace_mode != "protected_eval":
            raise ProtectedTraceError(
                f"PROTECTED_TRACE: a {record.trace_mode!r} trace cannot be written "
                "inside a protected evaluation")
        paths = self._paths_for(record.trace_id)
        _private_dir(paths.json_path.parent)
        _write_private(paths.json_path,
                       json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
        if record.trace_mode == "full":
            _private_dir(paths.markdown_path.parent)
            _write_private(paths.markdown_path, render_trace_markdown(record))
        self.prune()
        return paths

    def _tracked_names(self) -> dict | None:
        """Basenames git tracks under data/traces and wiki/traces, or None when
        they cannot be listed — and then nothing may be pruned."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.project_root), "ls-files", "-z", "--",
                 "data/traces", "wiki/traces"],
                capture_output=True, check=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        names = {"data": set(), "wiki": set()}
        for rel in filter(None, out.decode("utf-8", "replace").split("\0")):
            parts = rel.split("/")
            if parts[:2] == ["data", "traces"]:
                names["data"].add(parts[-1])
            elif parts[:2] == ["wiki", "traces"]:
                names["wiki"].add(parts[-1])
        return names

    def prune(self, *, days: int = RETENTION_DAYS, keep: int = RETENTION_KEEP,
              now: float | None = None, dry_run: bool = False) -> PruneResult:
        """Retention (design §3.2): remove untracked traces older than `days`
        or beyond the newest `keep`. A git-tracked trace is a frozen research
        artifact and is never removed."""
        tracked = self._tracked_names()
        if tracked is None:
            return PruneResult(refused="cannot list git-tracked traces; pruning nothing")
        now = time.time() if now is None else now
        cutoff = now - days * 86400
        candidates, kept_tracked = [], 0
        for path in (self.data_dir.glob("trace-*.json") if self.data_dir.exists() else []):
            if path.name in tracked["data"]:
                kept_tracked += 1
                continue
            candidates.append((path.stat().st_mtime, path.stem))
        candidates.sort(reverse=True)  # newest first
        removed = []
        for position, (mtime, trace_id) in enumerate(candidates):
            if position < keep and mtime >= cutoff:
                continue
            for path, kind in ((self.data_dir / f"{trace_id}.json", "data"),
                               (self.wiki_dir / f"{trace_id}.md", "wiki")):
                if path.name in tracked[kind] or not path.exists():
                    continue
                removed.append(str(path))
                if not dry_run:
                    path.unlink()
        return PruneResult(removed=removed, kept_tracked=kept_tracked)

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
