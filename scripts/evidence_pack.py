import os
import uuid
import json
from pathlib import Path
from scripts.evidence_collect import collect_evidence
from scripts.evidence_redact import redact_evidence_pack_items
from scripts.evidence_manifest import create_manifest

def _get_evidence_dir() -> Path:
    config_dir = os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan"))
    pack_dir = Path(config_dir) / "evidence-packs"
    pack_dir.mkdir(parents=True, exist_ok=True)
    return pack_dir

def build_evidence_pack(topic: str, projects: list[str] = None, hybrid: bool = False, 
                        graph: bool = False, include_decisions: bool = False, 
                        include_policy_radar: bool = False, limit: int = 20, 
                        redact: bool = True) -> dict:
    
    # 1. Generate ID
    pack_id = f"pack-{uuid.uuid4().hex[:12]}"
    pack_path = _get_evidence_dir() / pack_id
    pack_path.mkdir(parents=True, exist_ok=True)
    
    # 2. Collect evidence
    items = collect_evidence(topic, projects, hybrid, graph, include_decisions, include_policy_radar, limit)
    
    # 3. Redact
    if redact:
        items = redact_evidence_pack_items(items)
        
    # 4. Save raw items (JSON)
    items_file = pack_path / "items.json"
    items_file.write_text(json.dumps(items, indent=2), encoding="utf-8")
    
    # 5. Save manifest
    options = {
        "hybrid": hybrid,
        "graph": graph,
        "include_decisions": include_decisions,
        "include_policy_radar": include_policy_radar,
        "limit": limit,
        "redact": redact
    }
    manifest = create_manifest(pack_id, topic, projects or [], options, items, ["items.json", "manifest.json"])
    
    manifest_file = pack_path / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    
    return {
        "pack_id": pack_id,
        "path": pack_path.as_posix(),
        "item_count": len(items),
        "manifest": manifest
    }

def list_evidence_packs() -> list[dict]:
    pack_dir = _get_evidence_dir()
    if not pack_dir.exists():
        return []
        
    packs = []
    for d in pack_dir.iterdir():
        if d.is_dir():
            manifest_file = d / "manifest.json"
            if manifest_file.exists():
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    packs.append(manifest)
                except Exception:
                    pass
                    
    # Sort by created_at desc
    packs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return packs

def inspect_evidence_pack(pack_id: str) -> dict:
    pack_dir = _get_evidence_dir() / pack_id
    manifest_file = pack_dir / "manifest.json"
    if not manifest_file.exists():
        return None
        
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        
        items_file = pack_dir / "items.json"
        items = []
        if items_file.exists():
            items = json.loads(items_file.read_text(encoding="utf-8"))
            
        return {"manifest": manifest, "items": items, "path": pack_dir.as_posix()}
    except Exception:
        return None
