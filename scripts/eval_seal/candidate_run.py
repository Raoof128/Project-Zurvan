"""Candidate runner: clone, sandbox, launch, probe, wipe (contract s3.8).

Runs as the unprivileged `zurvancandidate` user (entry: bin/zurvan-candidate-run). The
OS user is the real boundary; the sandbox profile is defence in depth. stdin and stdout
are inherited from the broker (they ARE the protocol channel); stderr goes to /dev/null.
"""
from __future__ import annotations

import errno
import json
import os
import pwd
import re
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path

from .codes import SealRefusal
from .paths import CANDIDATE_USER, SealPaths
from .sysenv import clean_env

GIT = "/usr/bin/git"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PROFILE = Path(__file__).resolve().parent / "sandbox" / "candidate.sb"
DEFAULT_DEV_HOME = Path("/Users/dev")
DEFAULT_TIMEOUT = 7200
SEALED_IDS = ("eval_home", "keys_dir", "sets_dir", "runs_dir", "incidents_dir", "manifest_file")
_RUN_ID = re.compile(r"[0-9a-f]{16}")


def _cr_check_run_id(run_id) -> str:
    if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
        raise SealRefusal("BAD_ARGS")
    return run_id


# ---------------------------------------------------------------- launching

def _cr_default_launcher(cmd, env, cwd, timeout=None) -> int:
    """stdin/stdout inherited, stderr discarded, own process group so a timeout kills every child."""
    with open(os.devnull, "wb") as devnull:
        proc = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=None, stdout=None, stderr=devnull,
                                start_new_session=True)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _cr_kill_group(proc.pid)
            proc.wait()
            raise SealRefusal("CANDIDATE_FAILED") from None
        _cr_kill_group(proc.pid)  # reap grandchildren the candidate left behind
        return rc


def _cr_kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run(run_id: str, paths: SealPaths, *, sandbox: bool = True, launcher=None, python: str = None,
        timeout: int = DEFAULT_TIMEOUT, dev_home: Path = None) -> int:
    """Clone stage/<run_id>.bundle into scratch/<run_id>/repo and exec the candidate. Returns its exit code."""
    _cr_check_run_id(run_id)
    launcher = launcher or _cr_default_launcher
    python = python or str(paths.runtime_dir / "bin" / "python3")
    dev_home = Path(dev_home) if dev_home is not None else DEFAULT_DEV_HOME

    staged = paths.stage_dir / (run_id + ".bundle")
    try:
        st = os.lstat(str(staged))
    except OSError:
        raise SealRefusal("BUNDLE_INVALID") from None
    if not stat.S_ISREG(st.st_mode):
        raise SealRefusal("BUNDLE_INVALID")

    scratch_base = Path(os.path.realpath(str(paths.scratch_dir)))
    run_dir = scratch_base / run_id
    try:
        os.mkdir(str(run_dir), 0o700)  # never reuse a run directory: a leftover means a wipe failed
    except OSError:
        raise SealRefusal("SANDBOX_VIOLATION") from None
    tmp_dir = run_dir / "tmp"
    os.mkdir(str(tmp_dir), 0o700)
    repo = run_dir / "repo"

    git_env = clean_env(paths.candidate_home, {"TMPDIR": str(tmp_dir)})
    clone = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "clone", "--quiet",
             "--no-hardlinks", "--template=", str(staged), str(repo)]
    try:
        done = subprocess.run(clone, env=git_env, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("BUNDLE_INVALID") from None
    if done.returncode != 0:
        raise SealRefusal("BUNDLE_INVALID")

    entry = repo / "scripts" / "z1b_candidate.py"
    if not entry.is_file() or entry.is_symlink():
        raise SealRefusal("CANDIDATE_FAILED")

    env = clean_env(paths.candidate_home, {
        "TMPDIR": str(tmp_dir),
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": str(paths.runtime_dir / "hf"),
        "ZURVAN_SEARCH_DB": str(paths.corpus_dir / "search.sqlite"),
        "ZURVAN_CORPUS": str(paths.corpus_dir),
        "ZURVAN_ROOT": str(repo),
    })
    cmd = [python, "-B", "-E", "-s", str(entry)]
    if sandbox:
        cmd = [SANDBOX_EXEC, "-f", str(PROFILE),
               "-D", "SCRATCH=%s" % run_dir,
               "-D", "SEALED=%s" % os.path.realpath(str(paths.eval_home)),
               "-D", "DEVHOME=%s" % os.path.realpath(str(dev_home))] + cmd
    return launcher(cmd, env, str(run_dir), timeout=timeout)


# ---------------------------------------------------------------- probe

def _cr_probe_one(path: Path) -> str:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return "ENOENT"
        if exc.errno in (errno.EACCES, errno.EPERM):
            # an exec-only directory cannot be listed but its children are reachable by name
            try:
                if stat.S_ISDIR(os.stat(str(path)).st_mode) and os.access(str(path), os.X_OK):
                    return "READABLE"
            except OSError:
                pass
            return "EACCES"
        return "READABLE"  # unknown failure: assume exposure rather than report a clean denial
    os.close(fd)
    return "READABLE"


def probe(paths: SealPaths) -> dict:
    """Try to open each sealed path as this user. Anything but EACCES/ENOENT is an exposure."""
    return {pid: _cr_probe_one(getattr(paths, pid)) for pid in SEALED_IDS}


def stage_probe(paths: SealPaths) -> str:
    """The POSITIVE twin of probe(): can this user list the stage dir, where the broker leaves each run's bundle and
    corpus tar for the door to read? Gate 4 (2026-11-08): the installed sealed identities had PrimaryGroupID 20 (staff)
    instead of their own group, so the candidate could not traverse the zurvaneval:zurvancandidate 0750 stage dir;
    every charged run then died in vm-push-tree before git ran, reported only as CANDIDATE_FAILED. Asked of the
    KERNEL (a real listing), not computed from group records. "OK" or "DENIED"; never a file name."""
    try:
        os.listdir(str(paths.stage_dir))
    except OSError:
        return "DENIED"
    return "OK"


# ---------------------------------------------------------------- wipe

def _euid_user() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return ""


def _default_killer() -> bool:
    """SIGKILL every process of the effective user, but ONLY when that user is the candidate user."""
    if _euid_user() != CANDIDATE_USER:
        return False
    os.kill(-1, signal.SIGKILL)
    return True


def _cr_unlock_tree(top: str) -> None:
    for root, dirs, _files in os.walk(top, topdown=True, followlinks=False):
        for name in dirs:
            full = os.path.join(root, name)
            if not os.path.islink(full):
                try:
                    os.chmod(full, 0o700)
                except OSError:
                    pass


def wipe(paths: SealPaths, run_id: str, *, killer=None) -> None:
    """Kill leftovers, then remove scratch/<run_id>. Never follows a symlink."""
    _cr_check_run_id(run_id)
    try:
        (killer or _default_killer)()
    except Exception:  # noqa: BLE001 - a failing kill must not stop the removal
        pass
    target = str(Path(os.path.realpath(str(paths.scratch_dir))) / run_id)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode):  # a symlink (or file) planted at the run path: remove the link itself
        os.unlink(target)
        return
    os.chmod(target, 0o700)
    _cr_unlock_tree(target)
    shutil.rmtree(target)


