import os
import sys
import tarfile
import shutil
import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DIR = ROOT / "dist" / "snapshots"
BACKUP_DIR = ROOT / "dist" / "backups"

def safe_extract(tar, path="."):
    for member in tar.getmembers():
        member_path = os.path.normpath(member.name)
        if member_path.startswith('/') or member_path.startswith('..'):
            raise Exception(f"Unsafe path in tar archive: {member.name}")
        if member_path.startswith("raw/") or member_path == "raw":
            raise Exception(f"Attempted to restore into protected raw/ directory: {member.name}")
        tar.extract(member, path=path, filter="data")

def restore_snapshot(snapshot_name: str, force: bool = False):
    snapshot_path = SNAPSHOTS_DIR / snapshot_name
    
    if not snapshot_path.exists():
        print(f"❌ Snapshot not found: {snapshot_path}")
        sys.exit(1)
        
    if not force:
        print("❌ Refusing to restore and overwrite current data. Run with --force to confirm.")
        sys.exit(1)
        
    print("Creating safety backup before restore...")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"safety_backup_{timestamp}.tar.gz"
    
    with tarfile.open(backup_path, "w:gz") as tar:
        for p in ["wiki", "docs", "eval", "data", "AGENTS.md", "CHANGELOG.md"]:
            full_path = ROOT / p
            if full_path.exists():
                tar.add(full_path, arcname=p)
    print(f"✅ Safety backup created at {backup_path.relative_to(ROOT)}")
    
    print(f"Restoring snapshot {snapshot_name}...")
    try:
        with tarfile.open(snapshot_path, "r:gz") as tar:
            members = tar.getmembers()
            safe_extract(tar, path=str(ROOT))
            print(f"✅ Successfully restored {len(members)} entries from snapshot.")
    except Exception as e:
        print(f"❌ Restore failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_name", help="Name of the snapshot in dist/snapshots/")
    parser.add_argument("--force", action="store_true", help="Force overwrite of current data")
    args = parser.parse_args()
    
    restore_snapshot(args.snapshot_name, args.force)
