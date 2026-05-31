import pytest
import os
from pathlib import Path
from scripts.decision_memory import discover_decisions_in_project, init_decision_cache, cache_decisions, load_all_cached_decisions

@pytest.fixture
def mock_project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    
    # Decisions dir
    d_dir = p / "wiki" / "decisions"
    d_dir.mkdir(parents=True)
    
    # File in decisions
    d1 = d_dir / "d1.md"
    d1.write_text("---\ntitle: 'Test D1'\nstatus: accepted\ntags: [a, b]\n---\nBody1")
    
    # File not in decisions but with frontmatter
    n_dir = p / "wiki" / "notes"
    n_dir.mkdir(parents=True)
    d2 = n_dir / "n1.md"
    d2.write_text("---\ntype: decision\ntitle: Test D2\nstatus: pending\ntags: c\n---\nBody2")
    
    # Normal file
    n2 = n_dir / "n2.md"
    n2.write_text("---\ntitle: Not a decision\n---\nBody3")
    
    # Raw file (should be ignored)
    raw = p / "raw"
    raw.mkdir()
    r1 = raw / "r1.md"
    r1.write_text("---\ntype: decision\n---\nBody4")
    
    return p

def test_discover_decisions(mock_project):
    decisions = discover_decisions_in_project("proj", mock_project)
    
    assert len(decisions) == 2
    titles = [d["title"] for d in decisions]
    assert "Test D1" in titles
    assert "Test D2" in titles
    
    d1 = next(d for d in decisions if d["title"] == "Test D1")
    assert d1["status"] == "accepted"
    assert d1["tags"] == ["a", "b"]
    assert d1["excerpt"] == "Body1"
    
def test_cache_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path / ".zurvan"))
    init_decision_cache()
    
    decisions = [
        {
            "id": "p1:d1",
            "project": "p1",
            "relative_path": "d1.md",
            "title": "T1",
            "status": "S1",
            "tags": ["tag1"],
            "created_at": "date1",
            "updated_at": "date2",
            "content_hash": "hash1",
            "excerpt": "ex1",
            "full_text": ""
        }
    ]
    cache_decisions(decisions)
    
    cached = load_all_cached_decisions()
    assert len(cached) == 1
    assert cached[0]["id"] == "p1:d1"
    assert cached[0]["tags"] == ["tag1"]
