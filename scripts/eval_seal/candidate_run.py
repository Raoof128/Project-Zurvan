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


# ---------------------------------------------------------------- entry point

def main(argv, *, paths: SealPaths = None, killer=None) -> int:
    """run <run_id> | probe | wipe <run_id>. stdout belongs to the protocol (run) or one JSON doc (probe)."""
    try:
        paths = paths or SealPaths.real()
        argv = list(argv)
        if len(argv) == 2 and argv[0] == "run":
            return 0 if run(_cr_check_run_id(argv[1]), paths) == 0 else 1
        if argv == ["probe"]:
            sys.stdout.write(json.dumps(probe(paths), sort_keys=True) + "\n")
            sys.stdout.flush()
            return 0
        if len(argv) == 2 and argv[0] == "wipe":
            wipe(paths, _cr_check_run_id(argv[1]), killer=killer)
            return 0
        return 2
    except SealRefusal:
        return 2
    except Exception:  # noqa: BLE001
        return 3


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
