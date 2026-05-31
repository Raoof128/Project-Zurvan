import sys
from pathlib import Path

def print_version():
    root = Path(__file__).resolve().parents[1]
    
    version = "0.8.0-dev (Phase 8 MVP)"
    python_version = sys.version.split(" ")[0]
    
    print(f"Zurvan Version: {version}")
    print(f"Python Version: {python_version}")
    print(f"Project Root:   {root}")
    print("\nEnabled Major Features:")
    print("  ✅ CLI Memory Interface")
    print("  ✅ Local Hybrid Search")
    print("  ✅ Knowledge Graph Lite")
    print("  ✅ Local MCP Server")
    print("  ✅ Obsidian Integration Pack")
    print("  ✅ Snapshots & Release Packaging")

if __name__ == "__main__":
    print_version()
