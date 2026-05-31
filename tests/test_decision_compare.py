import pytest
import os
from pathlib import Path
from datetime import datetime, timedelta
from scripts.decision_compare import find_similar_decisions, find_possible_conflicts, find_stale_decisions, find_reusable_patterns

@pytest.fixture
def mock_decisions():
    return [
        {
            "project": "p1",
            "relative_path": "d1.md",
            "title": "Use SQLite for everything",
            "status": "accepted",
            "tags": ["db", "architecture"],
            "created_at": "2026-05-01T12:00:00",
            "excerpt": "We will use SQLite."
        },
        {
            "project": "p2",
            "relative_path": "d2.md",
            "title": "SQLite is best for local",
            "status": "pending",
            "tags": ["db", "local"],
            "created_at": "2026-05-01T12:00:00",
            "excerpt": "Deciding on SQLite."
        },
        {
            "project": "p3",
            "relative_path": "d3.md",
            "title": "Never use SQLite",
            "status": "rejected",
            "tags": ["db", "architecture"],
            "created_at": "2020-01-01T12:00:00",
            "excerpt": "SQLite is bad."
        },
        {
            "project": "p1",
            "relative_path": "d4.md",
            "title": "Old decision",
            "status": "pending",
            "tags": ["old"],
            "created_at": (datetime.now() - timedelta(days=100)).isoformat(),
            "excerpt": "Pending for a long time."
        }
    ]

def test_find_similar_decisions(mock_decisions):
    sim = find_similar_decisions(mock_decisions, "use sqlite everything")
    assert len(sim) > 0
    assert sim[0]["score"] > 0
    assert sim[0]["project"] == "p1" # Exact words
    
def test_find_possible_conflicts(mock_decisions):
    conflicts = find_possible_conflicts(mock_decisions)
    # Expect conflict between "Use SQLite for everything" and "Never use SQLite" due to high word overlap and different status.
    assert len(conflicts) > 0
    c = conflicts[0]
    assert c["decision1"]["project"] != c["decision2"]["project"]
    assert "Different statuses" in c["reason"] or "High title similarity" in c["reason"]

def test_find_stale_decisions(mock_decisions):
    stale = find_stale_decisions(mock_decisions, days=90)
    assert len(stale) == 1
    assert stale[0]["title"] == "Old decision"
    assert "Still pending after" in stale[0]["stale_reason"]
    
def test_find_reusable_patterns(mock_decisions):
    patterns = find_reusable_patterns(mock_decisions)
    assert len(patterns) == 3 # p1(d1), p2(d2), p3(d3) all share 'db'
    assert "db" in patterns[0]["pattern_reason"]
