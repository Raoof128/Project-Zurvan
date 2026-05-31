import pytest
import os
import json
from scripts.publication_bundle import create_bundle

def test_create_bundle(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    rep_dir = config_dir / "reports" / "report-bundle"
    rep_dir.mkdir(parents=True)
    (rep_dir / "report.json").write_text(json.dumps({
        "report_id": "report-bundle",
        "redaction_status": "redacted",
        "topic": "test",
        "template": "digest",
        "created_at": "2026-05-31",
        "source_pack_id": "pack-1",
        "citations": [],
        "claims": [],
        "warnings": []
    }))
    
    b_dir = create_bundle("report-bundle")
    assert b_dir.is_dir()
    
    assert (b_dir / "report-bundle.html").exists()
    assert (b_dir / "report-bundle.md").exists()
    assert (b_dir / "report-bundle.json").exists()
    
    man = json.loads((b_dir / "manifest.json").read_text())
    assert "report-bundle.html" in man["files"]
    
    # Test zip
    b_zip = create_bundle("report-bundle", fmt="zip")
    assert b_zip.is_file()
    assert b_zip.suffix == ".zip"
