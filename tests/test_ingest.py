import os
import pytest
from scripts.ingest import extract_text, calculate_hash

def test_calculate_hash(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    # sha256 of "hello" is 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    h = calculate_hash(str(f))
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

def test_extract_text_txt(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("some test content")
    assert extract_text(str(f)) == "some test content"

def test_extract_text_unsupported():
    with pytest.raises(ValueError):
        extract_text("file.unknown")
