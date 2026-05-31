import pytest
from scripts.contradiction_radar import detect_contradictions, format_contradictions

@pytest.fixture
def mock_items():
    return [
        {
            "project": "p1",
            "relative_path": "c1.md",
            "title": "Use Local Data",
            "excerpt": "We must build local first. no cloud allowed.",
            "source_kind": "claim",
            "full_text": "We must build local first. no cloud allowed."
        },
        {
            "project": "p2",
            "relative_path": "c2.md",
            "title": "Cloud sync",
            "excerpt": "We will use cloud api to sync data.",
            "source_kind": "claim",
            "full_text": "We will use cloud api to sync data."
        },
        {
            "project": "p3",
            "relative_path": "c3.md",
            "title": "Self Conflict",
            "excerpt": "raw is immutable but we can write to raw.",
            "source_kind": "claim",
            "full_text": "raw is immutable but we can write to raw."
        },
        {
            "project": "p4",
            "relative_path": "c4.md",
            "title": "Use SQLite DB",
            "status": "accepted",
            "excerpt": "",
            "source_kind": "decision",
            "full_text": ""
        },
        {
            "project": "p5",
            "relative_path": "c5.md",
            "title": "Use SQLite DB",
            "status": "rejected",
            "excerpt": "",
            "source_kind": "decision",
            "full_text": ""
        }
    ]

def test_detect_contradictions_policy_clash(mock_items):
    conflicts = detect_contradictions(mock_items)
    
    c = next((c for c in conflicts if c["category"] == "no_cloud"), None)
    assert c is not None
    assert set(c["projects"]) == {"p1", "p2"}
    assert c["confidence"] == "high"

def test_detect_contradictions_self(mock_items):
    conflicts = detect_contradictions(mock_items)
    
    c = next((c for c in conflicts if c["category"] == "raw_protection"), None)
    assert c is not None
    assert c["projects"] == ["p3"]
    
def test_detect_contradictions_heuristic(mock_items):
    conflicts = detect_contradictions(mock_items)
    
    c = next((c for c in conflicts if c["category"] == "heuristic_similarity"), None)
    assert c is not None
    assert set(c["projects"]) == {"p4", "p5"}
    assert c["confidence"] == "medium"
    
def test_format_contradictions():
    res = format_contradictions([{"projects": ["p1"], "paths": ["path1"], "category": "cat1", "confidence": "high", "reason": "test"}])
    assert "### Candidate 1: p1 (cat1)" in res
    assert "- **Confidence**: high" in res
