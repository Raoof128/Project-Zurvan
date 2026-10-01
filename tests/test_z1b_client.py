"""Z1b Task 13 tests: development-side client and CLI wiring (contract s4.1).

The broker is replaced by a fake executable (a tmp python script that prints canned closed-schema
JSON) reached through the `runner` seam, so every parse, refusal and "never printed" property is
exercised against a real child process. Git is real; sealed paths are simulated under tmp roots.
"""
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

import scripts.eval_datasets as ed
import scripts.z1b_client as zc
from scripts.eval_seal import intake, sysenv
from scripts.eval_seal import output as seal_output
from scripts.eval_seal.codes import ALL_CODES
from scripts.eval_seal.paths import SealPaths

REPO_ROOT = Path(__file__).resolve().parents[1]
_T13_SECRET = "SECRET-STDERR-LEAK-7f3a"

_T13_FAKE = '''
import json, os, sys
d = os.environ["T13_FAKE_DIR"]
with open(os.path.join(d, "calls.log"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
canned = json.load(open(os.path.join(d, "canned.json")))
entry = canned.get(sys.argv[1], canned.get("*"))
sys.stderr.write("%s\\n" % os.environ.get("T13_SECRET", "x"))
sys.stdout.write(entry["out"])
sys.exit(entry["rc"])
'''


# ------------------------------------------------------------------ canned broker documents

def _t13_metrics():
    return {"recall_at_1": 0.25, "recall_at_3": 0.5, "recall_at_10": 0.75, "mrr": 0.4}


def _t13_result(commit, suite="holdout", **over):
    res = {
        "ok": True, "schema": "zurvan.z1b.result.v1", "suite": suite, "suite_id": "0123456789abcdef",
        "n_items": 150, "corpus_sha256": "b" * 64, "suite_manifest_sha256": "c" * 64,
        "evaluator_version": "1.0.0", "broker_sha": "a" * 40, "candidate_commit": commit,
        "metrics": dict(_t13_metrics(), ci={m: [0.1, 0.9] for m in _t13_metrics()}),
        "per_stratum": None, "target_missing": 0, "abstain": None,
        "latency_ms": {"p50": 40, "p95": 120}, "peak_rss_mb": 900, "seal_valid": True,
        "cached": False, "budget_remaining": 7, "promotion": None,
    }
    res.update(over)
    return res


def _t13_status_doc(tree, **over):
    st = {
        "ok": True, "schema": "zurvan.z1b.status.v1", "configured": True, "broker_sha": "a" * 40,
        "installed_sha": "a" * 40, "installed_tree": tree, "stale": False, "manifest_valid": True,
        "corpus_match": True, "budget_remaining": 8, "open_incidents": 0, "holdout_n": 160,
        "reserve_n": 80, "suite_id": "0123456789abcdef",
    }
    st.update(over)
    return st


def _t13_sealcheck_doc(**over):
    sc = {"ok": True, "schema": "zurvan.z1b.sealcheck.v1", "broker_sha": "a" * 40,
          "checks": {cid: True for cid in seal_output.CHECK_IDS}}
    sc.update(over)
    return sc


def _t13_out(doc, serializer):
    return {"out": serializer(doc), "rc": 0}


# ------------------------------------------------------------------ git + fake broker plumbing

def _t13_git(repo, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_NOSYSTEM": "1"}
    cp = subprocess.run(["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                         "-C", str(repo)] + list(args), env=env, capture_output=True, text=True, check=True)
    return cp.stdout.strip()


def _t13_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    (repo / "scripts" / "eval_seal").mkdir(parents=True)
    _t13_git_init(repo)
    (repo / "scripts" / "eval_seal" / "x.py").write_text("X = 1\n")
    (repo / "README").write_text("one\n")
    _t13_git(repo, "add", "-A")
    _t13_git(repo, "commit", "-q", "-m", "one")
    c1 = _t13_git(repo, "rev-parse", "HEAD")
    (repo / "README").write_text("two\n")
    _t13_git(repo, "commit", "-q", "-am", "two")
    c2 = _t13_git(repo, "rev-parse", "HEAD")
    return repo, c1, c2


def _t13_git_init(repo):
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(repo), "GIT_CONFIG_GLOBAL": "/dev/null"})


class _T13Fake:
    def __init__(self, base):
        self.dir = base / "fake"
        self.dir.mkdir()
        (self.dir / "fake_broker.py").write_text(_T13_FAKE)
        self.set({})

    def set(self, canned):
        (self.dir / "canned.json").write_text(json.dumps(canned))

    def calls(self):
        log = self.dir / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def runner(self, cmd, **kw):
        assert cmd[0] == "/usr/bin/sudo" and cmd[1:4] == ["-n", "-u", "zurvaneval"]
        assert cmd[4] == zc.BROKER
        assert kw["stderr"] is subprocess.DEVNULL  # the client never captures broker stderr
        return subprocess.run(
            [sys.executable, str(self.dir / "fake_broker.py")] + list(cmd[5:]), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
            env={"T13_FAKE_DIR": str(self.dir), "T13_SECRET": _T13_SECRET})


@pytest.fixture
def t13(tmp_path, monkeypatch):
    repo, c1, c2 = _t13_repo(tmp_path)
    drop = tmp_path / "drop"
    drop.mkdir(mode=0o733)
    drop.chmod(0o733)
    broker = tmp_path / "zurvan-broker"
    broker.write_text("#!/bin/sh\n")
    fake = _T13Fake(tmp_path)
    monkeypatch.setattr(zc, "REPO", repo)
    monkeypatch.setattr(zc, "DROP_DIR", drop)
    monkeypatch.setattr(zc, "BROKER", str(broker))
    monkeypatch.setattr(zc.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_name=name))
    real_call = zc.call_broker
    monkeypatch.setattr(zc, "call_broker", lambda argv, *, runner=None: real_call(argv, runner=runner or fake.runner))
    tree = _t13_git(repo, "rev-parse", "HEAD:scripts/eval_seal")
    return types.SimpleNamespace(tmp=tmp_path, repo=repo, c1=c1, c2=c2, drop=drop, fake=fake, tree=tree)


