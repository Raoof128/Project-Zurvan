import os
import json
import pytest
from pathlib import Path
from scripts.project_registry import (
    load_registry, save_registry, register_project, 
    set_current_project, get_current_project, is_safe_slug
)

def test_is_safe_slug():
    assert is_safe_slug("my-project_123")
    assert not is_safe_slug("my project")
    assert not is_safe_slug("my/project")
    assert not is_safe_slug("a" * 65)

def test_registry_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path))
    
    # Initially empty
    reg = load_registry()
    assert reg == {"current": None, "projects": {}}
    
    # Register invalid name
    with pytest.raises(ValueError):
        register_project("bad/name", "/some/path")
        
    # Register in raw directory
    with pytest.raises(ValueError):
        register_project("test1", "/some/raw/path")
        
    # Register valid
    register_project("test1", "/valid/path")
    
    reg = load_registry()
    assert reg["current"] == "test1"
    assert "test1" in reg["projects"]
    assert str(Path("/valid/path").resolve()) == reg["projects"]["test1"]["path"]
    
    # Register duplicate without force
    with pytest.raises(ValueError):
        register_project("test1", "/another/path")
        
    # Register duplicate with force
    register_project("test1", "/another/path", force=True)
    reg = load_registry()
    assert str(Path("/another/path").resolve()) == reg["projects"]["test1"]["path"]
    
    # Register second
    register_project("test2", "/valid/path2")
    reg = load_registry()
    assert reg["current"] == "test1"  # current should not change
    
    # Set current
    set_current_project("test2")
    assert get_current_project()[0] == "test2"
    
    # Set invalid current
    with pytest.raises(ValueError):
        set_current_project("nonexistent")

def test_corrupted_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path))
    reg_path = tmp_path / "projects.json"
    with open(reg_path, "w") as f:
        f.write("{bad json")
        
    with pytest.raises(ValueError, match="Registry is corrupted"):
        load_registry()
