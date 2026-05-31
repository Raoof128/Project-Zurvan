import pytest
import os
from scripts.report_compose import compose_report, list_reports, inspect_report, validate_report

def test_compose_report(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    # Mock evidence pack inspect
    pack_data = {
        "manifest": {
            "pack_id": "pack-test",
            "topic": "test",
            "redaction_status": "redacted"
        },
        "items": [
            {
                "evidence_id": "ev-1",
                "item_type": "decision",
                "title": "Dec 1",
                "project": "proj1",
                "source_path": "a.md",
                "excerpt": "Dec 1 excerpt"
            }
        ]
    }
    monkeypatch.setattr("scripts.report_compose.inspect_evidence_pack", lambda x: pack_data)
    
    rep = compose_report("pack-test", "technical_audit")
    assert rep["report_id"].startswith("report-")
    assert rep["template"] == "technical_audit"
    assert len(rep["decisions"]) == 1
    
    reps = list_reports()
    assert len(reps) == 1
    assert reps[0]["report_id"] == rep["report_id"]
    
    insp = inspect_report(rep["report_id"])
    assert insp is not None
    assert len(insp["decisions"]) == 1
    
    val = validate_report(rep["report_id"])
    assert val["valid"] is True
