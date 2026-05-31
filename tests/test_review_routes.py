import pytest
from fastapi.testclient import TestClient
import os
from scripts.review_server import create_app
import json

@pytest.fixture
def client(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(os, "environ", {"ZURVAN_CONFIG_DIR": str(config_dir)})
    
    # Mock data
    reports_dir = config_dir / "reports"
    reports_dir.mkdir(parents=True)
    rep1 = reports_dir / "report-123"
    rep1.mkdir()
    rep_data = {
        "report_id": "report-123",
        "topic": "test",
        "template": "evidence_digest",
        "redaction_status": "redacted",
        "created_at": "now",
        "source_pack_id": "pack-123",
        "sections": ["claims"],
        "citations": [{"citation_id": "[1]", "evidence_id": "ev-1", "title": "C1", "project": "p", "path": "p.md"}],
        "claims": [{"evidence_id": "ev-1", "title": "C1", "excerpt": "claim text"}]
    }
    (rep1 / "report.json").write_text(json.dumps(rep_data))
    
    ev_dir = config_dir / "evidence-packs"
    ev_dir.mkdir(parents=True)
    p1 = ev_dir / "pack-123"
    p1.mkdir()
    pack_manifest = {
        "pack_id": "pack-123", "topic": "test", "redaction_status": "redacted"
    }
    (p1 / "manifest.json").write_text(json.dumps(pack_manifest))
    pack_items = [{"evidence_id": "ev-1", "title": "C1", "project": "p", "source_path": "p.md", "excerpt": "claim text"}]
    (p1 / "items.json").write_text(json.dumps(pack_items))
    
    app = create_app()
    return TestClient(app)

def test_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Zurvan Review Workbench" in response.text
    assert "report-123" in response.text

def test_reports_list(client):
    response = client.get("/reports")
    assert response.status_code == 200
    assert "report-123" in response.text

def test_report_detail(client):
    response = client.get("/reports/report-123")
    assert response.status_code == 200
    assert "C1" in response.text
    
def test_citations(client):
    response = client.get("/reports/report-123/citations")
    assert response.status_code == 200
    assert "[1]" in response.text
    
def test_export_route(client):
    # Mock export
    response = client.get("/reports/report-123/export?format=json")
    if response.status_code != 200:
        print("EXPORT ERROR:", response.text)
    assert response.status_code == 200
    data = response.json()
    assert data["report_id"] == "report-123"

def test_evidence_list(client):
    response = client.get("/evidence")
    assert response.status_code == 200
    assert "pack-123" in response.text

def test_evidence_detail(client):
    response = client.get("/evidence/pack-123")
    assert response.status_code == 200
    assert "C1" in response.text
