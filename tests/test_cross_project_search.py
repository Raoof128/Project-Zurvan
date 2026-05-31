import json
import pytest
from unittest.mock import patch, MagicMock
from scripts.cross_project_search import cross_project_search

@pytest.fixture
def mock_federated_projects():
    return [
        {"name": "p1", "path": "/tmp/p1", "has_search": True, "has_graph": True},
        {"name": "p2", "path": "/tmp/p2", "has_search": False, "has_graph": False}
    ]

@patch("scripts.cross_project_search.get_federated_projects")
@patch("scripts.cross_project_search.subprocess.run")
def test_cross_project_search(mock_run, mock_get_proj, mock_federated_projects):
    mock_get_proj.return_value = mock_federated_projects
    
    mock_run.return_value = MagicMock(
        stdout=json.dumps([
            {"source_path": "wiki/test.md", "heading": "Test", "snippet": "content", 
             "keyword_score": 1.0, "semantic_score": 0.0, "hybrid_score": 1.0}
        ])
    )
    
    res = cross_project_search("test query", limit=10)
    
    assert len(res["results"]) == 1
    assert res["results"][0]["project"] == "p1"
    assert res["results"][0]["source_path"] == "wiki/test.md"
    
    assert len(res["warnings"]) == 1
    assert "Search index missing for project p2" in res["warnings"][0]
    
    assert "p1" in res["projects_searched"]
    assert "p2" in res["projects_searched"]
    
@patch("scripts.cross_project_search.get_federated_projects")
@patch("scripts.cross_project_search.subprocess.run")
def test_cross_project_search_error(mock_run, mock_get_proj, mock_federated_projects):
    mock_get_proj.return_value = [{"name": "p1", "path": "/tmp/p1", "has_search": True, "has_graph": True}]
    
    mock_run.return_value = MagicMock(
        stdout=json.dumps({"error": "Failed to search"})
    )
    
    res = cross_project_search("test query")
    assert len(res["results"]) == 0
    assert len(res["warnings"]) == 1
    assert "p1 search error: Failed to search" in res["warnings"][0]
