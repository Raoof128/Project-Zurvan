import os
import json
import sqlite3
import hashlib
import re
from pathlib import Path
from datetime import datetime

# Regex for frontmatter
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

def _get_cache_db_path() -> Path:
    config_dir = os.environ.get("ZURVAN_CONFIG_DIR", os.path.expanduser("~/.zurvan"))
    cache_dir = Path(config_dir) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "decision_memory.sqlite"

def init_decision_cache():
    db_path = _get_cache_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                project TEXT,
                relative_path TEXT,
                title TEXT,
                status TEXT,
                tags TEXT,
                created_at TEXT,
                updated_at TEXT,
                content_hash TEXT,
                excerpt TEXT,
                full_text TEXT
            )
        """)

def _parse_frontmatter(content: str) -> dict:
    match = FRONTMATTER_RE.match(content)
    if not match:
        return {}
    
    fm_str = match.group(1)
    fm = {}
    for line in fm_str.split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("'\"")
    return fm

def _extract_excerpt(content: str) -> str:
    # Remove frontmatter
    body = FRONTMATTER_RE.sub("", content).strip()
    # Remove empty lines
    lines = [line.strip() for line in body.split("\n") if line.strip()]
    if not lines:
        return ""
    # Take first 3 non-empty lines, limit characters
    excerpt = " ".join(lines[:3])
    if len(excerpt) > 150:
        excerpt = excerpt[:147] + "..."
    return excerpt

def discover_decisions_in_project(project_name: str, project_path: Path) -> list[dict]:
    decisions = []
    
    # Ignore dirs
    ignore_dirs = {"raw", "dist", "data", ".git"}
    
    for root, dirs, files in os.walk(project_path):
        # Mutate dirs to skip ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        for file in files:
            if not file.endswith(".md"):
                continue
                
            file_path = Path(root) / file
            rel_path = file_path.relative_to(project_path).as_posix()
            
            try:
                content = file_path.read_text(encoding="utf-8")
                fm = _parse_frontmatter(content)
                
                is_decision = False
                if "wiki/decisions/" in rel_path:
                    is_decision = True
                elif fm.get("type") == "decision":
                    is_decision = True
                    
                if not is_decision:
                    continue
                
                # Use git or file stat for dates if missing from frontmatter?
                # For this, just use frontmatter or empty string as fallback
                stat = file_path.stat()
                created_at = fm.get("date") or datetime.fromtimestamp(stat.st_ctime).isoformat()
                updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
                title = fm.get("title") or file_path.stem
                status = fm.get("status") or "unknown"
                
                # Tags could be list or comma separated string
                tags_raw = fm.get("tags")
                tags = []
                if isinstance(tags_raw, str):
                    if tags_raw.startswith("["):
                        tags = [t.strip().strip("'\"") for t in tags_raw[1:-1].split(",")]
                    else:
                        tags = [t.strip() for t in tags_raw.split(",")]
                        
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                excerpt = _extract_excerpt(content)
                
                # Unique ID
                doc_id = f"{project_name}:{rel_path}"
                
                decisions.append({
                    "id": doc_id,
                    "project": project_name,
                    "relative_path": rel_path,
                    "title": title,
                    "status": status,
                    "tags": tags,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "content_hash": content_hash,
                    "excerpt": excerpt,
                    "full_text": content if os.environ.get("ZURVAN_DECISION_CACHE_FULL_TEXT") == "1" else ""
                })
            except Exception:
                pass # Skip unreadable files
                
    return decisions

def cache_decisions(decisions: list[dict]):
    db_path = _get_cache_db_path()
    with sqlite3.connect(db_path) as conn:
        for d in decisions:
            conn.execute("""
                INSERT OR REPLACE INTO decisions 
                (id, project, relative_path, title, status, tags, created_at, updated_at, content_hash, excerpt, full_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], d["project"], d["relative_path"], d["title"], d["status"],
                json.dumps(d["tags"]), d["created_at"], d["updated_at"],
                d["content_hash"], d["excerpt"], d["full_text"]
            ))
            
def load_all_cached_decisions() -> list[dict]:
    db_path = _get_cache_db_path()
    if not db_path.exists():
        return []
        
    decisions = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM decisions")
        for row in cursor.fetchall():
            d = dict(row)
            d["tags"] = json.loads(d["tags"])
            decisions.append(d)
    return decisions
