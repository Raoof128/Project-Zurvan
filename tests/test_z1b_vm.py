"""Z1b Rev 3 #1 unit tests: the VM runner lifecycle with a FAKE launcher (no real VM here; the real boot is
the VM-ISO live gate in tests/test_z1b_vm_live.py). These pin the config knobs that the boot experiment
proved necessary, the short-path discipline, and that teardown always runs and never raises."""
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import vm_runner as V  # noqa: E402
from scripts.eval_seal.codes import SealRefusal  # noqa: E402
from scripts.eval_seal.paths import SealPaths  # noqa: E402

RID = "0123456789abcdef"


def _vm_paths(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    for d in (p.broker_bin, p.scratch_dir):
        d.mkdir(parents=True, exist_ok=True)
    return p


def _vm_recorder(returncode=0, stdout=b""):
    calls = []

    def runner(argv, **kw):
        calls.append((list(argv), kw))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=b"")
    runner.calls = calls
    return runner


def test_t1vm_config_has_no_mounts_containerd_off_and_a_pinned_digest():
    cfg = V.vm_config_text()
    assert "mounts: []" in cfg
    assert "containerd:\n  system: false\n  user: false" in cfg
    assert "sha512:" in cfg and "nocloud_alpine" in cfg and 'arch: "aarch64"' in cfg


def test_t1vm_instance_name_is_short_and_derived_from_the_run_id():
    assert V.vm_instance_name(RID) == "z" + RID
    for bad in ("", "nothex", "0123", RID + "0", RID.upper(), None):
        with pytest.raises(SealRefusal):
            V.vm_instance_name(bad)


def test_t1vm_lima_home_stays_well_under_the_ssh_sock_limit(tmp_path):
    # the VZ ssh.sock path is <LIMA_HOME>/<name>/ssh.sock and must be < 104 chars (UNIX_PATH_MAX). Under a
    # real sealed scratch (/Users/zurvancandidate/scratch) the name is short; assert the suffix budget here.
    p = SealPaths.real()
    home = V._vm_lima_home(p)
    # lima's real control socket is <LIMA_HOME>/<name>/ssh.sock.<16 digits>; budget for the whole thing.
    sock = str(home / V.vm_instance_name(RID)) + "/ssh.sock." + "0" * 16
    assert len(sock) < 104, (len(sock), sock)
    assert home == p.candidate_home / "lima"


