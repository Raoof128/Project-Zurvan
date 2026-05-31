import os
import re
from pathlib import Path
from fastapi import HTTPException

def get_base_dir() -> Path:
    config_dir = os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan"))
    return Path(config_dir).resolve()

def validate_id_slug(slug: str) -> bool:
    if not slug:
        return False
    # Only allow safe characters
    if not re.match(r"^[a-zA-Z0-9_-]+$", slug):
        return False
    return True

def get_safe_evidence_path(pack_id: str) -> Path:
    if not validate_id_slug(pack_id):
        raise HTTPException(status_code=400, detail="Invalid pack ID")
        
    base = get_base_dir() / "evidence-packs"
    target = (base / pack_id).resolve()
    
    # Block path traversal
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
        
    return target

def get_safe_report_path(report_id: str) -> Path:
    if not validate_id_slug(report_id):
        raise HTTPException(status_code=400, detail="Invalid report ID")
        
    base = get_base_dir() / "reports"
    target = (base / report_id).resolve()
    
    # Block path traversal
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Path traversal blocked")
        
    return target

def check_no_raw_leakage(path: Path):
    if "raw/" in str(path) or "/raw" in str(path):
        raise HTTPException(status_code=403, detail="Access to raw data is blocked")
