import os
import re
import hashlib
from pathlib import Path
from scripts.federation import get_federated_projects

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

def _parse_frontmatter(content: str) -> dict:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    
    fm = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm

def _extract_excerpt(content: str) -> str:
    body = FRONTMATTER_RE.sub("", content).strip()
    lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not lines:
        return ""
    excerpt = " ".join(lines[:3])
    if len(excerpt) > 150:
        excerpt = excerpt[:147] + "..."
    return excerpt

def _determine_source_kind(rel_path: str, fm_type: str) -> str:
    if rel_path == "AGENTS.md" or rel_path.lower() == "agent.md":
        return "rule"
    if rel_path == "README.md":
        return "policy"
    if "wiki/decisions/" in rel_path or fm_type == "decision":
        return "decision"
    if "wiki/claims/" in rel_path or fm_type == "claim":
        return "claim"
    if "wiki/contradictions/" in rel_path or fm_type == "contradiction":
        return "contradiction"
    if rel_path.startswith("docs/"):
        return "policy"
    return "note"

def discover_claims_and_policies_in_project(project_name: str, project_path: Path) -> list[dict]:
    items = []
    
    ignore_dirs = {"raw", "dist", "data", ".git", ".venv", "__pycache__"}
    allowed_dirs = {"docs", "wiki"}
    allowed_files = {"AGENTS.md", "README.md", "agent.md", "CHANGELOG.md"}
    
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
            if not file.endswith(".md"):
                continue
                
            file_path = Path(root) / file
            rel_path = file_path.relative_to(project_path).as_posix()
            
            # Filter what we scan
            in_allowed_dir = any(rel_path.startswith(d + "/") for d in allowed_dirs)
            if not in_allowed_dir and file not in allowed_files:
                continue
                
            try:
                content = file_path.read_text(encoding="utf-8")
                fm = _parse_frontmatter(content)
                source_kind = _determine_source_kind(rel_path, fm.get("type"))
                
                # We only want claims, rules, policies, decisions, contradictions. 
                # Skip pure notes unless they have a matching frontmatter.
                if source_kind == "note":
                    continue
                    
                title = fm.get("title") or file_path.stem
                status = fm.get("status")
                
                tags_raw = fm.get("tags")
                tags = []
                if isinstance(tags_raw, str):
                    if tags_raw.startswith("["):
                        tags = [t.strip().strip("'\"") for t in tags_raw[1:-1].split(",")]
                    else:
                        tags = [t.strip() for t in tags_raw.split(",")]
                        
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                excerpt = _extract_excerpt(content)
                
                items.append({
                    "project": project_name,
                    "relative_path": rel_path,
                    "item_type": fm.get("type") or source_kind,
                    "title": title,
                    "status": status,
                    "tags": tags,
                    "content_hash": content_hash,
                    "excerpt": excerpt,
                    "source_kind": source_kind,
                    "full_text": content # Needed for policy extraction, but not persisted globally
                })
            except Exception:
                pass
                
    return items

def collect_federated_claims_and_policies(projects: list[str] = None, strict: bool = False, verbose: bool = False) -> list[dict]:
    federated = get_federated_projects(projects, strict, verbose)
    all_items = []
    
    for p in federated:
        items = discover_claims_and_policies_in_project(p["name"], Path(p["path"]))
        all_items.extend(items)
        
    return all_items