def _t13_refused(code, fn, *a, **kw):
    with pytest.raises(zc.ClientRefusal) as ei:
        fn(*a, **kw)
    assert ei.value.code == code
    assert str(ei.value) == code
    return ei.value


# ------------------------------------------------------------------ configuration

def test_t13_seal_configured_needs_both_identity_and_broker(t13, monkeypatch):
    assert zc.seal_configured() is True

    def no_user(name):
        raise KeyError(name)
    monkeypatch.setattr(zc.pwd, "getpwnam", no_user)
    assert zc.seal_configured() is False
    monkeypatch.setattr(zc.pwd, "getpwnam", lambda name: types.SimpleNamespace(pw_name=name))
    monkeypatch.setattr(zc, "BROKER", str(t13.tmp / "absent"))
    assert zc.seal_configured() is False


def test_t13_status_on_an_unconfigured_machine_raises_seal_not_configured_never_ok(monkeypatch):
    def no_user(name):
        raise KeyError(name)
    monkeypatch.setattr(zc.pwd, "getpwnam", no_user)
    calls = []
    monkeypatch.setattr(zc, "call_broker", lambda *a, **k: calls.append(a))
    for fn in (zc.status, zc.client_status, zc.seal_check, lambda: zc.run("a" * 40, "holdout")):
        _t13_refused("SEAL_NOT_CONFIGURED", fn)
    assert calls == []  # nothing was attempted


def test_t13_cli_returns_2_and_prints_only_the_code_when_unconfigured(monkeypatch, capsys):
    def no_user(name):
        raise KeyError(name)
    monkeypatch.setattr(zc.pwd, "getpwnam", no_user)
    for action in ("status", "seal-check", "run"):
        args = types.SimpleNamespace(z1b_action=action, commit="a" * 40, suite="holdout", declare=None,
                                     final_acceptance=False)
        assert zc.client_cli_main(args) == 2
        assert capsys.readouterr().out.strip() == "SEAL_NOT_CONFIGURED"


@pytest.mark.skipif(zc.seal_configured(), reason="Z1b: configured machine - the unconfigured CLI path is not testable")
def test_t13_real_cli_subprocess_exits_2_on_an_unconfigured_machine():
    for action in ("status", "seal-check"):
        cp = subprocess.run([sys.executable, "scripts/cli.py", "eval", "z1b", action], cwd=str(REPO_ROOT),
                            capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
        assert cp.returncode == 2, (action, cp.stderr[-300:])
        assert cp.stdout.strip() == "SEAL_NOT_CONFIGURED"


def test_t13_cli_parser_knows_every_z1b_action():
    cp = subprocess.run([sys.executable, "scripts/cli.py", "eval", "z1b", "--help"], cwd=str(REPO_ROOT),
                        capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
    assert cp.returncode == 0
    for action in ("status", "run", "seal-check", "dev", "promote", "final-acceptance"):
        assert action in cp.stdout


# ------------------------------------------------------------------ make_bundle

def _t13_heads(bundle):
    out = subprocess.run(["/usr/bin/git", "bundle", "list-heads", str(bundle)], capture_output=True, text=True,
                         check=True, env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null"}).stdout
    return dict((ref, sha) for sha, ref in (line.split(" ", 1) for line in out.splitlines()))


def test_t13_make_bundle_head_is_exactly_the_requested_commit_even_when_interior(t13):
    for commit in (t13.c2, t13.c1):  # c1 is NOT the repo HEAD
        name = zc.make_bundle(commit, repo=t13.repo, drop_dir=t13.drop)
        assert re.fullmatch(intake.BUNDLE_NAME_RE, name) and name.startswith("z1b-" + commit + "-")
        assert _t13_heads(t13.drop / name)["HEAD"] == commit
        assert stat.S_IMODE((t13.drop / name).stat().st_mode) == 0o644
    assert len(list(t13.drop.iterdir())) == 2


def test_t13_make_bundle_never_mutates_the_callers_repository(t13):
    before = _t13_git(t13.repo, "for-each-ref")
    head = _t13_git(t13.repo, "symbolic-ref", "HEAD")
    zc.make_bundle(t13.c1, repo=t13.repo, drop_dir=t13.drop)
    assert _t13_git(t13.repo, "for-each-ref") == before
    assert _t13_git(t13.repo, "symbolic-ref", "HEAD") == head
    assert "z1b" not in _t13_git(t13.repo, "branch", "-a")
    assert _t13_git(t13.repo, "status", "--porcelain") == ""


def test_t13_make_bundle_names_are_unique_per_call(t13):
    a = zc.make_bundle(t13.c2, repo=t13.repo, drop_dir=t13.drop)
    b = zc.make_bundle(t13.c2, repo=t13.repo, drop_dir=t13.drop)
    assert a != b


def test_t13_make_bundle_refuses_a_dirty_tree_and_writes_nothing(t13):
    (t13.repo / "README").write_text("dirty\n")
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.make_bundle, t13.c2, repo=t13.repo, drop_dir=t13.drop)
    assert list(t13.drop.iterdir()) == []


def test_t13_make_bundle_refuses_an_untracked_file_too(t13):
    (t13.repo / "stray.txt").write_text("x\n")
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.make_bundle, t13.c2, repo=t13.repo, drop_dir=t13.drop)
    assert list(t13.drop.iterdir()) == []


def test_t13_make_bundle_refuses_a_staged_but_uncommitted_change(t13):
    (t13.repo / "README").write_text("staged\n")
    _t13_git(t13.repo, "add", "README")
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.make_bundle, t13.c2, repo=t13.repo, drop_dir=t13.drop)
    assert list(t13.drop.iterdir()) == []


