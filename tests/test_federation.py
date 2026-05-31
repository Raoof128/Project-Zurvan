import os
import json
import pytest
from pathlib import Path
from scripts.federation import get_federated_projects, get_federation_stats, run_federation_doctor
from scripts.workspace import is_valid_zurvan_project

@pytest.fixture
def mock_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path / ".zurvan"))
    
    # Create valid project
    p1 = tmp_path / "p1"
    p1.mkdir()
    for d in ["wiki", "docs", "data", "scripts", "raw"]:
        (p1 / d).mkdir()
    (p1 / "README.md").touch()
    (p1 / "AGENTS.md").touch()
    (p1 / "CHANGELOG.md").touch()
    
    # Create invalid project
    p2 = tmp_path / "p2"
    p2.mkdir()
    
    config_dir = tmp_path / ".zurvan"
    config_dir.mkdir()
    
    registry = {
        "projects": {
            "p1": {"path": str(p1)},
            "p2": {"path": str(p2)}
        }
    }
    with open(config_dir / "projects.json", "w") as f:
        json.dump(registry, f)
        
    return tmp_path, p1, p2

def test_get_federated_projects(mock_registry):
    tmp_path, p1, p2 = mock_registry
    
    # Default skips invalid
    projects = get_federated_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "p1"
    
    # Strict fails on invalid
    with pytest.raises(ValueError, match="is invalid or missing"):
        get_federated_projects(strict=True)
        
    # Project filtering
    projects = get_federated_projects(selected_projects=["p1"])
    assert len(projects) == 1
    assert projects[0]["name"] == "p1"

def test_federation_stats(mock_registry):
    stats = get_federation_stats()
    assert stats["total"] == 2
    assert stats["healthy"] == 1
    
    names = {p["name"]: p["is_valid"] for p in stats["projects"]}
    assert names["p1"] is True
    assert names["p2"] is False

def test_federation_doctor(mock_registry, capsys):
    assert run_federation_doctor(strict=False) is True
    assert run_federation_doctor(strict=True) is False
    
    out, _ = capsys.readouterr()
    assert "checking 2 registered projects" in out
    assert "Project 'p1'" in out
    assert "WARNING: Project is missing or invalid" in out
