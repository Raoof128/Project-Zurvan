import json
import subprocess
from scripts.cross_project_search import cross_project_search
from scripts.federation import get_federated_projects

def build_federated_context(query: str, hybrid: bool = False, graph: bool = False, 
                            limit: int = 10, projects: list[str] = None, 
                            strict: bool = False, verbose: bool = False) -> str:
    
    search_data = cross_project_search(query, hybrid, limit, projects, strict, verbose)
    federated = get_federated_projects(projects, strict, verbose)
    
    bundle = ["# Zurvan Federated Context Bundle\n"]
    bundle.append(f"## Query\n{query}\n")
    
    bundle.append("## Projects Searched\n")
    for p in search_data["projects_searched"]:
        bundle.append(f"- {p}")
    bundle.append("\n## Search Matches\n")
    
    # Group search matches by project
    matches_by_project = {}
    for r in search_data["results"]:
        p = r["project"]
        if p not in matches_by_project:
            matches_by_project[p] = []
        matches_by_project[p].append(r)
        
    for p, matches in matches_by_project.items():
        bundle.append(f"### Project: {p}\n")
        for m in matches:
            score = m.get("hybrid_score") or m.get("keyword_score") or 0.0
            bundle.append(f"#### Source: {m['source_path']} (Score: {score:.2f})")
            if m['heading']:
                bundle.append(f"Heading: {m['heading']}")
            bundle.append(f"```markdown\n{m['snippet']}\n```\n")
            
    if graph:
        bundle.append("## Graph-Related Context\n")
        
        # We need to collect graph neighbours for the matched source paths per project.
        for p in federated:
            p_name = p["name"]
            if p_name not in matches_by_project:
                continue
                
            if not p["has_graph"]:
                search_data["warnings"].append(f"Graph index missing for project {p_name}")
                continue
                
            source_paths = list(set([m["source_path"] for m in matches_by_project[p_name]]))
            
            py_code = f"""
import sys
import json
from scripts.graph_context import expand_graph_context
try:
    items = expand_graph_context({json.dumps(source_paths)}, 1)
    print(json.dumps(items))
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
                    search_data["warnings"].append(f"Project {p_name} graph error: {data['error']}")
                elif data:
                    bundle.append(f"### Project: {p_name}\n")
                    for item in data:
                        bundle.append(f"- [{item['depth']}] {item['title']} ({item['node_type']}) - {item['relation']}")
                    bundle.append("")
            except subprocess.CalledProcessError as e:
                search_data["warnings"].append(f"Failed to expand graph for project {p_name}: {e.stderr}")
            except json.JSONDecodeError:
                search_data["warnings"].append(f"Failed to parse graph results from project {p_name}")
                
    if search_data["warnings"]:
        bundle.append("## Warnings\n")
        for w in search_data["warnings"]:
            bundle.append(f"- {w}")
            
    return "\n".join(bundle)
