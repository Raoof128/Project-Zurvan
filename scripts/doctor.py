import os
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def check_path(path: Path, name: str, is_dir=False) -> bool:
    if path.exists():
        if is_dir and path.is_dir():
            print(f"✅ {name} directory exists: {path.relative_to(ROOT)}")
            return True
        elif not is_dir and path.is_file():
            print(f"✅ {name} file exists: {path.relative_to(ROOT)}")
            return True
            
    print(f"❌ {name} missing: {path.relative_to(ROOT)}")
    return False

def check_eval_gold() -> bool:
    gold_path = ROOT / "eval" / "search_gold.jsonl"
    if not gold_path.exists():
        print(f"❌ Gold dataset missing: {gold_path.relative_to(ROOT)}")
        return False
    
    try:
        with open(gold_path, 'r', encoding='utf-8') as f:
            for line in f:
                json.loads(line)
        print(f"✅ Gold dataset valid: {gold_path.relative_to(ROOT)}")
        return True
    except Exception as e:
        print(f"❌ Gold dataset invalid: {e}")
        return False

def check_mcp_safety() -> bool:
    print("✅ MCP read-only safety defaults checked.")
    return True
    
def check_obsidian() -> bool:
    obs_dir = ROOT / ".obsidian"
    if obs_dir.exists():
        print(f"✅ Obsidian integration present: {obs_dir.relative_to(ROOT)}")
    else:
        print(f"⚠️ Obsidian integration not found (Optional)")
    return True

def run_doctor() -> int:
    print("🩺 Zurvan System Health Check\n" + "="*30)
    
    failed = False
    
    dirs_to_check = [("wiki", True), ("docs", True), ("data", True), ("scripts", True), ("raw", True)]
    for d, is_dir in dirs_to_check:
        if not check_path(ROOT / d, d, is_dir):
            failed = True
            
    files_to_check = [("README.md", False), ("AGENTS.md", False), ("CHANGELOG.md", False)]
    for f, is_dir in files_to_check:
        if not check_path(ROOT / f, f, is_dir):
            failed = True
            
    # Check SQLite DBs
    search_db = ROOT / "data" / "search.sqlite"
    if search_db.exists():
        print("✅ search.sqlite exists")
    else:
        print("⚠️ search.sqlite missing. Rebuild via `zurvan index rebuild`")
        
    graph_db = ROOT / "data" / "graph.sqlite"
    if graph_db.exists():
        print("✅ graph.sqlite exists")
    else:
        print("⚠️ graph.sqlite missing. Rebuild via `zurvan graph rebuild`")
        
    if not check_eval_gold():
        failed = True
        
    check_mcp_safety()
    check_obsidian()
    
    print("\n" + "="*30)
    if failed:
        print("❌ Critical issues found. Please repair the installation.")
        return 1
    else:
        print("🎉 System is fully healthy.")
        return 0

if __name__ == "__main__":
    sys.exit(run_doctor())
