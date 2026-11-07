"""Post-run sweep: did the candidate leave anything behind outside its scratch? (contract s3.8)

Runs on the broker side after every candidate run. A non-empty result means the
candidate escaped its sandbox or left a process alive, so queries may have leaked.
"""
from __future__ import annotations

import os
import stat
import subprocess

from .codes import SealRefusal
from .paths import SealPaths

DEFAULT_ROOTS = ("/tmp", "/private/tmp", "/private/var/tmp", "/Users/Shared")
FOLDERS_BASE = "/private/var/folders"
_PS = "/bin/ps"


def _sweep_check_uid(uid) -> int:
    if type(uid) is not int or uid < 1 or uid > 2 ** 31 - 1:
        raise SealRefusal("BAD_ARGS")
    return uid


def find_candidate_leftovers(paths: SealPaths, candidate_uid: int, roots=DEFAULT_ROOTS) -> list:
    """Every entry under `roots` owned by candidate_uid. Never follows symlinks (lstat only)."""
    uid = _sweep_check_uid(candidate_uid)
    scratch = os.path.realpath(str(paths.scratch_dir))
    found = set()

    def inside_scratch(path: str) -> bool:
        real = os.path.realpath(path)
        return real == scratch or real.startswith(scratch + os.sep)

    for root in roots:
        if not os.path.isdir(root):
            continue
        for top, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            # prune scratch itself: it is expected to hold candidate files until wipe()
            dirnames[:] = [d for d in dirnames if not inside_scratch(os.path.join(top, d))]
            for name in list(dirnames) + list(filenames):
                full = os.path.join(top, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if st.st_uid == uid:
                    found.add(full)
    return sorted(found)


def sweep_darwin_roots(candidate_uid: int, base: str = FOLDERS_BASE) -> list:
    """The per-user temp/cache trees (DARWIN_USER_TEMP_DIR / DARWIN_USER_CACHE_DIR live under
    <base>/<xx>/<hash>/{T,C,0}) that belong to candidate_uid. The directory name cannot be derived
    from the uid, so find them by owner. Missing base or unreadable entries yield nothing."""
    uid = _sweep_check_uid(candidate_uid)
    found = []
    try:
        level1 = sorted(os.listdir(base))
    except OSError:
        return found
    for first in level1:
        try:
            level2 = sorted(os.listdir(os.path.join(base, first)))
        except OSError:
            continue
        for second in level2:
            full = os.path.join(base, first, second)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if st.st_uid == uid and os.path.isdir(full) and not os.path.islink(full):
                found.append(full)
    return found


def sweep_roots(paths: SealPaths, candidate_uid: int) -> tuple:
    """Ruling R13: the STRICT roots (any candidate-owned entry is a leftover): the contract roots plus the
    candidate's home (find_candidate_leftovers prunes scratch). The candidate's DARWIN temp/cache trees are
    deliberately NOT here: macOS creates and keeps entries there for any process that ever ran as the user,
    so they are baseline-diffed instead (snapshot_darwin / darwin_changes)."""
    _sweep_check_uid(candidate_uid)
    return tuple(DEFAULT_ROOTS) + (str(paths.candidate_home),)


def _sweep_signature(st, opaque: bool = False) -> tuple:
    # (ino, mtime_ns, size, mode, ctime_ns, opaque). A LISTABLE directory's own times/size move when a child is
    # deleted, and a created child is reported as its own new path, so it is identified by inode and mode only.
    # An OPAQUE directory (its contents cannot be listed, e.g. the candidate's 0700 C/ and T/ seen from the eval
    # identity) hides its children, so its own times are the only evidence of a created or removed child: it
    # keeps everything, flagged so it never equals a listable directory's. Everything that is not a listable
    # directory also carries ctime: a user cannot set it, and it moves on content, xattr, chmod and rename, so a
    # same-size rewrite with mtime restored through utime is still seen.
    if stat.S_ISDIR(st.st_mode) and not opaque:
        return (st.st_ino, 0, 0, st.st_mode, 0, False)
    return (st.st_ino, st.st_mtime_ns, st.st_size, st.st_mode, st.st_ctime_ns, opaque)


def snapshot_darwin(candidate_uid: int, base=None) -> dict:
    """path -> signature (see _sweep_signature) for every candidate-owned entry under the candidate's
    darwin per-user roots (the roots themselves included). lstat only, never follows symlinks; an entry that
    vanishes mid-walk is skipped. Any other failure propagates: the caller treats it as a violation."""
    uid = _sweep_check_uid(candidate_uid)
    snap = {}
    opaque = set()

    def unlistable(err):
        # os.walk reports a directory it cannot list; one that merely vanished is skipped as before
        if getattr(err, "filename", None) and not isinstance(err, FileNotFoundError):
            opaque.add(err.filename)

    for root in sweep_darwin_roots(uid, FOLDERS_BASE if base is None else base):
        try:
            st = os.lstat(root)
        except OSError:
            continue
        if st.st_uid == uid:
            snap[root] = _sweep_signature(st)
        for top, dirnames, filenames in os.walk(root, topdown=True, onerror=unlistable, followlinks=False):
            for name in list(dirnames) + list(filenames):
                full = os.path.join(top, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if st.st_uid == uid:
                    snap[full] = _sweep_signature(st)
    for path in opaque:
        # each directory os.walk could not list is re-signed in full (lstat only; vanished -> skipped)
        try:
            st = os.lstat(path)
        except OSError:
            snap.pop(path, None)
            continue
        if st.st_uid == uid and stat.S_ISDIR(st.st_mode):
            snap[path] = _sweep_signature(st, opaque=True)
    return snap


def darwin_changes(candidate_uid: int, baseline: dict, base=None) -> list:
    """Paths under the darwin roots that are new, or whose signature differs from `baseline`. A path that has
    disappeared is not reported. A root that did not exist at baseline contributes all its entries."""
    if type(baseline) is not dict:
        raise SealRefusal("BAD_ARGS")
    now = snapshot_darwin(candidate_uid, base)
    return sorted(path for path, sig in now.items() if baseline.get(path) != sig)


def _sweep_default_runner(cmd):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    res = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=30)
    return res.returncode, res.stdout.decode("ascii", "replace")


def candidate_processes(candidate_uid: int, runner=None) -> int:
    """How many processes the candidate uid still owns (via /bin/ps -U).

    Ruling R13: an unparsable or failed ps is SANDBOX_VIOLATION (assume the worst: the caller burns
    every item it sent), not INTERNAL. A bad uid is still BAD_ARGS.
    """
    uid = _sweep_check_uid(candidate_uid)
    runner = runner or _sweep_default_runner
    try:
        rc, out = runner([_PS, "-U", str(uid), "-o", "pid="])
    except Exception:  # noqa: BLE001
        raise SealRefusal("SANDBOX_VIOLATION") from None
    if type(out) is not str:
        raise SealRefusal("SANDBOX_VIOLATION")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # ps exits 1 with no output when nothing matches; any other rc, or rc 1 WITH output, is not understood
    if rc not in (0, 1) or (rc == 1 and lines):
        raise SealRefusal("SANDBOX_VIOLATION")
    for ln in lines:
        if not (ln.isascii() and ln.isdigit()):
            raise SealRefusal("SANDBOX_VIOLATION")
    return len(lines)
