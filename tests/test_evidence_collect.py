import pytest
from scripts.evidence_collect import _create_evidence_item, collect_evidence

def test_create_evidence_item():
    it = _create_evidence_item("proj1", "file.md", "claim", "Test Claim", "Excerpt text", {"tags": ["t1"]})
    assert it["project"] == "proj1"
    assert it["source_path"] == "file.md"
    assert it["item_type"] == "claim"
    assert it["title"] == "Test Claim"
    assert it["tags"] == ["t1"]
    assert "evidence_id" in it
    assert "content_hash" in it
    
# Mocking collection behavior
def test_collect_evidence_deduplication(monkeypatch):
    def mock_collect(*args, **kwargs):
        return [
            {"project": "p1", "relative_path": "a.md", "item_type": "claim", "title": "A", "excerpt": "Ex", "source_kind": "claim"},
            {"project": "p1", "relative_path": "a.md", "item_type": "claim", "title": "A", "excerpt": "Ex", "source_kind": "claim"}
        ]
    
    monkeypatch.setattr("scripts.evidence_collect.cross_project_search", lambda *a, **k: {})
    monkeypatch.setattr("scripts.evidence_collect.collect_federated_claims_and_policies", mock_collect)
    
    items = collect_evidence("A")
    assert len(items) == 1 # Deduplicated
