import pytest
from unittest.mock import patch, MagicMock
from scripts.decision_federation import format_decisions_all, format_similar_decisions, format_decision_conflicts, format_stale_decisions

def test_format_decisions_all():
    decisions = [
        {"project": "p1", "title": "T1", "status": "S1", "tags": ["tag1"], "relative_path": "path1"}
    ]
    res = format_decisions_all(decisions)
    assert "# Federated Decision Memory Report" in res
    assert "### Project: p1" in res
    assert "- **T1** (S1) [tag1] - `path1`" in res

def test_format_similar_decisions():
    with patch("scripts.decision_compare.find_similar_decisions") as mock_find:
        mock_find.return_value = [
            {"project": "p1", "title": "T1", "score": 0.8, "relative_path": "path1"}
        ]
        res = format_similar_decisions([], "query")
        assert "## Similar Decision Candidates for 'query'" in res
        assert "- **T1** (Score: 0.80) - p1 `path1`" in res

def test_format_decision_conflicts():
    with patch("scripts.decision_compare.find_possible_conflicts") as mock_conf:
        mock_conf.return_value = [
            {
                "reason": "Test Reason",
                "decision1": {"project": "p1", "title": "T1", "status": "S1"},
                "decision2": {"project": "p2", "title": "T2", "status": "S2"}
            }
        ]
        res = format_decision_conflicts([])
        assert "## Possible Conflict Candidates" in res
        assert "### p1 vs p2" in res
        assert "- Reason: Test Reason" in res
        assert "- p1: **T1** (S1)" in res
        assert "- p2: **T2** (S2)" in res

def test_format_stale_decisions():
    with patch("scripts.decision_compare.find_stale_decisions") as mock_stale:
        mock_stale.return_value = [
            {"project": "p1", "title": "T1", "status": "pending", "stale_reason": "Too old", "relative_path": "path1"}
        ]
        res = format_stale_decisions([], 90)
        assert "## Stale Decisions" in res
        assert "- **T1** (pending) - p1 `path1`" in res
        assert "- Reason: Too old" in res
