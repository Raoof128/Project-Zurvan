import pytest
from unittest.mock import patch, MagicMock
from scripts.cross_project_context import build_federated_context

@patch("scripts.cross_project_context.cross_project_search")
@patch("scripts.cross_project_context.get_federated_projects")
@patch("scripts.cross_project_context.subprocess.run")
def test_build_federated_context(mock_run, mock_get_proj, mock_search):
    mock_search.return_value = {
        "results": [
            {"project": "p1", "source_path": "wiki/test.md", "heading": "Test", 
             "snippet": "content", "keyword_score": 1.0, "semantic_score": 0.0, "hybrid_score": 1.0}
        ],
        "warnings": [],
        "projects_searched": ["p1"]
    }
    mock_get_proj.return_value = [{"name": "p1", "path": "/tmp/p1", "has_search": True, "has_graph": True}]
    
    mock_run.return_value = MagicMock(
        stdout='[{"depth": 1, "title": "Node B", "node_type": "concept", "relation": "links to"}]'
    )
    
    res = build_federated_context("test query", graph=True)
    
    assert "# Zurvan Federated Context Bundle" in res
    assert "## Projects Searched\n- p1" in res.replace("\n\n-", "\n-")
    assert "### Project: p1" in res
    assert "#### Source: wiki/test.md (Score: 1.00)" in res
    assert "## Graph-Related Context" in res
    assert "[1] Node B (concept) - links to" in res

@patch("scripts.cross_project_context.cross_project_search")
@patch("scripts.cross_project_context.get_federated_projects")
def test_build_federated_context_no_graph(mock_get_proj, mock_search):
    mock_search.return_value = {
        "results": [],
        "warnings": ["Some warning"],
        "projects_searched": ["p1"]
    }
    mock_get_proj.return_value = []
    
    res = build_federated_context("test query", graph=False)
    
    assert "## Graph-Related Context" not in res
    assert "## Warnings" in res
    assert "- Some warning" in res
