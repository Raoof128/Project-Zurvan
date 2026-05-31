import re
from typing import List, Dict, Any
from scripts.policy_rules import identify_policies

def _tokenize(text: str) -> set:
    if not text:
        return set()
    return set(re.findall(r'\b\w+\b', text.lower()))

def _similarity(set_a: set, set_b: set) -> float:
    if not set_a and not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)

def detect_contradictions(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts = []
    
    # 1. Detect policy rule clashes
    # Map each item to its policy category matches
    item_policies = []
    for item in items:
        # Check title + full text
        text = item.get("title", "") + "\n" + item.get("full_text", "")
        pols = identify_policies(text)
        item_policies.append({"item": item, "policies": pols})
        
        # Self-contradiction in single item
        for cat, match in pols.items():
            if match["status"] == "conflict":
                conflicts.append({
                    "projects": [item["project"]],
                    "paths": [item["relative_path"]],
                    "category": cat,
                    "confidence": "high",
                    "reason": f"Item contains both positive and negative keywords for {cat}.",
                    "matched_terms": match,
                    "excerpts": [item["excerpt"]]
                })
                
    # Cross-item policy clashes
    n = len(item_policies)
    for i in range(n):
        for j in range(i + 1, n):
            ip1 = item_policies[i]
            ip2 = item_policies[j]
            
            p1 = ip1["policies"]
            p2 = ip2["policies"]
            
            common_cats = set(p1.keys()) & set(p2.keys())
            for cat in common_cats:
                s1 = p1[cat]["status"]
                s2 = p2[cat]["status"]
                
                if (s1 == "positive" and s2 == "negative") or (s1 == "negative" and s2 == "positive"):
                    conflicts.append({
                        "projects": [ip1["item"]["project"], ip2["item"]["project"]],
                        "paths": [ip1["item"]["relative_path"], ip2["item"]["relative_path"]],
                        "category": cat,
                        "confidence": "high",
                        "reason": f"Opposing stances on policy '{cat}'.",
                        "matched_terms": {"item1": p1[cat], "item2": p2[cat]},
                        "excerpts": [ip1["item"]["excerpt"], ip2["item"]["excerpt"]]
                    })
                    
            # 2. Heuristic word overlap but different statuses for decisions/claims
            it1 = ip1["item"]
            it2 = ip2["item"]
            
            # Only compare if they are decisions or claims
            if it1["source_kind"] in ("decision", "claim") and it2["source_kind"] in ("decision", "claim"):
                t1 = _tokenize(it1["title"])
                t2 = _tokenize(it2["title"])
                sim = _similarity(t1, t2)
                
                s1 = it1.get("status")
                s2 = it2.get("status")
                
                # If they are very similar but have different statuses, it's a conflict candidate
                if sim >= 0.5 and s1 and s2 and s1 != s2:
                    conflicts.append({
                        "projects": [it1["project"], it2["project"]],
                        "paths": [it1["relative_path"], it2["relative_path"]],
                        "category": "heuristic_similarity",
                        "confidence": "medium",
                        "reason": f"High title similarity ({sim:.2f}) but conflicting statuses ({s1} vs {s2}).",
                        "matched_terms": list(t1 & t2),
                        "excerpts": [it1["excerpt"], it2["excerpt"]]
                    })

    return conflicts

def format_contradictions(conflicts: List[Dict[str, Any]]) -> str:
    bundle = ["# Zurvan Policy Radar Report\n", "## Possible Contradiction Candidates\n"]
    bundle.append("*Note: Detection is heuristic. These are candidates, not guaranteed contradictions.*\n")
    
    if not conflicts:
        bundle.append("No contradiction candidates detected.")
        return "\n".join(bundle)
        
    for i, c in enumerate(conflicts):
        projs = " vs ".join(set(c["projects"]))
        bundle.append(f"### Candidate {i+1}: {projs} ({c['category']})")
        bundle.append(f"- **Confidence**: {c['confidence']}")
        bundle.append(f"- **Reason**: {c['reason']}")
        bundle.append(f"- **Paths**: {', '.join(c['paths'])}")
        bundle.append("")
        
    return "\n".join(bundle)
