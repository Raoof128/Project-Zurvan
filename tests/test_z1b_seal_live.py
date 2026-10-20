"""Z1b Task 17d: real-identity tests (spec 9, Z1b gates 1, 1b, 7 and the precondition of gate 9).

These need the real `zurvaneval` / `zurvancandidate` identities and the installed broker. On any other
machine the WHOLE module skips with a reason that begins `Z1b: NOT CONFIGURED`, so `pytest -rs` and
check.sh show the gap as UNVERIFIED, never as a pass. Nothing here runs a model, reads sealed content, or
spends a budget run: every sealed-path check is an open() that must fail, and the broker is only asked
questions it must refuse.
"""
import os
import pwd
import stat
import subprocess
from pathlib import Path

import pytest

import scripts.z1b_client as zc
from scripts.eval_seal.paths import AUTHOR_USER, CANDIDATE_USER, EVAL_USER, SealPaths


def _t17_not_configured_reason():
    try:
        pwd.getpwnam(EVAL_USER)
    except KeyError:
        return ("Z1b: NOT CONFIGURED - no zurvaneval identity on this machine; Z1b gates 1, 1b, 7 and the "
                "precondition of gate 9 are UNVERIFIED here (run setup.sh and install.sh as the operator)")
    if not os.path.exists(zc.BROKER):
        return ("Z1b: NOT CONFIGURED - the zurvaneval identity exists but the broker is not installed at "
                "%s; Z1b gates 1, 1b, 7 and the precondition of gate 9 are UNVERIFIED here" % zc.BROKER)
    return None


_T17_REASON = _t17_not_configured_reason()
if _T17_REASON:
    pytest.skip(_T17_REASON, allow_module_level=True)

REAL = SealPaths.real()
REPO = Path(__file__).resolve().parents[1]
_T17_EARLY_REFUSALS = ("MANIFEST_INVALID", "CORPUS_MISMATCH", "INTEGRITY_INCIDENT", "SCAN_INCOMPLETE")


def _t17_sudo_n(user, *argv):
    """Run `sudo -n -u <user> <argv>`; return (returncode, stdout). Never prompts, never hangs.
    cwd is "/": the target user cannot read the dev checkout, and `python3 -c` puts the cwd on sys.path,
    so from the repo the gate-2 probe died importing json (EACCES) before it opened any sealed path."""
    cp = subprocess.run(["/usr/bin/sudo", "-n", "-u", user] + list(argv), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
                        env={"PATH": "/usr/bin:/bin"}, cwd="/")
    return cp.returncode, cp.stdout


# ------------------------------------------------------------------ gate 1

def test_t17_live_gate1_dev_identity_gets_eacces_on_every_sealed_path():
    doc = zc.local_seal_check(REAL)
    assert doc["configured"] is True, doc["paths"]  # ENOENT is NOT CONFIGURED, never a pass
    bad = {p: v for p, v in doc["paths"].items() if v != "EACCES"}
    assert bad == {}, bad
    assert doc["ok"] is True, doc["checks"]


def test_t17_live_gate1_deny_rules_in_settings_cover_the_same_paths():
    assert zc.local_seal_check(REAL)["checks"]["local_deny_rules_present"] is True


def test_t17_live_gate1_candidate_with_the_sandbox_disabled_still_gets_eacces():
    """The broker's candidate probe opens the sealed paths as zurvancandidate WITHOUT sandbox-exec:
    the sandbox is defence in depth, the filesystem permission is the vault door."""
    doc = zc.client_seal_check()
    assert doc["checks"]["candidate_cannot_read_sealed"] is True, doc["checks"]


def test_t17_live_gate2_author_identity_cannot_read_the_keys_or_sealed_sets():
    """Rev 3 #2: the authoring identity (zurvanauthor) runs the models over the untrusted corpus. It must
    not be able to read the key owner's secrets, so a corpus prompt-injection that tells the author to read
    them hits EACCES. Checked by opening each sealed path AS zurvanauthor via a tiny stdlib probe."""
    probe = ("import os,sys,errno,json\n"
             "r={}\n"
             "for p in sys.argv[1:]:\n"
             "    try:\n"
             "        fd=os.open(p, os.O_RDONLY|os.O_NONBLOCK); os.close(fd); r[p]='READABLE'\n"
             "    except OSError as e:\n"
             "        r[p]='EACCES' if e.errno in (errno.EACCES,errno.EPERM) else 'ENOENT' if e.errno in (errno.ENOENT,errno.ENOTDIR) else 'ERR%d'%e.errno\n"
             "print(json.dumps(r))\n")
    sealed = [str(REAL.keys_dir), str(REAL.keys_dir / "age.key"), str(REAL.sets_dir),
              str(REAL.sets_dir / "holdout.age"), str(REAL.runs_dir), str(REAL.incidents_dir)]
    rc, out = _t17_sudo_n(AUTHOR_USER, "/usr/bin/python3", "-c", probe, *sealed)
    assert rc == 0, out
    import json as _j
    seen = _j.loads(out.decode())
    bad = {p: v for p, v in seen.items() if v == "READABLE"}
    assert bad == {}, bad  # EACCES or ENOENT only; never READABLE


