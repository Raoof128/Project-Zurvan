import pytest
from scripts.evidence_export import _format_markdown, export_evidence_pack

def test_format_markdown():
    pack_data = {
        "manifest": {
            "pack_id": "pack-1",
            "topic": "test",
            "projects_scanned": ["p1"],
            "created_at": "now",
            "item_count": 1,
            "redaction_status": "redacted"
        },
        "items": [
            {
                "project": "p1",
                "source_kind": "claim",
                "title": "Claim 1",
                "source_path": "a.md",
                "excerpt": "ex"
            }
        ]
    }
    
    md = _format_markdown(pack_data)
    assert "# Zurvan Evidence Pack" in md
    assert "## Topic\ntest" in md
    assert "## Claims\n### 1. Claim 1" in md
    assert "Pack ID: `pack-1`" in md
