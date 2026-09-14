import pytest
import os
from scripts.chunk import (
    extract_chunks_from_markdown,
    hash_content,
    scan_markdown_files,
    chunk_all_markdown,
    MAX_CHUNK_CHARS,
)

def test_extract_chunks_from_markdown(tmp_path):
    md_file = tmp_path / "test.md"
    content = """# Header 1
Line 1
Line 2
# Header 2
Line 3
## Subheader
Line 4"""
    md_file.write_text(content)
    
    chunks = extract_chunks_from_markdown(str(md_file))
    assert len(chunks) == 3
    assert chunks[0]['heading'] == "Header 1"
    assert "Line 1\nLine 2" in chunks[0]['text']
    assert chunks[1]['heading'] == "Header 2"
    assert "Line 3" in chunks[1]['text']
    assert chunks[2]['heading'] == "Subheader"
    assert "Line 4" in chunks[2]['text']

def test_scan_excludes_derived_trace_mirrors(tmp_path):
    # Trace mirror pages are derived audit artifacts and must not be indexed:
    # they are self-referential and pollute retrieval with the query's own terms.
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "traces").mkdir()
    (tmp_path / "wiki" / "real.md").write_text("# Real\nknowledge")
    (tmp_path / "wiki" / "traces" / "trace-x.md").write_text("# Trace Replay\nquery echo")

    found = scan_markdown_files(root=tmp_path)

    assert any(f.endswith("real.md") for f in found)
    assert not any("traces" in f.split(os.sep) for f in found)


def test_scan_is_cwd_independent(tmp_path, monkeypatch):
    # Regression: scanning used CWD-relative globs, so `index rebuild` from a
    # foreign working directory silently rebuilt an EMPTY search index.
    monkeypatch.chdir(tmp_path)

    found = scan_markdown_files()

    assert found, "scan must find repo files regardless of the working directory"
    # Identity stays repo-relative so chunk IDs are stable.
    assert all(not os.path.isabs(f) for f in found)


def test_chunk_all_markdown_reads_from_root(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "page.md").write_text("# Heading\nbody text")

    chunks = chunk_all_markdown(root=tmp_path)

    assert len(chunks) == 1
    assert chunks[0]["source_path"] == os.path.join("wiki", "page.md")


def test_chunk_id_is_deterministic(tmp_path):
    md_file = tmp_path / "test.md"
    content = "# A\nB"
    md_file.write_text(content)
    chunks1 = extract_chunks_from_markdown(str(md_file))
    chunks2 = extract_chunks_from_markdown(str(md_file))
    assert chunks1[0]['chunk_id'] == chunks2[0]['chunk_id']


def test_small_section_keeps_legacy_chunk_id(tmp_path):
    # A section under the size limit must produce exactly one chunk whose ID is
    # the legacy hash(f"{path}::{heading}::{text}") — so re-indexing after adding
    # sub-splitting reuses stored embeddings for the 94% of small chunks.
    md_file = tmp_path / "test.md"
    md_file.write_text("# H\nshort body")
    chunks = extract_chunks_from_markdown(str(md_file))
    assert len(chunks) == 1
    legacy = hash_content(f"{md_file}::H::{chunks[0]['text']}")
    assert chunks[0]['chunk_id'] == legacy


def test_oversized_section_is_sub_split_within_limit(tmp_path):
    # A heading-less blob (e.g. extracted PDF prose) must not become one giant
    # unsearchable chunk; every produced chunk stays within the embedder window.
    md_file = tmp_path / "big.md"
    body = "\n".join(f"paragraph line number {i} with some filler words" for i in range(400))
    md_file.write_text(body)  # no '#', so one "root" section far over the limit

    chunks = extract_chunks_from_markdown(str(md_file))

    assert len(chunks) > 1
    assert all(len(c['text']) <= MAX_CHUNK_CHARS for c in chunks)
    assert len({c['chunk_id'] for c in chunks}) == len(chunks)  # IDs unique
    # No content is dropped: concatenated pieces cover every source line.
    joined = "\n".join(c['text'] for c in chunks)
    assert "paragraph line number 0 " in joined
    assert "paragraph line number 399 " in joined


def test_single_line_longer_than_limit_is_hard_wrapped(tmp_path):
    md_file = tmp_path / "wide.md"
    md_file.write_text("# H\n" + "x" * (MAX_CHUNK_CHARS * 3))
    chunks = extract_chunks_from_markdown(str(md_file))
    assert all(len(c['text']) <= MAX_CHUNK_CHARS for c in chunks)


# ── Phase 0A: root-document coverage ─────────────────────────────────────────

def _repo(tmp_path):
    for d in ("wiki", "docs", "raw", "wiki/traces"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "page.md").write_text("wiki page", encoding="utf-8")
    (tmp_path / "docs" / "guide.md").write_text("docs page", encoding="utf-8")
    (tmp_path / "raw" / "untrusted.md").write_text("raw", encoding="utf-8")
    (tmp_path / "wiki" / "traces" / "t.md").write_text("trace", encoding="utf-8")
    return tmp_path


def test_root_knowledge_documents_are_indexed(tmp_path):
    # Step 0 found README/AGENTS/CLAUDE with ZERO chunks: Zurvan's own rules,
    # quickstart and overview had never been searchable, which accounted for 4
    # of 14 gold failures. Those are absence failures - no reranker, fusion
    # weight, embedding model or storage engine can fix them.
    from scripts.chunk import scan_markdown_files, ROOT_INDEX_FILES

    r = _repo(tmp_path)
    for name in ROOT_INDEX_FILES:
        (r / name).write_text(f"# {name}\n\ncontent", encoding="utf-8")

    found = set(scan_markdown_files(root=r))
    for name in ROOT_INDEX_FILES:
        assert name in found, f"{name} must be indexed"


def test_only_the_named_root_files_are_indexed(tmp_path):
    # Deliberately an allowlist, NOT "every root .md". Recursive root indexing
    # would sweep in temporary reports, generated files and future private
    # notes that happen to land at the repo root.
    from scripts.chunk import scan_markdown_files

    r = _repo(tmp_path)
    (r / "README.md").write_text("readme", encoding="utf-8")
    (r / "SCRATCH_NOTES.md").write_text("private scratch", encoding="utf-8")
    (r / "generated-report.md").write_text("generated", encoding="utf-8")

    found = set(scan_markdown_files(root=r))
    assert "README.md" in found
    assert "SCRATCH_NOTES.md" not in found
    assert "generated-report.md" not in found


def test_a_missing_root_file_is_not_an_error(tmp_path):
    from scripts.chunk import scan_markdown_files

    found = set(scan_markdown_files(root=_repo(tmp_path)))   # no root docs written
    assert "wiki/page.md" in found


def test_coverage_change_does_not_weaken_the_exclusions(tmp_path):
    # raw/ is untrusted; wiki/traces/ are self-referential replay mirrors that
    # would pollute retrieval with the query's own terms.
    from scripts.chunk import scan_markdown_files

    r = _repo(tmp_path)
    (r / "README.md").write_text("readme", encoding="utf-8")

    found = set(scan_markdown_files(root=r))
    assert not any(f.startswith("raw") for f in found)
    assert not any("traces" in f.split(os.sep) for f in found)
    assert {"wiki/page.md", "docs/guide.md"} <= {f.replace(os.sep, "/") for f in found}
