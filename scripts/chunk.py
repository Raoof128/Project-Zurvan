import os
import glob
import hashlib
from datetime import datetime
from pathlib import Path

from scripts.config import PROJECT_ROOT

# Heading-only splitting leaves prose without markdown structure (e.g. text
# extracted from a PDF) as one giant chunk. A single 190k-char chunk is
# unsearchable: the sentence-transformer embeds only its first ~256 tokens and
# BM25 dilutes to noise. Any heading-section longer than this is sub-split on
# line boundaries so every chunk is embeddable. Sized to the embedder's window;
# only ~6% of the existing corpus exceeds it, so 94% of chunk IDs are unchanged.
MAX_CHUNK_CHARS = 1000

def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def _split_text_by_size(text: str, limit: int) -> list:
    """Greedily pack lines into pieces <= limit chars. A single line longer than
    the limit is hard-wrapped so no piece can exceed it."""
    pieces, cur, cur_len = [], [], 0
    for ln in text.split('\n'):
        if len(ln) > limit:
            if cur:
                pieces.append('\n'.join(cur))
                cur, cur_len = [], 0
            for i in range(0, len(ln), limit):
                pieces.append(ln[i:i + limit])
            continue
        if cur and cur_len + len(ln) + 1 > limit:
            pieces.append('\n'.join(cur))
            cur, cur_len = [], 0
        cur.append(ln)
        cur_len += len(ln) + 1
    if cur:
        pieces.append('\n'.join(cur))
    return pieces

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
        if not text:
            return

        # Small sections stay a single chunk with the legacy chunk_id (no
        # ``::idx::`` segment) so 94% of the corpus keeps byte-identical IDs and
        # reuses its stored embedding. Oversized sections are sub-split.
        if len(text) <= MAX_CHUNK_CHARS:
            parts = [text]
        else:
            parts = [p.strip() for p in _split_text_by_size(text, MAX_CHUNK_CHARS) if p.strip()]

        for idx, part in enumerate(parts):
            if len(parts) == 1:
                chunk_id_str = f"{filepath}::{current_heading}::{part}"
            else:
                # idx keeps otherwise-identical sub-parts (repeated boilerplate)
                # from colliding on the same chunk_id.
                chunk_id_str = f"{filepath}::{current_heading}::{idx}::{part}"
            chunk_id = hash_content(chunk_id_str)

            chunks.append({
                "chunk_id": chunk_id,
                "source_path": filepath,
                "heading": current_heading,
                "text": part,
                "content_hash": hash_content(part),
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

# Root-level documents that ARE knowledge and must be searchable.
#
# An explicit allowlist, never "every .md at the repo root". Step 0 of the
# retrieval project found README.md, AGENTS.md and CLAUDE.md with ZERO chunks
# each: Zurvan's own overview, rules and quickstart had never been searchable,
# which accounted for 4 of the 14 failing gold queries. Those were ABSENCE
# failures — unreachable by reranking, fusion weights, better embeddings,
# contextual chunks, sqlite-vec or KNN, because none of them can rank a
# document that is not in the index.
#
# Kept as a named tuple rather than a glob so a temporary report, a generated
# file or a future private note dropped at the repo root is never swept in.
ROOT_INDEX_FILES = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
)

# Directories excluded by PREFIX rather than by segment name, so the rule states
# exactly one location instead of banning a common word everywhere it appears.
#
# `docs/evaluation/retrieval/` holds the frozen retrieval experiment reports —
# documents ABOUT the retriever's own measured behaviour. Indexing them closes a
# measurement loop: the Step 0 report added 7 chunks to the very index whose
# baseline it froze, and turned up again as part of an unexplained +28 chunk
# drift when Phase 1 tried to reconcile its corpus. Every future report would
# retire the baseline it exists to record. Same defect `wiki/traces/` was
# excluded for: a derived artifact about retrieval must not become material for
# retrieval.
#
# The rule is deliberately NOT all of `docs/evaluation/`. That was tried and
# measured: it also removed `docs/evaluation/provenance.md`, which gold item #44
# legitimately expects (referenced by INDEX: a tracked file reproducing the
# measuring instrument is itself the leak this project blocks),
# turning a passing query into an `index_coverage_miss` — the very class Step 0
# was created to find. A provenance report documents a different subsystem and
# is ordinary knowledge. Only reports about RETRIEVAL feed back into retrieval,
# so only those are excluded, and they live in one directory to make the rule
# structural rather than a naming convention.
EXCLUDED_PATH_PREFIXES = (
    "docs/evaluation/retrieval",
)


def scan_markdown_files(root: Path | str | None = None):
    """Scans wiki/, docs/ and the named root documents under the repo root,
    ignoring raw/ and derived trace mirrors. Returns repo-relative paths so
    chunk IDs stay stable no matter which working directory the process was
    launched from (previously a foreign CWD silently produced an empty index).

    ``wiki/traces/`` holds replay mirrors of retrieval traces — derived,
    self-referential audit artifacts that would pollute retrieval with the
    query's own terms, so they are never indexed. ``docs/evaluation/`` is
    excluded for the same reason: see ``EXCLUDED_PATH_PREFIXES``.
    """
    base = Path(root) if root is not None else PROJECT_ROOT
    excluded_dirs = {"raw", "traces"}
    files = []
    for directory in ["wiki", "docs"]:
        dir_path = base / directory
        if dir_path.exists():
            for filepath in glob.glob(str(dir_path / "**" / "*.md"), recursive=True):
                rel = os.path.relpath(filepath, base)
                if excluded_dirs.intersection(rel.split(os.sep)):
                    continue
                posix = rel.replace(os.sep, "/")
                if any(posix == p or posix.startswith(p + "/")
                       for p in EXCLUDED_PATH_PREFIXES):
                    continue
                files.append(rel)
    for name in ROOT_INDEX_FILES:
        if (base / name).is_file():
            files.append(name)
    return files

def iter_all_markdown_chunks(root: Path | str | None = None):
    """Yield every chunk in the wiki one file at a time.

    Materialising the whole corpus was fine at 861 pages and fatal at 3,823:
    with 131 MB of source text the list alone ran to hundreds of megabytes and
    the indexer was OOM-killed mid-rebuild. Streaming keeps only one file's
    chunks live at a time; ``chunk_all_markdown`` stays as the list-returning
    wrapper so existing callers are unaffected.
    """
    base = Path(root) if root is not None else PROJECT_ROOT
    for f in scan_markdown_files(base):
        yield from extract_chunks_from_markdown(f, base_dir=base)


def chunk_all_markdown(root: Path | str | None = None):
    return list(iter_all_markdown_chunks(root))
