import subprocess
import sys
from pathlib import Path

BLOCKED_PATTERNS = [
    "raw/",
    "dist/snapshots/",
    "dist/backups/",
    "evidence-packs/",
    "reports/",
    "data/search.sqlite",
    "data/graph.sqlite",
    ".zurvan/",
    ".env",
    "snapshots/",
    "publications/",
    "dist/publications/",
    ".pdf",
    ".docx"
]

def main():
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to run git ls-files: {e}")
        sys.exit(1)

    tracked = result.stdout.splitlines()
    bad = []
    for path in tracked:
        for pattern in BLOCKED_PATTERNS:
            if pattern.endswith("/"):
                if path.startswith(pattern):
                    if not path.endswith(".gitkeep"):
                        bad.append(path)
            elif pattern.startswith("."):
                if path.endswith(pattern):
                    # Exclude docs/*.pdf
                    if not path.startswith("docs/") and not path.startswith("tests/"):
                        bad.append(path)
            else:
                if path == pattern:
                    bad.append(path)

    if bad:
        print("Public repo guard failed. Remove these tracked private files:")
        for path in bad:
            print(f" - {path}")
        sys.exit(1)

    print("Public repo guard passed.")

if __name__ == "__main__":
    main()
