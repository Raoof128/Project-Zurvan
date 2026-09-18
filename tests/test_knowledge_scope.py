"""What counts as authoritative knowledge — asked once, answered once.

Revision tracking and manifest generation must agree. When they disagreed, a
write into a derived subtree advanced a revision whose manifest could not
change, which is a revision that means nothing.
"""

from pathlib import Path

import pytest

from scripts import knowledge_scope as ks


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "traces").mkdir()
    (tmp_path / "docs").mkdir()
    return tmp_path


def test_a_wiki_page_is_authoritative(root):
    assert ks.is_authoritative(root / "wiki" / "decisions" / "a.md", root)


def test_a_derived_trace_is_not_authoritative(root):
    """`scripts/trace_writer.py:20` writes here outside `safe_write`. Counting
    it would make Zurvan's own output read as external tampering."""
    assert not ks.is_authoritative(root / "wiki" / "traces" / "t.json", root)


def test_a_file_outside_wiki_is_not_authoritative(root):
    """`safe_write.is_safe_path` permits everything under the root except
    `raw/`, so `docs/` would otherwise mint a revision changing no manifest."""
    assert not ks.is_authoritative(root / "docs" / "x.md", root)


def test_a_relative_path_is_rejected_rather_than_resolved_against_the_cwd(root):
    """Measured: `Path("a").resolve()` -> the REAL repository during a test run.
    A bare relative path is a production path wearing a disguise, so the
    predicate refuses it instead of guessing."""
    with pytest.raises(ValueError):
        ks.is_authoritative("a", root)


def test_the_file_list_and_the_predicate_agree(root):
    (root / "wiki" / "decisions" / "a.md").write_text("x")
    (root / "wiki" / "traces" / "t.json").write_text("{}")
    listed = ks.authoritative_files(root)
    assert all(ks.is_authoritative(p, root) for p in listed)
    assert (root / "wiki" / "traces" / "t.json") not in listed


def test_the_root_follows_zurvan_root_set_after_import(tmp_path, monkeypatch):
    import scripts.config
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    assert ks.store_root() == tmp_path.resolve()
    assert scripts.config.PROJECT_ROOT != tmp_path
