"""Rev 3 #1: disposable per-run VM isolation for the candidate (spec s5.3; design 2026-10-02-z1b-rev3 s1).

The candidate necessarily receives each raw HOLDOUT query, so on a shared host it can leak a query through
a child process's argv to the developer's `ps`. The fix is a hardware boundary: each HOLDOUT/RESERVE run
gets its own disposable ARM64 Linux VM (Apple Virtualization via a pinned Lima), booted once, streamed the
queries over `limactl shell` stdin/stdout, then destroyed. The guest has no host mounts, so host-side
sealed secrets (keys, sets) are unreachable from inside it.

This module is the host-side lifecycle: render the pinned VM config, start a per-run instance, hand the
broker a Popen whose stdin/stdout speak the existing one-query-at-a-time protocol, and tear the instance
down in a `finally` that covers success, failure, timeout and kill. It is stdlib-only and Python 3.9.

Proven end-to-end on 2026-10-02 (Lima 2.2.0, macOS 27.0.1 arm64): boot, in-guest candidate over the
protocol, `ls /Users` -> "No such file or directory" (no host crossover), clean teardown. The config knobs
below are the measured-necessary ones; see design s7c/s7d.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .codes import SealRefusal
from .sysenv import clean_env

# The guest image is pinned by URL + sha512 digest (Lima verifies the digest on pull). Alpine aarch64 is
# tiny; the real candidate runtime/corpus are laid into a golden image + a read-only corpus disk at install.
GUEST_IMAGE_URL = ("https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/"
                   "nocloud_alpine-3.23.4-aarch64-uefi-cloudinit-r0.qcow2")
GUEST_IMAGE_ARCH = "aarch64"
GUEST_IMAGE_DIGEST = ("sha512:737b0eb365944ffcf6da477c059c105bbde222942fd5a8380c07db088365b18db"
                      "c60a0f2824ea9a3904d57e194e0d05a7074302c3ebc104d0aecb6ddc6ffd91c")
START_TIMEOUT = 300
TEARDOWN_TIMEOUT = 120


def vm_config_text() -> str:
    """The pinned Lima config. Every line here is measured-necessary (design s7d):
    - `mounts: []`        : the guest sees NO host filesystem (the boundary against host-side secrets);
    - `containerd` off    : Alpine has no systemd, so lima's containerd check would mark the VM DEGRADED
                            and make `start` exit non-zero though the guest is usable;
    - pinned image+digest : the guest is a known, verified image, not whatever is latest.
    """
    return (
        "images:\n"
        '- location: "%s"\n'
        '  arch: "%s"\n'
        '  digest: "%s"\n'
        "mounts: []\n"
        "containerd:\n"
        "  system: false\n"
        "  user: false\n"
        "cpus: 2\n"
        'memory: "1GiB"\n'
        'disk: "4GiB"\n'
    ) % (GUEST_IMAGE_URL, GUEST_IMAGE_ARCH, GUEST_IMAGE_DIGEST)


def vm_instance_name(run_id: str) -> str:
    """A short instance name. `LIMA_HOME` + this must keep the VZ ssh.sock path under 104 chars
    (UNIX_PATH_MAX), so the scratch LIMA_HOME is short and the name is just the run id."""
    import re
    if type(run_id) is not str or re.fullmatch(r"[0-9a-f]{16}", run_id) is None:
        raise SealRefusal("BAD_ARGS")
    return "z" + run_id


def _vm_limactl(paths) -> str:
    """The pinned, root-owned limactl. Install copies the WHOLE lima asset tree
    (bin/limactl + share/lima/{templates,_images,guestagents}) so a copied binary can still find its guest
    agent (design s7c); the digest of bin/limactl is pinned like the other broker binaries."""
    return str(Path(paths.broker_bin) / "limactl")


def _vm_env(paths, lima_home: Path) -> dict:
    # A short LIMA_HOME (ssh.sock length) and nothing inherited from the caller.
    return clean_env(paths.candidate_home, {"LIMA_HOME": str(lima_home)})


def _vm_lima_home(paths, run_id: str) -> Path:
    # Under the candidate scratch, kept short on purpose.
    return Path(paths.scratch_dir) / ("l" + run_id)


def vm_start(run_id: str, paths, *, runner=None) -> None:
    """Create and boot the per-run instance from the pinned config. Raises CANDIDATE_FAILED on any failure
    (a VM that will not boot is a failed run, charged by the broker)."""
    runner = runner or subprocess.run
    name = vm_instance_name(run_id)
    lima_home = _vm_lima_home(paths, run_id)
    if lima_home.exists():
        raise SealRefusal("SANDBOX_VIOLATION")  # a leftover LIMA_HOME means a prior teardown failed
    lima_home.mkdir(parents=True)
    os.chmod(str(lima_home), 0o700)
    cfg = lima_home / "z1b.yaml"
    cfg.write_text(vm_config_text())
    argv = [_vm_limactl(paths), "start", "--tty=false", "--name=" + name, str(cfg)]
    try:
        done = runner(argv, env=_vm_env(paths, lima_home), stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=START_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    if getattr(done, "returncode", 1) != 0:
        raise SealRefusal("CANDIDATE_FAILED")


def vm_shell_argv(run_id: str, paths, guest_cmd) -> list:
    """The argv that runs the candidate INSIDE the guest and connects its stdin/stdout to the broker's
    protocol loop. guest_cmd is the candidate entry already inside the guest image."""
    return [_vm_limactl(paths), "shell", vm_instance_name(run_id), "--"] + list(guest_cmd)


def vm_launch(run_id: str, paths, guest_cmd, *, runner=None, popen=None):
    """Boot the instance, then return a started Popen of `limactl shell` whose stdin/stdout are the
    candidate protocol channel. The caller (broker) drives the existing one-query-at-a-time loop and must
    call vm_teardown(run_id, paths) in a finally."""
    popen = popen or subprocess.Popen
    vm_start(run_id, paths, runner=runner)
    lima_home = _vm_lima_home(paths, run_id)
    return popen(vm_shell_argv(run_id, paths, guest_cmd), env=_vm_env(paths, lima_home),
                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
                 start_new_session=True)


def vm_teardown(run_id: str, paths, *, runner=None) -> None:
    """Destroy the instance and remove its LIMA_HOME. Idempotent and never raises: a run that failed, timed
    out or was killed must leave no reusable guest state. Both `delete -f` and the dir removal are attempted
    regardless of each other's outcome."""
    runner = runner or subprocess.run
    name = vm_instance_name(run_id)
    lima_home = _vm_lima_home(paths, run_id)
    env = _vm_env(paths, lima_home)
    try:
        runner([_vm_limactl(paths), "delete", "-f", name], env=env, stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass
    shutil.rmtree(str(lima_home), ignore_errors=True)


def vm_leftover_instances(paths, *, runner=None) -> list:
    """Any z1b instances still present after teardown (the post-run sweep's VM arm): a non-empty list is a
    teardown failure that must burn the run's items. Lists across the scratch LIMA_HOMEs."""
    runner = runner or subprocess.run
    scratch = Path(paths.scratch_dir)
    leftover = []
    if not scratch.is_dir():
        return leftover
    for entry in sorted(scratch.glob("l*")):
        if not entry.is_dir():
            continue
        try:
            done = runner([_vm_limactl(paths), "list", "--format", "{{.Name}}"],
                          env=_vm_env(paths, entry), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            leftover.append(entry.name)
            continue
        out = getattr(done, "stdout", b"") or b""
        names = [n for n in out.decode("ascii", "replace").split() if n]
        leftover.extend(names)
    return leftover
