import os
import datetime
from pathlib import Path
from typing import Dict, Any, List

from scripts.safe_write import append_file_safely, write_file_safely
from scripts.filename_utils import sanitize_filename


# ── Log helpers ────────────────────────────────────────────────────────────────

def append_log_event(kind: str, *parts: str) -> None:
    """Shared log formatter. Produces grep-parseable ## [YYYY-MM-DD] entries."""
    log_path = Path("wiki") / "log.md"
    date = datetime.date.today().isoformat()
    safe_parts = [str(p).replace("|", "\\|") for p in parts]
    entry = f"\n## [{date}] {kind} | {' | '.join(safe_parts)}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
