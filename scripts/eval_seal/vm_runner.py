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
import re
import shutil
import subprocess
from pathlib import Path

from .codes import SealRefusal
from .paths import CANDIDATE_USER
from .sysenv import clean_env

# The guest image is pinned by URL + sha512 digest (Lima verifies the digest on pull). Alpine aarch64 is
# tiny; the real candidate runtime/corpus are laid into a golden image + a read-only corpus disk at install.
GUEST_IMAGE_URL = ("https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/cloud/"
                   "nocloud_alpine-3.23.4-aarch64-uefi-cloudinit-r0.qcow2")
GUEST_IMAGE_ARCH = "aarch64"
GUEST_IMAGE_DIGEST = ("sha512:737b0eb365944ffcf6da477c059c105bbde222942fd5a8380c07db088365b18db"
                      "c60a0f2824ea9a3904d57e194e0d05a7074302c3ebc104d0aecb6ddc6ffd91c")
# The GOLDEN image the broker actually boots (Debian 12 aarch64 + /opt/z1b runtime/candidate/model, baked
# and validated end-to-end 2026-10-03, design §7m). Its sha512 is computed AFTER flattening; install.sh
# places broker_dir/images/guest.qcow2 only when it matches this, and it is bound into the run identity so
# a changed image is never served from cache. A rebake (e.g. CPU-only torch) re-pins this.
GOLDEN_IMAGE_DIGEST = ("sha512:41bfeb0bac30ec1c00b2ba766265c0b0d365abffd683f57e717b44deb2b17ac43ecfb"
                       "ea709a3f4fabf5528e9e9b301dd3a7d7826fd0f969baf4aecf39c121728")
START_TIMEOUT = 300
TEARDOWN_TIMEOUT = 120


def vm_config_text(image_location: str = None, profile: str = "isolation") -> str:
    """The pinned Lima config. Every line here is measured-necessary (design s7d):
    - `mounts: []`        : the guest sees NO host filesystem (the boundary against host-side secrets);
    - `containerd` off    : Alpine has no systemd, so lima's containerd check would mark the VM DEGRADED
                            and make `start` exit non-zero though the guest is usable;
    - pinned image+digest : the guest is a known, verified image, not whatever is latest.

    profile "isolation" is the tiny Alpine base the VM-ISO gate boots. profile "golden" is what the BROKER boots
    (operator gate 2026-10-05: the broker path had only ever rendered the Alpine profile, so Lima aborted on a
    digest mismatch): the golden image's own pinned digest, 4 GiB for the torch/MiniLM runtime, Lima's default
    sparse disk (the image is 20 GiB virtual, and the corpus is staged + squashed in-guest), and lima's host
    resolver OFF (the candidate's network is closed in-guest; see broker_provision_corpus)."""
    if profile == "golden":
        if not image_location:
            raise SealRefusal("CANDIDATE_FAILED")
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
            'memory: "4GiB"\n'
            "hostResolver:\n"
            "  enabled: false\n"
        ) % (image_location, GUEST_IMAGE_ARCH, GOLDEN_IMAGE_DIGEST)
    if profile != "isolation":
        raise SealRefusal("CANDIDATE_FAILED")
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
    ) % (image_location or GUEST_IMAGE_URL, GUEST_IMAGE_ARCH, GUEST_IMAGE_DIGEST)

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


# lima appends a 16-digit suffix to the control socket: <LIMA_HOME>/<name>/ssh.sock.<16 digits>. That whole
# path must stay under UNIX_PATH_MAX (104). So LIMA_HOME is one SHORT, persistent dir (it also caches the
# pinned image across runs), and per-run isolation is the unique instance name plus delete-after-run.
SSH_SOCK_SUFFIX = len("/ssh.sock.") + 16


def _vm_lima_home(paths) -> Path:
    return Path(paths.candidate_home) / "lima"


