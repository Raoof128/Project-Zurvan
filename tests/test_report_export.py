import pytest
import os
from pathlib import Path
from scripts.report_export import export_report

def test_export_report_markdown(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    report_data = {
        "report_id": "report-123",
        "topic": "Test Topic",
        "template": "evidence_digest",
        "created_at": "now",
        "source_pack_id": "pack-1",
        "redaction_status": "redacted",
        "sections": ["claims", "decisions"],
        "claims": [
            {"evidence_id": "ev-1", "title": "C1", "excerpt": "claim text"}
        ],
        "decisions": [],
        "citations": [
            {"citation_id": "[1]", "evidence_id": "ev-1", "title": "C1", "project": "p1", "path": "p1.md"}
        ]
    }
    
    monkeypatch.setattr("scripts.report_export.inspect_report", lambda x: report_data)
    
    out = export_report("report-123", "markdown")
    md = Path(out).read_text()
    
    assert "# Zurvan Composed Report" in md
    assert "## Topic\nTest Topic" in md
    assert "C1" in md
    assert "[1]" in md
