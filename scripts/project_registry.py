import json
import re
import datetime
import tempfile
import os
from pathlib import Path
from scripts.config import get_registry_path

def is_safe_slug(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    return bool(re.match(r'^[a-z0-9_-]+$', name))

def load_registry() -> dict:
    path = get_registry_path()
    if not path.exists():
        return {"current": None, "projects": {}}
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise ValueError(f"Registry is corrupted: {e}")

def save_registry(registry: dict):
    path = get_registry_path()
    
    # Atomic write
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix="registry_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        os.remove(tmp_path)
        raise e

def register_project(name: str, path: str, force: bool = False):
    if not is_safe_slug(name):
        raise ValueError(f"Unsafe project name: {name}")
    
    abs_path = str(Path(path).resolve())
    if "raw" in Path(abs_path).parts:
        raise ValueError("Cannot register a project inside a raw/ directory.")
        
    registry = load_registry()
    if name in registry["projects"] and not force:
        raise ValueError(f"Project '{name}' already exists. Use --force to overwrite.")
        
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    registry["projects"][name] = {
        "path": abs_path,
        "created_at": registry["projects"].get(name, {}).get("created_at", now),
        "updated_at": now
    }
    
    if registry["current"] is None:
        registry["current"] = name
        
    save_registry(registry)

def set_current_project(name: str):
    registry = load_registry()
    if name not in registry["projects"]:
        raise ValueError(f"Project '{name}' not found in registry.")
    
    registry["current"] = name
    save_registry(registry)

def get_current_project() -> tuple[str, str]:
    registry = load_registry()
    name = registry.get("current")
    if not name or name not in registry["projects"]:
        return None, None
    return name, registry["projects"][name]["path"]
