"""Git plumbing for the commit gate: staged post-images and sink labelling.

Scanning diff hunks is unsafe and this module exists to avoid it. A protected
span can be assembled across a changed/unchanged boundary, split across two
hunks, or moved by a rename that produces no textual diff at all. The gate
therefore scans the COMPLETE staged post-image of every changed path.
"""
import subprocess
from pathlib import Path

from scripts.chunk import EXCLUDED_PATH_PREFIXES, ROOT_INDEX_FILES

# Paths the indexer reads. A file here is a RETRIEVAL_CORPUS member as well as a
# PUBLIC_ARTIFACT when tracked — labels are non-exclusive and deny overrides
# allow, so both must be reported or the stricter rule never fires.
INDEXED_PREFIXES = ("wiki/", "docs/")


def _git(repo, *args) -> bytes:
    return subprocess.run(["git", *args], cwd=str(repo), check=True,
                          stdout=subprocess.PIPE).stdout


def sinks_for(path: str):
    """Every sink label that applies to a repository path."""
    labels = ["PUBLIC_ARTIFACT"]
    posix = path.replace("\\", "/")
    excluded = any(posix == p or posix.startswith(p + "/") for p in EXCLUDED_PATH_PREFIXES)
    indexed = (any(posix.startswith(p) for p in INDEXED_PREFIXES) or posix in ROOT_INDEX_FILES)
    if indexed and not excluded:
        labels.append("RETRIEVAL_CORPUS")
    return labels


def staged_blobs(repo):
    """[(path, change_type, post_image_bytes)] for everything staged.

    Deletions are skipped: they have no post image and cannot move bytes INTO a
    sink. Renames are included under their NEW path with full content, which is
    the case a diff-driven scanner misses entirely.
    """
    out = _git(repo, "diff", "--cached", "--name-status", "-M", "-z").decode("utf-8")
    fields = [f for f in out.split("\0") if f != ""]
    results, i = [], 0
    while i < len(fields):
        status = fields[i]
        code = status[0]
        if code in ("R", "C"):                 # rename/copy: old NUL new
            path = fields[i + 2]
            i += 3
        else:
            path = fields[i + 1]
            i += 2
        if code == "D":
            continue
        try:
            data = _git(repo, "show", f":{path}")
        except subprocess.CalledProcessError:
            continue                            # not in the index; nothing to scan
        results.append((path, {"R": "rename", "C": "copy", "A": "add"}.get(code, "modify"), data))
    return results


def scan_staged(boundary_root, repo=None):
    """Scan every staged post-image against the sinks its path belongs to.

    Returns [(path, change_type, Verdict)] for BLOCKED paths only.
    """
    from scripts import boundary
    repo = Path(repo or ".")
    blocked = []
    for path, change, data in staged_blobs(repo):
        verdict = boundary.scan_blob(boundary_root, path, data, sink=sinks_for(path),
                                     change_type=change)
        if verdict.blocked:
            blocked.append((path, change, verdict))
    return blocked
