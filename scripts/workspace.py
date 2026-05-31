import os
from pathlib import Path
from scripts.project_registry import load_registry, get_current_project

def is_valid_zurvan_project(path_str: str) -> bool:
    p = Path(path_str).resolve()
    if not p.exists() or not p.is_dir():
        return False
        
    required = ["AGENTS.md", "README.md", "wiki", "docs", "scripts"]
    for req in required:
        if not (p / req).exists():
            return False
            
    if "raw" in p.parts:
        return False
        
    return True

def shorten_path(path_str: str) -> str:
    p = str(Path(path_str).resolve())
    home = str(Path.home())
    if p.startswith(home):
        return p.replace(home, "~", 1)
    return p

def resolve_project_root(project_name: str = None) -> Path:
    """
    Returns the absolute Path to the requested project, or the current one if None.
    Raises ValueError if project not found or invalid.
    """
    if project_name:
        registry = load_registry()
        if project_name not in registry["projects"]:
            raise ValueError(f"Project '{project_name}' not registered.")
        p = Path(registry["projects"][project_name]["path"])
    else:
        name, path = get_current_project()
        if not path:
            raise ValueError("No current project set. Please register or select one.")
        p = Path(path)
        
    if not is_valid_zurvan_project(str(p)):
        raise ValueError(f"Path is not a valid Zurvan project: {shorten_path(str(p))}")
        
    return p