@pytest.mark.parametrize("bad", ["0" * 40, "f" * 40, "abc", "", "A" * 40, "HEAD", "-" * 40,
                                 "a" * 39, "a" * 41, "main", "a" * 40 + "\n", None, 12])
def test_t13_make_bundle_refuses_an_unknown_or_malformed_commit(t13, bad):
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.make_bundle, bad, repo=t13.repo, drop_dir=t13.drop)
    assert list(t13.drop.iterdir()) == []


def test_t13_make_bundle_refuses_a_missing_repo_and_a_missing_drop_dir(t13):
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.make_bundle, t13.c2, repo=t13.tmp / "nope", drop_dir=t13.drop)
    _t13_refused("INTERNAL", zc.make_bundle, t13.c2, repo=t13.repo, drop_dir=t13.tmp / "no-drop")


def test_t13_make_bundle_never_overwrites_or_follows_a_planted_name(t13, monkeypatch):
    monkeypatch.setattr(secrets, "token_hex", lambda n: "0" * (2 * n))
    planted = t13.tmp / "victim"
    planted.write_text("keep")
    (t13.drop / ("z1b-%s-%s.bundle" % (t13.c2, "0" * 16))).symlink_to(planted)
    _t13_refused("INTERNAL", zc.make_bundle, t13.c2, repo=t13.repo, drop_dir=t13.drop)
    assert planted.read_text() == "keep"


def test_t13_make_bundle_works_from_a_linked_worktree(t13):
    wt = t13.tmp / "wt"
    _t13_git(t13.repo, "worktree", "add", "-q", "-b", "side", str(wt), t13.c1)
    name = zc.make_bundle(t13.c1, repo=wt, drop_dir=t13.drop)
    assert _t13_heads(t13.drop / name)["HEAD"] == t13.c1


def test_t13_make_bundle_then_intake_stage_then_verify_round_trip(t13):
    """R10: the bundle HEAD must equal the frozen commit, as Task 8's verify_bundle demands."""
    paths = SealPaths.for_root(t13.tmp / "sealroot")
    paths.stage_dir.mkdir(parents=True)
    paths.drop_dir.mkdir(parents=True)
    name = zc.make_bundle(t13.c1, repo=t13.repo, drop_dir=paths.drop_dir)
    staged = intake.stage_bundle(name, paths, expected_uid=os.getuid(), run_id="0123456789abcdef")
    env = sysenv.clean_env(t13.tmp)
    intake.verify_bundle(staged, t13.c1, env=env)  # passes
    with pytest.raises(Exception) as ei:
        intake.verify_bundle(staged, t13.c2, env=env)  # the other commit is not this bundle's HEAD
    assert getattr(ei.value, "code", None) == "CANDIDATE_NOT_FROZEN"
    assert not (paths.drop_dir / name).exists()  # intake consumed the drop file


