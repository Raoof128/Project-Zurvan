"""Post-run sweep: did the candidate leave anything behind outside its scratch? (contract s3.8)

Runs on the broker side after every candidate run. A non-empty result means the
candidate escaped its sandbox or left a process alive, so queries may have leaked.
"""
from __future__ import annotations

import errno
import json
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


def _sweep_has_acl(path: str) -> bool:
    """True when `path` carries an extended (NFSv4-style) ACL, or when that cannot be determined (fail closed: an
    ACL can grant another identity access that the mode bits do not show)."""
    import ctypes
    import ctypes.util
    try:
        os.lstat(path)            # acl_get_link_np reports ENOENT for "no ACL" AND for "no such path"
    except OSError:
        return True
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.acl_get_link_np.restype = ctypes.c_void_p
        libc.acl_get_link_np.argtypes = (ctypes.c_char_p, ctypes.c_int)
        acl = libc.acl_get_link_np(os.fsencode(path), 0x00000100)          # ACL_TYPE_EXTENDED
    except (OSError, AttributeError, ValueError):
        return True
    if not acl:
        return ctypes.get_errno() != errno.ENOENT                           # ENOENT = no ACL; anything else: unknown
    try:
        libc.acl_free(ctypes.c_void_p(acl))
    except (OSError, AttributeError):
        pass
    return True


def _sweep_private_dir(path: str, st) -> bool:
    """A directory no other identity can enter: no group/other execute bit and no ACL. Its CONTENTS cannot reach
    the development identity (the OS permission is the wall the whole seal rests on), so only its identity and mode
    are tracked -- a chmod or an added ACL that would open it is still a change."""
    return stat.S_ISDIR(st.st_mode) and not st.st_mode & 0o011 and not _sweep_has_acl(path)


def _sweep_private_signature(st) -> tuple:
    return (st.st_ino, 0, 0, st.st_mode, 0, True)


def snapshot_darwin(candidate_uid: int, base=None) -> dict:
    """path -> signature for every candidate-owned entry under the candidate's darwin per-user roots (the roots
    themselves included). lstat only, never follows symlinks; an entry that vanishes mid-walk, or that the kernel
    refuses even to lstat (an Apple data vault, e.g. 0/com.apple.LaunchServices.dv), is skipped.

    Option A (Raouf, 2026-11-08, after gate 4 failed three times on macOS's own per-user services -- Spotlight
    importers, lsd, distnoted -- writing there as the candidate): a PRIVATE directory (_sweep_private_dir: T/ and
    C/ are 0700) is recorded by identity and mode only and not descended. Everything another identity can enter
    keeps the full content signature, including a directory its owner cannot list but others can traverse."""
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
            if _sweep_private_dir(root, st):
                snap[root] = _sweep_private_signature(st)
                continue
            snap[root] = _sweep_signature(st)
        for top, dirnames, filenames in os.walk(root, topdown=True, onerror=unlistable, followlinks=False):
            keep = []
            for name in list(dirnames) + list(filenames):
                full = os.path.join(top, name)
                try:
                    st = os.lstat(full)
                except OSError:
                    continue
                if st.st_uid != uid:
                    if name in dirnames:
                        keep.append(name)
                    continue
                if _sweep_private_dir(full, st):
                    snap[full] = _sweep_private_signature(st)
                    continue                                  # never descended: its contents are not tracked
                snap[full] = _sweep_signature(st)
                if name in dirnames:
                    keep.append(name)
            dirnames[:] = keep
    for path in opaque:
        # each directory os.walk could not list is re-signed (lstat only; vanished -> skipped): in full when other
        # identities can traverse it (its own times are the only evidence of a child), else as a private directory
        try:
            st = os.lstat(path)
        except OSError:
            snap.pop(path, None)
            continue
        if st.st_uid == uid and stat.S_ISDIR(st.st_mode):
            snap[path] = (_sweep_private_signature(st) if _sweep_private_dir(path, st)
                          else _sweep_signature(st, opaque=True))
    return snap