# ------------------------------------------------------------------ local lifecycle (runs AS the VM owner)
#
# These functions run limactl directly as whoever calls them. In production that is ONLY the candidate
# identity, inside the digest-pinned `zurvan-candidate-run` door (candidate_run.main vm-*): the VM's host
# processes (limactl hostagent and its user-mode network) are owned by zurvancandidate, so the pf anchor
# blocks the guest's outbound traffic and a hypervisor escape lands in an identity that holds no key. The
# dev-side VM-ISO harness calls them as the developer.

def local_start(run_id: str, paths, *, runner=None, image_location: str = None, profile: str = "isolation") -> None:
    """Boot this run's instance in the shared LIMA_HOME from the pinned config. A pre-existing instance of
    the same name means a prior teardown failed (SANDBOX_VIOLATION). CANDIDATE_FAILED on any boot failure
    (a VM that will not boot is a failed run, charged by the broker)."""
    runner = runner or subprocess.run
    name = vm_instance_name(run_id)
    lima_home = _vm_lima_home(paths)
    lima_home.mkdir(parents=True, exist_ok=True)
    os.chmod(str(lima_home), 0o700)
    if (lima_home / name).exists():
        raise SealRefusal("SANDBOX_VIOLATION")  # a leftover instance means a prior teardown failed
    cfg = lima_home / (name + ".yaml")
    cfg.write_text(vm_config_text(image_location, profile))
    argv = [_vm_limactl(paths), "start", "--tty=false", "--name=" + name, str(cfg)]
    try:
        done = runner(argv, env=_vm_env(paths, lima_home), stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=START_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    if getattr(done, "returncode", 1) != 0:
        raise SealRefusal("CANDIDATE_FAILED")


def local_shell_argv(run_id: str, paths, guest_argv) -> list:
    """`limactl shell <instance> -- <guest_argv>`: the guest argv is passed through verbatim (the caller has
    already added any `sudo -u` / `env -i` prefix)."""
    return [_vm_limactl(paths), "shell", vm_instance_name(run_id), "--"] + list(guest_argv)


def local_copy(run_id: str, paths, src, dst: str, *, runner=None) -> None:
    """Copy a host file into the running guest via `limactl copy` (scp over lima's ssh; NOT a host mount).
    Raises CANDIDATE_FAILED on failure."""
    runner = runner or subprocess.run
    name = vm_instance_name(run_id)
    argv = [_vm_limactl(paths), "copy", str(src), "%s:%s" % (name, dst)]
    try:
        done = runner(argv, env=_vm_env(paths, _vm_lima_home(paths)), stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    if getattr(done, "returncode", 1) != 0:
        raise SealRefusal("CANDIDATE_FAILED")


def local_teardown(run_id: str, paths, *, runner=None) -> None:
    """Destroy the instance and remove its per-run config. Idempotent and never raises: a run that failed,
    timed out or was killed must leave no reusable guest state. Both steps are attempted regardless."""
    runner = runner or subprocess.run
    name = vm_instance_name(run_id)
    lima_home = _vm_lima_home(paths)
    env = _vm_env(paths, lima_home)
    try:
        runner([_vm_limactl(paths), "delete", "-f", name], env=env, stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        (lima_home / (name + ".yaml")).unlink()
    except OSError:
        pass


def local_leftovers(paths, *, runner=None) -> list:
    """Any instances still present in the shared LIMA_HOME (the post-run sweep's VM arm): a non-empty list
    is a teardown failure that must burn the run's items."""
    runner = runner or subprocess.run
    lima_home = _vm_lima_home(paths)
    if not lima_home.is_dir():
        return []
    try:
        done = runner([_vm_limactl(paths), "list", "--format", "{{.Name}}"],
                      env=_vm_env(paths, lima_home), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ["<limactl-list-failed>"]
    out = getattr(done, "stdout", b"") or b""
    return [n for n in out.decode("ascii", "replace").split() if n]


def _guest_env_prefix(guest_env) -> list:
    """`/usr/bin/env -i K=V ...` so the candidate runs with EXACTLY the sealed env (nothing inherited from
    the guest login): keys sorted for determinism. Empty/None -> no prefix. Keys/values are restricted to a
    safe charset so nothing can break out of the argv (the broker is the only caller and passes fixed keys)."""
    if not guest_env:
        return []
    for k, v in guest_env.items():
        if not re.fullmatch(r"[A-Z0-9_]+", k) or "\x00" in str(v) or "\n" in str(v):
            raise SealRefusal("CANDIDATE_FAILED")
    return ["/usr/bin/env", "-i"] + ["%s=%s" % (k, guest_env[k]) for k in sorted(guest_env)]


def _guest_user_prefix(guest_user) -> list:
    """`/usr/bin/sudo -n -u <user> --`: run the guest command as an unprivileged guest user. The guest login
    user has passwordless sudo, so a candidate run AS the login user is root in the guest and could undo any
    in-guest control; the broker runs it as a no-sudo user instead (operator gate 2026-10-05)."""
    if guest_user is None:
        return []
    if type(guest_user) is not str or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", guest_user):
        raise SealRefusal("CANDIDATE_FAILED")
    return ["/usr/bin/sudo", "-n", "-u", guest_user, "--"]


# ------------------------------------------------------------------ the broker side: through the sudo door
#
# The broker runs as zurvaneval and never runs limactl itself. Every VM operation goes through the existing,
# digest-pinned `zurvaneval ALL=(zurvancandidate) NOPASSWD: sha256:<digest> zurvan-candidate-run` rule (the
# sudoers file keeps exactly two rules). The door re-validates every argument and renders the VM config
# itself, so the broker cannot point a run at another image or inject Lima config.

DOOR_RC_LEFTOVER = 4          # the door's exit code for "an instance of this name already exists"
_DOOR_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


def vm_door(paths) -> list:
    return ["/usr/bin/sudo", "-n", "-u", CANDIDATE_USER, str(Path(paths.broker_bin) / "zurvan-candidate-run")]


def vm_start(run_id: str, paths, *, runner=None) -> None:
    """Boot this run's golden-image instance AS zurvancandidate. A leftover instance of the same name is a
    failed prior teardown (SANDBOX_VIOLATION); any other failure is CANDIDATE_FAILED (charged)."""
    runner = runner or subprocess.run
    argv = vm_door(paths) + ["vm-start", vm_instance_name(run_id)[1:]]
    try:
        done = runner(argv, env=dict(_DOOR_ENV), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE, timeout=START_TIMEOUT + 60)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    rc = getattr(done, "returncode", 1)
    if rc == DOOR_RC_LEFTOVER:
        raise SealRefusal("SANDBOX_VIOLATION")
    if rc != 0:
        raise SealRefusal("CANDIDATE_FAILED")


def vm_shell_argv(run_id: str, paths, guest_cmd, guest_env=None, guest_user=None) -> list:
    """The argv that runs a command INSIDE the guest, through the door, with stdin/stdout passed straight
    through (for the candidate they ARE the protocol channel). guest_user (optional) runs it as that
    unprivileged guest user; guest_env (optional) is forced with `env -i`."""
    return (vm_door(paths) + ["vm-shell", vm_instance_name(run_id)[1:], "--"]
            + _guest_user_prefix(guest_user) + _guest_env_prefix(guest_env) + list(guest_cmd))


def vm_run(run_id: str, paths, guest_cmd, *, runner=None, timeout: int = TEARDOWN_TIMEOUT):
    """Run a SYNCHRONOUS command inside the guest (corpus provisioning) and return the completed process.
    CANDIDATE_FAILED on a non-zero exit or spawn error -- a failed setup step is a failed run, never a host
    fallback."""
    runner = runner or subprocess.run
    try:
        done = runner(vm_shell_argv(run_id, paths, guest_cmd), env=dict(_DOOR_ENV),
                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    if getattr(done, "returncode", 1) != 0:
        raise SealRefusal("CANDIDATE_FAILED")
    return done


def vm_launch(run_id: str, paths, guest_cmd, *, runner=None, popen=None, guest_env=None, guest_user=None,
              boot: bool = True):
    """Return a started Popen whose stdin/stdout are the candidate protocol channel. Boots the instance first
    unless boot=False (the broker boots + provisions the corpus, then launches the candidate on the running
    guest). The caller drives the one-query-at-a-time loop and must call vm_teardown in a finally."""
    popen = popen or subprocess.Popen
    if boot:
        vm_start(run_id, paths, runner=runner)
    return popen(vm_shell_argv(run_id, paths, guest_cmd, guest_env=guest_env, guest_user=guest_user),
                 env=dict(_DOOR_ENV), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                 close_fds=True, start_new_session=True)


def vm_teardown(run_id: str, paths, *, runner=None) -> None:
    """Destroy the instance (through the door). Idempotent and never raises."""
    runner = runner or subprocess.run
    try:
        runner(vm_door(paths) + ["vm-delete", vm_instance_name(run_id)[1:]], env=dict(_DOOR_ENV),
               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=TEARDOWN_TIMEOUT + 30)
    except (OSError, subprocess.SubprocessError, SealRefusal):
        pass


def vm_leftover_instances(paths, *, runner=None) -> list:
    """Instances still present (through the door). A non-empty list -- or a failed listing, reported as a
    sentinel entry -- is a teardown failure that must burn the run's items."""
    runner = runner or subprocess.run
    try:
        done = runner(vm_door(paths) + ["vm-list"], env=dict(_DOOR_ENV), stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT + 30)
    except (OSError, subprocess.SubprocessError):
        return ["<limactl-list-failed>"]
    if getattr(done, "returncode", 1) != 0:
        return ["<limactl-list-failed>"]
    out = getattr(done, "stdout", b"") or b""
    return [n for n in out.decode("ascii", "replace").split() if n]


# ------------------------------------------------------------------ read-only corpus provisioning
#
# Invariant (Rev 3 §7k.1): the HOST supplies corpus CONTENT only; the GUEST constructs the immutable
# read-only filesystem. The host never builds or verifies a mountable image -- it stages a tar of the frozen
# corpus content, and the guest extracts it, re-derives corpus_sha256 (binding the declared identity to the
# bytes that actually reach the VM), builds the squashfs and loop-mounts it read-only. See
# scripts/eval_seal/broker.py broker_provision_corpus.

GUEST_CORPUS_SQUASHFS = "/tmp/z1b-corpus.squashfs"  # built IN-GUEST from the staged content, then wiped (the
GUEST_CORPUS_MOUNT = "/opt/z1b/corpus"              #   loop mount keeps the data; the file itself is removed)
GUEST_CORPUS_TAR = "/tmp/z1b-corpus.tar"            # the host stages corpus CONTENT here (never a host image)
GUEST_CORPUS_CONTENT = "/tmp/z1b-corpus-content"    # extracted + digest-verified + squashed in-guest, then wiped


def vm_copy_into_guest(run_id: str, paths, src, dst: str, *, runner=None) -> None:
    """Copy a host file into the running guest, through the door (scp over lima's ssh; NOT a host mount). The
    door accepts only a regular file under the stage dir. Raises CANDIDATE_FAILED on failure."""
    runner = runner or subprocess.run
    argv = vm_door(paths) + ["vm-copy", vm_instance_name(run_id)[1:], str(src), dst]
    try:
        done = runner(argv, env=dict(_DOOR_ENV), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, timeout=TEARDOWN_TIMEOUT + 300)
    except (OSError, subprocess.SubprocessError):
        raise SealRefusal("CANDIDATE_FAILED") from None
    if getattr(done, "returncode", 1) != 0:
        raise SealRefusal("CANDIDATE_FAILED")