def test_t13_bare_commit_bundle_would_fail_the_head_check_so_the_alternates_route_is_needed(t13):
    """Guard against a 'simplification' to `git bundle create <commit>`: no HEAD is recorded."""
    out = t13.tmp / "naive.bundle"
    subprocess.run(["/usr/bin/git", "-C", str(t13.repo), "bundle", "create", str(out), t13.c1],
                   capture_output=True, env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null"})
    heads = _t13_heads(out) if out.exists() else {}
    assert heads.get("HEAD") != t13.c1


# ------------------------------------------------------------------ call_broker

def test_t13_call_broker_returns_a_validated_status_document(t13):
    t13.fake.set({"status": _t13_out(_t13_status_doc(t13.tree), seal_output.serialize_status)})
    doc = zc.call_broker(["status"])
    assert doc["schema"] == "zurvan.z1b.status.v1" and doc["holdout_n"] == 160


def test_t13_call_broker_runs_exactly_sudo_n_u_zurvaneval_broker_then_argv(t13):
    seen = []

    def spy(cmd, **kw):
        seen.append((cmd, kw))
        return t13.fake.runner(cmd, **kw)
    t13.fake.set({"status": _t13_out(_t13_status_doc(t13.tree), seal_output.serialize_status)})
    zc.call_broker(["status"], runner=spy)
    cmd, kw = seen[0]
    assert cmd == ["/usr/bin/sudo", "-n", "-u", "zurvaneval", zc.BROKER, "status"]
    assert kw["stdin"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL
    assert set(kw["env"]) == {"PATH"}  # the dev environment is not forwarded


def test_t13_a_refusal_document_becomes_a_client_refusal_with_that_code(t13):
    for code in ("SUITE_RENEWAL_REQUIRED", "INTEGRITY_INCIDENT", "CORPUS_MISMATCH", "MANIFEST_INVALID"):
        t13.fake.set({"*": {"out": seal_output.serialize_refusal(code, "a" * 40), "rc": 2}})
        _t13_refused(code, zc.call_broker, ["status"])


@pytest.mark.parametrize("raw", [
    "not json at all", "", "{}", "[]", "null",
    '{"ok":true,"schema":"zurvan.z1b.status.v1"}',  # missing fields
    '{"ok":false,"code":"FREE TEXT LEAK /Users/zurvaneval/sets/holdout.age","broker_sha":"' + "a" * 40 + '"}',
    '{"ok":false,"code":"SEAL_COMPROMISED","broker_sha":"' + "a" * 40 + '","detail":"x"}',
    '{"ok":true,"schema":"zurvan.z1b.status.v1","extra":1}',
    "Traceback (most recent call last):\n  File secret.py line 1",
])
def test_t13_broker_stdout_outside_the_closed_schema_is_internal_and_never_printed(t13, capfd, raw):
    t13.fake.set({"*": {"out": raw, "rc": 0}})
    err = _t13_refused("INTERNAL", zc.call_broker, ["status"])
    shown = capfd.readouterr()
    assert _T13_SECRET not in shown.out + shown.err
    if raw.strip():
        assert raw not in shown.out + shown.err
        assert raw not in repr(err) and raw not in str(err) and err.__cause__ is None


def test_t13_call_broker_extra_key_in_a_valid_looking_status_is_internal(t13):
    doc = _t13_status_doc(t13.tree)
    doc["note"] = "free text"
    t13.fake.set({"*": {"out": json.dumps(doc), "rc": 0}})
    _t13_refused("INTERNAL", zc.call_broker, ["status"])


def test_t13_call_broker_ok_document_with_a_failing_exit_status_is_internal(t13):
    t13.fake.set({"*": {"out": seal_output.serialize_status(_t13_status_doc(t13.tree)), "rc": 3}})
    _t13_refused("INTERNAL", zc.call_broker, ["status"])


def test_t13_call_broker_oversize_stdout_is_internal(t13):
    t13.fake.set({"*": {"out": "x" * (zc._MAX_STDOUT + 1), "rc": 0}})
    _t13_refused("INTERNAL", zc.call_broker, ["status"])


def test_t13_call_broker_runner_failures_are_internal(t13):
    def boom(cmd, **kw):
        raise OSError("sudo missing: /Users/zurvaneval")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    for fn in (boom, slow):
        err = _t13_refused("INTERNAL", zc.call_broker, ["status"], runner=fn)
        assert "zurvaneval" not in str(err)


def test_t13_broker_stderr_is_never_shown(t13, capfd):
    t13.fake.set({"status": _t13_out(_t13_status_doc(t13.tree), seal_output.serialize_status)})
    zc.call_broker(["status"])
    shown = capfd.readouterr()
    assert _T13_SECRET not in shown.out + shown.err


def test_t13_client_refusal_unknown_code_collapses_to_internal():
    assert zc.ClientRefusal("whatever text").code == "INTERNAL"
    assert str(zc.ClientRefusal("whatever text")) == "INTERNAL"
    for code in ALL_CODES:
        assert zc.ClientRefusal(code).code == code


# ------------------------------------------------------------------ status and staleness

def test_t13_broker_stale_compares_installed_tree_with_head_tree(t13):
    assert zc.broker_stale(t13.tree, t13.repo) is False
    assert zc.broker_stale("0" * 40, t13.repo) is True
    assert zc.broker_stale(None, t13.repo) is True
    assert zc.broker_stale("not-a-tree", t13.repo) is True
    assert zc.broker_stale(t13.tree, t13.tmp / "no-such-repo") is True  # fail closed


def test_t13_a_committed_change_under_scripts_eval_seal_makes_the_broker_stale(t13):
    (t13.repo / "scripts" / "eval_seal" / "x.py").write_text("X = 2\n")
    _t13_git(t13.repo, "commit", "-q", "-am", "change broker tree")
    assert zc.broker_stale(t13.tree, t13.repo) is True


def test_t13_a_change_outside_the_broker_tree_does_not_make_it_stale(t13):
    (t13.repo / "README").write_text("three\n")
    _t13_git(t13.repo, "commit", "-q", "-am", "docs")
    assert zc.broker_stale(t13.tree, t13.repo) is False


def test_t13_status_reports_stale_as_a_warning_not_a_refusal(t13, capsys):
    t13.fake.set({"status": _t13_out(_t13_status_doc("0" * 40, stale=False), seal_output.serialize_status)})
    doc = zc.status()
    assert doc["stale"] is True  # the client overrides the broker's field
    args = types.SimpleNamespace(z1b_action="status")
    assert zc.client_cli_main(args) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["stale"] is True and "BROKER_STALE" in cap.err


def test_t13_status_rejects_a_document_of_the_wrong_kind(t13):
    t13.fake.set({"status": _t13_out(_t13_sealcheck_doc(), seal_output.serialize_sealcheck)})
    _t13_refused("INTERNAL", zc.status)


# ------------------------------------------------------------------ run

def _t13_ok_status(t13):
    return _t13_out(_t13_status_doc(t13.tree), seal_output.serialize_status)


def test_t13_run_success_builds_the_bundle_calls_the_broker_and_cleans_the_drop(t13):
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": _t13_out(_t13_result(t13.c2), seal_output.serialize_result)})
    doc = zc.run(t13.c2, "holdout", declare="eval/z1b/declarations/pre-1.json")
    assert doc["candidate_commit"] == t13.c2 and doc["suite"] == "holdout"
    argv = [c for c in t13.fake.calls() if c[0] == "run"][0]
    assert argv[:5] == ["run", "--commit", t13.c2, "--bundle", argv[4]]
    assert re.fullmatch(intake.BUNDLE_NAME_RE, argv[4])
    assert argv[5:] == ["--suite", "holdout", "--declare", "eval/z1b/declarations/pre-1.json"]
    assert list(t13.drop.iterdir()) == []  # a refusal-before-staging would leave one; success must not


def test_t13_run_refusal_from_the_broker_propagates_and_leaves_no_bundle(t13):
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": {"out": seal_output.serialize_refusal("SUITE_RENEWAL_REQUIRED", "a" * 40), "rc": 2}})
    _t13_refused("SUITE_RENEWAL_REQUIRED", zc.run, t13.c2, "holdout")
    assert list(t13.drop.iterdir()) == []


def test_t13_run_dirty_tree_is_refused_with_no_broker_call_and_no_bundle(t13):
    (t13.repo / "README").write_text("dirty\n")
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.run, t13.c2, "holdout")
    assert t13.fake.calls() == [] and list(t13.drop.iterdir()) == []


def test_t13_run_unknown_commit_is_refused_with_no_broker_call(t13):
    _t13_refused("CANDIDATE_NOT_FROZEN", zc.run, "e" * 40, "holdout")
    assert t13.fake.calls() == [] and list(t13.drop.iterdir()) == []


def test_t13_run_stale_broker_is_refused_after_status_and_before_any_bundle_or_run(t13):
    t13.fake.set({"status": _t13_out(_t13_status_doc("0" * 40), seal_output.serialize_status)})
    _t13_refused("BROKER_STALE", zc.run, t13.c2, "holdout")
    assert [c[0] for c in t13.fake.calls()] == ["status"] and list(t13.drop.iterdir()) == []


def test_t13_run_argument_grammar_is_enforced_locally_before_any_call(t13):
    _t13_refused("BAD_ARGS", zc.run, t13.c2, "holdout", final_acceptance=True)
    _t13_refused("BAD_ARGS", zc.run, t13.c2, "dev")
    _t13_refused("BAD_ARGS", zc.run, t13.c2, "holdout", None, "yes")
    _t13_refused("RESERVE_NOT_ELIGIBLE", zc.run, t13.c2, "reserve")  # no --final-acceptance
    for bad in ("../x.json", "eval/z1b/declarations/../../x.json", "eval/z1b/declarations/A.json",
                "eval/z1b/declarations/x.json\n", "/etc/passwd", "eval/z1b/x.json", "", 7,
                "eval/z1b/declarations/" + "a" * 65 + ".json", "eval/z1b/declarations/a/b.json"):
        _t13_refused("DECLARATION_INVALID", zc.run, t13.c2, "holdout", bad)
    assert t13.fake.calls() == [] and list(t13.drop.iterdir()) == []


def test_t13_run_reserve_passes_final_acceptance_through(t13):
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": _t13_out(_t13_result(t13.c2, "reserve"), seal_output.serialize_result)})
    zc.run(t13.c2, "reserve", final_acceptance=True)
    argv = [c for c in t13.fake.calls() if c[0] == "run"][0]
    assert argv[-3:] == ["--suite", "reserve", "--final-acceptance"]


def test_t13_run_result_for_a_different_commit_or_suite_is_not_believed(t13):
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": _t13_out(_t13_result(t13.c1), seal_output.serialize_result)})
    _t13_refused("INTERNAL", zc.run, t13.c2, "holdout")
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": _t13_out(_t13_result(t13.c2, "reserve"), seal_output.serialize_result)})
    _t13_refused("INTERNAL", zc.run, t13.c2, "holdout")
    assert list(t13.drop.iterdir()) == []


def test_t13_run_wrong_document_kind_is_internal(t13):
    t13.fake.set({"status": _t13_ok_status(t13),
                  "run": _t13_out(_t13_status_doc(t13.tree), seal_output.serialize_status)})
    _t13_refused("INTERNAL", zc.run, t13.c2, "holdout")


# ------------------------------------------------------------------ seal_check (merged)

def _t13_local(**over):
    doc = {"configured": True, "ok": True, "paths": {},
           "checks": {"dev_cannot_read_sealed": True, "local_deny_rules_present": True,
                      "local_broker_root_owned": True, "local_broker_not_writable": True}}
    doc.update(over)
    return doc


def test_t13_seal_check_merges_the_broker_and_dev_halves(t13, monkeypatch):
    monkeypatch.setattr(zc, "local_seal_check", lambda paths: _t13_local())
    t13.fake.set({"seal-check": _t13_out(_t13_sealcheck_doc(), seal_output.serialize_sealcheck)})
    doc = zc.seal_check()
    assert doc["ok"] is True and doc["checks"]["dev_cannot_read_sealed"] is True
    assert set(doc["checks"]) >= set(seal_output.CHECK_IDS)


def test_t13_seal_check_is_not_ok_when_the_dev_half_fails_even_if_the_broker_is_ok(t13, monkeypatch):
    bad = _t13_local()
    bad["checks"]["dev_cannot_read_sealed"] = False
    monkeypatch.setattr(zc, "local_seal_check", lambda paths: bad)
    t13.fake.set({"seal-check": _t13_out(_t13_sealcheck_doc(), seal_output.serialize_sealcheck)})
    assert zc.seal_check()["ok"] is False
    args = types.SimpleNamespace(z1b_action="seal-check")
    assert zc.client_cli_main(args) == 2


def test_t13_seal_check_is_not_ok_when_the_broker_half_fails(t13, monkeypatch):
    monkeypatch.setattr(zc, "local_seal_check", lambda paths: _t13_local())
    checks = {cid: True for cid in seal_output.CHECK_IDS}
    checks["broker_not_writable"] = False
    t13.fake.set({"seal-check": _t13_out(_t13_sealcheck_doc(ok=False, checks=checks),
                                         seal_output.serialize_sealcheck)})
    assert zc.seal_check()["ok"] is False


def test_t13_seal_check_local_enoent_is_seal_not_configured_and_never_ok(t13, monkeypatch):
    monkeypatch.setattr(zc, "local_seal_check", lambda paths: _t13_local(configured=False, ok=False))
    t13.fake.set({"seal-check": _t13_out(_t13_sealcheck_doc(), seal_output.serialize_sealcheck)})
    _t13_refused("SEAL_NOT_CONFIGURED", zc.seal_check)
    assert t13.fake.calls() == []  # the broker is not even asked


# ------------------------------------------------------------------ local_seal_check (dev identity)

@pytest.fixture
def t13seal(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("permission checks are meaningless as root")
    root = tmp_path / "root"
    paths = SealPaths.for_root(root)
    for d in (paths.keys_dir, paths.sets_dir, paths.runs_dir, paths.incidents_dir, paths.corpus_dir,
              paths.stage_dir, paths.scratch_dir, paths.broker_bin, paths.broker_lib):
        d.mkdir(parents=True)
    paths.manifest_file.write_text("{}")
    (paths.broker_bin / "zurvan-broker").write_text("#!/bin/sh\n")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"permissions": {"deny": zc.client_deny_rules(paths)}}))
    monkeypatch.setattr(zc, "SETTINGS_PATH", settings)
    monkeypatch.setattr(zc, "ROOT_UID", os.getuid())
    for d in (paths.broker_dir, paths.broker_bin, paths.broker_lib):
        d.chmod(0o755)
    (paths.broker_bin / "zurvan-broker").chmod(0o555)
    for sealed in (paths.eval_home, paths.corpus_dir, paths.stage_dir, paths.candidate_home):
        sealed.chmod(0o000)
    yield types.SimpleNamespace(paths=paths, settings=settings)
    for sealed in (paths.eval_home, paths.corpus_dir, paths.stage_dir, paths.candidate_home):
        if sealed.exists():
            sealed.chmod(0o700)


