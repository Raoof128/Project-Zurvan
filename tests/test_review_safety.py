import pytest
import os
from pathlib import Path
from fastapi import HTTPException
from scripts.review_safety import validate_id_slug, get_safe_evidence_path, get_safe_report_path, check_no_raw_leakage

def test_validate_id_slug():
    assert validate_id_slug("pack-123") is True
    assert validate_id_slug("report-abc") is True
    assert validate_id_slug("invalid/path") is False
    assert validate_id_slug("../traversal") is False
    assert validate_id_slug("") is False

def test_get_safe_paths(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    # Valid
    p = get_safe_evidence_path("pack-123")
    assert str(p).endswith("pack-123")
    
    # Invalid slug
    with pytest.raises(HTTPException) as exc:
        get_safe_evidence_path("../pack-123")
    assert exc.value.status_code == 400
    
def test_check_no_raw_leakage():
    check_no_raw_leakage(Path("/Users/raouf/.zurvan/reports/report-1"))
    
    with pytest.raises(HTTPException):
        check_no_raw_leakage(Path("/Users/raouf/Zurvan/raw/notes/test.md"))