# ---------------------------------------------------------------- the VM door (Rev 3 #1, operator gate 2026-10-05)
#
# The broker (zurvaneval) never runs limactl. It asks this door, which runs as zurvancandidate, so the VM's host
# processes belong to the candidate identity: the pf anchor blocks the guest's outbound traffic, and a
# hypervisor escape lands where no key is readable. The door re-validates everything and renders the VM config
# itself from the pinned golden image, so the broker can neither swap the image nor inject Lima config.

_VM_DST = re.compile(r"/[A-Za-z0-9._/-]{1,200}")


def _cr_golden_location(paths) -> str:
    img = Path(paths.broker_dir) / "images" / "guest.qcow2"
    st = os.lstat(str(img))                      # raises -> refused: no golden image installed
    if not stat.S_ISREG(st.st_mode):
        raise SealRefusal("CANDIDATE_FAILED")
    return "file://" + str(img)


def _cr_stage_file(paths, src) -> str:
    """A copy source must be a regular, non-symlink file directly reachable under the stage dir."""
    if type(src) is not str or "\x00" in src or not os.path.isabs(src):
        raise SealRefusal("BAD_ARGS")
    stage = os.path.realpath(str(paths.stage_dir)) + os.sep
    real = os.path.realpath(src)
    if not real.startswith(stage):
        raise SealRefusal("BAD_ARGS")
    st = os.lstat(src)
    if not stat.S_ISREG(st.st_mode) or real != os.path.abspath(src):
        raise SealRefusal("BAD_ARGS")
    return real


def _cr_vm(argv, paths, *, execve=None) -> int:
    from . import vm_runner as vmr
    verb = argv[0]
    if verb == "vm-list" and len(argv) == 1:
        for name in vmr.local_leftovers(paths):
            sys.stdout.write(name + "\n")
        sys.stdout.flush()
        return 0
    if len(argv) < 2:
        return 2
    rid = _cr_check_run_id(argv[1])
    if verb == "vm-start" and len(argv) == 2:
        try:
            vmr.local_start(rid, paths, image_location=_cr_golden_location(paths), profile="golden")
        except SealRefusal as exc:
            return vmr.DOOR_RC_LEFTOVER if exc.code == "SANDBOX_VIOLATION" else 2
        except OSError:
            return 2
        return 0
    if verb == "vm-shell" and len(argv) >= 4 and argv[2] == "--":
        exe = vmr._vm_limactl(paths)
        (execve or os.execve)(exe, vmr.local_shell_argv(rid, paths, argv[3:]),
                              vmr._vm_env(paths, vmr._vm_lima_home(paths, rid)))
        return 3                                     # execve does not return; a test double may
    if verb == "vm-copy" and len(argv) == 4:
        dst = argv[3]
        if not _VM_DST.fullmatch(dst) or ".." in dst.split("/"):
            return 2
        try:
            vmr.local_copy(rid, paths, _cr_stage_file(paths, argv[2]), dst)
        except (SealRefusal, OSError):
            return 2
        return 0
    if verb == "vm-delete" and len(argv) == 2:
        vmr.local_teardown(rid, paths)
        return 0
    if verb == "vm-push-tree" and len(argv) == 3:
        if not _VM_COMMIT.fullmatch(argv[2]):
            return 2
        try:
            _run_dir, tar = _cr_commit_tree_tar(rid, argv[2], paths)
            vmr.local_copy(rid, paths, tar, vmr.GUEST_TREE_TAR)
        except (SealRefusal, OSError, subprocess.SubprocessError):
            return 2
        finally:
            # ALWAYS wipe this run's scratch -- also when the build failed after creating it, or the next run would
            # refuse it as a leftover. Never the default killer: it SIGKILLs every candidate-owned process, which
            # is the running VM.
            wipe(paths, rid, killer=lambda: False)
        return 0
    return 2