def test_t13_local_check_all_sealed_paths_eacces_is_ok(t13seal):
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["configured"] is True and doc["ok"] is True, doc
    assert set(doc["paths"].values()) == {"EACCES"}
    assert all(doc["checks"].values())


def test_t13_local_check_a_readable_sealed_path_fails_gate_one(t13seal):
    t13seal.paths.eval_home.chmod(0o755)  # keys, sets, runs, incidents become reachable
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["ok"] is False and doc["checks"]["dev_cannot_read_sealed"] is False
    assert "READABLE" in doc["paths"].values()


def test_t13_local_check_enoent_means_not_configured_and_never_ok(t13seal, tmp_path):
    for sealed in (t13seal.paths.eval_home, t13seal.paths.corpus_dir, t13seal.paths.stage_dir,
                   t13seal.paths.candidate_home):
        sealed.chmod(0o700)
    import shutil
    shutil.rmtree(t13seal.paths.corpus_dir)
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["configured"] is False and doc["ok"] is False
    assert doc["paths"][str(t13seal.paths.corpus_dir)] == "ENOENT"
    # an entirely empty machine is "not configured", not a vacuous pass
    empty = zc.local_seal_check(SealPaths.for_root(tmp_path / "nothing-here"))
    assert empty["configured"] is False and empty["ok"] is False
    assert set(empty["paths"].values()) == {"ENOENT"}


