import hashlib

def generate_citation_appendix(report: dict, evidence_pack: dict) -> list[dict]:
    appendix = []
    
    # gather all evidence items from pack
    items_by_id = {}
    for item in evidence_pack.get("items", []):
        items_by_id[item["id"]] = item
        
    citations = report.get("citations", [])
    
    for cit in citations:
        ev_id = cit.get("evidence_id")
        ev_item = items_by_id.get(ev_id)
        
        if ev_item:
            content = str(ev_item)
            c_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
            
            appendix.append({
                "evidence_id": ev_id,
                "project": ev_item.get("project_name", "unknown"),
                "relative_path": ev_item.get("path", "unknown"),
                "title": ev_item.get("title", ""),
                "excerpt": ev_item.get("excerpt", "") or ev_item.get("content", "")[:200],
                "content_hash": c_hash
            })
        else:
            appendix.append({
                "evidence_id": ev_id,
                "project": "MISSING",
                "relative_path": "MISSING",
                "title": "Missing Evidence Item",
                "excerpt": "This citation mapping references a non-existent evidence item.",
                "content_hash": ""
            })
            
    return appendix
