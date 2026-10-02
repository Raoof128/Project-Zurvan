"""Z1b Rev 3 #1 VM-ISO live gate: boot a REAL disposable guest with the pinned config and prove the four
closure properties the reviewer required (not merely that a VM boots):

  1. the candidate workload executes INSIDE the guest (a command runs and returns over the protocol channel);
  2. host-side sealed secrets are inaccessible from the guest (no host mounts -> the host home is not there);
  3. results come back ONLY through the defined stdin/stdout protocol;
  4. teardown after the run leaves no reusable guest state (success, and after a kill).

Needs `limactl` (Lima + lima-additional-guestagents) and Apple Virtualization. On any machine without it the
WHOLE module skips with a reason beginning `Z1b: NOT CONFIGURED`, so check.sh shows the gap as UNVERIFIED,
never a pass. A boot takes ~1-2 min, so this module is marked slow.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import vm_runner as V  # noqa: E402
from scripts.eval_seal.paths import SealPaths  # noqa: E402

_LIMACTL = shutil.which("limactl")
if _LIMACTL is None:
    pytest.skip("Z1b: NOT CONFIGURED - limactl is not installed; the VM-ISO gate is UNVERIFIED on this "
                "machine (brew install lima lima-additional-guestagents)", allow_module_level=True)
# These boot real VMs (minutes) and must run SERIALLY - never in a parallel (xdist) pool, where two guests
# collide on VZ resources. So they are opt-in: the default/parallel suite skips them, and the dedicated
# serial VM-ISO step sets ZURVAN_Z1B_VM_LIVE=1. This mirrors the NOT CONFIGURED discipline: never a false
# pass, and never a spurious parallel failure.
if os.environ.get("ZURVAN_Z1B_VM_LIVE") != "1":
    pytest.skip("Z1b: VM-ISO gate is opt-in and serial; set ZURVAN_Z1B_VM_LIVE=1 to run it (it boots real "
                "VMs). UNVERIFIED in this run.", allow_module_level=True)

_IMG_CACHE = Path("/tmp/z1b-vm-image/alpine-aarch64.qcow2")


def _ensure_image():
    """Pre-fetch the pinned guest image ONCE to a local cache (production pins the image FILE; the guest has
    no network). Retries a few times; if it cannot be obtained the VM-ISO gate is NOT CONFIGURED here, never
    a false pass. Returns a file:// location for the config."""
    if not _IMG_CACHE.exists() or _IMG_CACHE.stat().st_size < 1_000_000:
        _IMG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ok = False
        for _ in range(3):
            r = subprocess.run(["/usr/bin/curl", "-fsSL", "--retry", "2", "-m", "180", "-o",
                                str(_IMG_CACHE), V.GUEST_IMAGE_URL], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            if r.returncode == 0 and _IMG_CACHE.exists() and _IMG_CACHE.stat().st_size > 1_000_000:
                ok = True
                break
        if not ok:
            pytest.skip("Z1b: NOT CONFIGURED - the pinned guest image could not be fetched; the VM-ISO gate "
                        "is UNVERIFIED here (network). The boundary itself is unaffected.")
    return "file://" + str(_IMG_CACHE)


def _live_paths(tmp_path):
    # A SHORT real root so <LIMA_HOME>/<name>/ssh.sock stays under UNIX_PATH_MAX. /tmp/zv-<pid> is short.
    root = Path("/tmp") / ("zv%d" % os.getpid())
    shutil.rmtree(str(root), ignore_errors=True)
    p = SealPaths.for_root(root)
    for d in (p.broker_bin, p.scratch_dir):
        d.mkdir(parents=True, exist_ok=True)
    # Faithfully "pin the whole asset tree" (design s7c): lima resolves its guest agents + templates from
    # <dir-of-limactl>/../share/lima, so a lone binary cannot find them. Copy limactl into broker_dir/bin
    # and share/lima into broker_dir/share/lima; `../share/lima` then resolves from the pinned binary.
    shutil.copy2(os.path.realpath(_LIMACTL), str(p.broker_bin / "limactl"))
    share_src = Path(os.path.realpath(_LIMACTL)).parent.parent / "share" / "lima"
    if not share_src.is_dir():
        share_src = Path("/opt/homebrew/share/lima")
    shutil.copytree(str(share_src), str(p.broker_dir / "share" / "lima"), symlinks=True)
    return p, root


def _shell(p, run_id, script, timeout=60):
    argv = V.vm_shell_argv(run_id, p, ["sh", "-c", script])
    env = V._vm_env(p, V._vm_lima_home(p))
    return subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)