def test_t13_local_check_eperm_or_other_errors_are_not_counted_as_eacces(t13seal, monkeypatch):
    real_open = os.open

    def eperm(path, flags, *a, **k):
        if str(path) == str(t13seal.paths.keys_dir):
            raise OSError(1, "Operation not permitted")
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(zc.os, "open", eperm)
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["paths"][str(t13seal.paths.keys_dir)] == "ERROR"
    assert doc["ok"] is False


def test_t13_local_check_deny_rules_must_cover_every_sealed_path_for_read_and_edit(t13seal):
    rules = zc.client_deny_rules(t13seal.paths)
    for drop_tool in ("Read", "Edit"):
        kept = [r for r in rules if not r.startswith(drop_tool + "(")]
        t13seal.settings.write_text(json.dumps({"permissions": {"deny": kept}}))
        doc = zc.local_seal_check(t13seal.paths)
        assert doc["checks"]["local_deny_rules_present"] is False and doc["ok"] is False, drop_tool
    t13seal.settings.write_text(json.dumps({"permissions": {"deny": rules}}))
    assert zc.local_seal_check(t13seal.paths)["checks"]["local_deny_rules_present"] is True


def test_t13_local_check_a_single_slash_rule_is_project_relative_and_does_not_count(t13seal):
    rules = [r.replace("(//", "(/") for r in zc.client_deny_rules(t13seal.paths)]
    t13seal.settings.write_text(json.dumps({"permissions": {"deny": rules}}))
    assert zc.local_seal_check(t13seal.paths)["checks"]["local_deny_rules_present"] is False


