import os
from pathlib import Path
from typing import List, Dict, Any
from scripts.policy_rules import identify_policies, POLICY_CATEGORIES
from scripts.contradiction_radar import format_contradictions, detect_contradictions
from scripts.claim_federation import collect_federated_claims_and_policies

def analyze_policies(items: List[Dict[str, Any]]) -> dict:
    # project -> category -> list of items affirming it
    # We only care about 'positive' matches for tracking policy coverage
    project_coverage = {}
    projects = set()
    
    for item in items:
        p = item["project"]
        projects.add(p)
        if p not in project_coverage:
            project_coverage[p] = {cat: [] for cat in POLICY_CATEGORIES}
            
        text = item.get("title", "") + "\n" + item.get("full_text", "")
        pols = identify_policies(text)
        
        for cat, match in pols.items():
            if match["status"] == "positive":
                project_coverage[p][cat].append(item)
                
    # Identify consistent, missing, drift
    consistent = []
    missing = {p: [] for p in projects}
    drift = []
    reusable = []
    
    for cat in POLICY_CATEGORIES:
        present_in = [p for p in projects if project_coverage[p][cat]]
        absent_in = [p for p in projects if not project_coverage[p][cat]]
        
        if len(present_in) == len(projects) and len(projects) > 0:
            consistent.append(cat)
            if len(projects) > 1:
                reusable.append(cat)
        elif len(present_in) > 0 and len(absent_in) > 0:
            drift.append({
                "category": cat,
                "present": present_in,
                "absent": absent_in
            })
            
        for p in absent_in:
            missing[p].append(cat)
            
    return {
        "projects_scanned": list(projects),
        "consistent": consistent,
        "missing": missing,
        "drift": drift,
        "reusable": reusable,
        "project_coverage": project_coverage
    }

def format_policy_scan(items: List[Dict[str, Any]]) -> str:
    bundle = ["# Zurvan Policy Radar Report\n", "## Items Discovered\n"]
    
    by_project = {}
    for item in items:
        p = item["project"]
        if p not in by_project:
            by_project[p] = []
        by_project[p].append(item)
        
    for p, it_list in by_project.items():
        bundle.append(f"### Project: {p}")
        for it in it_list:
            kind = it["source_kind"]
            bundle.append(f"- **{it['title']}** ({kind}) - `{it['relative_path']}`")
        bundle.append("")
        
    return "\n".join(bundle)

def format_policy_coverage(analysis: dict) -> str:
    bundle = ["# Zurvan Policy Radar Report\n", "## Policy Coverage\n"]
    
    for p, coverage in analysis["project_coverage"].items():
        bundle.append(f"### Project: {p}")
        for cat, items in coverage.items():
            if items:
                bundle.append(f"- **{cat}**: Detected in {len(items)} source(s)")
        bundle.append("")
        
    return "\n".join(bundle)

def format_policy_drift(analysis: dict) -> str:
    bundle = ["# Zurvan Policy Radar Report\n", "## Possible Policy Drift\n"]
    
    if not analysis["drift"]:
        bundle.append("No policy drift detected.")
        
    for d in analysis["drift"]:
        bundle.append(f"### Category: {d['category']}")
        bundle.append(f"- **Adopted by**: {', '.join(d['present'])}")
        bundle.append(f"- **Missing in**: {', '.join(d['absent'])}")
        bundle.append("")
        
    return "\n".join(bundle)

def generate_full_report(items: List[Dict[str, Any]]) -> str:
    analysis = analyze_policies(items)
    conflicts = detect_contradictions(items)
    
    bundle = [
        "# Zurvan Policy Radar Report",
        f"## Projects Scanned\n{', '.join(analysis['projects_scanned'])}\n",
        "## Consistent Policies"
    ]
    
    if analysis["consistent"]:
        for c in analysis["consistent"]:
            bundle.append(f"- {c}")
    else:
        bundle.append("None")
        
    bundle.append("\n## Missing Policies")
    for p, missing_cats in analysis["missing"].items():
        if missing_cats:
            bundle.append(f"- **{p}**: {', '.join(missing_cats)}")
            
    bundle.append("\n## Possible Policy Drift")
    if analysis["drift"]:
        for d in analysis["drift"]:
            bundle.append(f"- **{d['category']}** (Adopted: {', '.join(d['present'])} | Missing: {', '.join(d['absent'])})")
    else:
        bundle.append("None")
        
    bundle.append("\n## Possible Contradiction Candidates")
    if conflicts:
        for i, c in enumerate(conflicts):
            projs = " vs ".join(set(c["projects"]))
            bundle.append(f"- **Candidate {i+1}**: {projs} ({c['category']}) - Confidence: {c['confidence']}")
    else:
        bundle.append("None")
        
    bundle.append("\n## Reusable Policies")
    if analysis["reusable"]:
        for r in analysis["reusable"]:
            bundle.append(f"- {r}")
    else:
        bundle.append("None")
        
    bundle.append("\n## Warnings")
    bundle.append("- *This report is generated using local heuristics. Results are candidates for review.*")
    
    return "\n".join(bundle)

def save_report_locally(report: str):
    config_dir = os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan"))
    report_dir = Path(config_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "policy_radar_report.md"
    report_path.write_text(report, encoding="utf-8")
    return report_path.as_posix()
