import pytest
import os
from scripts.chunk import extract_chunks_from_markdown, hash_content

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

def test_chunk_id_is_deterministic(tmp_path):
    md_file = tmp_path / "test.md"
    content = "# A\nB"
    md_file.write_text(content)
    chunks1 = extract_chunks_from_markdown(str(md_file))
    chunks2 = extract_chunks_from_markdown(str(md_file))
    assert chunks1[0]['chunk_id'] == chunks2[0]['chunk_id']
