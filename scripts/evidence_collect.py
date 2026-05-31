import os
import hashlib
from datetime import datetime, timezone
from scripts.cross_project_search import cross_project_search
from scripts.cross_project_search import cross_project_search
from scripts.claim_federation import collect_federated_claims_and_policies
from scripts.contradiction_radar import detect_contradictions
from scripts.decision_federation import rebuild_decision_memory, collect_federated_decisions
from scripts.federation import get_federated_projects

def _create_evidence_item(project: str, rel_path: str, item_type: str, title: str, excerpt: str, extra: dict = None) -> dict:
    if extra is None:
        extra = {}
        
    content_hash = hashlib.sha256(f"{project}:{rel_path}:{excerpt}".encode("utf-8")).hexdigest()
    
    item = {
        "evidence_id": f"ev-{content_hash[:8]}",
        "project": project,
        "source_path": rel_path,
        "item_type": item_type,
        "title": title,
        "excerpt": excerpt,
        "content_hash": content_hash,
        "collected_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Merge optional fields
    for k in ["quote", "claim_text", "status", "tags", "confidence", "reason", "matched_terms", "source_kind"]:
        if k in extra:
            item[k] = extra[k]
            
    return item

def collect_evidence(topic: str, projects: list[str] = None, hybrid: bool = False, 
                     graph: bool = False, include_decisions: bool = False, 
                     include_policy_radar: bool = False, limit: int = 20) -> list[dict]:
                     
    evidence = []
    
    # 1. Search / Context
    search_res = cross_project_search(topic, hybrid, limit, projects, False, False)
    # search_res has "results" list
    matches_by_proj = {}
    for sr in search_res.get("results", []):
        proj = sr["project"]
        if proj not in matches_by_proj:
            matches_by_proj[proj] = []
        matches_by_proj[proj].append(sr)
        
        # Add search evidence
        evidence.append(_create_evidence_item(
            proj, sr.get("relative_path", sr.get("source_path", "")), "search_result", 
            sr.get("heading", sr.get("relative_path", "Untitled")), 
            sr.get("snippet", ""), {"tags": sr.get("tags", [])}
        ))
        
    if graph:
        federated = get_federated_projects(projects, False, False)
        for p in federated:
            p_name = p["name"]
            if p_name not in matches_by_proj or not p["has_graph"]:
                continue
            
            source_paths = list(set([m.get("source_path", m.get("relative_path", "")) for m in matches_by_proj[p_name]]))
            
            import subprocess, json
            py_code = f"""
import sys, json
from scripts.graph_context import expand_graph_context
try:
    print(json.dumps(expand_graph_context({json.dumps(source_paths)}, 1)))
except Exception:
    print("[]")
"""
            try:
                res = subprocess.run(["python", "-c", py_code], cwd=p["path"], capture_output=True, text=True)
                data = json.loads(res.stdout.strip())
                if isinstance(data, list):
                    for gn in data:
                        evidence.append(_create_evidence_item(
                            p_name, gn.get("path", ""), "graph_neighbor", gn.get("title", gn.get("name", "")), 
                            gn.get("snippet", gn.get("excerpt", "")), {"source_kind": gn.get("node_type", "node")}
                        ))
            except Exception:
                pass
                
                
    # 2. Claims / Policies directly (if matching topic roughly, or all if we want. We'll just fetch all and filter)
    radar_items = collect_federated_claims_and_policies(projects)
    topic_lower = topic.lower()
    
    for item in radar_items:
        # Simple heuristic filter
        text = (item.get("title", "") + " " + item.get("excerpt", "")).lower()
        if topic_lower in text:
            evidence.append(_create_evidence_item(
                item["project"], item["relative_path"], item["item_type"], item["title"], item["excerpt"], 
                {"status": item.get("status"), "tags": item.get("tags"), "source_kind": item["source_kind"]}
            ))
            
    # 3. Policy Radar Contradictions
    if include_policy_radar:
        conflicts = detect_contradictions(radar_items)
        for i, c in enumerate(conflicts):
            # Only include if it matches topic roughly, or if we just dump all conflicts
            text = (c.get("category", "") + " " + c.get("reason", "")).lower()
            if topic_lower in text or not topic_lower:
                for idx, proj in enumerate(c["projects"]):
                    path = c["paths"][idx] if idx < len(c["paths"]) else ""
                    excerpt = c["excerpts"][idx] if idx < len(c["excerpts"]) else ""
                    evidence.append(_create_evidence_item(
                        proj, path, "radar_ping", f"Conflict Candidate: {c['category']}", excerpt,
                        {"confidence": c["confidence"], "reason": c["reason"], "matched_terms": c.get("matched_terms")}
                    ))
                    
    # 4. Decisions
    if include_decisions:
        # Get decisions from cache
        decs = collect_federated_decisions(projects)
        for d in decs:
            text = (d.get("title", "") + " " + d.get("excerpt", "")).lower()
            if topic_lower in text:
                evidence.append(_create_evidence_item(
                    d["project"], d["relative_path"], "decision", d["title"], d.get("excerpt", ""),
                    {"status": d.get("status"), "tags": d.get("tags")}
                ))
                
    # Deduplicate by content hash
    seen = set()
    deduped = []
    for e in evidence:
        if e["content_hash"] not in seen:
            seen.add(e["content_hash"])
            deduped.append(e)
            
    return deduped
