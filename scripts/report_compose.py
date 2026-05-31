import os
import uuid
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any
from scripts.evidence_pack import inspect_evidence_pack
from scripts.evidence_redact import redact_evidence_pack_items, redact_text

def _get_reports_dir() -> Path:
    config_dir = os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan"))
    report_dir = Path(config_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir

def get_template(template_name: str) -> dict:
    templates = {
        "executive_summary": {
            "sections": ["executive_summary", "key_findings", "decisions", "limitations"],
            "required_types": [],
            "style": "concise"
        },
        "technical_audit": {
            "sections": ["executive_summary", "scope", "decisions", "contradictions", "risks", "source_appendix"],
            "required_types": ["decision"],
            "style": "detailed"
        },
        "research_brief": {
            "sections": ["executive_summary", "claims", "graph_context", "source_appendix"],
            "required_types": ["claim", "search_result"],
            "style": "academic"
        },
        "decision_log": {
            "sections": ["executive_summary", "decisions", "contradictions"],
            "required_types": ["decision"],
            "style": "list"
        },
        "risk_review": {
            "sections": ["executive_summary", "contradictions", "risks", "limitations"],
            "required_types": ["radar_ping", "contradiction"],
            "style": "critical"
        },
        "evidence_digest": {
            "sections": ["executive_summary", "claims", "decisions", "sources", "contradictions", "graph_context", "source_appendix"],
            "required_types": [],
            "style": "comprehensive"
        }
    }
    return templates.get(template_name, templates["evidence_digest"])

def compose_report(pack_id: str, template_name: str = "evidence_digest", allow_unsafe: bool = False) -> dict:
    pack_data = inspect_evidence_pack(pack_id)
    if not pack_data:
        raise ValueError(f"Evidence pack {pack_id} not found.")
        
    manifest = pack_data["manifest"]
    items = pack_data["items"]
    
    template = get_template(template_name)
    report_id = f"report-{uuid.uuid4().hex[:12]}"
    
    # Validation against template
    types_found = set([it.get("source_kind") or it.get("item_type") for it in items])
    warnings = []
    for req in template["required_types"]:
        if req not in types_found:
            warnings.append(f"Insufficient evidence: Template '{template_name}' requires '{req}' evidence, but none found.")
            
    if manifest.get("redaction_status") != "redacted" and not allow_unsafe:
        warnings.append("Source evidence pack is unredacted. Unsafe content may be present.")
        
    # Group items
    grouped = {"claim": [], "decision": [], "source": [], "radar_ping": [], "contradiction": [], "search_result": [], "graph_neighbor": []}
    citations = []
    
    for idx, it in enumerate(items):
        kind = it.get("source_kind") or it.get("item_type")
        if kind in grouped:
            grouped[kind].append(it)
        else:
            grouped["source"].append(it)
            
        citations.append({
            "citation_id": f"[{idx+1}]",
            "evidence_id": it["evidence_id"],
            "title": it["title"],
            "project": it["project"],
            "path": it["source_path"]
        })
        
    # Generate structured report
    report = {
        "report_id": report_id,
        "topic": manifest.get("topic", "Unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_pack_id": pack_id,
        "source_manifest_hash": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8")).hexdigest(),
        "template": template_name,
        "sections": template["sections"],
        "claims": grouped["claim"] + grouped["search_result"],
        "decisions": grouped["decision"],
        "contradictions": grouped["radar_ping"] + grouped["contradiction"],
        "graph_context": grouped["graph_neighbor"],
        "citations": citations,
        "limitations": [],
        "warnings": warnings + manifest.get("warnings", []),
        "redaction_status": "redacted" if not allow_unsafe else "unredacted",
        "generation_config": {
            "version": "0.5.0",
            "style": template["style"]
        }
    }
    
    # Add auto limitations based on evidence
    if not report["claims"] and not report["decisions"]:
        report["limitations"].append("No substantive claims or decisions found in evidence.")
    if len(items) < 3:
        report["limitations"].append("Evidence base is extremely small (<3 items). Confidence is low.")
        
    # Save internally
    rep_dir = _get_reports_dir() / report_id
    rep_dir.mkdir(parents=True, exist_ok=True)
    
    (rep_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    
    return report

def list_reports() -> list[dict]:
    rep_dir = _get_reports_dir()
    if not rep_dir.exists():
        return []
        
    reports = []
    for d in rep_dir.iterdir():
        if d.is_dir():
            rep_file = d / "report.json"
            if rep_file.exists():
                try:
                    rep = json.loads(rep_file.read_text(encoding="utf-8"))
                    reports.append({
                        "report_id": rep["report_id"],
                        "topic": rep["topic"],
                        "template": rep["template"],
                        "created_at": rep["created_at"],
                        "source_pack_id": rep["source_pack_id"]
                    })
                except Exception:
                    pass
    
    reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return reports

def inspect_report(report_id: str) -> dict:
    rep_file = _get_reports_dir() / report_id / "report.json"
    if not rep_file.exists():
        return None
    try:
        return json.loads(rep_file.read_text(encoding="utf-8"))
    except Exception:
        return None

def validate_report(report_id: str) -> dict:
    report = inspect_report(report_id)
    if not report:
        raise ValueError(f"Report {report_id} not found.")
        
    issues = []
    warnings = []
    # 1. Check citations
    evidence_ids = {cit["evidence_id"] for cit in report.get("citations", [])}
    
    # Verify claims
    for c in report.get("claims", []):
        if c.get("evidence_id") not in evidence_ids:
            issues.append(f"Claim unsupported by citation: {c.get('title')}")
            
    # Check sections for content
    empty_sections = []
    for sec in report.get("sections", []):
        if sec in ["claims", "key_findings", "decisions"] and not report.get(sec, []):
            empty_sections.append(sec)
            
    if empty_sections:
        warnings.append(f"Sections marked as evidence-insufficient: {', '.join(empty_sections)}")
        
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings
    }