@pytest.mark.parametrize("content", ["not json", "{}", '{"permissions":{}}', '{"permissions":{"deny":"Read"}}',
                                     '{"permissions":{"deny":null}}', "[]"])
def test_t13_local_check_missing_or_garbled_settings_fail_closed(t13seal, content):
    t13seal.settings.write_text(content)
    assert zc.local_seal_check(t13seal.paths)["checks"]["local_deny_rules_present"] is False
    t13seal.settings.unlink()
    assert zc.local_seal_check(t13seal.paths)["checks"]["local_deny_rules_present"] is False


def test_t13_local_check_broker_tree_must_be_root_owned_and_unwritable(t13seal, monkeypatch):
    paths = t13seal.paths
    assert zc.local_seal_check(paths)["checks"]["local_broker_root_owned"] is True
    monkeypatch.setattr(zc, "ROOT_UID", os.getuid() + 1)  # nobody we own is "root"
    doc = zc.local_seal_check(paths)
    assert doc["checks"]["local_broker_root_owned"] is False and doc["ok"] is False
    monkeypatch.setattr(zc, "ROOT_UID", os.getuid())
    (paths.broker_bin / "zurvan-broker").chmod(0o775)  # group-writable
    doc = zc.local_seal_check(paths)
    assert doc["checks"]["local_broker_not_writable"] is False and doc["ok"] is False
    (paths.broker_bin / "zurvan-broker").chmod(0o555)
    paths.broker_lib.chmod(0o757)  # world-writable directory
    assert zc.local_seal_check(paths)["checks"]["local_broker_not_writable"] is False
    paths.broker_lib.chmod(0o755)


def test_t13_local_check_a_symlink_inside_the_broker_tree_fails_both_broker_checks(t13seal, tmp_path):
    (t13seal.paths.broker_lib / "evil").symlink_to(tmp_path)
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["checks"]["local_broker_root_owned"] is False
    assert doc["checks"]["local_broker_not_writable"] is False


def test_t13_local_check_missing_broker_tree_is_not_configured(t13seal):
    import shutil
    shutil.rmtree(t13seal.paths.broker_dir)
    doc = zc.local_seal_check(t13seal.paths)
    assert doc["configured"] is False and doc["ok"] is False


def test_t13_real_layout_deny_rules_are_the_four_sealed_roots():
    rules = zc.client_deny_rules(SealPaths.real())
    for root in ("/Users/zurvaneval", "/Library/ZurvanEval/corpus", "/Library/ZurvanEval/stage",
                 "/Users/zurvancandidate"):
        for tool in ("Read", "Edit", "Write"):
            assert "%s(/%s/**)" % (tool, root) in rules
        assert "Bash(*%s*)" % root in rules


# ------------------------------------------------------------------ dev (DEV set, local)

def _t13_item(stratum="exact_identifier", page="wiki/a.md", **over):
    grading = "abstain" if stratum == "not_found" else ("set" if stratum in ("multi_hop", "synthesis") else "rank")
    item = {"id": secrets.token_hex(16), "stratum": stratum, "query": "where is " + stratum,
            "targets": [] if grading == "abstain" else
            [{"page": page, "source_id": None, "locator": {"kind": "page", "value": page}}],
            "grading": grading, "difficulty": "easy", "provenance": {"author": "a", "reviewer": "r"}}
    item.update(over)
    return item


