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


# ---------------------------------------------------------------- local lifecycle (runs AS the VM owner)
#
# local_* run limactl directly. In production only the candidate-run door (as zurvancandidate) calls them;
# the dev VM-ISO harness calls them as the developer.

def test_t1vm_local_start_builds_the_pinned_launcher_argv_and_writes_the_config(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.local_start(RID, p, runner=r)
    argv, kw = r.calls[0]
    assert argv[0] == str(p.broker_bin / "limactl") and argv[1] == "start"
    assert "--name=z" + RID in argv
    cfg = Path(argv[-1])
    assert cfg.read_text() == V.vm_config_text() and cfg.name == "z" + RID + ".yaml"
    # nothing inherited; LIMA_HOME is the short shared candidate home dir
    assert kw["env"]["LIMA_HOME"] == str(p.candidate_home / "lima")
    assert "PYTHONPATH" not in kw["env"]


def test_t1vm_local_start_renders_the_golden_profile_when_asked(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.local_start(RID, p, runner=r, image_location="file:///g/guest.qcow2", profile="golden")
    cfg = Path(r.calls[0][0][-1]).read_text()
    assert cfg == V.vm_config_text("file:///g/guest.qcow2", "golden")


def test_t1vm_local_start_refuses_a_leftover_instance_as_a_sandbox_violation(tmp_path):
    p = _vm_paths(tmp_path)
    (V._vm_lima_home(p) / V.vm_instance_name(RID)).mkdir(parents=True)  # a prior teardown left the instance
    with pytest.raises(SealRefusal) as ei:
        V.local_start(RID, p, runner=_vm_recorder())
    assert ei.value.code == "SANDBOX_VIOLATION"


def test_t1vm_local_start_turns_a_failed_boot_into_candidate_failed(tmp_path):
    p = _vm_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        V.local_start(RID, p, runner=_vm_recorder(returncode=1))
    assert ei.value.code == "CANDIDATE_FAILED"


def test_t1vm_local_shell_argv_passes_the_guest_argv_through(tmp_path):
    p = _vm_paths(tmp_path)
    argv = V.local_shell_argv(RID, p, ["/opt/z1b/candidate", "x"])
    assert argv == [str(p.broker_bin / "limactl"), "shell", "z" + RID, "--", "/opt/z1b/candidate", "x"]


def test_t1vm_local_teardown_deletes_the_instance_and_the_per_run_config_but_keeps_the_shared_home(tmp_path):
    p = _vm_paths(tmp_path)
    home = V._vm_lima_home(p)
    home.mkdir(parents=True)
    (home / ("z" + RID + ".yaml")).write_text("cfg")
    r = _vm_recorder()
    V.local_teardown(RID, p, runner=r)
    assert r.calls[0][0][1:3] == ["delete", "-f"] and ("z" + RID) in r.calls[0][0]
    assert not (home / ("z" + RID + ".yaml")).exists()  # per-run config gone
    assert home.exists()  # the shared home (image cache) stays


def test_t1vm_local_teardown_never_raises_even_when_delete_fails(tmp_path):
    p = _vm_paths(tmp_path)
    home = V._vm_lima_home(p)
    home.mkdir(parents=True)
    (home / ("z" + RID + ".yaml")).write_text("cfg")

    def boom(argv, **kw):
        raise OSError("limactl exploded")
    V.local_teardown(RID, p, runner=boom)   # must not raise
    assert not (home / ("z" + RID + ".yaml")).exists()  # still cleans the per-run config


def test_t1vm_local_leftovers_reports_an_instance_a_teardown_left_behind(tmp_path):
    p = _vm_paths(tmp_path)
    V._vm_lima_home(p).mkdir(parents=True)
    assert V.local_leftovers(p, runner=_vm_recorder(stdout=b"")) == []
    assert V.local_leftovers(p, runner=_vm_recorder(stdout=b"z" + RID.encode() + b"\n")) == ["z" + RID]


def test_t1vm_config_uses_a_pinned_image_location_override_when_given():
    # production pins the image FILE (guest has no network); the default stays the pinned URL
    assert 'location: "file:///pinned/alpine.qcow2"' in V.vm_config_text("file:///pinned/alpine.qcow2")
    assert V.GUEST_IMAGE_URL in V.vm_config_text()


def test_t1vm_golden_profile_pins_the_golden_digest_and_a_viable_machine():
    """Regression (operator gate 2026-10-05): the broker rendered the ALPINE profile for the golden image, so
    Lima aborted on a digest mismatch; it also asked for 1 GiB and a 4 GiB disk for a 20 GiB torch image."""
    cfg = V.vm_config_text("file:///g/guest.qcow2", "golden")
    assert V.GOLDEN_IMAGE_DIGEST in cfg and V.GUEST_IMAGE_DIGEST not in cfg
    assert 'location: "file:///g/guest.qcow2"' in cfg and "mounts: []" in cfg
    assert 'memory: "4GiB"' in cfg and "disk:" not in cfg            # lima's default sparse disk (>= image)
    assert "hostResolver:\n  enabled: false" in cfg
    with pytest.raises(SealRefusal):
        V.vm_config_text(None, "golden")                              # the golden profile needs a pinned file
    with pytest.raises(SealRefusal):
        V.vm_config_text("file:///x", "bogus")


def test_t1vm_local_copy_uses_limactl_copy_not_a_host_mount(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.local_copy(RID, p, tmp_path / "corpus.tar", V.GUEST_CORPUS_TAR, runner=r)
    argv = r.calls[0][0]
    assert argv[1] == "copy" and argv[-1] == "z" + RID + ":" + V.GUEST_CORPUS_TAR


# ---------------------------------------------------------------- the broker side: through the sudo door

def _door(p):
    return ["/usr/bin/sudo", "-n", "-u", "zurvancandidate", str(p.broker_bin / "zurvan-candidate-run")]


def test_t1vm_broker_never_runs_limactl_itself_every_call_goes_through_the_door(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.vm_start(RID, p, runner=r)
    V.vm_teardown(RID, p, runner=r)
    V.vm_leftover_instances(p, runner=r)
    V.vm_copy_into_guest(RID, p, "/stage/c.tar", V.GUEST_CORPUS_TAR, runner=r)
    V.vm_run(RID, p, ["true"], runner=r)
    verbs = [c[0][len(_door(p))] for c in r.calls]
    assert verbs == ["vm-start", "vm-delete", "vm-list", "vm-copy", "vm-shell"]
    for argv, kw in r.calls:
        assert argv[:5] == _door(p) and str(p.broker_bin / "limactl") not in argv
        assert kw["env"] == {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}           # nothing inherited


def test_t1vm_door_start_maps_a_leftover_to_sandbox_violation_and_other_failures_to_candidate_failed(tmp_path):
    p = _vm_paths(tmp_path)
    V.vm_start(RID, p, runner=_vm_recorder())                                    # rc 0: booted
    with pytest.raises(SealRefusal) as ei:
        V.vm_start(RID, p, runner=_vm_recorder(returncode=V.DOOR_RC_LEFTOVER))
    assert ei.value.code == "SANDBOX_VIOLATION"
    for rc in (1, 2, 3):
        with pytest.raises(SealRefusal) as ei:
            V.vm_start(RID, p, runner=_vm_recorder(returncode=rc))
        assert ei.value.code == "CANDIDATE_FAILED"


def test_t1vm_door_shell_argv_runs_the_candidate_as_the_unprivileged_guest_user_with_env_minus_i(tmp_path):
    p = _vm_paths(tmp_path)
    argv = V.vm_shell_argv(RID, p, ["/opt/z1b/x"], guest_env={"B": "2", "A": "1"}, guest_user="z1bcand")
    assert argv == _door(p) + ["vm-shell", RID, "--", "/usr/bin/sudo", "-n", "-u", "z1bcand", "--",
                               "/usr/bin/env", "-i", "A=1", "B=2", "/opt/z1b/x"]
    for bad in ("root;x", "Z1B", "", "a b"):
        with pytest.raises(SealRefusal):
            V.vm_shell_argv(RID, p, ["/x"], guest_user=bad)


def test_t1vm_door_launch_boots_then_returns_a_shell_popen_on_the_protocol_channel(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    seen = {}

    def fake_popen(argv, **kw):
        seen["argv"] = list(argv)
        seen["kw"] = kw
        return types.SimpleNamespace(stdin=object(), stdout=object(), pid=4321)
    proc = V.vm_launch(RID, p, ["/opt/z1b/candidate"], runner=r, popen=fake_popen, guest_user="z1bcand")
    assert r.calls[0][0][len(_door(p))] == "vm-start"            # booted first, through the door
    assert seen["argv"][:len(_door(p)) + 3] == _door(p) + ["vm-shell", RID, "--"]
    assert seen["kw"]["stdin"] is subprocess.PIPE and seen["kw"]["stdout"] is subprocess.PIPE
    assert seen["kw"]["close_fds"] is True and proc.pid == 4321


def test_t1vm_door_teardown_never_raises_and_a_failed_listing_is_a_leftover(tmp_path):
    p = _vm_paths(tmp_path)

    def boom(argv, **kw):
        raise OSError("sudo exploded")
    V.vm_teardown(RID, p, runner=boom)                                            # must not raise
    assert V.vm_leftover_instances(p, runner=boom) == ["<limactl-list-failed>"]
    assert V.vm_leftover_instances(p, runner=_vm_recorder(returncode=3)) == ["<limactl-list-failed>"]
    assert V.vm_leftover_instances(p, runner=_vm_recorder(stdout=b"z" + RID.encode() + b"\n")) == ["z" + RID]
    assert V.vm_leftover_instances(p, runner=_vm_recorder(stdout=b"")) == []


def test_t1vm_door_copy_failure_is_candidate_failed(tmp_path):
    p = _vm_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        V.vm_copy_into_guest(RID, p, "/stage/c.tar", V.GUEST_CORPUS_TAR, runner=_vm_recorder(returncode=2))
    assert ei.value.code == "CANDIDATE_FAILED"
