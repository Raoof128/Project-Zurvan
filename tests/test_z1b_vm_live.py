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

from scripts.eval_seal import broker as B  # noqa: E402
from scripts.eval_seal import vm_runner as V  # noqa: E402
from scripts.eval_seal.codes import SealRefusal  # noqa: E402
from scripts.eval_seal.corpus import corpus_sha256  # noqa: E402
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
    argv = V.local_shell_argv(run_id, p, ["sh", "-c", script])
    env = V._vm_env(p, V._vm_lima_home(p))
    return subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)


def test_t1_vm_iso_gate_boundary_holds_end_to_end(tmp_path):
    p, root = _live_paths(tmp_path)
    run_id = "00000000" + ("%08x" % (os.getpid() & 0xffffffff))
    booted = False
    try:
        V.local_start(run_id, p, image_location=_ensure_image())
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
        V.local_teardown(run_id, p)
    # (4) teardown left no reusable guest state: no instance remains and the per-run config is gone. The
    # shared LIMA_HOME persists on purpose (it caches the pinned image); it is NOT reusable guest state.
    if booted:
        assert V.local_leftovers(p) == []
        assert not (V._vm_lima_home(p) / (V.vm_instance_name(run_id) + ".yaml")).exists()
    shutil.rmtree(str(root), ignore_errors=True)


