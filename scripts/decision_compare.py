import re
import datetime
from typing import List, Dict, Any

def _tokenize(text: str) -> set:
    if not text:
        return set()
    text = text.lower()
    return set(re.findall(r'\b\w+\b', text))

def _similarity(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)

def find_similar_decisions(decisions: List[Dict[str, Any]], query: str = None, limit: int = 10) -> List[Dict[str, Any]]:
    if not query:
        return []
    
    query_tokens = _tokenize(query)
    scored = []
    
    for d in decisions:
        content_tokens = _tokenize(d.get("title", "") + " " + d.get("excerpt", ""))
        score = _similarity(query_tokens, content_tokens)
        
        # Boost for tag match
        tag_tokens = set([t.lower() for t in d.get("tags", [])])
        if tag_tokens & query_tokens:
            score += 0.2
            
        if score > 0.0:
            d_copy = d.copy()
            d_copy["score"] = score
            scored.append(d_copy)
            
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]

def find_possible_conflicts(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Group decisions by project is not necessary, we want cross-project comparisons
    conflicts = []
    
    # We heuristically flag if decisions share a lot of keywords but have contrasting statuses
    # or if they talk about similar topics but belong to different projects (suggesting policy diffs).
    
    n = len(decisions)
    for i in range(n):
        for j in range(i + 1, n):
            d1 = decisions[i]
            d2 = decisions[j]
            
            # Skip if same project
            if d1["project"] == d2["project"]:
                continue
                
            t1 = _tokenize(d1.get("title", ""))
            t2 = _tokenize(d2.get("title", ""))
            
            sim = _similarity(t1, t2)
            
            if sim >= 0.4:
                # Potential conflict candidate
                reason = "High title similarity across projects. Check for policy alignment."
                if d1.get("status") != d2.get("status"):
                    reason = f"Different statuses ({d1.get('status')} vs {d2.get('status')}) for similar topics."
                    
                conflicts.append({
                    "decision1": d1,
                    "decision2": d2,
                    "reason": reason
                })
                
    return conflicts

def find_stale_decisions(decisions: List[Dict[str, Any]], days: int = 90) -> List[Dict[str, Any]]:
    stale = []
    now = datetime.datetime.now()
    
    for d in decisions:
        status = d.get("status", "").lower()
        if status in ("rejected", "deprecated", "superseded"):
            continue # Already handled
            
        date_str = d.get("updated_at") or d.get("created_at")
        
        if not date_str:
            d_copy = d.copy()
            d_copy["stale_reason"] = "Missing date information."
            stale.append(d_copy)
            continue
            
        try:
            # Parse ISO date
            dt = datetime.datetime.fromisoformat(date_str)
            age = (now - dt).days
            if age > days or status in ("pending", "proposed"):
                d_copy = d.copy()
                if status in ("pending", "proposed") and age > 30:
                    d_copy["stale_reason"] = f"Still {status} after {age} days."
                    stale.append(d_copy)
                elif age > days:
                    d_copy["stale_reason"] = f"No updates in {age} days (threshold {days})."
                    stale.append(d_copy)
        except ValueError:
            pass # Invalid format
            
    return stale

def find_reusable_patterns(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Look for cross-project tags or extremely common keywords
    tag_counts = {}
    for d in decisions:
        for t in d.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
            
    reusable_tags = {k for k, v in tag_counts.items() if v > 1}
    
    patterns = []
    for d in decisions:
        shared_tags = set(d.get("tags", [])) & reusable_tags
        if shared_tags:
            d_copy = d.copy()
            d_copy["pattern_reason"] = f"Shares tags across projects: {', '.join(shared_tags)}"
            patterns.append(d_copy)
            
    return patterns
