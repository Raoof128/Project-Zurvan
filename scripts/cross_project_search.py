import os
import subprocess
import sys
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
            
        # Read-only federation: run the search in-process *of the target
        # project* via a subprocess so no cross-project state bleeds. The query
        # is passed as argv — never interpolated into the code — so quotes or
        # code in the query cannot break or inject into the snippet.
        py_code = """
import sys
import json
from scripts.context_export import _search_internal
query = sys.argv[1]
hybrid = sys.argv[2] == "1"
limit = int(sys.argv[3])
try:
    results = _search_internal(query, hybrid, limit)
    output = []
    for r in results:
        output.append({
            "source_path": r.get("source_path"),
            "heading": r.get("heading"),
            "snippet": (r.get("text") or "")[:300],
            "keyword_score": r.get("keyword_score"),
            "semantic_score": r.get("semantic_score"),
            "hybrid_score": r.get("hybrid_score")
        })
    print(json.dumps(output))
except Exception as e:
    print(json.dumps({"error": str(e)}))
"""

        try:
            result = subprocess.run(
                [sys.executable, "-c", py_code, query, "1" if hybrid else "0", str(limit)],
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