def test_t1_vm_iso_gate_boundary_holds_end_to_end(tmp_path):
    p, root = _live_paths(tmp_path)
    run_id = "00000000" + ("%08x" % (os.getpid() & 0xffffffff))
    booted = False
    try:
        V.vm_start(run_id, p, image_location=_ensure_image())
        booted = True
        # (1) candidate executes inside the guest; (3) result returns only via the protocol stdout
        got = _shell(p, run_id, "printf 'RESULT %s ok\\n' 42")
        assert got.returncode == 0 and b"RESULT 42 ok" in got.stdout
        # the guest really is a separate aarch64 Linux machine
        uname = _shell(p, run_id, "uname -s -m")
        assert b"Linux" in uname.stdout and b"aarch64" in uname.stdout
        # (2) no host crossover: the host home and a host file are not visible inside the guest
        homes = _shell(p, run_id, "ls /Users 2>&1; echo rc=$?")
        assert b"rc=0" not in homes.stdout, homes.stdout
        secret = _shell(p, run_id, "cat %s/CLAUDE.md 2>&1 | head -1; echo rc=${PIPESTATUS[0]}" % str(ROOT))
        assert b"rc=0" not in secret.stdout, secret.stdout
    finally:
        V.vm_teardown(run_id, p)
    # (4) teardown left no reusable guest state: no instance remains and the per-run config is gone. The
    # shared LIMA_HOME persists on purpose (it caches the pinned image); it is NOT reusable guest state.
    if booted:
        assert V.vm_leftover_instances(p) == []
        assert not (V._vm_lima_home(p) / (V.vm_instance_name(run_id) + ".yaml")).exists()
    shutil.rmtree(str(root), ignore_errors=True)


def test_t1_vm_iso_gate_a_killed_run_leaves_no_reusable_guest(tmp_path):
    p, root = _live_paths(tmp_path)
    run_id = "ffffffff" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        proc = V.vm_launch(run_id, p, ["sh", "-c", "sleep 600"], image_location=_ensure_image())
        time.sleep(1)
        proc.kill()                      # abnormal termination mid-run
        proc.wait(timeout=30)
    finally:
        V.vm_teardown(run_id, p)
    assert V.vm_leftover_instances(p) == []
    assert not (V._vm_lima_home(p) / (V.vm_instance_name(run_id) + ".yaml")).exists()
    shutil.rmtree(str(root), ignore_errors=True)


_MKSQUASHFS = shutil.which("mksquashfs")