def test_t1vm_start_builds_the_pinned_launcher_argv_and_writes_the_config(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.vm_start(RID, p, runner=r)
    argv, kw = r.calls[0]
    assert argv[0] == str(p.broker_bin / "limactl") and argv[1] == "start"
    assert "--name=z" + RID in argv
    cfg = Path(argv[-1])
    assert cfg.read_text() == V.vm_config_text() and cfg.name == "z" + RID + ".yaml"
    # nothing inherited; LIMA_HOME is the short shared candidate home dir
    assert kw["env"]["LIMA_HOME"] == str(p.candidate_home / "lima")
    assert "PYTHONPATH" not in kw["env"]


def test_t1vm_start_refuses_a_leftover_instance_as_a_sandbox_violation(tmp_path):
    p = _vm_paths(tmp_path)
    (V._vm_lima_home(p) / V.vm_instance_name(RID)).mkdir(parents=True)  # a prior teardown left the instance
    with pytest.raises(SealRefusal) as ei:
        V.vm_start(RID, p, runner=_vm_recorder())
    assert ei.value.code == "SANDBOX_VIOLATION"


def test_t1vm_start_turns_a_failed_boot_into_candidate_failed(tmp_path):
    p = _vm_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        V.vm_start(RID, p, runner=_vm_recorder(returncode=1))
    assert ei.value.code == "CANDIDATE_FAILED"


def test_t1vm_shell_argv_runs_the_guest_candidate_inside_the_named_instance(tmp_path):
    p = _vm_paths(tmp_path)
    argv = V.vm_shell_argv(RID, p, ["/opt/z1b/candidate"])
    assert argv == [str(p.broker_bin / "limactl"), "shell", "z" + RID, "--", "/opt/z1b/candidate"]


def test_t1vm_launch_boots_then_returns_a_shell_popen_on_the_protocol_channel(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = list(argv)
        seen["kw"] = kw
        return types.SimpleNamespace(stdin=object(), stdout=object(), pid=4321)
    proc = V.vm_launch(RID, p, ["/opt/z1b/candidate"], runner=r, popen=fake_popen)
    assert r.calls[0][0][1] == "start"            # booted first
    assert seen["argv"][1] == "shell"             # then a shell on the instance
    assert seen["kw"]["stdin"] is subprocess.PIPE and seen["kw"]["stdout"] is subprocess.PIPE
    assert seen["kw"]["close_fds"] is True and proc.pid == 4321


def test_t1vm_teardown_deletes_the_instance_and_the_per_run_config_but_keeps_the_shared_home(tmp_path):
    p = _vm_paths(tmp_path)
    home = V._vm_lima_home(p)
    home.mkdir(parents=True)
    (home / ("z" + RID + ".yaml")).write_text("cfg")
    r = _vm_recorder()
    V.vm_teardown(RID, p, runner=r)
    assert r.calls[0][0][1:3] == ["delete", "-f"] and ("z" + RID) in r.calls[0][0]
    assert not (home / ("z" + RID + ".yaml")).exists()  # per-run config gone
    assert home.exists()  # the shared home (image cache) stays


def test_t1vm_teardown_never_raises_even_when_delete_fails(tmp_path):
    p = _vm_paths(tmp_path)
    home = V._vm_lima_home(p)
    home.mkdir(parents=True)
    (home / ("z" + RID + ".yaml")).write_text("cfg")

    def boom(argv, **kw):
        raise OSError("limactl exploded")
    V.vm_teardown(RID, p, runner=boom)   # must not raise
    assert not (home / ("z" + RID + ".yaml")).exists()  # still cleans the per-run config


def test_t1vm_leftover_instances_reports_an_instance_a_teardown_left_behind(tmp_path):
    p = _vm_paths(tmp_path)
    V._vm_lima_home(p).mkdir(parents=True)
    assert V.vm_leftover_instances(p, runner=_vm_recorder(stdout=b"")) == []
    assert V.vm_leftover_instances(p, runner=_vm_recorder(stdout=b"z" + RID.encode() + b"\n")) == ["z" + RID]


def test_t1vm_config_uses_a_pinned_image_location_override_when_given():
    # production pins the image FILE (guest has no network); the default stays the pinned URL
    assert 'location: "file:///pinned/alpine.qcow2"' in V.vm_config_text("file:///pinned/alpine.qcow2")
    assert V.GUEST_IMAGE_URL in V.vm_config_text()


# ---------------------------------------------------------------- read-only corpus provisioning (unit)

def _t1vm_squashfs(tmp_path, body=b"hsqs" + b"\x00" * 60):
    f = tmp_path / "corpus.squashfs"
    f.write_bytes(body)
    return f


def test_t1vm_verify_corpus_image_accepts_a_squashfs_matching_the_declared_digest(tmp_path):
    import hashlib
    img = _t1vm_squashfs(tmp_path)
    digest = hashlib.sha256(img.read_bytes()).hexdigest()
    V.verify_corpus_image(img, digest)  # no raise


def test_t1vm_verify_corpus_image_rejects_the_wrong_disk_even_if_read_only(tmp_path):
    import hashlib
    img = _t1vm_squashfs(tmp_path)
    wrong = hashlib.sha256(b"a different corpus").hexdigest()
    with pytest.raises(SealRefusal) as ei:
        V.verify_corpus_image(img, wrong)       # right magic, wrong bytes
    assert ei.value.code == "CORPUS_MISMATCH"


def test_t1vm_verify_corpus_image_rejects_a_non_squashfs_or_symlink(tmp_path):
    import hashlib
    notsq = tmp_path / "ext4.img"
    notsq.write_bytes(b"\x53\xef" + b"\x00" * 62)  # not the squashfs magic
    with pytest.raises(SealRefusal):
        V.verify_corpus_image(notsq, hashlib.sha256(notsq.read_bytes()).hexdigest())
    link = tmp_path / "link.squashfs"
    link.symlink_to(_t1vm_squashfs(tmp_path))
    with pytest.raises(SealRefusal):
        V.verify_corpus_image(link, "0" * 64)
    with pytest.raises(SealRefusal):
        V.verify_corpus_image(tmp_path / "missing", "0" * 64)


def test_t1vm_copy_into_guest_uses_limactl_copy_not_a_host_mount(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.vm_copy_into_guest(RID, p, tmp_path / "c.squashfs", "/tmp/z1b-corpus.squashfs", runner=r)
    argv = r.calls[0][0]
    assert argv[1] == "copy" and argv[-1] == "z" + RID + ":/tmp/z1b-corpus.squashfs"


def test_t1vm_copy_into_guest_failure_is_candidate_failed(tmp_path):
    p = _vm_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        V.vm_copy_into_guest(RID, p, tmp_path / "c", "/tmp/x", runner=_vm_recorder(returncode=1))
    assert ei.value.code == "CANDIDATE_FAILED"
