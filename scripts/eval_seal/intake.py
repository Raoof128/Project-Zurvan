"""Bundle intake for the sealed broker (contract s3.7).

The drop directory is world-writable (1733), so everything in it is attacker-chosen:
names, types, links, sizes. stage_bundle opens ONE file descriptor, validates that
descriptor with fstat, and copies the bytes through the same descriptor, so nothing
can be swapped between check and use. verify_bundle only ever sees the staged copy.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from .codes import SealRefusal
from .paths import SealPaths

BUNDLE_NAME_RE = r"^z1b-[0-9a-f]{40}-[0-9a-f]{16}\.bundle$"
MAX_BUNDLE_BYTES = 2 * 1024 ** 3
GIT = "/usr/bin/git"

_NAME = re.compile(BUNDLE_NAME_RE)
_RUN_ID = re.compile(r"[0-9a-f]{16}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_HEAD_LINE = re.compile(r"([0-9a-f]{40}) [^\x00-\x1f]+")
_CHUNK = 1024 * 1024
_GIT_TIMEOUT = 600
# the only GIT_* variables a caller env may carry, with the values sysenv.clean_env pins
_PINNED_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ATTR_NOSYSTEM": "1",
}


def _bad() -> SealRefusal:
    return SealRefusal("BUNDLE_INVALID")


def stage_bundle(name: str, paths: SealPaths, *, expected_uid: int, run_id: str) -> Path:
    """Copy drop_dir/<name> to stage_dir/<run_id>.bundle through one validated fd."""
    # fullmatch, not match: "$" would let a trailing newline through
    if type(name) is not str or not _NAME.fullmatch(name):
        raise _bad()
    if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
        raise _bad()
    if type(expected_uid) is not int or isinstance(expected_uid, bool):
        raise _bad()

    staged = paths.stage_dir / (run_id + ".bundle")
    dir_fd = src_fd = dst_fd = None
    created = False
    try:
        try:
            dir_fd = os.open(str(paths.drop_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            src_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except OSError:
            raise _bad() from None
        st = os.fstat(src_fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != expected_uid or st.st_nlink != 1:
            raise _bad()
        if st.st_size > MAX_BUNDLE_BYTES:
            raise _bad()
        try:
            dst_fd = os.open(str(staged), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        except OSError:
            raise _bad() from None  # includes a pre-existing stage file: never overwritten
        created = True
        total = 0
        try:
            while True:
                chunk = os.read(src_fd, _CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BUNDLE_BYTES:  # file grew while we copied
                    raise _bad()
                view = memoryview(chunk)
                while view:
                    view = view[os.write(dst_fd, view):]
            os.fsync(dst_fd)
        except OSError:
            raise _bad() from None
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            pass  # best effort: the staged copy is already private
        return staged
    except SealRefusal:
        if created:
            try:
                os.unlink(str(staged))
            except OSError:
                pass
        raise
    except Exception:  # noqa: BLE001 - nothing but a code may cross this boundary
        if created:
            try:
                os.unlink(str(staged))
            except OSError:
                pass
        raise _bad() from None
    finally:
        for fd in (src_fd, dst_fd, dir_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _check_env(env: dict) -> None:
    if not isinstance(env, dict) or not env:
        raise _bad()
    for key, val in env.items():
        if type(key) is not str or type(val) is not str:
            raise _bad()
        if key.startswith("GIT_") and _PINNED_GIT_ENV.get(key) != val:
            raise _bad()
        if key.startswith(("DYLD_", "LD_", "PYTHON")):
            raise _bad()
    for key, val in _PINNED_GIT_ENV.items():
        if env.get(key) != val:
            raise _bad()


def _git(args: list, env: dict, scratch_repo: str = None) -> "subprocess.CompletedProcess":
    cmd = [GIT, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
           "-c", "transfer.fsckObjects=true"]
    if scratch_repo is not None:
        cmd += ["--git-dir", scratch_repo]
    cmd += list(args)
    return subprocess.run(cmd, env=env, cwd="/", stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=_GIT_TIMEOUT)


def verify_bundle(staged: Path, commit: str, *, env: dict) -> None:
    """The staged bundle is well formed, lists `commit` as a head, and its pack unpacks cleanly.

    `git bundle verify` alone does not read the pack of a full-history bundle (a flipped
    byte passes it), so the objects are also unbundled into a throwaway empty repository.
    """
    if type(commit) is not str or not _COMMIT.fullmatch(commit):
        raise SealRefusal("BAD_ARGS")
    _check_env(env)
    staged = Path(staged)
    work = None
    try:
        work = tempfile.mkdtemp(prefix=".verify-", dir=str(staged.parent))
        repo = os.path.join(work, "r.git")
        if _git(["init", "--bare", "--quiet", "--template=", repo], env).returncode != 0:
            raise _bad()
        if _git(["bundle", "verify", str(staged)], env, repo).returncode != 0:
            raise _bad()
        listed = _git(["bundle", "list-heads", str(staged)], env, repo)
        if listed.returncode != 0:
            raise _bad()
        heads = set()
        for line in listed.stdout.decode("utf-8", "replace").splitlines():
            m = _HEAD_LINE.fullmatch(line)
            if not m:
                raise _bad()
            heads.add(m.group(1))
        if commit not in heads:
            raise SealRefusal("CANDIDATE_NOT_FROZEN")
        if _git(["bundle", "unbundle", str(staged)], env, repo).returncode != 0:
            raise _bad()
        if _git(["cat-file", "-e", commit + "^{commit}"], env, repo).returncode != 0:
            raise _bad()
    except SealRefusal:
        raise
    except Exception:  # noqa: BLE001
        raise _bad() from None
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)
