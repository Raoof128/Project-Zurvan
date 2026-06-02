import os
import datetime
from typing import Dict, Any, List

from scripts.safe_write import write_file_safely
from scripts.filename_utils import sanitize_filename


# ── Log helpers ────────────────────────────────────────────────────────────────

def append_log_event(kind: str, *parts: str) -> None:
    """Shared log formatter. Produces grep-parseable ## [YYYY-MM-DD] entries.

    Uses direct open() for wiki/log.md — it is a hardcoded relative path, not
    a user-supplied one, so the safe_write path-traversal gate is not needed here.
    safe_write.is_safe_path also rejects tmp_path in tests (it checks against the
    physical project root, not CWD), which would silently swallow all log writes.
    """
    log_path = os.path.join("wiki", "log.md")
    date = datetime.date.today().isoformat()
    safe_parts = [str(p).replace("|", "\\|") for p in parts]
    entry = f"\n## [{date}] {kind} | {' | '.join(safe_parts)}\n"
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def append_log_ingest(path: str) -> None:
    append_log_event("ingest", path)


def append_log_merge(name: str, source_count: int) -> None:
    append_log_event("merge", name, f"{source_count} sources")


def append_log_save(slug: str) -> None:
    append_log_event("query-save", slug)


def append_log_image_skip(filename: str) -> None:
    append_log_event("image-skip", filename, "pending visual extraction")
