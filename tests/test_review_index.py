import pytest
import os
import json
from scripts.review_index import rebuild_index, get_index

def test_rebuild_index(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    # Setup dummy data
    ev_dir = config_dir / "evidence-packs"
    ev_dir.mkdir(parents=True)
    p1 = ev_dir / "pack-idx"
    p1.mkdir()
    (p1 / "manifest.json").write_text(json.dumps({
        "pack_id": "pack-idx", "topic": "test", "redaction_status": "redacted"
    }))
    
    rep_dir = config_dir / "reports"
    rep_dir.mkdir(parents=True)
    r1 = rep_dir / "report-idx"
    r1.mkdir()
    (r1 / "report.json").write_text(json.dumps({
        "report_id": "report-idx",
        "redaction_status": "redacted",
        "topic": "test",
        "template": "digest",
        "created_at": "2026-05-31T00:00:00Z",
        "source_pack_id": "pack-idx",
        "citations": [],
        "claims": []
    }))
    
    idx = rebuild_index()
    assert len(idx["packs"]) == 1
    assert len(idx["reports"]) == 1
    
    assert idx["reports"][0]["status"] == "pass"
    
    # Test get_index reads it
    idx2 = get_index()
    assert idx2["reports"][0]["report_id"] == "report-idx"
