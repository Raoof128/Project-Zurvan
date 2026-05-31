import os
import json
from pathlib import Path

from scripts.report_compose import inspect_report
from scripts.evidence_pack import inspect_evidence_pack
from scripts.review_audit import audit_report
from scripts.publication_safety import get_publications_dir, check_publication_safety
from scripts.publication_citations import generate_citation_appendix
from scripts.publication_templates import render_html_report

def export_publication(report_id: str, fmt: str, force: bool = False, output_dir: Path = None) -> Path:
    audit = audit_report(report_id)
    
    if audit["status"] == "fail" and not force:
        raise ValueError(f"Report {report_id} failed audit. Cannot export without --force.")
        
    report = inspect_report(report_id)
    if not report:
        raise ValueError(f"Report {report_id} not found.")
        
    pack_id = report.get("source_pack_id")
    pack = inspect_evidence_pack(pack_id) if pack_id else None
    if not pack:
        pack = {}
    
    if report.get("redaction_status") != "redacted" and not force:
        raise ValueError(f"Report {report_id} is not redacted. Cannot export.")
        
    appendix = generate_citation_appendix(report, pack)
    
    # Generate content based on format
    content_str = ""
    ext = ""
    
    if fmt == "json":
        out_data = {
            "report": report,
            "citation_appendix": appendix,
            "audit": audit
        }
        content_str = json.dumps(out_data, indent=2)
        ext = "json"
    elif fmt == "html":
        content_str = render_html_report(report, appendix)
        ext = "html"
    elif fmt == "markdown":
        # simple markdown export
        md = f"# {report.get('topic', 'Report')}\n\n"
        md += f"**ID:** {report_id}\n\n"
        
        for sec in ["claims", "decisions", "contradictions"]:
            if report.get(sec):
                md += f"## {sec.capitalize()}\n"
                for item in report[sec]:
                    md += f"- {item['excerpt']} [{item['evidence_id']}]\n"
                md += "\n"
                
        if appendix:
            md += "## Citation Appendix\n"
            for cit in appendix:
                md += f"- **[{cit['evidence_id']}]** {cit['project']} / {cit['relative_path']}: {cit['excerpt']}\n"
                
        content_str = md
        ext = "md"
    elif fmt in ["pdf", "docx"]:
        raise NotImplementedError(f"Format {fmt} dependencies missing. Degrading gracefully.")
    else:
        raise ValueError(f"Unknown format {fmt}")
        
    # Safety Check
    safety_fails = check_publication_safety(content_str)
    if safety_fails and not force:
        raise ValueError(f"Safety violations detected: {safety_fails}")
        
    # Write to output
    if not output_dir:
        output_dir = get_publications_dir()
        
    output_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_dir / f"{report_id}.{ext}"
    
    out_file.write_text(content_str, encoding="utf-8")
    return out_file
