import os
from pathlib import Path
from scripts.project_registry import load_registry
from scripts.workspace import is_valid_zurvan_project, shorten_path

def get_federated_projects(selected_projects: list[str] = None, strict: bool = False, verbose: bool = False) -> list[dict]:
    registry = load_registry()
    projects = []
    
    for name, data in registry.get("projects", {}).items():
        if selected_projects and name not in selected_projects:
            continue
            
        path = data["path"]
        is_valid = is_valid_zurvan_project(path)
        
        display_path = path if verbose else shorten_path(path)
        
        if not is_valid:
            if strict:
                raise ValueError(f"Project '{name}' at {display_path} is invalid or missing.")
            else:
                if verbose:
                    print(f"Warning: Skipping invalid project '{name}' at {display_path}")
                continue
                
        projects.append({
            "name": name,
            "path": path,
            "display_path": display_path,
            "has_search": (Path(path) / "data" / "search.sqlite").exists(),
            "has_graph": (Path(path) / "data" / "graph.sqlite").exists()
        })
        
    return projects

def get_federation_stats(verbose: bool = False) -> dict:
    registry = load_registry()
    total = len(registry.get("projects", {}))
    healthy = 0
    projects_info = []
    
    for name, data in registry.get("projects", {}).items():
        path = data["path"]
        is_valid = is_valid_zurvan_project(path)
        display_path = path if verbose else shorten_path(path)
        
        has_search = (Path(path) / "data" / "search.sqlite").exists()
        has_graph = (Path(path) / "data" / "graph.sqlite").exists()
        
        if is_valid:
            healthy += 1
            
        projects_info.append({
            "name": name,
            "display_path": display_path,
            "is_valid": is_valid,
            "has_search": has_search,
            "has_graph": has_graph
        })
        
    return {
        "total": total,
        "healthy": healthy,
        "projects": projects_info
    }

def run_federation_doctor(strict: bool = False, verbose: bool = False) -> bool:
    stats = get_federation_stats(verbose)
    all_healthy = True
    
    print(f"Federation Doctor: checking {stats['total']} registered projects.")
    
    for p in stats["projects"]:
        status = "✅" if p["is_valid"] else "❌"
        print(f"{status} Project '{p['name']}' at {p['display_path']}")
        
        if p["is_valid"]:
            search_status = "✅" if p["has_search"] else "⚠️ Missing"
            graph_status = "✅" if p["has_graph"] else "⚠️ Missing"
            print(f"   Search Index: {search_status}")
            print(f"   Graph Index:  {graph_status}")
        else:
            all_healthy = False
            print("   WARNING: Project is missing or invalid Zurvan structure.")
            
    if strict and not all_healthy:
        return False
        
    return True