# ------------------------------------------------------------------ gate 1b

def test_t17_live_gate1b_broker_tree_is_root_owned_and_writable_by_no_identity():
    broker_dir = REAL.broker_dir
    root_dev = os.lstat(str(broker_dir)).st_dev
    seen = 0
    for base, dirs, files in os.walk(str(broker_dir), followlinks=False):
        for name in dirs + files:
            st = os.lstat(os.path.join(base, name))
            assert not stat.S_ISLNK(st.st_mode), name
            assert st.st_uid == 0, (base, name)
            assert not st.st_mode & 0o022, (base, name)
            seen += 1
    assert seen > 0 and root_dev


def test_t17_live_gate1b_the_broker_reports_its_own_files_keys_and_exclusions_ok():
    doc = zc.client_seal_check()
    for check in ("broker_root_owned", "broker_not_writable", "tm_excluded_keys"):
        assert doc["checks"].get(check) is True, (check, doc["checks"].get(check))
    assert doc["ok"] is True


def test_t17_live_gate1b_the_dev_identity_cannot_write_into_the_broker_tree(tmp_path):
    for target in (REAL.broker_dir, REAL.broker_bin, REAL.broker_lib):
        assert not os.access(str(target), os.W_OK), target
    with pytest.raises(OSError):
        open(str(REAL.broker_bin / "zurvan-broker"), "ab").close()


# ------------------------------------------------------------------ gate 7

def _t17_head():
    out = subprocess.run(["/usr/bin/git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                         check=True, env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null"})
    return out.stdout.strip()


def test_t17_live_gate7_reserve_without_final_acceptance_is_refused_by_the_broker_itself():
    """The client also refuses locally; this talks to the broker directly to prove ITS independent rule."""
    commit = _t17_head()
    try:
        name = zc.make_bundle(commit, repo=REPO, drop_dir=REAL.drop_dir)
    except zc.ClientRefusal as exc:
        pytest.skip("Z1b: gate 7 not exercised, cannot freeze HEAD (%s); commit or stash first" % exc.code)
    try:
        with pytest.raises(zc.ClientRefusal) as ei:
            zc.call_broker(["run", "--commit", commit, "--bundle", name, "--suite", "reserve"])
    finally:
        try:
            os.unlink(str(REAL.drop_dir / name))
        except OSError:
            pass
    if ei.value.code in _T17_EARLY_REFUSALS:
        pytest.skip("Z1b: gate 7 UNVERIFIED - the suite is not authored/installed yet (broker said %s)"
                    % ei.value.code)
    assert ei.value.code == "RESERVE_NOT_ELIGIBLE"


def test_t17_live_gate7_holdout_never_accepts_final_acceptance():
    with pytest.raises(zc.ClientRefusal) as ei:
        zc.call_broker(["run", "--commit", "a" * 40, "--bundle", "z1b-%s-%s.bundle" % ("a" * 40, "b" * 16),
                        "--suite", "holdout", "--final-acceptance"])
    assert ei.value.code == "BAD_ARGS"


# ------------------------------------------------------------------ gate 9 precondition

def test_t17_live_gate9_precondition_sudo_offers_the_dev_identity_exactly_one_door():
    """No other command runs as zurvaneval or zurvancandidate from the dev session.
    A cached sudo ticket is the OPERATOR's authority (gate 2 needs one), not the dev identity's, so it is
    dropped first: this measures the sudoers rules alone."""
    subprocess.run(["/usr/bin/sudo", "-k"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=30, env={"PATH": "/usr/bin:/bin"}, cwd="/")
    for user in (EVAL_USER, CANDIDATE_USER):
        for argv in (["/usr/bin/id"], ["/bin/cat", str(REAL.sets_dir / "holdout.age")], ["/bin/sh", "-c", "id"],
                     ["/usr/bin/env"], ["/bin/ls", str(REAL.eval_home)]):
            rc, out = _t17_sudo_n(user, *argv)
            assert rc != 0 and out == b"", (user, argv)


def test_t17_live_gate9_precondition_the_broker_door_answers_only_in_closed_schema():
    for argv in (["bogus"], ["status", "extra"], ["run"], ["ack-incident", "not-hex"], ["--help"], [""]):
        with pytest.raises(zc.ClientRefusal) as ei:
            zc.call_broker(argv)
        assert ei.value.code in ("BAD_ARGS", "INTERNAL"), (argv, ei.value.code)


def test_t17_live_gate9_precondition_the_dev_session_cannot_list_or_read_sealed_homes():
    for target in (REAL.eval_home, REAL.candidate_home, REAL.corpus_dir, REAL.stage_dir):
        with pytest.raises(PermissionError):
            os.listdir(str(target))
    for target in (REAL.sets_dir / "holdout.age", REAL.keys_dir / "age.key", REAL.manifest_file):
        with pytest.raises(PermissionError):
            open(str(target), "rb")