def test_t1_vm_iso_gate_a_killed_run_leaves_no_reusable_guest(tmp_path):
    p, root = _live_paths(tmp_path)
    run_id = "ffffffff" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.local_start(run_id, p, image_location=_ensure_image())
        proc = subprocess.Popen(V.local_shell_argv(run_id, p, ["sh", "-c", "sleep 600"]),
                                env=V._vm_env(p, V._vm_lima_home(p)), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(1)
        proc.kill()                      # abnormal termination mid-run
        proc.wait(timeout=30)
    finally:
        V.local_teardown(run_id, p)
    assert V.local_leftovers(p) == []
    assert not (V._vm_lima_home(p) / (V.vm_instance_name(run_id) + ".yaml")).exists()
    shutil.rmtree(str(root), ignore_errors=True)


_GOLDEN = Path(os.environ.get("ZURVAN_Z1B_GOLDEN", str(Path.home() / ".zurvan" / "z1b" / "guest.qcow2")))


def _golden_image():
    """The pinned GOLDEN guest image (squashfs-tools + /opt/z1b python runtime baked in) -- the in-guest
    corpus build needs it, the Alpine isolation base does not have it. Absent or wrong digest => the in-guest
    corpus gate is NOT CONFIGURED here (UNVERIFIED), never a false pass."""
    if not _GOLDEN.is_file():
        pytest.skip("Z1b: NOT CONFIGURED - golden guest image not present at %s; the in-guest read-only "
                    "corpus gate is UNVERIFIED here" % _GOLDEN)
    import hashlib
    h = hashlib.sha512()
    with open(str(_GOLDEN), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if "sha512:" + h.hexdigest() != V.GOLDEN_IMAGE_DIGEST:
        pytest.skip("Z1b: NOT CONFIGURED - golden image digest does not match the pinned "
                    "GOLDEN_IMAGE_DIGEST; the in-guest corpus gate is UNVERIFIED here")
    return "file://" + str(_GOLDEN)


def _write_live_corpus(p):
    """A small frozen corpus CONTENT tree (no squashfs on the host); returns its corpus_sha256."""
    c = Path(p.corpus_dir)
    shutil.rmtree(str(c), ignore_errors=True)
    (c / "pages").mkdir(parents=True)
    (c / "search.sqlite").write_bytes(b"SQLite format 3\x00frozen-index-bytes")
    (c / "pages" / "doc1.md").write_text("known-corpus-content-v1\n")
    (c / "pages" / "doc2.md").write_text("second doc\n")
    return corpus_sha256(c)


def _local_door(monkeypatch, p):
    """In production the broker reaches the VM through the candidate-run door (as zurvancandidate). This dev
    harness has no installed door, so the broker's door calls are replaced by the SAME local_* code the door
    runs -- the guest script, staging and digest checks are the broker's own."""
    def run(rid, paths, guest_cmd, *, runner=None, timeout=V.TEARDOWN_TIMEOUT):
        done = subprocess.run(V.local_shell_argv(rid, paths, guest_cmd), env=V._vm_env(paths, V._vm_lima_home(paths)),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if done.returncode != 0:
            raise SealRefusal("CANDIDATE_FAILED")
        return done
    monkeypatch.setattr(V, "vm_run", run)
    monkeypatch.setattr(V, "vm_copy_into_guest", lambda rid, paths, src, dst, **kw: V.local_copy(rid, paths, src, dst))
    p.stage_dir.mkdir(parents=True, exist_ok=True)


def test_t1_vm_iso_gate_read_only_corpus_boundary(tmp_path, monkeypatch):
    # The HOST supplies corpus CONTENT only; the GUEST builds the immutable read-only filesystem. This drives
    # the REAL broker_provision_corpus (content tar -> in-guest digest-verify -> mksquashfs -> loop ro mount)
    # against the golden image, then proves the closure properties the reviewer required.
    image = _golden_image()
    p, root = _live_paths(tmp_path)
    _local_door(monkeypatch, p)
    declared = _write_live_corpus(p)
    import hashlib
    before = {q.name: hashlib.sha256(q.read_bytes()).hexdigest()
              for q in sorted(Path(p.corpus_dir).rglob("*")) if q.is_file()}
    run_id = "cccccccc" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.local_start(run_id, p, image_location=image, profile="golden")
        B.broker_provision_corpus(run_id, p, declared)            # the real content->guest->ro-mount path
        # the candidate can READ the known content on the mount
        got = _shell(p, run_id, "cat %s/pages/doc1.md" % V.GUEST_CORPUS_MOUNT)
        assert b"known-corpus-content-v1" in got.stdout
        # write / create / delete / rename all FAIL (squashfs is physically read-only, even under sudo)
        for script in ("echo x > %s/pages/doc1.md" % V.GUEST_CORPUS_MOUNT,
                       "touch %s/new" % V.GUEST_CORPUS_MOUNT,
                       "rm %s/pages/doc2.md" % V.GUEST_CORPUS_MOUNT,
                       "mv %s/pages/doc1.md %s/renamed" % (V.GUEST_CORPUS_MOUNT, V.GUEST_CORPUS_MOUNT)):
            r = _shell(p, run_id, "sudo sh -c '%s' 2>&1; echo rc=$?" % script)
            assert b"rc=0" not in r.stdout, (script, r.stdout)
        # in-guest isolation of the candidate (operator gate 2026-10-05): it runs as the no-sudo guest user, reads
        # the corpus, and every non-loopback packet it sends -- TCP and DNS alike -- is REJECTed. This dev VM's
        # hostagent has NO pf block, so the in-guest layer must hold on its own here.
        U = B.GUEST_CANDIDATE_USER
        rd = _shell(p, run_id, "sudo -n -u %s cat %s/pages/doc1.md" % (U, V.GUEST_CORPUS_MOUNT))
        assert b"known-corpus-content-v1" in rd.stdout, rd.stdout
        su = _shell(p, run_id, "sudo -n -u %s sudo -n true 2>/dev/null; echo rc=$?" % U)
        assert b"rc=0" not in su.stdout, su.stdout                              # the candidate has no sudo
        probe = ("python3 -c \\\"import socket; socket.create_connection(('1.1.1.1', 443), timeout=5)\\\"")
        tcp = _shell(p, run_id, "sudo -n -u %s sh -c \"%s\" 2>/dev/null; echo rc=$?" % (U, probe))
        assert b"rc=0" not in tcp.stdout, tcp.stdout                            # TCP egress blocked
        dns = _shell(p, run_id, "sudo -n -u %s getent hosts example.com; echo rc=$?" % U)
        assert b"rc=0" not in dns.stdout, dns.stdout                            # DNS exfil channel blocked
        ctl = _shell(p, run_id, "getent hosts example.com >/dev/null; echo rc=$?")
        assert b"rc=0" in ctl.stdout, ctl.stdout     # control: the login user still resolves, so the block is z1bcand's
        # cleanup: the staged plaintext, the tar and the squashfs FILE are gone; only the ro mount remains
        for gone in (V.GUEST_CORPUS_CONTENT, V.GUEST_CORPUS_TAR, V.GUEST_CORPUS_SQUASHFS):
            g = _shell(p, run_id, "test -e %s; echo rc=$?" % gone)
            assert b"rc=1" in g.stdout, (gone, g.stdout)
        # no host mount leaked in
        assert b"rc=0" not in _shell(p, run_id, "ls /Users 2>&1; echo rc=$?").stdout
        # MUTATION / positive control: the read-only guarantee is squashfs being physically unwritable. Prove
        # the write-probe is load-bearing (not vacuously failing) by running the SAME probe against a writable
        # tmpfs: it must SUCCEED. A corpus wired to a writable fs would therefore turn every write assertion
        # above red -- which is exactly the "no fallback to a writable dir" guarantee.
        ctrl = _shell(p, run_id, "sudo sh -c 'mkdir -p /tmp/rwctl && mount -t tmpfs none /tmp/rwctl && "
                                 "echo x > /tmp/rwctl/probe' 2>&1; echo rc=$?")
        assert b"rc=0" in ctrl.stdout, ctrl.stdout
    finally:
        V.local_teardown(run_id, p)
    # the host source corpus is byte-for-byte unchanged (the host only read it), and teardown left nothing
    after = {q.name: hashlib.sha256(q.read_bytes()).hexdigest()
             for q in sorted(Path(p.corpus_dir).rglob("*")) if q.is_file()}
    assert after == before and not list(Path(p.corpus_dir).glob("*.squashfs"))
    assert V.local_leftovers(p) == []

    # THE tamper gate: the digest is declared, then one corpus byte changes before provisioning. The guest
    # re-derives corpus_sha256 of the bytes it received and REFUSES -> provisioning raises CANDIDATE_FAILED.
    (Path(p.corpus_dir) / "pages" / "doc1.md").write_text("TAMPERED-after-digest\n")
    run_t = "eeeeeeee" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.local_start(run_t, p, image_location=image, profile="golden")
        with pytest.raises(SealRefusal) as ei:
            B.broker_provision_corpus(run_t, p, declared)         # declared != bytes now reaching the guest
        assert ei.value.code == "CANDIDATE_FAILED"
        # and nothing got mounted (fail-closed, no writable fallback)
        assert b"rc=0" not in _shell(p, run_t, "test -e %s/pages; echo rc=$?" % V.GUEST_CORPUS_MOUNT).stdout
    finally:
        V.local_teardown(run_t, p)
    assert V.local_leftovers(p) == []

    # a second clean guest starts from the PRISTINE corpus (no state carried over from any prior run)
    declared2 = _write_live_corpus(p)
    run2 = "dddddddd" + ("%08x" % (os.getpid() & 0xffffffff))
    try:
        V.local_start(run2, p, image_location=image, profile="golden")
        B.broker_provision_corpus(run2, p, declared2)
        g2 = _shell(p, run2, "cat %s/pages/doc1.md; ls %s/pages" % (V.GUEST_CORPUS_MOUNT, V.GUEST_CORPUS_MOUNT))
        assert b"known-corpus-content-v1" in g2.stdout and b"doc2.md" in g2.stdout and b"new" not in g2.stdout
    finally:
        V.local_teardown(run2, p)
    assert V.local_leftovers(p) == []
    shutil.rmtree(str(root), ignore_errors=True)