_VM_COMMIT = re.compile(r"[0-9a-f]{40}")


def _cr_commit_tree_tar(rid, commit, paths):
    """The commit's tree, as a tar, built AS zurvancandidate from the staged (broker-verified) bundle -- untrusted
    git pack parsing never runs as the key owner (plan "Decisions for Raouf" #2). Clone with hooks and fsmonitor
    off and no template, exactly as the legacy runner did; then `git archive <commit>`. Returns (run_dir, tar)."""
    staged = paths.stage_dir / (rid + ".bundle")
    st = os.lstat(str(staged))
    if not stat.S_ISREG(st.st_mode):
        raise SealRefusal("BUNDLE_INVALID")
    run_dir = Path(os.path.realpath(str(paths.scratch_dir))) / rid
    try:
        os.mkdir(str(run_dir), 0o700)       # never reuse a run directory: a leftover means a wipe failed
    except OSError:
        raise SealRefusal("SANDBOX_VIOLATION") from None
    tmp_dir = run_dir / "tmp"
    os.mkdir(str(tmp_dir), 0o700)
    repo = run_dir / "repo"
    env = clean_env(paths.candidate_home, {"TMPDIR": str(tmp_dir)})
    git = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    steps = (git + ["clone", "--quiet", "--no-hardlinks", "--no-checkout", "--template=", str(staged), str(repo)],
             git + ["-C", str(repo), "cat-file", "-e", commit + "^{commit}"],
             git + ["-C", str(repo), "archive", "--format=tar", "-o", str(run_dir / "tree.tar"), commit])
    for argv in steps:
        done = subprocess.run(argv, env=env, cwd=str(run_dir), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
        if done.returncode != 0:
            raise SealRefusal("BUNDLE_INVALID")
    return run_dir, run_dir / "tree.tar"


# ---------------------------------------------------------------- entry point

def darwin_snapshot(darwin_base=None) -> int:
    """Print the candidate's OWN darwin per-user folders (sweep.snapshot_darwin) as one JSON document. Runs only
    as the candidate (the broker invokes it through the door before launch and after teardown): the candidate
    can list its 0700 T/ and C/, the eval identity cannot, so the broker diffs real entries instead of an opaque
    directory mtime that the VM machinery's transient temp files move (gate 4 failure, 2026-11-08)."""
    from . import sweep
    uid = os.geteuid()
    if uid != pwd.getpwnam(CANDIDATE_USER).pw_uid:
        return 2
    snap = sweep.snapshot_darwin(uid, darwin_base)
    sys.stdout.write(sweep.serialize_snapshot(snap) + "\n")
    sys.stdout.flush()
    return 0


def main(argv, *, paths: SealPaths = None, killer=None, execve=None, darwin_base=None) -> int:
    """probe | stage-probe | wipe <run_id> | darwin-snapshot | vm-start|vm-shell|vm-copy|vm-delete <run_id> ... | vm-list.

    Rev 3 #1: candidate CODE no longer executes through this wrapper. The candidate runs inside a
    disposable VM (vm_runner), so the privileged `sudo -u zurvancandidate zurvan-candidate-run` door
    exposes only the read-denial probe and the scratch wipe - never `run`. `run()` below is the legacy
    host-sandbox executor, kept for its tests but unreachable from main: there is no host execution path.
    """
    try:
        paths = paths or SealPaths.real()
        argv = list(argv)
        if argv and argv[0] == "run":
            return 2  # host candidate execution is removed in VM mode; the broker boots a VM instead
        if argv == ["probe"]:
            sys.stdout.write(json.dumps(probe(paths), sort_keys=True) + "\n")
            sys.stdout.flush()
            return 0
        if len(argv) == 2 and argv[0] == "wipe":
            wipe(paths, _cr_check_run_id(argv[1]), killer=killer)
            return 0
        if argv == ["stage-probe"]:
            sys.stdout.write(stage_probe(paths) + "\n")
            sys.stdout.flush()
            return 0
        if argv == ["darwin-snapshot"]:
            return darwin_snapshot(darwin_base)
        if argv[:1] and argv[0].startswith("vm-"):
            return _cr_vm(argv, paths, execve=execve)
        return 2
    except SealRefusal:
        return 2
    except Exception:  # noqa: BLE001
        return 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
