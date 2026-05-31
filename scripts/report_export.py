import json
import os
from pathlib import Path
from scripts.report_compose import inspect_report, _get_reports_dir
from scripts.evidence_redact import redact_text

def _format_report_markdown(report: dict) -> str:
    md = [
        f"# Zurvan Composed Report",
        f"## Topic\n{report.get('topic', 'Unknown')}\n",
        "## Executive Summary",
        f"**Report ID**: `{report['report_id']}`",
        f"**Source Pack**: `{report['source_pack_id']}`",
        f"**Template**: `{report['template']}`",
        f"**Generated**: {report['created_at']}\n"
    ]
    
    sections = report.get("sections", [])
    
    def _add_items(title, items):
        if not items:
            md.append(f"*(No {title.lower()} found in evidence)*\n")
            return
        for it in items:
            citation_ref = next((c["citation_id"] for c in report.get("citations", []) if c["evidence_id"] == it["evidence_id"]), "")
            status = f" **[Status: {it['status']}]**" if "status" in it and it["status"] else ""
            md.append(f"### {it['title']}{status} {citation_ref}")
            md.append(f"> {it['excerpt']}\n")
    
    if "claims" in sections or "key_findings" in sections:
        md.append("## Claims & Key Findings")
        _add_items("Claims", report.get("claims", []))
        
    if "decisions" in sections:
        md.append("## Decisions")
        _add_items("Decisions", report.get("decisions", []))
        
    if "contradictions" in sections or "risks" in sections:
        md.append("## Contradictions & Risks")
        _add_items("Contradictions", report.get("contradictions", []))
        
    if "graph_context" in sections:
        md.append("## Graph Context")
        _add_items("Graph Neighbors", report.get("graph_context", []))
        
    if report.get("limitations"):
        md.append("## Limitations")
        for lim in report["limitations"]:
            md.append(f"- {lim}")
        md.append("")
        
    if report.get("warnings"):
        md.append("## Warnings")
        for warn in report["warnings"]:
            md.append(f"- {warn}")
        md.append("")
        
    md.append("## Source Appendix")
    for cit in report.get("citations", []):
        md.append(f"{cit['citation_id']} **{cit['title']}** (`{cit['project']}`)")
        md.append(f"  - Path: `{cit['path']}`")
        md.append(f"  - Evidence ID: `{cit['evidence_id']}`")
        
    return "\n".join(md)

def export_report(report_id: str, fmt: str = "markdown", output_dir: str = None, allow_unsafe: bool = False) -> str:
    report = inspect_report(report_id)
    if not report:
        raise ValueError(f"Report {report_id} not found.")
        
    if report.get("redaction_status") != "redacted" and not allow_unsafe:
        # Failsafe check
        report_str = json.dumps(report)
        if "[REDACTED" not in report_str:
            pass # We rely on phase 13's redaction. If the report says unredacted, we block it unless allow_unsafe.
            raise ValueError("Report is unredacted. Use --allow-unsafe to export.")
            
    if output_dir:
        out_dir = Path(output_dir)
        if ".git" in out_dir.resolve().parts or "zurvan" in out_dir.resolve().name.lower():
            print("WARNING: You are exporting a report inside a repository.")
    else:
        out_dir = _get_reports_dir() / report_id
        
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Extra redaction pass just in case
    if not allow_unsafe:
        report_str = json.dumps(report)
        redacted_str = redact_text(report_str)
        report = json.loads(redacted_str)
        
    if fmt == "markdown":
        out_file = out_dir / f"{report_id}.md"
        content = _format_report_markdown(report)
        out_file.write_text(content, encoding="utf-8")
        return out_file.as_posix()
    elif fmt == "json":
        out_file = out_dir / f"{report_id}_export.json"
        content = json.dumps(report, indent=2)
        out_file.write_text(content, encoding="utf-8")
        return out_file.as_posix()
    else:
        raise ValueError(f"Unsupported format: {fmt}")
