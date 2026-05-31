import subprocess
import sys

BLOCKED_PATTERNS = [
    "raw/",
    "dist/snapshots/",
    "dist/backups/",
    "data/search.sqlite",
    "data/graph.sqlite",
    ".zurvan/",
    ".env",
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
    bad = [
        path for path in tracked
        if any(path.startswith(pattern) or path == pattern for pattern in BLOCKED_PATTERNS)
        and not path.endswith(".gitkeep")
    ]

    if bad:
        print("Public repo guard failed. Remove these tracked private files:")
        for path in bad:
            print(f" - {path}")
        sys.exit(1)

    print("Public repo guard passed.")

if __name__ == "__main__":
    main()
