import pytest
import os
import json
from scripts.publication_export import export_publication

def test_export_publication(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    rep_dir = config_dir / "reports" / "report-123"
    rep_dir.mkdir(parents=True)
    
    (rep_dir / "report.json").write_text(json.dumps({
        "report_id": "report-123",
        "redaction_status": "redacted",
        "topic": "test",
        "template": "digest",
        "created_at": "2026-05-31",
        "source_pack_id": "pack-1",
        "citations": [],
        "claims": [{"evidence_id": "ev-1", "excerpt": "Claim"}],
        "warnings": []
    }))
    
    # Export without force should fail because missing citation mapping
    with pytest.raises(ValueError, match="failed audit"):
        export_publication("report-123", "markdown")
        
    # Export with force should work
    out = export_publication("report-123", "markdown", force=True)
    assert out.exists()
    assert out.read_text().startswith("# test")
    
    # Test JSON
    out_json = export_publication("report-123", "json", force=True)
    assert out_json.exists()
    
    # Test HTML
    out_html = export_publication("report-123", "html", force=True)
    assert out_html.exists()
    
    # Test missing PDF
    with pytest.raises(NotImplementedError):
        export_publication("report-123", "pdf", force=True)
