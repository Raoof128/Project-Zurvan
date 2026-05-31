from pathlib import Path
from typing import List, Dict, Any
from scripts.federation import get_federated_projects
from scripts.decision_memory import discover_decisions_in_project, cache_decisions, init_decision_cache

def rebuild_decision_memory(projects: list[str] = None, strict: bool = False, verbose: bool = False):
    federated = get_federated_projects(projects, strict, verbose)
    
    all_decisions = []
    for p in federated:
        decisions = discover_decisions_in_project(p["name"], Path(p["path"]))
        all_decisions.extend(decisions)
        
    init_decision_cache()
    cache_decisions(all_decisions)
    return len(all_decisions)

def collect_federated_decisions(projects: list[str] = None, strict: bool = False, verbose: bool = False) -> List[Dict[str, Any]]:
    # We always discover fresh in runtime unless explicitly using cached data
    federated = get_federated_projects(projects, strict, verbose)
    
    all_decisions = []
    for p in federated:
        decisions = discover_decisions_in_project(p["name"], Path(p["path"]))
        all_decisions.extend(decisions)
        
    return all_decisions

def format_decisions_all(decisions: List[Dict[str, Any]]) -> str:
    bundle = ["# Federated Decision Memory Report\n", "## Decisions Found\n"]
    
    by_project = {}
    for d in decisions:
        p = d["project"]
        if p not in by_project:
            by_project[p] = []
        by_project[p].append(d)
        
    for p, ds in by_project.items():
        bundle.append(f"### Project: {p}")
        for d in ds:
            tags = ", ".join(d["tags"])
            bundle.append(f"- **{d['title']}** ({d['status']}) [{tags}] - `{d['relative_path']}`")
        bundle.append("")
        
    return "\n".join(bundle)

def format_similar_decisions(decisions: List[Dict[str, Any]], query: str, limit: int = 10) -> str:
    from scripts.decision_compare import find_similar_decisions
    similar = find_similar_decisions(decisions, query, limit)
    
    bundle = ["# Federated Decision Memory Report\n", f"## Similar Decision Candidates for '{query}'\n"]
    for d in similar:
        bundle.append(f"- **{d['title']}** (Score: {d['score']:.2f}) - {d['project']} `{d['relative_path']}`")
        
    return "\n".join(bundle)

def format_decision_conflicts(decisions: List[Dict[str, Any]]) -> str:
    from scripts.decision_compare import find_possible_conflicts
    conflicts = find_possible_conflicts(decisions)
    
    bundle = ["# Federated Decision Memory Report\n", "## Possible Conflict Candidates\n"]
    bundle.append("*Note: Heuristic detection only.*\n")
    
    for c in conflicts:
        d1 = c["decision1"]
        d2 = c["decision2"]
        bundle.append(f"### {d1['project']} vs {d2['project']}")
        bundle.append(f"- Reason: {c['reason']}")
        bundle.append(f"- {d1['project']}: **{d1['title']}** ({d1['status']})")
        bundle.append(f"- {d2['project']}: **{d2['title']}** ({d2['status']})\n")
        
    return "\n".join(bundle)

def format_stale_decisions(decisions: List[Dict[str, Any]], days: int = 90) -> str:
    from scripts.decision_compare import find_stale_decisions
    stale = find_stale_decisions(decisions, days)
    
    bundle = ["# Federated Decision Memory Report\n", "## Stale Decisions\n"]
    for d in stale:
        bundle.append(f"- **{d['title']}** ({d['status']}) - {d['project']} `{d['relative_path']}`")
        bundle.append(f"  - Reason: {d['stale_reason']}")
        
    return "\n".join(bundle)