def diff_snapshots(baseline: dict, now: dict) -> list:
    """Paths in `now` that are new, or whose signature differs from `baseline`. A path that has disappeared is not
    reported (a transient file the VM machinery created and removed leaves no residue to leak through)."""
    if type(baseline) is not dict or type(now) is not dict:
        raise SealRefusal("BAD_ARGS")
    return sorted(path for path, sig in now.items() if baseline.get(path) != sig)


def darwin_changes(candidate_uid: int, baseline: dict, base=None) -> list:
    """Paths under the darwin roots that are new, or whose signature differs from `baseline`. A path that has
    disappeared is not reported. A root that did not exist at baseline contributes all its entries."""
    if type(baseline) is not dict:
        raise SealRefusal("BAD_ARGS")
    return diff_snapshots(baseline, snapshot_darwin(candidate_uid, base))


# Gate 4 (2026-11-08): the candidate's T/ and C/ are 0700, so from the EVAL identity they are opaque and their
# own mtime is the only evidence -- and a transient temp file made and removed by the host-side VM machinery
# (running as the candidate) moved T/'s mtime and failed a clean run closed. The candidate door therefore takes
# the snapshot AS THE CANDIDATE, who can list its own folders, and prints it here; the broker keeps the baseline
# and diffs. Every malformed document is refused (the caller turns that into a violation).
SNAPSHOT_SCHEMA = "zurvan.z1b.darwin-snapshot.v2"   # v2: private directories are identity+mode only
MAX_SNAPSHOT_ENTRIES = 200000


def serialize_snapshot(snap: dict) -> str:
    return json.dumps({"schema": SNAPSHOT_SCHEMA, "entries": sorted([p, list(sig)] for p, sig in snap.items())},
                      separators=(",", ":"))


def parse_snapshot(text, base: str = None) -> dict:
    """The inverse of serialize_snapshot, strict: every path absolute under the darwin folders base, unique, no
    NUL/newline, no `..`; every signature exactly (ino, mtime_ns, size, mode, ctime_ns) ints >= 0 + an opaque bool."""
    base = (FOLDERS_BASE if base is None else base).rstrip("/") + "/"
    bad = SealRefusal("SANDBOX_VIOLATION")
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            raise bad from None
    try:
        doc = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        raise bad from None
    if type(doc) is not dict or set(doc) != {"schema", "entries"} or doc["schema"] != SNAPSHOT_SCHEMA:
        raise bad
    entries = doc["entries"]
    if type(entries) is not list or len(entries) > MAX_SNAPSHOT_ENTRIES:
        raise bad
    out = {}
    for entry in entries:
        if type(entry) is not list or len(entry) != 2:
            raise bad
        path, sig = entry
        if (type(path) is not str or not path.startswith(base) or "\x00" in path or "\n" in path
                or "/../" in path + "/" or path in out):
            raise bad
        if (type(sig) is not list or len(sig) != 6 or type(sig[5]) is not bool
                or not all(type(v) is int and v >= 0 for v in sig[:5])):
            raise bad
        out[path] = tuple(sig)
    return out


def _sweep_default_runner(cmd):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    res = subprocess.run(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, timeout=30)
    return res.returncode, res.stdout.decode("ascii", "replace")


# Apple system workers launchd may start AS THE CANDIDATE on its own (not descended from anything the run started).
# Identified by the kernel's executable path (proc_pidpath), never argv or comm, which a process can choose; /System
# is the sealed, signed system volume, so no candidate-owned file can have this path.
SPOTLIGHT_WORKER = ("/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/"
                    "Versions/A/Support/mdworker_shared")
_METADATA_SUPPORT = ("/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/Metadata.framework/"
                     "Versions/A/Support/")
