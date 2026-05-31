import pytest
from scripts.policy_radar import analyze_policies

@pytest.fixture
def mock_items():
    return [
        {
            "project": "p1",
            "title": "Local only",
            "full_text": "no cloud",
            "relative_path": "a.md",
            "source_kind": "policy"
        },
        {
            "project": "p1",
            "title": "Raw protect",
            "full_text": "no raw writes",
            "relative_path": "b.md",
            "source_kind": "policy"
        },
        {
            "project": "p2",
            "title": "Local only too",
            "full_text": "no cloud apis",
            "relative_path": "c.md",
            "source_kind": "policy"
        }
    ]

def test_analyze_policies(mock_items):
    res = analyze_policies(mock_items)
    
    assert set(res["projects_scanned"]) == {"p1", "p2"}
    
    assert "no_cloud" in res["consistent"]
    assert "no_cloud" in res["reusable"]
    
    drift_cats = [d["category"] for d in res["drift"]]
    assert "raw_protection" in drift_cats
    
    drift = next(d for d in res["drift"] if d["category"] == "raw_protection")
    assert "p1" in drift["present"]
    assert "p2" in drift["absent"]
    
    assert "raw_protection" in res["missing"]["p2"]
