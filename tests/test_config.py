import os
from pathlib import Path
from scripts.config import get_config_dir, get_registry_path

def test_get_config_dir_default(monkeypatch):
    monkeypatch.delenv("ZURVAN_CONFIG_DIR", raising=False)
    # mock home
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))
    
    # We can't actually assert without hitting permissions if it tries to mkdir /fake/home
    # So we'll patch mkdir
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    d = get_config_dir()
    assert str(d) == "/fake/home/.zurvan"

def test_get_config_dir_override(monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", "/fake/override")
    
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    d = get_config_dir()
    assert str(d) == "/fake/override"
    
def test_get_registry_path(monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", "/fake/override")
    
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    p = get_registry_path()
    assert str(p) == "/fake/override/projects.json"
