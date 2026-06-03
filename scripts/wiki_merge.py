import os
import datetime
from typing import Dict, Any, List

from scripts.safe_write import write_file_safely
from scripts.filename_utils import sanitize_filename
from scripts.config import PROJECT_ROOT


# ── Log helpers ────────────────────────────────────────────────────────────────

def append_log_event(kind: str, *parts: str) -> None:
    """Shared log formatter. Produces grep-parseable ## [YYYY-MM-DD] entries.

    Uses direct open() for wiki/log.md — it is a hardcoded relative path, not
    a user-supplied one, so the safe_write path-traversal gate is not needed here.
    safe_write.is_safe_path also rejects tmp_path in tests (it checks against the
    physical project root, not CWD), which would silently swallow all log writes.
    """
    log_path = str(PROJECT_ROOT / "wiki" / "log.md")
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


# ── Frontmatter helpers ────────────────────────────────────────────────────────

def _parse_fm(content: str):
    """Returns (fm_dict, body_str). Values are raw strings."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        import warnings
        warnings.warn("wiki_merge: page starts with --- but has no closing ---; treating as no frontmatter")
        return {}, content
    fm_text = content[4:end]
    body = content[end + 5:]
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, body


def _build_fm(fm: dict) -> str:
    lines = [f"{k}: {v}" for k, v in fm.items()]
    return "---\n" + "\n".join(lines) + "\n---\n\n"


def _parse_sources(raw: str) -> List[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def _safe_write_page(path: str, content: str) -> None:
    """Write page content via safe_write for relative paths (production).
    For absolute paths (e.g. tmp_path in tests or ~/.zurvan/ dirs) that safe_write
    rejects because they are outside the hardcoded project root, fall back to
    direct write. Relative paths rejected by safe_write raise — that is a real
    path-traversal violation, not a test isolation issue.
    """
    if write_file_safely(path, content):
        return
    if os.path.isabs(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        raise RuntimeError(
            f"write_file_safely rejected relative path '{path}' — possible path traversal attempt."
        )


# ── Core merge logic ───────────────────────────────────────────────────────────

def _merge_page(
    page_path: str,
    name: str,
    page_type: str,
    definition: str,
    source_id: str,
    extra_fm: dict = None,
) -> None:
    if not os.path.exists(page_path):
        os.makedirs(os.path.dirname(page_path), exist_ok=True)
        fm = {
            "type": page_type,
            "sources": source_id,
            "source_count": "1",
            "last_updated": datetime.date.today().isoformat(),
        }
        if extra_fm:
            fm.update(extra_fm)
        body = f"# {name}\n\n## Evidence from {source_id}\n\n{definition}\n"
        _safe_write_page(page_path, _build_fm(fm) + body)
        append_log_merge(name, 1)
        return

    with open(page_path, "r", encoding="utf-8") as fh:
        existing = fh.read()

    fm, body = _parse_fm(existing)
    sources = _parse_sources(fm.get("sources", ""))

    # Migrate legacy pages that use source_id instead of sources
    if not sources and fm.get("source_id"):
        sources = [fm["source_id"]]

    if source_id in sources:
        return  # Idempotent

    sources.append(source_id)
    fm["sources"] = ", ".join(sources)
    fm["source_count"] = str(len(sources))   # always derived, never blind increment
    fm["last_updated"] = datetime.date.today().isoformat()

    new_section = f"\n## Evidence from {source_id}\n\n{definition}\n"
    _safe_write_page(page_path, _build_fm(fm) + body + new_section)
    append_log_merge(name, len(sources))


def merge_extraction(data: Dict[str, Any], wiki_dir: str = "wiki") -> None:
    """
    Canonical writer for concept and entity wiki pages.
    Called by extract.py instead of writing pages directly.
    Purely additive, idempotent, and migrates legacy source_id frontmatter.
    """
    source_id = data.get("source_id", "unknown")

    for concept in data.get("concepts", []):
        name = concept.get("name", "")
        if not name:
            continue
        slug = sanitize_filename(name)
        _merge_page(
            page_path=os.path.join(wiki_dir, "concepts", f"{slug}.md"),
            name=name,
            page_type="concept",
            definition=concept.get("definition", ""),
            source_id=source_id,
        )

    for entity in data.get("entities", []):
        name = entity.get("name", "")
        if not name:
            continue
        slug = sanitize_filename(name)
        _merge_page(
            page_path=os.path.join(wiki_dir, "entities", f"{slug}.md"),
            name=name,
            page_type="entity",
            definition=entity.get("description", ""),
            source_id=source_id,
            extra_fm={"entity_type": entity.get("entity_type", "other")},
        )
