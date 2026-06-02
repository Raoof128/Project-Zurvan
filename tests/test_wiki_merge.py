import os
import re
import pytest
from pathlib import Path


def test_log_event_matches_grep_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("ingest", "example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log
    assert "example.pdf" in log


def test_log_event_escapes_pipe_in_parts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("query-save", "my|topic|with|pipes")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "my\\|topic\\|with\\|pipes" in log


def test_log_ingest_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_ingest
    append_log_ingest("notes.txt")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "ingest" in log and "notes.txt" in log


def test_log_merge_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_merge
    append_log_merge("RAG", 3)

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "merge" in log and "RAG" in log and "3 sources" in log


def test_log_save_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_save
    append_log_save("vector-search-reliability")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "query-save" in log and "vector-search-reliability" in log


def test_log_image_skip_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_image_skip
    append_log_image_skip("diagram.png")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log
    assert "diagram.png" in log
    assert "pending visual extraction" in log