# Per-user Apple agents launchd keeps running AS THE CANDIDATE (observed live 2026-11-08: mdworker_shared after the
# 12:31 run; distnoted, lsd and mdbulkimport alive 33 min after the 12:14 run), plus the per-user Security/CF agents
# any Apple framework the VM tooling loads can start. None of them executes caller-supplied code.
SYSTEM_WORKERS = frozenset({
    SPOTLIGHT_WORKER,
    _METADATA_SUPPORT + "mdbulkimport",
    "/usr/sbin/distnoted",
    "/usr/libexec/lsd",
    "/usr/sbin/cfprefsd",
    "/usr/libexec/trustd",
    "/usr/libexec/secinitd",
    # observed live 2026-11-08 19:52 run: PlugInKit's per-user registry daemon (com.apple.pluginkit.pkd), still alive
    # 300 s after teardown. It indexes plugin bundles; it never runs them.
    "/usr/libexec/pkd",
    # observed live 2026-11-08 20:49 run (alive past the 300 s settle window) and in the uncharged rehearsal of a full
    # host-side run lifecycle as the candidate (21:07): the container manager. It creates app-container directories;
    # it never runs them. That rehearsal saw exactly the eight Apple workers in this set and nothing else.
    "/usr/libexec/containermanagerd",
})
# Paths on the sealed system volume: a process running one of these files cannot have a name the candidate chose,
# so the diagnostic may print it. Anything else is printed as "other".
_SEALED_PREFIXES = ("/System/", "/usr/bin/", "/usr/sbin/", "/usr/libexec/", "/usr/lib/", "/bin/", "/sbin/")
SETTLE_TIMEOUT_S = 300
SETTLE_POLL_S = 2


def _sweep_pid_path(pid: int):
    """The kernel's executable path for pid, or None when it cannot be read (exited, or refused)."""
    import ctypes
    import ctypes.util
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
        buf = ctypes.create_string_buffer(4096)
        n = lib.proc_pidpath(int(pid), buf, 4096)
    except (OSError, AttributeError, ValueError):
        return None
    if n <= 0:
        return None
    try:
        return buf.value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _sweep_pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True      # EPERM: it exists, it is just not ours
    return True


def settle_processes(count_fn, timeout=None, poll=None, *, sleep=None, monotonic=None) -> int:
    """Poll count_fn() until it is 0 or `timeout` seconds pass; returns the last count. An exception propagates
    (the caller treats it as a violation). Waiting never skips a check; it only lets system workers exit."""
    import time
    timeout = SETTLE_TIMEOUT_S if timeout is None else timeout
    poll = SETTLE_POLL_S if poll is None else poll
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    deadline = monotonic() + timeout
    while True:
        n = int(count_fn())
        if n <= 0 or monotonic() >= deadline:
            return n
        sleep(poll)


def candidate_processes(candidate_uid: int, runner=None, allow=frozenset(), pid_path=None, alive=None) -> int:
    """How many processes the candidate uid still owns (via /bin/ps -U).

    Ruling R13: an unparsable or failed ps is SANDBOX_VIOLATION (assume the worst: the caller burns
    every item it sent), not INTERNAL. A bad uid is still BAD_ARGS.

    `allow`: kernel executable paths not counted (SYSTEM_WORKERS). A process whose path cannot be read is
    counted unless it has exited -- an unknown process is never assumed to be a system worker.
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
    if not allow:
        return len(lines)
    pid_path = pid_path or _sweep_pid_path
    alive = alive or _sweep_pid_alive
    count = 0
    for ln in lines:
        path = pid_path(int(ln))
        if path is None:
            count += 1 if alive(int(ln)) else 0
        elif path not in allow:
            count += 1
    return count


def foreign_process_report(candidate_uid: int, runner=None, allow=SYSTEM_WORKERS, pid_path=None, alive=None) -> str:
    """For the private diagnostic only: the kernel exe path of each candidate process that is NOT an allowed
    worker, printed only when it lies on the sealed system volume (a name the candidate cannot choose); else
    "other" / "unreadable". Never raises (a diagnostic never changes a verdict)."""
    try:
        uid = _sweep_check_uid(candidate_uid)
        rc, out = (runner or _sweep_default_runner)([_PS, "-U", str(uid), "-o", "pid="])
        pids = [int(ln) for ln in out.split() if ln.isascii() and ln.isdigit()]
    except Exception:  # noqa: BLE001
        return "ps_failed"
    pid_path = pid_path or _sweep_pid_path
    alive = alive or _sweep_pid_alive
    seen = []
    for pid in pids:
        path = pid_path(pid)
        if path is None:
            if alive(pid):
                seen.append("unreadable")
            continue
        if path in allow:
            continue
        seen.append(path if path.startswith(_SEALED_PREFIXES) and ".." not in path
                    and all(c.isprintable() and c not in " ,;" for c in path) else "other")
    return ",".join(sorted(set(seen))) or "none"
