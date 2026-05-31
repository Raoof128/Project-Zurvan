import json
import os
from pathlib import Path
from typing import Dict, Any

from scripts.review_safety import get_base_dir
from scripts.report_compose import list_reports
from scripts.evidence_pack import list_evidence_packs
from scripts.review_audit import audit_report

def rebuild_index() -> Dict[str, Any]:
    base_dir = get_base_dir()
    index_file = base_dir / "review-index.json"
    
    packs = list_evidence_packs()
    reports = list_reports()
    
    index_data = {
        "packs": [],
        "reports": []
    }
    
    for p in packs:
        index_data["packs"].append({
            "pack_id": p.get("pack_id"),
            "topic": p.get("topic"),
            "created_at": p.get("created_at"),
            "redaction_status": p.get("redaction_status")
        })
        
    for r in reports:
        report_id = r.get("report_id")
        audit = audit_report(report_id)
        
        index_data["reports"].append({
            "report_id": report_id,
            "topic": r.get("topic"),
            "template": r.get("template"),
            "created_at": r.get("created_at"),
            "redaction_status": r.get("redaction_status"),
            "status": audit["status"],
            "warning_count": len(audit["warnings"]),
            "failure_count": len(audit["failures"]),
            "claims": audit["stats"]["claims"],
            "mapped_citations": audit["stats"]["mapped_citations"],
            "missing_citations": audit["stats"]["missing_citations"]
        })
        
    index_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")
    return index_data

def get_index() -> Dict[str, Any]:
    base_dir = get_base_dir()
    index_file = base_dir / "review-index.json"
    
    if not index_file.exists():
        return rebuild_index()
        
    return json.loads(index_file.read_text(encoding="utf-8"))
