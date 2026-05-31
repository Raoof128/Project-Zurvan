import os
import sys
import json
import tarfile
import hashlib
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DIR = ROOT / "dist" / "snapshots"

def get_hash(filepath: Path) -> str:
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    return sha256.hexdigest()

def create_snapshot(include_raw: bool = False):
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"zurvan_snapshot_{timestamp}.tar.gz"
    snapshot_path = SNAPSHOTS_DIR / snapshot_name
    manifest_path = SNAPSHOTS_DIR / f"manifest_{timestamp}.json"
    
    manifest = {
        "timestamp": timestamp,
        "files": {},
        "version": "0.8.0-dev",
        "snapshot_name": snapshot_name
    }
    
    include_paths = [
        "wiki",
        "docs",
        "eval",
        "README.md",
        "AGENTS.md",
        "CHANGELOG.md",
        "data/search.sqlite",
        "data/graph.sqlite",
    ]
    
    if include_raw:
        include_paths.append("raw")
        
    def filter_tar(tarinfo):
        # Exclude specific paths
        excl = [".git", ".venv", "__pycache__", "dist"]
        for e in excl:
            if f"/{e}/" in f"/{tarinfo.name}/" or tarinfo.name == e or tarinfo.name.startswith(e + "/"):
                return None
        return tarinfo

    print(f"Creating snapshot {snapshot_name}...")
    
    with tarfile.open(snapshot_path, "w:gz") as tar:
        for p in include_paths:
            full_path = ROOT / p
            if full_path.exists():
                tar.add(full_path, arcname=p, filter=filter_tar)
                
                if full_path.is_file():
                    manifest["files"][p] = get_hash(full_path)
                elif full_path.is_dir():
                    for root, _, files in os.walk(full_path):
                        for f in files:
                            file_path = Path(root) / f
                            rel_path = file_path.relative_to(ROOT)
                            # Exclude check
                            excl = [".git", ".venv", "__pycache__", "dist"]
                            skip = False
                            for e in excl:
                                if e in rel_path.parts:
                                    skip = True
                            if not skip:
                                manifest["files"][str(rel_path)] = get_hash(file_path)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        
    print(f"✅ Snapshot created successfully at {snapshot_path.relative_to(ROOT)}")
    print(f"✅ Manifest created at {manifest_path.relative_to(ROOT)}")
    return str(snapshot_path)

def list_snapshots():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = list(SNAPSHOTS_DIR.glob("zurvan_snapshot_*.tar.gz"))
    
    if not snapshots:
        print("No snapshots found.")
        return
        
    print("Available Snapshots:")
    for s in sorted(snapshots, reverse=True):
        size_mb = s.stat().st_size / (1024 * 1024)
        time_str = datetime.datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"- {s.name} ({size_mb:.2f} MB, {time_str})")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["create", "list"])
    parser.add_argument("--include-raw", action="store_true")
    args = parser.parse_args()
    
    if args.action == "create":
        create_snapshot(args.include_raw)
    elif args.action == "list":
        list_snapshots()
