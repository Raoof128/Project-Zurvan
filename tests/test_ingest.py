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

import re

def test_append_log_uses_grep_parseable_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_ingest
    append_log_ingest("example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log and "example.pdf" in log