@pytest.fixture
def t13dev(tmp_path, monkeypatch):
    registry = tmp_path / "datasets.json"
    registry.write_text(json.dumps({"schema": "zurvan.eval.datasets.v1", "datasets": {}}))
    monkeypatch.setattr(ed, "REGISTRY", registry)
    dev = tmp_path / "dev.jsonl"
    items = [_t13_item("exact_identifier", "wiki/a.md"), _t13_item("path", "wiki/b.md"),
             _t13_item("not_found")]
    dev.write_text("\n".join(json.dumps(i) for i in items) + "\n")
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    return types.SimpleNamespace(registry=registry, dev=dev, items=items, corpus=corpus)


def _t13_search(table):
    calls = []

    def search(query):
        calls.append(query)
        return table.get(query, [])
    search.calls = calls
    return search


def test_t13_dev_scores_the_dev_set_with_the_sealed_grader(t13dev):
    table = {"where is exact_identifier": [{"page": "wiki/a.md", "heading": None, "score": 0.9}],
             "where is path": [{"page": "wiki/x.md", "heading": None, "score": 0.8},
                               {"page": "wiki/b.md", "heading": None, "score": 0.7}],
             "where is not_found": [{"page": "wiki/x.md", "heading": None, "score": 0.1}]}
    doc = zc.client_dev(t13dev.dev, t13dev.corpus, search=_t13_search(table))
    assert doc["n_items"] == 3 and doc["abstain"] == {"passed": 1, "n": 1}
    assert doc["metrics"]["recall_at_1"] == 0.5 and doc["metrics"]["recall_at_3"] == 1.0
    assert doc["metrics"]["mrr"] == 0.75
    assert doc["dataset"]["state"] == "DEV" and doc["evaluator_version"] == "1.0.0"
    assert [r["rank"] for r in doc["rows"] if r["stratum"] != "not_found"] == [1, 2]
    assert set(doc["per_stratum"]) == {"exact_identifier", "path"}


@pytest.mark.parametrize("state", ["HOLDOUT", "RESERVE"])
def test_t13_dev_refuses_a_file_registered_as_a_sealed_dataset_before_any_search(t13dev, state):
    digest = ed.file_sha256(t13dev.dev)
    t13dev.registry.write_text(json.dumps({"datasets": {"x": {"state": state, "sha256": digest}}}))
    search = _t13_search({})
    with pytest.raises(ed.DatasetNotEligible) as ei:
        zc.client_dev(t13dev.dev, t13dev.corpus, search=search)
    assert ei.value.code == "SEALED_DATASET" and search.calls == []


def test_t13_dev_accepts_a_retired_registered_file(t13dev):
    digest = ed.file_sha256(t13dev.dev)
    t13dev.registry.write_text(json.dumps({"datasets": {"old": {"state": "RETIRED", "sha256": digest}}}))
    assert zc.client_dev(t13dev.dev, t13dev.corpus, search=_t13_search({}))["dataset"]["state"] == "RETIRED"


def test_t13_dev_refuses_a_malformed_item_a_missing_file_and_an_empty_file(t13dev, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(_t13_item(stratum="exact_identifier", query="")) + "\n")
    _t13_refused("BAD_ARGS", zc.client_dev, bad, t13dev.corpus, search=_t13_search({}))
    bad.write_text("not json\n")
    _t13_refused("BAD_ARGS", zc.client_dev, bad, t13dev.corpus, search=_t13_search({}))
    bad.write_text("\n\n")
    _t13_refused("BAD_ARGS", zc.client_dev, bad, t13dev.corpus, search=_t13_search({}))
    _t13_refused("BAD_ARGS", zc.client_dev, tmp_path / "missing.jsonl", t13dev.corpus, search=_t13_search({}))


def test_t13_dev_cli_prints_the_code_for_a_sealed_dataset(t13dev, capsys):
    digest = ed.file_sha256(t13dev.dev)
    t13dev.registry.write_text(json.dumps({"datasets": {"x": {"state": "HOLDOUT", "sha256": digest}}}))
    args = types.SimpleNamespace(z1b_action="dev", dev=str(t13dev.dev), corpus=str(t13dev.corpus))
    assert zc.client_cli_main(args) == 2
    assert capsys.readouterr().out.strip() == "SEALED_DATASET"


def test_t13_cli_unexpected_exception_prints_internal_only(t13, monkeypatch, capsys):
    monkeypatch.setattr(zc, "client_status", lambda: (_ for _ in ()).throw(RuntimeError("/Users/zurvaneval/x")))
    assert zc.client_cli_main(types.SimpleNamespace(z1b_action="status")) == 2
    shown = capsys.readouterr()
    assert shown.out.strip() == "INTERNAL" and "zurvaneval" not in shown.out + shown.err


def test_t13_cli_unknown_action_is_bad_args(capsys):
    assert zc.client_cli_main(types.SimpleNamespace(z1b_action="nope")) == 2
    assert capsys.readouterr().out.strip() == "BAD_ARGS"


def test_t13_module_defines_no_name_that_collides_with_another_part_of_the_plan():
    text = (REPO_ROOT / "scripts" / "z1b_client.py").read_text()
    for generic in ("status", "run", "seal_check", "main"):
        assert not re.search(r"^def %s\(" % generic, text, re.M), generic
    assert zc.status is zc.client_status and zc.run is zc.client_run and zc.seal_check is zc.client_seal_check
