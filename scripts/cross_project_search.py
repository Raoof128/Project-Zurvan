import os
import subprocess
import json
from pathlib import Path
from scripts.federation import get_federated_projects

def cross_project_search(query: str, hybrid: bool = False, limit: int = 10, 
                        projects: list[str] = None, strict: bool = False, 
                        verbose: bool = False) -> dict:
    
    federated = get_federated_projects(projects, strict, verbose)
    all_results = []
    warnings = []
    
    for p in federated:
        if not p["has_search"]:
            msg = f"Search index missing for project {p['name']}. Run: zurvan --project {p['name']} index search"
            warnings.append(msg)
            if verbose:
                print(msg)
            continue
            
        # We can't import search_memory directly because it uses a hardcoded ROOT.
        # Instead, we will run the search CLI command in the project directory,
        # but wait, search_memory doesn't return JSON by default unless we modify it or capture output.
        # Wait, the prompt says "Implement scripts/cross_project_search.py ... run local search against each selected registered project".
        # If we need structured results, maybe we can run a custom python snippet inside the project directory using subprocess?
        # Yes, we can run python -c "..." in the cwd of the project.
        
        py_code = f"""
import sys
import json
from scripts.context_export import _search_internal
try:
    results = _search_internal("{query}", {hybrid}, {limit})
    output = []
    for r in results:
        output.append({{
            "source_path": r.get("source_path"),
            "heading": r.get("heading"),
            "snippet": r.get("snippet"),
            "keyword_score": r.get("keyword_score"),
            "semantic_score": r.get("semantic_score"),
            "hybrid_score": r.get("hybrid_score")
        }})
    print(json.dumps(output))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
        
        try:
            result = subprocess.run(
                ["python", "-c", py_code],
                cwd=p["path"],
                capture_output=True,
                text=True,
                check=True
            )
            data = json.loads(result.stdout.strip())
            if isinstance(data, dict) and "error" in data:
                warnings.append(f"Project {p['name']} search error: {data['error']}")
            else:
                for r in data:
                    r["project"] = p["name"]
                    all_results.append(r)
        except subprocess.CalledProcessError as e:
            warnings.append(f"Failed to search project {p['name']}: {e.stderr}")
        except json.JSONDecodeError:
            warnings.append(f"Failed to parse search results from project {p['name']}")
            
    # Sort all results by hybrid_score if available, else keyword_score
    all_results.sort(key=lambda x: x.get("hybrid_score") or x.get("keyword_score") or 0.0, reverse=True)
    all_results = all_results[:limit]
    
    return {
        "results": all_results,
        "warnings": warnings,
        "projects_searched": [p["name"] for p in federated]
    }
