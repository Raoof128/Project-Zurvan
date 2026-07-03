import os
import glob
import hashlib
from datetime import datetime
from pathlib import Path

from scripts.config import PROJECT_ROOT

def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def extract_chunks_from_markdown(filepath: str, base_dir: Path | str | None = None):
    """
    Splits markdown by headings.
    Returns list of dicts: chunk_id, source_path, heading, text, content_hash, indexed_at

    `filepath` is kept verbatim in chunk identity (chunk_id/source_path) so
    repo-relative paths produce stable chunk IDs; `base_dir` only anchors the
    read when `filepath` is relative.
    """
    read_path = filepath
    if base_dir is not None and not os.path.isabs(filepath):
        read_path = str(Path(base_dir) / filepath)
    with open(read_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    chunks = []

    current_heading = "root"
    current_text = []

    def finalize_chunk():
        text = "\n".join(current_text).strip()
        if text:
            # Deterministic chunk ID
            chunk_id_str = f"{filepath}::{current_heading}::{text}"
            chunk_id = hash_content(chunk_id_str)

            chunks.append({
                "chunk_id": chunk_id,
                "source_path": filepath,
                "heading": current_heading,
                "text": text,
                "content_hash": hash_content(text),
                "indexed_at": datetime.now().isoformat()
            })

    for line in lines:
        if line.startswith('#'):
            finalize_chunk()
            current_heading = line.lstrip('#').strip()
            current_text = [line]
        else:
            current_text.append(line)

    finalize_chunk()
    return chunks

def scan_markdown_files(root: Path | str | None = None):
    """Scans wiki/ and docs/ under the repo root, ignoring raw/ and derived
    trace mirrors. Returns repo-relative paths so chunk IDs stay stable no
    matter which working directory the process was launched from (previously
    a foreign CWD silently produced an empty index).

    ``wiki/traces/`` holds replay mirrors of retrieval traces — derived,
    self-referential audit artifacts that would pollute retrieval with the
    query's own terms, so they are never indexed.
    """
    base = Path(root) if root is not None else PROJECT_ROOT
    excluded_dirs = {"raw", "traces"}
    files = []
    for directory in ["wiki", "docs"]:
        dir_path = base / directory
        if dir_path.exists():
            for filepath in glob.glob(str(dir_path / "**" / "*.md"), recursive=True):
                rel = os.path.relpath(filepath, base)
                if not excluded_dirs.intersection(rel.split(os.sep)):
                    files.append(rel)
    return files

def chunk_all_markdown(root: Path | str | None = None):
    base = Path(root) if root is not None else PROJECT_ROOT
    files = scan_markdown_files(base)
    all_chunks = []
    for f in files:
        all_chunks.extend(extract_chunks_from_markdown(f, base_dir=base))
    return all_chunks
