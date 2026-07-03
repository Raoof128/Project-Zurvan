import os
from pathlib import Path
from scripts.federation import get_federated_projects


def _relative_source_path(source_path: str, project_root: str) -> str:
    """Keyword search returns absolute paths under the project root; federated
    output stays project-relative so no machine-specific paths leak."""
    if source_path and os.path.isabs(source_path):
        try:
            return os.path.relpath(source_path, project_root)
        except ValueError:
            return source_path
    return source_path


def cross_project_search(query: str, hybrid: bool = False, limit: int = 10,
                        projects: list[str] = None, strict: bool = False,
                        verbose: bool = False) -> dict:

    federated = get_federated_projects(projects, strict, verbose)
    all_results = []
    warnings = []

    # Read-only federation, run in-process: Zurvan's own retriever is pointed
    # at each project's root. Previously this spawned `python -c "from
    # scripts...."` inside the target repo, which required every registered
    # project to embed the entire Zurvan engine; knowledge-only projects
    # (wiki/ + docs/) are now first-class citizens.
    from scripts.context_export import _search_internal

    for p in federated:
        if hybrid and not p["has_search"]:
            msg = (f"Search index missing for project {p['name']} (hybrid needs it). "
                   f"Run: zurvan --project {p['name']} index search")
            warnings.append(msg)
            if verbose:
                print(msg)
            continue

        try:
            results = _search_internal(query, hybrid, limit, root=p["path"])
        except Exception as e:
            warnings.append(f"Project {p['name']} search error: {e}")
            continue

        for r in results:
            all_results.append({
                "source_path": _relative_source_path(str(r.get("source_path", "")), p["path"]),
                "heading": r.get("heading"),
                "snippet": (r.get("text") or "")[:300],
                "keyword_score": r.get("keyword_score"),
                "semantic_score": r.get("semantic_score"),
                "hybrid_score": r.get("hybrid_score"),
                "project": p["name"],
            })

    # Sort all results by hybrid_score if available, else keyword_score
    all_results.sort(key=lambda x: x.get("hybrid_score") or x.get("keyword_score") or 0.0, reverse=True)
    all_results = all_results[:limit]

    return {
        "results": all_results,
        "warnings": warnings,
        "projects_searched": [p["name"] for p in federated]
    }
