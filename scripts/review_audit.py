import os
import json
import re
from pathlib import Path
from typing import Dict, Any, List

from scripts.report_compose import inspect_report, _get_reports_dir
from scripts.review_safety import get_safe_report_path

def _check_secrets(text: str) -> List[str]:
    failures = []
    # Check absolute paths
    if re.search(r'(?i)(?:[a-z]:\\|/Users/|/home/|/etc/|/var/)', text):
        failures.append("Leaked absolute path detected in content.")
    
    # Check API keys/tokens basic heuristical checks
    if re.search(r'(?i)(?:api_key|token|secret|password)["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{16,}', text):
        failures.append("Token-like secret detected in content.")
        
    if re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text):
        failures.append("Email address detected in content.")
        
    return failures

def audit_report(report_id: str) -> Dict[str, Any]:
    report_path = get_safe_report_path(report_id)
    report_json_path = report_path / "report.json"
    
    warnings = []
    failures = []
    
    report_data = inspect_report(report_id)
    
    if not report_data:
        failures.append("Report JSON manifest is missing or invalid.")
        return {
            "report_id": report_id,
            "status": "fail",
            "warnings": warnings,
            "failures": failures,
            "checked_at": "now",
            # Keep the shape consistent: review_index reads audit["stats"]
            # unconditionally and would 500 the dashboard on a corrupt report.
            "stats": {"claims": 0, "mapped_citations": 0, "missing_citations": 0}
        }
    
    if report_data.get("redaction_status") != "redacted":
        failures.append("Report redaction status is not 'redacted'.")
        
    if "warnings" in report_data and report_data["warnings"]:
        warnings.extend(report_data["warnings"])
        
    # Validate citations
    citations = report_data.get("citations", [])
    valid_ev_ids = {c["evidence_id"] for c in citations}
    
    claims_count = 0
    mapped_citations = 0
    missing_citations = 0
    
    for section in ["claims", "decisions", "contradictions", "graph_context"]:
        for item in report_data.get(section, []):
            claims_count += 1
            if item.get("evidence_id") in valid_ev_ids:
                mapped_citations += 1
            else:
                missing_citations += 1
                failures.append(f"Missing citation mapping for evidence_id: {item.get('evidence_id')}")

    # Check for secrets
    report_str = json.dumps(report_data)
    secret_fails = _check_secrets(report_str)
    failures.extend(secret_fails)
    
    status = "pass"
    if warnings:
        status = "warn"
    if failures:
        status = "fail"
        
    return {
        "report_id": report_id,
        "status": status,
        "warnings": warnings,
        "failures": failures,
        "checked_at": "now",
        "stats": {
            "claims": claims_count,
            "mapped_citations": mapped_citations,
            "missing_citations": missing_citations
        }
    }

def audit_all_reports() -> List[Dict[str, Any]]:
    reports_dir = _get_reports_dir()
    if not reports_dir.exists():
        return []
    
    audits = []
    for d in reports_dir.iterdir():
        if d.is_dir():
            audit = audit_report(d.name)
            audits.append(audit)
            
    return audits