def _build_corpus(tmp_path):
    """A small synthetic corpus as a squashfs (a read-only filesystem by construction)."""
    if _MKSQUASHFS is None:
        pytest.skip("Z1b: NOT CONFIGURED - mksquashfs absent; read-only corpus gate UNVERIFIED "
                    "(brew install squashfs)")
    src = tmp_path / "corpussrc"
    src.mkdir()
    (src / "doc1.md").write_text("known-corpus-content-v1\n")
    (src / "doc2.md").write_text("second doc\n")
    img = tmp_path / "corpus.squashfs"
    r = subprocess.run([_MKSQUASHFS, str(src), str(img), "-noappend", "-quiet"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert r.returncode == 0 and img.exists()
    import hashlib
    return img, hashlib.sha256(img.read_bytes()).hexdigest()


def _mount_corpus(p, run_id):
    V.vm_copy_into_guest(run_id, p, _CORPUS_IMG, V.GUEST_CORPUS_SQUASHFS)
    m = _shell(p, run_id, "sudo mkdir -p %s && sudo mount -o loop,ro %s %s; echo rc=$?"
               % (V.GUEST_CORPUS_MOUNT, V.GUEST_CORPUS_SQUASHFS, V.GUEST_CORPUS_MOUNT))
    assert b"rc=0" in m.stdout, m.stdout


_CORPUS_IMG = None
_CORPUS_SHA = None


def test_t1_vm_iso_gate_read_only_corpus_boundary(tmp_path):
    global _CORPUS_IMG, _CORPUS_SHA
    _CORPUS_IMG, _CORPUS_SHA = _build_corpus(tmp_path)
    # identity: a read-only disk that is the WRONG disk is refused; the right one passes.
    V.verify_corpus_image(_CORPUS_IMG, _CORPUS_SHA)
    with pytest.raises(Exception):
        V.verify_corpus_image(_CORPUS_IMG, "0" * 64)
    before = _CORPUS_SHA
    p, root = _live_paths(tmp_path)
    run_id = "cccccccc" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.vm_start(run_id, p, image_location=_ensure_image())
        _mount_corpus(p, run_id)
        # the candidate can READ the known file
        got = _shell(p, run_id, "cat %s/doc1.md" % V.GUEST_CORPUS_MOUNT)
        assert b"known-corpus-content-v1" in got.stdout
        # write / create / delete / rename all FAIL (squashfs is physically read-only, even under sudo)
        for script in ("echo x > %s/doc1.md" % V.GUEST_CORPUS_MOUNT,
                       "touch %s/new" % V.GUEST_CORPUS_MOUNT,
                       "rm %s/doc2.md" % V.GUEST_CORPUS_MOUNT,
                       "mv %s/doc1.md %s/renamed" % (V.GUEST_CORPUS_MOUNT, V.GUEST_CORPUS_MOUNT)):
            r = _shell(p, run_id, "sudo sh -c '%s' 2>&1; echo rc=$?" % script)
            assert b"rc=0" not in r.stdout, (script, r.stdout)
        # no host mount leaked in
        assert b"rc=0" not in _shell(p, run_id, "ls /Users 2>&1; echo rc=$?").stdout
        # MUTATION / positive control: the read-only guarantee is squashfs being physically unwritable, so
        # flipping the mount flag to rw cannot make writes succeed (the kernel never writes squashfs). What
        # WOULD flip this gate red is provisioning the corpus on a WRITABLE backing. Prove the write-probe
        # is load-bearing (not vacuously failing) by running the SAME probe against a writable tmpfs: it
        # must SUCCEED. A corpus wired to a writable fs would therefore turn every assertion above red.
        ctrl = _shell(p, run_id, "sudo sh -c 'mkdir -p /tmp/rwctl && mount -t tmpfs none /tmp/rwctl && "
                                 "echo x > /tmp/rwctl/probe' 2>&1; echo rc=$?")
        assert b"rc=0" in ctrl.stdout, ctrl.stdout
    finally:
        V.vm_teardown(run_id, p)
    # the host corpus image is byte-for-byte unchanged, and teardown left nothing
    import hashlib
    assert hashlib.sha256(_CORPUS_IMG.read_bytes()).hexdigest() == before
    assert V.vm_leftover_instances(p) == []
    # a second guest starts from the PRISTINE corpus (no state carried over from the first run)
    run2 = "dddddddd" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.vm_start(run2, p, image_location=_ensure_image())
        _mount_corpus(p, run2)
        g2 = _shell(p, run2, "cat %s/doc1.md; ls %s" % (V.GUEST_CORPUS_MOUNT, V.GUEST_CORPUS_MOUNT))
        assert b"known-corpus-content-v1" in g2.stdout and b"doc2.md" in g2.stdout and b"new" not in g2.stdout
    finally:
        V.vm_teardown(run2, p)
    assert V.vm_leftover_instances(p) == []
    shutil.rmtree(str(root), ignore_errors=True)
