import os
from pathlib import Path

def get_config_dir() -> Path:
    env_dir = os.environ.get("ZURVAN_CONFIG_DIR")
    if env_dir:
        config_dir = Path(env_dir).resolve()
    else:
        config_dir = Path.home() / ".zurvan"
    
    # Safely create it if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return config_dir

def get_registry_path() -> Path:
    return get_config_dir() / "projects.json"
