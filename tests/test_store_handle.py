"""F3 (audit 2026-10-22): hybrid federation ignored the selected project's root
and searched the home index. A store handle now names the store explicitly."""
from pathlib import Path


def test_foreign_root_ignores_the_home_override(tmp_path, monkeypatch):
    from scripts.store_handle import resolve_store
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "elsewhere.sqlite"))
    h = resolve_store(tmp_path / "other", project="other")
    assert h.search_db == tmp_path / "other" / "data" / "search.sqlite"
    assert h.graph_db == tmp_path / "other" / "data" / "graph.sqlite"
    assert h.root == tmp_path / "other" and h.project == "other"


def test_home_store_honours_the_override(tmp_path, monkeypatch):
    from scripts.store_handle import resolve_store
    from scripts.config import PROJECT_ROOT
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "elsewhere.sqlite"))
    h = resolve_store()
    assert h.search_db == tmp_path / "elsewhere.sqlite"
    assert h.root == Path(PROJECT_ROOT) and h.project is None


def test_home_store_without_override_is_the_canonical_path(monkeypatch):
    from scripts.store_handle import resolve_store
    from scripts.config import PROJECT_ROOT
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    assert resolve_store().search_db == Path(PROJECT_ROOT) / "data" / "search.sqlite"
