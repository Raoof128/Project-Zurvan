"""Z1b Rev 3 #1 unit tests: the VM runner lifecycle with a FAKE launcher (no real VM here; the real boot is
the VM-ISO live gate in tests/test_z1b_vm_live.py). These pin the config knobs that the boot experiment
proved necessary, the short-path discipline, and that teardown always runs and never raises."""
import os
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
    home = V._vm_lima_home(p, RID)
    # lima's real control socket is <LIMA_HOME>/<name>/ssh.sock.<16 digits>; budget for the whole thing.
    sock = str(home / V.vm_instance_name(RID)) + "/ssh.sock." + "0" * 16
    assert len(sock) < 104, (len(sock), sock)
    assert home == p.candidate_home / "l" / RID                        # a FRESH lima home per run


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
    assert kw["env"]["LIMA_HOME"] == str(p.candidate_home / "l" / RID)
    assert "PYTHONPATH" not in kw["env"]


def test_t1vm_local_start_renders_the_golden_profile_when_asked(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.local_start(RID, p, runner=r, image_location="file:///g/guest.qcow2", profile="golden")
    cfg = Path(r.calls[0][0][-1]).read_text()
    assert cfg == V.vm_config_text("file:///g/guest.qcow2", "golden", V.vm_ssh_port(RID))


def test_t1vm_local_start_refuses_a_leftover_instance_as_a_sandbox_violation(tmp_path):
    p = _vm_paths(tmp_path)
    V._vm_lima_home(p, RID).mkdir(parents=True)        # a prior teardown left this run's lima home
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


def test_t1vm_local_teardown_removes_the_whole_per_run_lima_home(tmp_path):
    """Security review 2026-10-05 (cross-run integrity): lima merges $LIMA_HOME/_config/{default,override}.yaml
    into every instance, so nothing of a run's lima home -- config, keys, instance -- may survive it."""
    p = _vm_paths(tmp_path)
    home = V._vm_lima_home(p, RID)
    (home / "_config").mkdir(parents=True)
    (home / "_config" / "override.yaml").write_text("mounts: [{location: /}]\n")    # planted config
    (home / ("z" + RID + ".yaml")).write_text("cfg")
    r = _vm_recorder()
    V.local_teardown(RID, p, runner=r)
    assert r.calls[0][0][1:3] == ["delete", "-f"] and ("z" + RID) in r.calls[0][0]
    assert r.calls[0][1]["env"]["LIMA_HOME"] == str(home)
    assert not home.exists() and V._vm_lima_base(p).exists()          # the run's home is gone entirely


def test_t1vm_local_teardown_never_raises_and_never_follows_a_planted_symlink(tmp_path):
    p = _vm_paths(tmp_path)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("k")
    V._vm_lima_base(p).mkdir(parents=True)
    V._vm_lima_home(p, RID).symlink_to(victim)                        # a link planted at the run path

    def boom(argv, **kw):
        raise OSError("limactl exploded")
    V.local_teardown(RID, p, runner=boom)                             # must not raise
    assert not os.path.lexists(str(V._vm_lima_home(p, RID)))           # the link itself is removed...
    assert (victim / "keep").exists()                                  # ...and its target is untouched


def test_t1vm_each_run_gets_a_fresh_lima_home_and_a_leftover_is_reported(tmp_path):
    p = _vm_paths(tmp_path)
    other = "fedcba9876543210"
    assert V._vm_lima_home(p, RID) != V._vm_lima_home(p, other)
    assert V.local_leftovers(p) == []                                  # nothing yet
    V.local_start(RID, p, runner=_vm_recorder())
    assert V.local_leftovers(p) == ["z" + RID]                         # a live/left home is a leftover
    V.local_teardown(RID, p, runner=_vm_recorder())
    assert V.local_leftovers(p) == []
    (V._vm_lima_base(p) / "junk").mkdir()
    assert V.local_leftovers(p) == ["<unexpected:junk>"]               # anything unexpected is reported too


def test_t1vm_config_uses_a_pinned_image_location_override_when_given():
    # production pins the image FILE (guest has no network); the default stays the pinned URL
    assert 'location: "file:///pinned/alpine.qcow2"' in V.vm_config_text("file:///pinned/alpine.qcow2")
    assert V.GUEST_IMAGE_URL in V.vm_config_text()


def test_t1vm_golden_profile_pins_the_golden_digest_and_a_viable_machine():
    """Regression (operator gate 2026-10-05): the broker rendered the ALPINE profile for the golden image, so
    Lima aborted on a digest mismatch; it also asked for 1 GiB and a 4 GiB disk for a 20 GiB torch image."""
    cfg = V.vm_config_text("file:///g/guest.qcow2", "golden", 40123)
    assert V.GOLDEN_IMAGE_DIGEST in cfg and V.GUEST_IMAGE_DIGEST not in cfg
    assert 'location: "file:///g/guest.qcow2"' in cfg and "mounts: []" in cfg
    assert 'memory: "4GiB"' in cfg and "disk:" not in cfg            # lima's default sparse disk (>= image)
    assert "hostResolver:\n  enabled: false" in cfg
    # security review 2026-10-05: the ssh control forward is pinned inside the pf-permitted loopback range, and
    # lima's automatic guest->host port forwards are all ignored (no guest service appears on host loopback)
    assert "ssh:\n  localPort: 40123\n" in cfg
    assert cfg.count("guestPortRange: [1, 65535]\n  ignore: true") == 2
    assert 'guestIP: "0.0.0.0"' in cfg and 'guestIP: "127.0.0.1"' in cfg
    with pytest.raises(SealRefusal):
        V.vm_config_text(None, "golden", 40123)                       # the golden profile needs a pinned file
    for bad_port in (None, 22, V.SSH_PORT_LO - 1, V.SSH_PORT_HI + 1, "40100"):
        with pytest.raises(SealRefusal):
            V.vm_config_text("file:///x", "golden", bad_port)         # never outside the pf-permitted range
    with pytest.raises(SealRefusal):
        V.vm_config_text("file:///x", "bogus")


def test_t1vm_reserved_ssh_range_is_below_the_ephemeral_ports():
    """On lo0 the ssh forward's replies are outbound candidate-owned packets, so pf passes traffic FROM the range;
    that is safe only if no implicit (ephemeral) source port can fall inside it."""
    assert V.SSH_PORT_LO <= V.SSH_PORT_HI < V.EPHEMERAL_PORT_FIRST
    import subprocess as sp
    try:
        first = int(sp.run(["/usr/sbin/sysctl", "-n", "net.inet.ip.portrange.first"], capture_output=True,
                           text=True, timeout=5).stdout.strip())
    except (OSError, ValueError, sp.SubprocessError):
        return
    assert V.SSH_PORT_HI < first, (V.SSH_PORT_HI, first)                 # this machine agrees


def test_t1vm_ssh_port_is_deterministic_per_run_and_inside_the_pf_range():
    ports = {V.vm_ssh_port("%016x" % (i * 7919)) for i in range(500)}
    assert all(V.SSH_PORT_LO <= x <= V.SSH_PORT_HI for x in ports) and len(ports) > 100
    assert V.vm_ssh_port(RID) == V.vm_ssh_port(RID)
    with pytest.raises(SealRefusal):
        V.vm_ssh_port("nothex")


def test_t1vm_vm_env_forces_lima_onto_the_pure_go_resolver(tmp_path):
    """Security review 2026-10-05: Go's default darwin resolver hands lima's host-side DNS to mDNSResponder
    (root), which pf cannot attribute; the pure resolver queries from lima's own candidate-owned socket."""
    p = _vm_paths(tmp_path)
    env = V._vm_env(p, V._vm_lima_home(p, RID))
    assert env["GODEBUG"] == "netdns=go" and env["LIMA_HOME"] == str(V._vm_lima_home(p, RID))
    assert env["HOME"] == str(p.candidate_home) and "PYTHONPATH" not in env


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



def test_t1vm_door_push_tree_passes_the_commit_and_refuses_a_malformed_one(tmp_path):
    p = _vm_paths(tmp_path)
    r = _vm_recorder()
    V.vm_push_tree(RID, p, "c" * 40, runner=r)
    assert r.calls[0][0] == _door(p) + ["vm-push-tree", RID, "c" * 40]
    for bad in ("C" * 40, "c" * 39, "", None, "c" * 40 + "\n"):
        with pytest.raises(SealRefusal):
            V.vm_push_tree(RID, p, bad, runner=_vm_recorder())
    with pytest.raises(SealRefusal) as ei:
        V.vm_push_tree(RID, p, "c" * 40, runner=_vm_recorder(returncode=2))
    assert ei.value.code == "CANDIDATE_FAILED"
