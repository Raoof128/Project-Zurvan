import os
import pytest
from pathlib import Path
from scripts.workspace import is_valid_zurvan_project, shorten_path, resolve_project_root
from scripts.project_registry import register_project

def test_is_valid_zurvan_project(tmp_path):
    # Missing folders
    assert not is_valid_zurvan_project(str(tmp_path))
    
    # Create required
    for req in ["AGENTS.md", "README.md", "wiki", "docs", "scripts"]:
        if "." in req:
            (tmp_path / req).touch()
        else:
            (tmp_path / req).mkdir()
            
    assert is_valid_zurvan_project(str(tmp_path))
    
    # In raw/
    raw_path = tmp_path / "raw" / "sub"
    raw_path.mkdir(parents=True)
    for req in ["AGENTS.md", "README.md", "wiki", "docs", "scripts"]:
        if "." in req:
            (raw_path / req).touch()
        else:
            (raw_path / req).mkdir()
            
    assert not is_valid_zurvan_project(str(raw_path))

def test_shorten_path(monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: Path("/Users/fake"))
    assert shorten_path("/Users/fake/projects/zurvan") == "~/projects/zurvan"
    assert shorten_path("/Users/other/fake") == "/Users/other/fake"

def test_resolve_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(tmp_path / ".zurvan"))
    
    # Create valid project
    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    for req in ["AGENTS.md", "README.md", "wiki", "docs", "scripts"]:
        if "." in req:
            (proj_dir / req).touch()
        else:
            (proj_dir / req).mkdir()
            
    register_project("myproj", str(proj_dir))
    
    # Resolve explicit
    assert resolve_project_root("myproj") == proj_dir.resolve()
    
    # Resolve implicit (current)
    assert resolve_project_root() == proj_dir.resolve()
    
    # Invalid project name
    with pytest.raises(ValueError, match="not registered"):
        resolve_project_root("invalid")
