def render_html_report(report: dict, citation_appendix: list) -> str:
    """
    Renders a simple, dependency-free HTML string.
    No CDNs, local anchors only.
    """
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{report.get('topic', 'Zurvan Report')}</title>
<style>
  body {{ font-family: sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; color: #333; line-height: 1.6; }}
  h1 {{ border-bottom: 2px solid #3498db; padding-bottom: 0.5rem; }}
  h2 {{ color: #2c3e50; margin-top: 2rem; }}
  .meta {{ background: #fdfdfd; padding: 1rem; border-left: 4px solid #bdc3c7; margin-bottom: 2rem; font-size: 0.9em; }}
  .citation-link {{ text-decoration: none; color: #3498db; font-size: 0.85em; vertical-align: super; }}
  .appendix {{ margin-top: 4rem; border-top: 2px solid #bdc3c7; padding-top: 2rem; }}
  .appendix-item {{ margin-bottom: 1.5rem; padding: 1rem; background: #f9f9f9; border: 1px solid #ddd; }}
</style>
</head>
<body>
<h1>{report.get('topic', 'Report')}</h1>
<div class="meta">
  <p><strong>Report ID:</strong> {report.get('report_id')}</p>
  <p><strong>Created At:</strong> {report.get('created_at')}</p>
  <p><strong>Template:</strong> {report.get('template')}</p>
</div>
"""
    
    # Claims
    if "claims" in report and report["claims"]:
        html += "<h2>Claims</h2><ul>"
        for c in report["claims"]:
            html += f"<li>{c['excerpt']} <a class='citation-link' href='#cit-{c['evidence_id']}'>[{c['evidence_id']}]</a></li>"
        html += "</ul>"
        
    # Decisions
    if "decisions" in report and report["decisions"]:
        html += "<h2>Decisions</h2><ul>"
        for d in report["decisions"]:
            html += f"<li>{d['excerpt']} <a class='citation-link' href='#cit-{d['evidence_id']}'>[{d['evidence_id']}]</a></li>"
        html += "</ul>"
        
    # Contradictions
    if "contradictions" in report and report["contradictions"]:
        html += "<h2>Contradictions</h2><ul>"
        for c in report["contradictions"]:
            html += f"<li>{c['excerpt']} <a class='citation-link' href='#cit-{c['evidence_id']}'>[{c['evidence_id']}]</a></li>"
        html += "</ul>"
        
    # Appendix
    if citation_appendix:
        html += "<div class='appendix'><h2>Citation Appendix</h2>"
        for cit in citation_appendix:
            html += f"""<div class="appendix-item" id="cit-{cit['evidence_id']}">
                <strong>[{cit['evidence_id']}]</strong> {cit.get('project', 'unknown')} / {cit.get('relative_path', 'unknown')}
                <p><em>"{cit.get('excerpt', '')}"</em></p>
                <small>Hash: {cit.get('content_hash', 'N/A')}</small>
            </div>"""
        html += "</div>"
        
    html += "</body></html>"
    return html
