from scripts.doctor import run_doctor
from unittest.mock import patch

def test_doctor_healthy(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.doctor.ROOT", tmp_path)
    
    # Create required dirs
    for d in ["wiki", "docs", "data", "scripts", "raw"]:
        (tmp_path / d).mkdir()
        
    # Create required files
    for f in ["README.md", "AGENTS.md", "CHANGELOG.md"]:
        with open(tmp_path / f, "w") as fd: fd.write("test")
        
    # SQLite
    with open(tmp_path / "data" / "search.sqlite", "w") as fd: fd.write("")
    with open(tmp_path / "data" / "graph.sqlite", "w") as fd: fd.write("")
        
    # Eval gold
    (tmp_path / "eval").mkdir()
    with open(tmp_path / "eval" / "search_gold.jsonl", "w") as fd: fd.write('{"test": 1}')
        
    assert run_doctor() == 0

def test_doctor_unhealthy(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.doctor.ROOT", tmp_path)
    
    # Missing everything
    assert run_doctor() == 1
