import pytest
import os
from scripts.chunk import extract_chunks_from_markdown, hash_content, scan_markdown_files

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

def test_scan_excludes_derived_trace_mirrors(tmp_path, monkeypatch):
    # Trace mirror pages are derived audit artifacts and must not be indexed:
    # they are self-referential and pollute retrieval with the query's own terms.
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "traces").mkdir()
    (tmp_path / "wiki" / "real.md").write_text("# Real\nknowledge")
    (tmp_path / "wiki" / "traces" / "trace-x.md").write_text("# Trace Replay\nquery echo")
    monkeypatch.chdir(tmp_path)

    found = scan_markdown_files()

    assert any(f.endswith("real.md") for f in found)
    assert not any("traces" in f.split(os.sep) for f in found)


def test_chunk_id_is_deterministic(tmp_path):
    md_file = tmp_path / "test.md"
    content = "# A\nB"
    md_file.write_text(content)
    chunks1 = extract_chunks_from_markdown(str(md_file))
    chunks2 = extract_chunks_from_markdown(str(md_file))
    assert chunks1[0]['chunk_id'] == chunks2[0]['chunk_id']
