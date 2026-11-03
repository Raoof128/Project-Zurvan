import json
import os
import pytest
from unittest.mock import patch
from scripts.cross_project_search import cross_project_search


def _knowledge_project(tmp_path, name, note_text):
    """A knowledge-only project: wiki/ pages, no embedded Zurvan engine."""
    root = tmp_path / name
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_text(f"# Note\n{note_text}\n")
    return {"name": name, "path": str(root), "has_search": False, "has_graph": False}


@patch("scripts.cross_project_search.get_federated_projects")
def test_keyword_search_spans_knowledge_only_projects(mock_get_proj, tmp_path):
    # Federation runs Zurvan's own retriever against each project root
    # in-process; registered projects no longer need the Zurvan engine
    # (previously a subprocess imported scripts.* inside the target repo).
    p1 = _knowledge_project(tmp_path, "p1", "zebra retrieval facts QQ111")
    p2 = _knowledge_project(tmp_path, "p2", "zebra memory safety QQ222")
    mock_get_proj.return_value = [p1, p2]

    res = cross_project_search("zebra", hybrid=False, limit=10)

    projects_found = {r["project"] for r in res["results"]}
    assert projects_found == {"p1", "p2"}
    assert res["warnings"] == []
    # Paths are project-relative — no absolute machine paths leak.
    for r in res["results"]:
        assert not os.path.isabs(r["source_path"])
        assert r["snippet"]


def _indexed_project(tmp_path, name, note_text, monkeypatch):
    """A project with its OWN <root>/data/search.sqlite, built under the mock provider."""
    from scripts.rebuild_search_index import rebuild_search_index
    root = tmp_path / name
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "note.md").write_text(f"# Note\n{note_text}\n")
    monkeypatch.setenv("ZURVAN_EMBED_PROVIDER", "mock")
    monkeypatch.setenv("ZURVAN_EMBED_MODEL", "mock_model")
    rebuild_search_index(db_path=str(root / "data" / "search.sqlite"), root=root)
    return {"name": name, "path": str(root), "has_search": True, "has_graph": False}


@patch("scripts.cross_project_search.get_federated_projects")
def test_hybrid_federation_searches_the_selected_project_index_only(mock_get_proj, tmp_path, monkeypatch):
    # F3 reproduction (audit 2026-10-22): hybrid mode ignored the selected
    # root, searched the HOME index and labelled its hits with the requested
    # project. The home store here is a distinct tmp index named by the same
    # ZURVAN_SEARCH_DB override production uses.
    _indexed_project(tmp_path, "home", "SENTINEL_HOME alpha", monkeypatch)
    other = _indexed_project(tmp_path, "other", "SENTINEL_OTHER alpha", monkeypatch)
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "home" / "data" / "search.sqlite"))
    mock_get_proj.return_value = [other]

    res = cross_project_search("alpha", hybrid=True, limit=10, projects=["other"])

    assert res["results"], res["warnings"]
    assert {r["project"] for r in res["results"]} == {"other"}
    assert all("SENTINEL_HOME" not in r["snippet"] for r in res["results"])
    assert all("SENTINEL_OTHER" in r["snippet"] for r in res["results"])
    assert all(r["index_path"].endswith(os.path.join("other", "data", "search.sqlite")) for r in res["results"])


@patch("scripts.cross_project_search.get_federated_projects")
def test_hybrid_requires_search_index_and_warns(mock_get_proj, tmp_path):
    p1 = _knowledge_project(tmp_path, "p1", "anything")
    mock_get_proj.return_value = [p1]

    res = cross_project_search("anything", hybrid=True, limit=10)

    assert res["results"] == []
    assert len(res["warnings"]) == 1
    assert "Search index missing for project p1" in res["warnings"][0]


@patch("scripts.cross_project_search.get_federated_projects")
def test_query_with_quotes_and_fts_keywords_is_safe(mock_get_proj, tmp_path):
    # Regression (was a code-injection surface): the query used to be
    # f-string-interpolated into generated Python run in a subprocess.
    # In-process federation has no code-generation step at all.
    p1 = _knowledge_project(tmp_path, "p1", 'say "hello" AND goodbye QQ333')
    mock_get_proj.return_value = [p1]

    res = cross_project_search('say "hello" AND', hybrid=False, limit=5)

    assert len(res["results"]) == 1
    assert res["results"][0]["project"] == "p1"


@patch("scripts.cross_project_search.get_federated_projects")
def test_search_error_becomes_warning(mock_get_proj):
    mock_get_proj.return_value = [
        {"name": "p1", "path": "/tmp/definitely-missing-p1", "has_search": False, "has_graph": False}
    ]

    with patch("scripts.context_export._search_internal", side_effect=RuntimeError("boom")):
        res = cross_project_search("query", hybrid=False)

    assert res["results"] == []
    assert any("p1 search error: boom" in w for w in res["warnings"])
