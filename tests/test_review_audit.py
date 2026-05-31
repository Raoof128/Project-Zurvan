import pytest
import os
import json
from scripts.review_audit import audit_report, _check_secrets

def test_check_secrets():
    fails = _check_secrets("Here is a token: api_key='1234567890abcdef123'")
    assert len(fails) > 0
    assert "Token-like secret detected" in fails[0]
    
    fails = _check_secrets("Contact me at user@example.com.")
    assert len(fails) > 0
    assert "Email address detected" in fails[0]
    
    fails = _check_secrets("File is at /Users/raouf/secret.txt")
    assert len(fails) > 0
    assert "Leaked absolute path detected" in fails[0]

def test_audit_report(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    reports_dir = config_dir / "reports"
    rep1 = reports_dir / "report-audit"
    rep1.mkdir(parents=True)
    
    # 1. Test missing file
    audit = audit_report("report-audit")
    assert audit["status"] == "fail"
    
    # 2. Test valid report
    valid_data = {
        "report_id": "report-audit",
        "redaction_status": "redacted",
        "citations": [{"evidence_id": "ev-1"}],
        "claims": [{"evidence_id": "ev-1", "excerpt": "valid"}],
        "warnings": []
    }
    (rep1 / "report.json").write_text(json.dumps(valid_data))
    
    audit = audit_report("report-audit")
    assert audit["status"] == "pass"
    
    # 3. Test missing citation mapping
    invalid_data = {
        "report_id": "report-audit",
        "redaction_status": "redacted",
        "citations": [],
        "claims": [{"evidence_id": "ev-1", "excerpt": "valid"}],
        "warnings": []
    }
    (rep1 / "report.json").write_text(json.dumps(invalid_data))
    
    audit = audit_report("report-audit")
    assert audit["status"] == "fail"
    assert "Missing citation mapping" in audit["failures"][0]
    
    # 4. Test warning propagation
    warn_data = {
        "report_id": "report-audit",
        "redaction_status": "redacted",
        "citations": [{"evidence_id": "ev-1"}],
        "claims": [{"evidence_id": "ev-1", "excerpt": "valid"}],
        "warnings": ["Section missing"]
    }
    (rep1 / "report.json").write_text(json.dumps(warn_data))
    
    audit = audit_report("report-audit")
    assert audit["status"] == "warn"
