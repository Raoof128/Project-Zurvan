"""Z1b Task 10 tests: the broker, end to end in a tmp SealPaths root (contract s3.9, addendum A).

Seams: decrypt is a plaintext read, the candidate is a REAL python subprocess that speaks the protocol
(no sudo, no sandbox), probe/tmutil/sweep are fakes. The ledger, manifest (real ssh-keygen keys), intake
(real git bundles) and grader are the real modules. Nothing here touches /Library, /Users/zurvan*, sudo.
"""
import contextlib
import datetime
import hashlib
import io
import json
import multiprocessing
import os
import random
import subprocess
import sys
import time
import types
import warnings
from pathlib import Path

import pytest

from scripts.eval_seal import broker as B
from scripts.eval_seal import manifest as manifest_mod
from scripts.eval_seal import output, sealed_store, sweep, sysenv
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.corpus import corpus_sha256
from scripts.eval_seal.ledger import Ledger
from scripts.eval_seal.paths import SealPaths
from scripts.eval_seal.spec import STRATA, coverage, load_spec

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "eval" / "z1b" / "spec.json"
SSH_KEYGEN = "/usr/bin/ssh-keygen"
GIT = "/usr/bin/git"
pytestmark = pytest.mark.skipif(not os.path.exists(SSH_KEYGEN) or not os.path.exists(GIT),
                                reason="needs /usr/bin/ssh-keygen and /usr/bin/git")

_T10_SHA = "ab" * 20
_T10_TREE = "cd" * 20
_T10_NOW = datetime.datetime(2026, 10, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
_T10_PLANTED = "PLANTED-QUERY-"
_T10_STRATA_CYCLE = ("exact_identifier", "path", "semantic", "multi_hop", "not_found")
_T10_GRADING = {"multi_hop": "set", "not_found": "abstain"}

_T10_CANDIDATE = r'''
import json, os, sys, time
cfg = json.load(open(sys.argv[1]))
rec = open(cfg["rec"], "ab")
out = sys.stdout.buffer
mode, at, n = cfg.get("mode", "ok"), cfg.get("at", 0), 0
if cfg.get("delay"):
    time.sleep(cfg["delay"])
for line in sys.stdin.buffer:
    rec.write(line); rec.flush()
    req = json.loads(line)
    n += 1
    if mode == "hang" and n == at:
        time.sleep(120)
    if mode == "crash" and n == at:
        os._exit(7)
    if mode == "garbage" and n == at:
        out.write(b"this is not json\n"); out.flush(); continue
    pages = cfg["oracle"].get(req["q"], [])
    results = [{"page": p, "heading": None, "score": 0.9 - 0.01 * i} for i, p in enumerate(pages)]
    out.write((json.dumps({"qid": req["qid"], "results": results, "abstain": False}) + "\n").encode())
    if mode == "extra" and n == at:
        out.write(b'{"qid": 99}\n')
    out.flush()
if mode == "badexit":
    sys.exit(5)
if mode == "trailing":
    out.write(b'{"qid": 5}\n'); out.flush()
'''


# ================================================================ builders

def _t10_genv():
    return sysenv.clean_env(sysenv.home_of_euid())


def _t10_git(repo, *args):
    cmd = [GIT, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
           "-C", str(repo)] + [str(a) for a in args]
    return subprocess.run(cmd, env=_t10_genv(), check=True, capture_output=True).stdout.decode().strip()


def _t10_item(i, pool="p"):
    stratum = _T10_STRATA_CYCLE[i % len(_T10_STRATA_CYCLE)]
    grading = _T10_GRADING.get(stratum, "rank")
    page = "wiki/%s%d.md" % (pool, i)
    targets = []
    if grading == "rank":
        targets = [{"page": page, "source_id": None, "locator": {"kind": "page", "value": ""}}]
    elif grading == "set":
        targets = [{"page": page, "source_id": None, "locator": {"kind": "page", "value": ""}},
                   {"page": "wiki/%s%db.md" % (pool, i), "source_id": None,
                    "locator": {"kind": "page", "value": ""}}]
    return {
        "id": hashlib.sha256(("item-%s-%d" % (pool, i)).encode()).hexdigest()[:32],
        "stratum": stratum, "grading": grading, "difficulty": "easy",
        "query": "%s%s%03d-%s" % (_T10_PLANTED, pool, i, hashlib.sha256(b"q%d%s" % (i, pool.encode())).hexdigest()[:12]),
        "targets": targets, "provenance": {"author": "a", "reviewer": "r"},
    }


def _t10_oracle(items):
    out = {}
    for item in items:
        out[item["query"]] = [t["page"] for t in item["targets"]]
    return out


def _t10_keygen(d):
    d.mkdir(parents=True, exist_ok=True)
    key = d / "signing_ed25519"
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    pub = (d / "signing_ed25519.pub").read_text().split()
    (d / "allowed_signers").write_text('zurvaneval namespaces="zurvan-z1b-manifest" %s %s\n' % (pub[0], pub[1]))
    return key


def _t10_counts(items):
    out = {}
    for item in items:
        out[item["stratum"]] = out.get(item["stratum"], 0) + 1
    return out


def _t10_seal(E, *, sequence=1, holdout=None, reserve=None, suite_id="0123456789abcdef"):
    """Write plaintext 'age' sets + a signed sealed manifest for them. Returns the manifest dict."""
    holdout = E.holdout if holdout is None else holdout
    reserve = E.reserve if reserve is None else reserve
    P = E.paths
    P.sets_dir.mkdir(parents=True, exist_ok=True)
    gen = sealed_store.new_generation(P, sequence)
    (gen / sealed_store.HOLDOUT).write_bytes(B.broker_dump_items(holdout))
    (gen / sealed_store.RESERVE).write_bytes(B.broker_dump_items(reserve))
    m = manifest_mod.build_manifest(
        suite_id=suite_id, sequence=sequence, created="2026-10-02T10:00:00Z",
        models={"author": "claude-opus-4-5", "reviewer": "claude-sonnet-5-5"},
        prompt_sha256="11" * 32, corpus_sha256=corpus_sha256(P.corpus_dir),
        holdout_age_sha256=hashlib.sha256((gen / sealed_store.HOLDOUT).read_bytes()).hexdigest(),
        reserve_age_sha256=hashlib.sha256((gen / sealed_store.RESERVE).read_bytes()).hexdigest(),
        dev_sha256="22" * 32,
        strata_counts={"holdout": _t10_counts(holdout), "reserve": _t10_counts(reserve)},
        accepted=len(holdout) + len(reserve), rejected={}, short=[], evaluator_version="1.0.0",
        burn_ledger=[])
    mf = gen / sealed_store.MANIFEST
    manifest_mod.write_manifest(mf, m)
    manifest_mod.sign(mf, E.key)
    sealed_store.promote(P, sequence)
    return m


def _t10_build(tmp_path, *, n_holdout=10, n_reserve=5, commits=1):
    E = types.SimpleNamespace()
    E.tmp = tmp_path
    E.paths = P = SealPaths.for_root(tmp_path / "root")
    for d in (P.broker_dir, P.broker_bin, P.broker_lib, P.keys_dir, P.sets_dir, P.runs_dir, P.corpus_dir,
              P.stage_dir, P.drop_dir, P.scratch_dir, P.incidents_dir):
        d.mkdir(parents=True, exist_ok=True)
    P.installed_sha_file.write_text(_T10_SHA + "\n")
    P.installed_tree_file.write_text(_T10_TREE + "\n")
    E.key = _t10_keygen(P.keys_dir)
    E.holdout = [_t10_item(i, "p") for i in range(n_holdout)]
    E.reserve = [_t10_item(i, "r") for i in range(n_reserve)]
    pages = set()
    for item in E.holdout + E.reserve:
        for t in item["targets"]:
            pages.add(t["page"])
    for page in sorted(pages | {"wiki/extra.md"}):
        f = P.corpus_dir / page
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("# page %s\n" % page)
    (P.corpus_dir / "search.sqlite").write_bytes(b"\x00stub-index")
    E.spec_path = SPEC
    E.rec = tmp_path / "candidate-stdin.rec"
    E.trace = tmp_path / "trace.jsonl"
    E.script = tmp_path / "fake_candidate.py"
    E.script.write_text(_T10_CANDIDATE)
    E.cand_cfg = {"mode": "ok", "at": 0, "rec": str(E.rec), "delay": 0,
                  "oracle": _t10_oracle(E.holdout + E.reserve)}
    E.launched = []
    E.repo = tmp_path / "repo"
    E.repo.mkdir()
    _t10_git(E.repo, "init", "-q")
    E.commits = []
    for i in range(commits):
        # Rev 3: HOLDOUT now requires a declaration (canonical §9), so commit 0 carries a default one;
        # _t10_run auto-declares holdout runs against it unless a test opts out with declare=None.
        E.commits.append(_t10_commit(E, "candidate code v%d\n" % i,
                                     extra={_T10_DECL_DEFAULT: json.dumps(_t10_decl_body(E))}))
    E.default_decl = _T10_DECL_DEFAULT
    E.manifest = _t10_seal(E)
    return E


def _t10_commit(E, text, extra=None):
    (E.repo / "code.txt").write_text(text)
    for rel, body in (extra or {}).items():
        (E.repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (E.repo / rel).write_text(body)
    _t10_git(E.repo, "add", "-A")
    _t10_git(E.repo, "commit", "-q", "-m", "c")
    return _t10_git(E.repo, "rev-parse", "HEAD")


def _t10_drop(E, commit, *, data=None):
    """A bundle whose HEAD is `commit`, dropped under a valid name. Returns the name."""
    out = E.tmp / ("made-%s.bundle" % commit[:8])
    if not out.exists():
        _t10_git(E.repo, "checkout", "-q", "--detach", commit)
        _t10_git(E.repo, "bundle", "create", out, "HEAD")
    name = "z1b-%s-%s.bundle" % (commit, os.urandom(8).hex())
    target = E.paths.drop_dir / name
    target.write_bytes(out.read_bytes() if data is None else data)
    os.chmod(str(target), 0o644)
    return name


def _t10_launcher(E):
    def launch(run_id):
        E.launched.append((run_id, (E.paths.stage_dir / (run_id + ".bundle")).is_file()))
        with open(str(E.trace), "a") as fh:
            fh.write(json.dumps({"ev": "launch", "run": run_id, "t": time.time()}) + "\n")
        cfg = E.tmp / ("cfg-%s.json" % run_id)
        cfg.write_text(json.dumps(E.cand_cfg))
        return subprocess.Popen([sys.executable, "-B", str(E.script), str(cfg)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env={"PATH": "/usr/bin:/bin"}, close_fds=True)
    return launch


def _t10_ctx(E, **over):
    def wipe(run_id):
        with open(str(E.trace), "a") as fh:
            fh.write(json.dumps({"ev": "wipe", "run": run_id, "t": time.time()}) + "\n")

    base = dict(
        spec_path=E.spec_path, decrypt=lambda p: Path(p).read_bytes(),
        encrypt=lambda data, dest: Path(dest).write_bytes(data),
        launch_candidate=_t10_launcher(E), wipe=wipe,
        probe=lambda: {k: "EACCES" for k in ("eval_home", "keys_dir", "sets_dir", "runs_dir",
                                             "incidents_dir", "manifest_file")},
        tmutil_isexcluded=lambda p: True, now=lambda: _T10_NOW, sudo_uid=lambda: os.getuid(),
        sweep_leftovers=lambda: [], sweep_processes=lambda: 0, runtime_sha256=lambda: "ab" * 32,
        rss_mb=lambda: 12, scan=lambda paths, ctx, led: {"hits": [], "canary_hits": 0, "unreadable": 0},
        reseal=lambda paths, ctx, led: {"resealed": False}, is_tty=lambda: True,
        scan_agent_present=lambda: True, git_env=_t10_genv(), root_uid=os.getuid(), eval_uid=os.getuid(),
        candidate_uid=os.getuid() + 1)
    base.update(over)
    return B.BrokerContext(**base)


def _t10_argv(commit, name, suite="holdout", declare=None, final=False):
    argv = ["run", "--commit", commit, "--bundle", name, "--suite", suite]
    if declare:
        argv += ["--declare", declare]
    if final:
        argv.append("--final-acceptance")
    return argv


def _t10_call(E, argv, ctx=None, paths=None):
    """Run broker_main in-process; returns (rc, stdout text, validated doc)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = B.broker_main(argv, paths=paths or E.paths, ctx=ctx or _t10_ctx(E))
    text = out.getvalue()
    assert text.endswith("\n") and text.count("\n") == 1, text
    return rc, text, output.validate_broker_json(text)


_T10_AUTO = object()


def _t10_run(E, commit=None, *, suite="holdout", ctx=None, declare=_T10_AUTO, final=False, name=None):
    commit = commit or E.commits[0]
    if declare is _T10_AUTO:
        # holdout must be declared; reserve is never declared. A test forces undeclared with declare=None.
        declare = getattr(E, "default_decl", None) if suite == "holdout" else None
    name = name or _t10_drop(E, commit)
    return _t10_call(E, _t10_argv(commit, name, suite, declare, final), ctx)


def _t10_refused(res, code):
    rc, _text, doc = res
    assert doc == {"ok": False, "code": code, "broker_sha": _T10_SHA}, doc
    assert rc == (3 if code == "INTERNAL" else 2)


def _t10_ledger(E):
    return Ledger(E.paths.runs_dir)


def _t10_kinds(E):
    return [e["kind"] for e in _t10_ledger(E).entries()]


# ================================================================ grammar

def test_t10_grammar_accepts_exactly_the_documented_forms():
    c, n = "a" * 40, "z1b-%s-%s.bundle" % ("a" * 40, "b" * 16)
    assert B.broker_parse_argv(["status"]) == {"cmd": "status"}
    assert B.broker_parse_argv(["seal-check"]) == {"cmd": "seal-check"}
    assert B.broker_parse_argv(["scan"]) == {"cmd": "scan"}
    assert B.broker_parse_argv(["ack-incident", "0" * 16]) == {"cmd": "ack-incident", "incident_id": "0" * 16}
    got = B.broker_parse_argv(["run", "--commit", c, "--bundle", n, "--suite", "holdout"])
    assert got == {"cmd": "run", "commit": c, "bundle": n, "suite": "holdout", "declare": None,
                   "final_acceptance": False}
    d = "eval/z1b/declarations/my-decl-1.json"
    got = B.broker_parse_argv(["run", "--suite", "holdout", "--declare", d, "--bundle", n, "--commit", c])
    assert got["declare"] == d
    got = B.broker_parse_argv(["run", "--commit", c, "--bundle", n, "--suite", "reserve", "--final-acceptance"])
    assert got["suite"] == "reserve" and got["final_acceptance"] is True
    # a reserve run WITHOUT the flag parses; the broker refuses it later with RESERVE_NOT_ELIGIBLE
    assert B.broker_parse_argv(["run", "--commit", c, "--bundle", n, "--suite", "reserve"])["suite"] == "reserve"


_T10_C, _T10_N = "a" * 40, "z1b-%s-%s.bundle" % ("a" * 40, "b" * 16)


def _t10_bad(argv):
    with pytest.raises(SealRefusal) as ei:
        B.broker_parse_argv(argv)
    assert ei.value.code == "BAD_ARGS"


@pytest.mark.parametrize("argv", [
    [], None, "status", ["STATUS"], ["Status"], ["status "], ["stat"], ["rm", "-rf", "/"], ["help"], ["--help"],
    ["-h"], ["decrypt"], ["author"], ["ack"], [""], [" "], ["status\n"], ["run;status"], [["status"]], [b"status"],
])
def test_t10_argv_fuzz_unknown_subcommand_is_bad_args(argv):
    _t10_bad(argv)


@pytest.mark.parametrize("argv", [
    ["status", "extra"], ["status", "--json"], ["seal-check", "x"], ["scan", "/Users/dev"],
    ["scan", "--targets", "/tmp"], ["ack-incident"], ["ack-incident", "0" * 16, "extra"],
    ["ack-incident", "0" * 15], ["ack-incident", "0" * 17], ["ack-incident", "A" * 16],
    ["ack-incident", "../" + "0" * 13], ["run"], ["run", "extra"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N], ["run", "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--suite", "holdout"], ["run", "--bundle", _T10_N, "--suite", "holdout"],
])
def test_t10_argv_fuzz_extra_or_missing_arguments_are_bad_args(argv):
    _t10_bad(argv)


@pytest.mark.parametrize("path_arg", [
    "/etc/passwd", "/Users/zurvaneval/sets/holdout.age", "../../etc/passwd", "./x.bundle", "~/x.bundle",
    "/Users/Shared/zurvan-eval-drop/" + _T10_N,
])
def test_t10_argv_fuzz_path_arguments_are_bad_args(path_arg):
    _t10_bad(["run", "--commit", _T10_C, "--bundle", path_arg, "--suite", "holdout"])
    _t10_bad(["run", "--commit", path_arg, "--bundle", _T10_N, "--suite", "holdout"])
    _t10_bad(["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", path_arg])
    _t10_bad(["scan", path_arg])
    _t10_bad(["status", path_arg])


@pytest.mark.parametrize("bundle", [
    "../" + _T10_N, "sub/" + _T10_N, _T10_N + "/../x", _T10_N + "\n", _T10_N + ".bak", " " + _T10_N,
    "z1b-%s-%s.bundle" % ("a" * 40, "b" * 15), "z1b-%s-%s.bundle" % ("a" * 41, "b" * 16),
    "z1b-%s-%s.bundle" % ("A" * 40, "b" * 16), "z1b-%s-%s.bundle" % ("a" * 40, "b" * 16) + "\x00",
    "..", ".", "",
    "z1b-%s-%s.bundle" % ("c" * 40, "b" * 16),  # well formed, but names a different commit than --commit
])
def test_t10_argv_fuzz_bundle_traversal_and_bad_names_are_bad_args(bundle):
    _t10_bad(["run", "--commit", _T10_C, "--bundle", bundle, "--suite", "holdout"])


@pytest.mark.parametrize("declare", [
    "eval/z1b/declarations/x.json/../../../etc/passwd", "../eval/z1b/declarations/x.json",
    "/eval/z1b/declarations/x.json", "eval/z1b/declarations/X.json", "eval/z1b/declarations/x.JSON",
    "eval/z1b/declarations/x.json\n", "eval/z1b/declarations/.json", "eval/z1b/declarations/a/b.json",
    "eval/z1b/declarations/" + "a" * 65 + ".json", "eval/z1b/other/x.json", "eval/z1b/declarations/x.json.bak",
    "eval/z1b/declarations/x y.json", "eval/z1b/declarations/x_y.json", "eval/z1b/declarations/а.json", "",
])
def test_t10_argv_fuzz_declare_outside_the_pattern_is_bad_args(declare):
    _t10_bad(["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout", "--declare", declare])


def test_t10_argv_fuzz_final_acceptance_on_holdout_is_bad_args():
    _t10_bad(["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout", "--final-acceptance"])


def test_t10_argv_fuzz_declare_on_reserve_is_bad_args():
    _t10_bad(["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "reserve", "--final-acceptance",
              "--declare", "eval/z1b/declarations/x.json"])


@pytest.mark.parametrize("argv", [
    ["run", "--commit", _T10_C, "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout", "--suite", "reserve"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "reserve", "--final-acceptance",
     "--final-acceptance"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout",
     "--declare", "eval/z1b/declarations/x.json", "--declare", "eval/z1b/declarations/y.json"],
])
def test_t10_argv_fuzz_duplicated_flags_are_bad_args(argv):
    _t10_bad(argv)


@pytest.mark.parametrize("argv", [
    ["run", "--commit=" + _T10_C, "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle=" + _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite=holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "reserve", "--final-acceptance=1"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout",
     "--declare=eval/z1b/declarations/x.json"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout", "--unknown", "x"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout", "-v"],
    ["run", "--commit", "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite"],
])
def test_t10_argv_fuzz_equals_joined_and_malformed_flags_are_bad_args(argv):
    _t10_bad(argv)


@pytest.mark.parametrize("argv", [
    ["run", "--commit", "１" * 40, "--bundle", "z1b-%s-%s.bundle" % ("１" * 40, "b" * 16), "--suite", "holdout"],
    ["run", "--commit", "١" * 40, "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", "z1b-%s-%s.bundle" % ("a" * 40, "а" * 16), "--suite", "holdout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "hоldout"],
    ["run", "--commit", _T10_C, "--bundle", _T10_N, "--suite", "holdout​"],
    ["ack-incident", "٠" * 16], ["ack-incident", "a" * 15 + "①"],
    ["stаtus"], ["run", "--cоmmit", _T10_C, "--bundle", _T10_N, "--suite", "holdout"],
    ["run", "--commit", _T10_C + "\u0000", "--bundle", _T10_N, "--suite", "holdout"],
])
def test_t10_argv_fuzz_unicode_lookalikes_are_bad_args(argv):
    _t10_bad(argv)


def test_t10_argv_fuzz_random_garbage_is_valid_or_bad_args_never_another_exception():
    rng = random.Random(1010)
    atoms = ["run", "status", "scan", "seal-check", "ack-incident", "--commit", "--bundle", "--suite",
             "--declare", "--final-acceptance", _T10_C, _T10_N, "holdout", "reserve", "0" * 16, "x", "",
             "--", "-", "а", "eval/z1b/declarations/a.json", "a" * 40]
    for _ in range(3000):
        argv = [rng.choice(atoms) for _ in range(rng.randint(0, 9))]
        try:
            got = B.broker_parse_argv(argv)
        except SealRefusal as exc:
            assert exc.code == "BAD_ARGS"
        else:
            assert got["cmd"] in ("status", "seal-check", "scan", "ack-incident", "run")


def test_t10_main_turns_bad_args_into_a_refusal_document_with_empty_stderr(tmp_path, capfd):
    E = _t10_build(tmp_path)
    res = _t10_call(E, ["run", "--commit=" + _T10_C])
    _t10_refused(res, "BAD_ARGS")
    assert capfd.readouterr().err == ""


# ================================================================ output discipline

def test_t10_stdout_is_exactly_one_validated_json_document_for_every_command(tmp_path):
    E = _t10_build(tmp_path)
    for argv in (["status"], ["seal-check"], ["scan"], ["ack-incident", "f" * 16], ["bogus"]):
        rc, text, doc = _t10_call(E, argv)
        assert text.count("\n") == 1 and text == json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n"
        assert rc in (0, 2, 3)
    rc, text, doc = _t10_run(E)
    assert rc == 0 and doc["ok"] is True and text.count("\n") == 1


def test_t10_stderr_is_empty_in_every_refusal(tmp_path, capfd):
    E = _t10_build(tmp_path, commits=2)
    capfd.readouterr()
    scenarios = []
    scenarios.append(lambda: _t10_call(E, ["nope"]))                                       # BAD_ARGS
    scenarios.append(lambda: _t10_run(E, suite="reserve"))                                  # RESERVE_NOT_ELIGIBLE
    scenarios.append(lambda: _t10_run(E, name="z1b-%s-%s.bundle" % (E.commits[0], "9" * 16)))  # BUNDLE_INVALID
    (E.paths.corpus_dir / "wiki" / "extra.md").write_text("changed")
    scenarios.append(lambda: _t10_run(E))                                                   # CORPUS_MISMATCH
    for sc in scenarios:
        res = sc()
        assert res[2]["ok"] is False
        assert capfd.readouterr().err == ""


def test_t10_internal_error_leaks_nothing_and_writes_only_errors_log(tmp_path, capfd):
    E = _t10_build(tmp_path)
    secret = E.holdout[0]["query"]

    def boom(paths, ctx, led):
        print("stderr noise " + secret, file=sys.stderr)
        warnings.warn("warning noise " + secret)
        raise RuntimeError("explosion carrying " + secret)

    capfd.readouterr()
    res = _t10_run(E, ctx=_t10_ctx(E, scan=boom))
    _t10_refused(res, "INTERNAL")
    out, err = capfd.readouterr()
    assert err == ""
    assert secret not in res[1] and "explosion" not in res[1] and "Traceback" not in res[1]
    log = E.paths.runs_dir / "errors.log"
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    body = log.read_text()
    assert "RuntimeError" in body and secret in body and "Traceback" in body


def test_t10_internal_error_exit_code_is_three_and_hooks_are_restored(tmp_path):
    E = _t10_build(tmp_path)
    hooks = (sys.stderr, sys.excepthook, warnings.showwarning)

    def boom(paths, ctx, led):
        raise ValueError("x")

    rc, _t, _d = _t10_run(E, ctx=_t10_ctx(E, scan=boom))
    assert rc == 3
    assert (sys.stderr, sys.excepthook, warnings.showwarning) == hooks


# ================================================================ hostile environment

def test_t10_hostile_environment_has_no_effect(tmp_path, monkeypatch):
    E = _t10_build(tmp_path)
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "MARKER"
    (evil / ".gitconfig").write_text("[core]\n\tfsmonitor = touch %s\n\tpager = touch %s\n[alias]\n\tshow = !touch %s\n"
                                     % (marker, marker, marker))
    (evil / "sitecustomize.py").write_text("open(%r, 'w').write('x')\n" % str(marker))
    monkeypatch.setenv("PYTHONPATH", str(evil))
    monkeypatch.setenv("PATH", str(evil))
    monkeypatch.setenv("HOME", str(evil))
    monkeypatch.setenv("GIT_DIR", str(evil))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(evil / ".gitconfig"))
    monkeypatch.setenv("ZURVAN_ROOT", str(evil))
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", str(evil / "x.dylib"))
    monkeypatch.setenv("PYTHONSTARTUP", str(evil / "sitecustomize.py"))
    rc, _text, doc = _t10_run(E)
    assert rc == 0 and doc["ok"] is True
    assert not marker.exists()
    assert list(evil.iterdir()) and sorted(p.name for p in evil.iterdir()) == [".gitconfig", "sitecustomize.py"]
    # every sealed artefact landed under the INJECTED SealPaths
    assert (E.paths.runs_dir / "ledger.jsonl").is_file() and any((E.paths.runs_dir / "rows").iterdir())
    # and a declared run (git show/diff-tree path) is equally unaffected
    assert not marker.exists()


def test_t10_every_subprocess_gets_the_clean_env_and_an_absolute_binary(tmp_path, monkeypatch):
    E = _t10_build(tmp_path)
    for k, v in (("PYTHONPATH", "/evil"), ("PATH", "/evil"), ("HOME", "/evil"), ("GIT_DIR", "/evil"),
                 ("DYLD_INSERT_LIBRARIES", "/evil"), ("ZURVAN_ROOT", "/evil"), ("LD_PRELOAD", "/evil")):
        monkeypatch.setenv(k, v)
    seen = []
    real = subprocess.run

    def spy(cmd, *a, **kw):
        seen.append((list(cmd), dict(kw.get("env") or {})))
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    _t10_run(E)
    assert len(seen) >= 6
    allowed = {"PATH", "HOME", "LANG", "LC_ALL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
               "GIT_TERMINAL_PROMPT", "GIT_ATTR_NOSYSTEM"}
    for cmd, env in seen:
        assert cmd[0].startswith("/"), cmd
        assert env and set(env) <= allowed, (cmd, sorted(env))
        assert env["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin" and env["HOME"] != "/evil"
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"


# ================================================================ gates

def test_t10_open_incident_refuses_with_integrity_incident_and_charges_nothing(tmp_path):
    E = _t10_build(tmp_path)
    _t10_ledger(E).record_incident("canary_hit", [E.holdout[0]["id"]])
    scans = []
    ctx = _t10_ctx(E, scan=lambda paths, c, led: scans.append(1) or {})
    _t10_refused(_t10_run(E, ctx=ctx), "INTEGRITY_INCIDENT")
    assert "reserved" not in _t10_kinds(E)
    assert E.launched == [] and scans == []     # refused before the scan, the manifest or the bundle


def test_t10_an_incident_raised_by_the_scan_refuses_the_same_run(tmp_path):
    E = _t10_build(tmp_path)

    def scan(paths, ctx, led):
        led.record_incident("item_leak", [E.holdout[0]["id"]])
        return {"hits": [E.holdout[0]["id"]], "canary_hits": 0, "unreadable": 0}

    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, scan=scan)), "INTEGRITY_INCIDENT")
    assert "reserved" not in _t10_kinds(E)


def test_t10_scan_refusal_codes_propagate_unchanged(tmp_path):
    E = _t10_build(tmp_path)

    def scan(paths, ctx, led):
        raise SealRefusal("SCAN_INCOMPLETE")

    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, scan=scan)), "SCAN_INCOMPLETE")
    assert "reserved" not in _t10_kinds(E)


def test_t10_tampered_sealed_manifest_is_manifest_invalid(tmp_path):
    E = _t10_build(tmp_path)
    data = bytearray(sealed_store.manifest(E.paths).read_bytes())
    data[data.index(b'"accepted":') + 11] ^= 0x01
    sealed_store.manifest(E.paths).write_bytes(bytes(data))
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_missing_signature_or_signers_is_manifest_invalid(tmp_path):
    E = _t10_build(tmp_path)
    sig = sealed_store.manifest_sig(E.paths)
    saved = sig.read_bytes()
    sig.unlink()
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")
    sig.write_bytes(saved)
    (E.paths.keys_dir / "allowed_signers").unlink()
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")


def test_t10_rollback_sequence_is_manifest_invalid(tmp_path):
    E = _t10_build(tmp_path)
    _t10_seal(E, sequence=2)
    _t10_ledger(E).append({"kind": "manifest_seen", "sequence": 9, "manifest_sha256": "ee" * 32})
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_a_different_manifest_under_an_already_seen_sequence_is_manifest_invalid(tmp_path):
    E = _t10_build(tmp_path)
    _t10_run(E)  # records manifest_seen for sequence 1
    _t10_seal(E, sequence=1, suite_id="fedcba9876543210")  # same sequence, different (validly signed) content
    _t10_refused(_t10_run(E, E.commits[0]), "MANIFEST_INVALID")


def test_t10_corpus_byte_changed_is_corpus_mismatch(tmp_path):
    E = _t10_build(tmp_path)
    page = E.paths.corpus_dir / "wiki" / "p0.md"
    page.write_text(page.read_text() + "x")
    _t10_refused(_t10_run(E), "CORPUS_MISMATCH")
    assert "reserved" not in _t10_kinds(E)


def test_t10_holdout_set_digest_mismatch_is_manifest_invalid(tmp_path):
    E = _t10_build(tmp_path)
    f = sealed_store.holdout_age(E.paths)
    f.write_bytes(f.read_bytes() + b"\n")
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_a_burned_item_sitting_in_the_holdout_set_is_manifest_invalid_before_any_charge(tmp_path):
    E = _t10_build(tmp_path)
    # a crafted but validly signed manifest + set that still contains an item the ledger has burned
    _t10_ledger(E).burn_items([E.holdout[3]["id"]], "item_leak", "2026-10-01")
    _t10_refused(_t10_run(E), "MANIFEST_INVALID")
    assert "reserved" not in _t10_kinds(E) and E.launched == []


def test_t10_bundle_head_that_is_not_the_commit_is_candidate_not_frozen(tmp_path):
    E = _t10_build(tmp_path, commits=2)
    wrong = _t10_drop(E, E.commits[1])                       # HEAD is commit 1 ...
    name = "z1b-%s-%s.bundle" % (E.commits[0], "5" * 16)      # ... but the name and --commit claim commit 0
    (E.paths.drop_dir / name).write_bytes((E.paths.drop_dir / wrong).read_bytes())
    _t10_refused(_t10_run(E, E.commits[0], name=name), "CANDIDATE_NOT_FROZEN")
    assert "reserved" not in _t10_kinds(E)
    assert not list(E.paths.stage_dir.iterdir())


def test_t10_corrupt_or_foreign_bundles_are_bundle_invalid(tmp_path):
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, name=_t10_drop(E, E.commits[0], data=b"not a bundle at all")), "BUNDLE_INVALID")
    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, sudo_uid=lambda: os.getuid() + 1)), "BUNDLE_INVALID")
    _t10_refused(_t10_run(E, name="z1b-%s-%s.bundle" % (E.commits[0], "7" * 16)), "BUNDLE_INVALID")  # absent
    assert "reserved" not in _t10_kinds(E) and not list(E.paths.stage_dir.iterdir())


def test_t10_missing_sudo_uid_is_bad_args(tmp_path, monkeypatch):
    E = _t10_build(tmp_path)
    monkeypatch.delenv("SUDO_UID", raising=False)
    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, sudo_uid=sysenv.sudo_uid)), "BAD_ARGS")


# ================================================================ runs, cache, budget

def test_t10_a_perfect_candidate_run_returns_a_valid_overall_only_result(tmp_path):
    E = _t10_build(tmp_path)
    rc, _t, doc = _t10_run(E)
    assert rc == 0
    assert doc["suite"] == "holdout" and doc["suite_id"] == "0123456789abcdef"
    assert doc["n_items"] == 10 and doc["target_missing"] == 0
    assert doc["candidate_commit"] == E.commits[0] and doc["broker_sha"] == _T10_SHA
    # six rank items hit at rank 1; the two set items hold 2 targets each, one of which is rank 1
    assert doc["metrics"]["recall_at_1"] == 0.875 and doc["metrics"]["recall_at_3"] == 1.0
    assert doc["metrics"]["recall_at_10"] == 1.0 and doc["metrics"]["mrr"] == 1.0
    assert doc["abstain"] == {"passed": 2, "n": 2}
    # Rev 3: holdout is always declared. No baseline exists, so promotion is a FAIL verdict (never None),
    # and the per-stratum block never leaves the seal (G20).
    assert doc["per_stratum"] is None and doc["promotion"]["verdict"] == "FAIL"
    assert doc["cached"] is False and doc["seal_valid"] is True and doc["budget_remaining"] == 7
    # G20: the printed result is the projection (memory and latency snapped up the ladder); the sealed
    # ledger keeps the exact figures
    assert doc["peak_rss_mb"] == 64 and doc["latency_ms"]["p50"] in output.LATENCY_LADDER_MS
    assert doc["metrics"]["ci"] is None
    sealed = [e for e in _t10_ledger(E).entries() if e["kind"] == "completed"][-1]["result"]
    assert sealed["peak_rss_mb"] == 12 and sealed["metrics"]["ci"] is not None
    assert doc["suite_manifest_sha256"] == manifest_mod.suite_manifest_sha256(sealed_store.manifest(E.paths))
    assert doc["corpus_sha256"] == E.manifest["corpus_sha256"]
    assert E.launched and all(flag for _rid, flag in E.launched)   # the bundle was staged under the run id
    assert not list(E.paths.stage_dir.iterdir())                   # and cleaned up
    assert _t10_kinds(E) == ["manifest_seen", "reserved", "completed"]


def test_t10_a_result_that_cannot_be_serialised_is_a_failed_charged_run_not_a_completed_one(tmp_path):
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, rss_mb=lambda: -5)), "INTERNAL")
    led = _t10_ledger(E)
    kinds = [e["kind"] for e in led.entries()]
    assert "completed" not in kinds and [e["code"] for e in led.entries() if e["kind"] == "failed"] == ["INTERNAL"]
    assert led.budget_remaining("0123456789abcdef", 8) == 7 and not led.unresolved_runs()
    assert not any((E.paths.runs_dir / "rows").glob("*")) if (E.paths.runs_dir / "rows").exists() else True


def test_t10_a_wrong_candidate_scores_low_and_the_grader_not_the_candidate_decides(tmp_path):
    E = _t10_build(tmp_path)
    E.cand_cfg["oracle"] = {}
    rc, _t, doc = _t10_run(E)
    assert rc == 0 and doc["metrics"]["recall_at_1"] == 0.0 and doc["metrics"]["mrr"] == 0.0
    assert doc["abstain"] == {"passed": 2, "n": 2}  # empty answers abstain on the not_found items


def test_t10_repeat_run_is_cached_and_costs_no_budget(tmp_path):
    E = _t10_build(tmp_path)
    first = _t10_run(E)[2]
    launched = len(E.launched)
    again = _t10_run(E)[2]
    assert again["cached"] is True and first["cached"] is False
    assert {k: v for k, v in again.items() if k != "cached"} == {k: v for k, v in first.items() if k != "cached"}
    assert again["budget_remaining"] == first["budget_remaining"] == 7
    assert len(E.launched) == launched and _t10_kinds(E).count("reserved") == 1


def test_t10_a_changed_runtime_digest_is_a_cache_miss(tmp_path):
    # ruling R14: the run key includes the candidate runtime's identity
    E = _t10_build(tmp_path)
    _t10_run(E)
    again = _t10_run(E, ctx=_t10_ctx(E, runtime_sha256=lambda: "cd" * 32))[2]
    assert again["cached"] is False and again["budget_remaining"] == 6


def test_t10_eight_runs_then_the_ninth_is_suite_renewal_required(tmp_path):
    E = _t10_build(tmp_path, commits=9)
    for i in range(8):
        doc = _t10_run(E, E.commits[i])[2]
        assert doc["ok"] is True and doc["budget_remaining"] == 7 - i
    _t10_refused(_t10_run(E, E.commits[8]), "SUITE_RENEWAL_REQUIRED")
    assert _t10_kinds(E).count("reserved") == 8
    assert _t10_run(E, E.commits[0])[2]["cached"] is True   # a cached key still answers at zero budget


def test_t10_a_crashed_previous_run_is_failed_and_charged_at_startup(tmp_path):
    E = _t10_build(tmp_path)
    led = _t10_ledger(E)
    led.reserve_run("ab" * 32, "0123456789abcdef", 8)          # reserved, never resolved: a crash
    assert led.unresolved_runs()
    doc = _t10_run(E)[2]
    assert doc["ok"] is True and doc["budget_remaining"] == 8 - 2   # the crashed run AND this one are charged
    entries = led.entries()
    failed = [e for e in entries if e["kind"] == "failed"]
    assert len(failed) == 1 and failed[0]["code"] == "INTERNAL"
    assert not led.unresolved_runs()


@pytest.mark.parametrize("mode", ["garbage", "crash", "extra"])
def test_t10_candidate_protocol_violation_or_crash_is_candidate_failed_and_charged(tmp_path, mode):
    E = _t10_build(tmp_path)
    E.cand_cfg.update(mode=mode, at=3)
    _t10_refused(_t10_run(E), "CANDIDATE_FAILED")
    led = _t10_ledger(E)
    assert [e["code"] for e in led.entries() if e["kind"] == "failed"] == ["CANDIDATE_FAILED"]
    assert led.budget_remaining("0123456789abcdef", 8) == 7
    assert not list(E.paths.stage_dir.iterdir())
    # the failed key can be retried, at a second charge
    E.cand_cfg.update(mode="ok")
    assert _t10_run(E)[2]["budget_remaining"] == 6


@pytest.mark.parametrize("mode", ["badexit", "trailing"])
def test_t10_a_candidate_that_misbehaves_after_its_last_answer_is_candidate_failed(tmp_path, mode):
    E = _t10_build(tmp_path)
    E.cand_cfg.update(mode=mode)
    _t10_refused(_t10_run(E), "CANDIDATE_FAILED")
    assert _t10_ledger(E).budget_remaining("0123456789abcdef", 8) == 7


def test_t10_candidate_timeout_is_candidate_failed_and_charged(tmp_path):
    E = _t10_build(tmp_path)
    policy = json.loads(SPEC.read_text())
    policy["run_policy"]["query_timeout_s"] = 1
    E.spec_path = tmp_path / "spec-fast.json"
    E.spec_path.write_text(json.dumps(policy))
    E.cand_cfg.update(mode="hang", at=2)
    t0 = time.time()
    _t10_refused(_t10_run(E), "CANDIDATE_FAILED")
    assert time.time() - t0 < 20
    assert _t10_ledger(E).budget_remaining("0123456789abcdef", 8) == 7


def test_t10_a_candidate_that_exits_before_answering_is_candidate_failed(tmp_path):
    E = _t10_build(tmp_path)
    E.cand_cfg.update(mode="crash", at=1)
    _t10_refused(_t10_run(E), "CANDIDATE_FAILED")


def test_t10_candidate_never_receives_targets_or_grading(tmp_path):
    E = _t10_build(tmp_path)
    _t10_run(E)
    data = E.rec.read_bytes()
    lines = data.splitlines()
    assert len(lines) == len(E.holdout)
    queries = {i["query"] for i in E.holdout}
    seen = set()
    for line in lines:
        obj = json.loads(line)
        assert set(obj) == {"qid", "q"}
        seen.add(obj["q"])
    assert seen == queries
    text = data.decode()
    for item in E.holdout + E.reserve:
        assert item["id"] not in text
        for t in item["targets"]:
            assert t["page"] not in text
    for stratum in STRATA:
        assert stratum not in text
    for word in ("grading", "difficulty", "targets", "provenance", "rank"):
        assert word not in text


def test_t10_rows_file_holds_opaque_ids_and_flags_and_no_query_or_target_text(tmp_path):
    E = _t10_build(tmp_path)
    E.cand_cfg["mode"] = "ok"
    _t10_run(E)
    rows_files = list((E.paths.runs_dir / "rows").iterdir())
    assert len(rows_files) == 1 and oct(rows_files[0].stat().st_mode & 0o777) == "0o600"
    blob = rows_files[0].read_text()
    for item in E.holdout + E.reserve:
        assert item["query"] not in blob and _T10_PLANTED not in blob
        for t in item["targets"]:
            assert t["page"] not in blob and "wiki/" not in blob
    rows = json.loads(blob)["rows"]
    assert sorted(r["id"] for r in rows) == sorted(i["id"] for i in E.holdout)
    allowed = {"id", "stratum", "grading", "rank", "set_fraction", "set_fraction_at", "abstained", "target_missing"}
    assert all(set(r) <= allowed for r in rows)


def test_t10_the_ledger_and_logs_hold_no_query_text_either(tmp_path):
    E = _t10_build(tmp_path)
    _t10_run(E)
    for path in E.paths.runs_dir.rglob("*"):
        if path.is_file():
            assert _T10_PLANTED not in path.read_text(errors="replace"), path


# ================================================================ reserve

def test_t10_reserve_without_final_acceptance_is_not_eligible(tmp_path):
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, suite="reserve"), "RESERVE_NOT_ELIGIBLE")
    assert "reserved" not in _t10_kinds(E) and not list(E.paths.stage_dir.iterdir())


def test_t10_reserve_with_the_flag_but_no_prior_holdout_pass_is_not_eligible(tmp_path):
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, suite="reserve", final=True), "RESERVE_NOT_ELIGIBLE")
    _t10_run(E)  # an UNDECLARED holdout run has no promotion verdict: still not a pass
    _t10_refused(_t10_run(E, suite="reserve", final=True), "RESERVE_NOT_ELIGIBLE")
    assert "reserved" in _t10_kinds(E) and _t10_kinds(E).count("reserved") == 1


# ================================================================ declared runs and promotion

_T10_DECL_PATH = "eval/z1b/declarations/cand-1.json"
_T10_DECL_PATH2 = "eval/z1b/declarations/cand-2.json"
_T10_DECL_BASE = "eval/z1b/declarations/base-1.json"
_T10_DECL_DEFAULT = "eval/z1b/declarations/default.json"


def _t10_decl_body(E, *, margins=None, budgets=None, **over):
    body = {
        "role": "candidate", "suite_id": "0123456789abcdef", "primary_metric": "recall_at_10",
        "critical_strata": ["exact_identifier", "path"],
        "non_inferiority_margins": margins or {"critical": 0.05, "overall": 0.02},
        "improvement_target": {"metric": "mrr", "min_delta": 0.01},
        "resource_budgets": budgets or {"latency_p95_ms": 100000, "peak_rss_mb": 4096},
        "resource_improvement": None,
    }
    body.update(over)
    return body


def _t10_declared(E, *, parent=None, decl=None, extra_files=None, margins=None, budgets=None,
                  path=_T10_DECL_PATH):
    """Commit P (code) then C = P + the declaration file `path`. Returns (C, P, drop name).
    R20/R24: the declaration is bound by the commit that contains it; it has no candidate_commit field and
    carries a role (candidate by default)."""
    if parent is None:
        parent = _t10_commit(E, "code for declaration %s\n" % os.urandom(4).hex())
    body = {
        "role": "candidate", "suite_id": "0123456789abcdef", "primary_metric": "recall_at_10",
        "critical_strata": ["exact_identifier", "path"],
        "non_inferiority_margins": margins or {"critical": 0.05, "overall": 0.02},
        "improvement_target": {"metric": "mrr", "min_delta": 0.01},
        "resource_budgets": budgets or {"latency_p95_ms": 100000, "peak_rss_mb": 4096},
        "resource_improvement": None,
    }
    if decl:
        body.update(decl)
    files = {path: json.dumps(body)}
    files.update(extra_files or {})
    commit = _t10_commit(E, (E.repo / "code.txt").read_text(), extra=files)
    return commit, parent, _t10_drop(E, commit)


def _t10_weak_baseline(E):
    """R25: a declared BASELINE run of a candidate that answers nothing; later candidates compare to it."""
    saved = E.cand_cfg["oracle"]
    E.cand_cfg["oracle"] = {}
    commit, _p, name = _t10_declared(E, decl={"role": "baseline"}, path=_T10_DECL_BASE)
    rc, _t, doc = _t10_run(E, commit, declare=_T10_DECL_BASE, name=name)
    E.cand_cfg["oracle"] = saved
    assert rc == 0 and doc["promotion"] is None and doc["per_stratum"] is None  # G20: never printed
    sealed = [e for e in _t10_ledger(E).entries() if e["kind"] == "completed"][-1]["result"]
    assert sealed["per_stratum"] is not None and sealed["promotion"] is None  # the ledger keeps it
    return commit

def test_t10_a_declared_run_returns_per_stratum_and_a_promotion_verdict(tmp_path):
    E = _t10_build(tmp_path)
    _t10_weak_baseline(E)
    commit, _p, name = _t10_declared(E)
    rc, _t, doc = _t10_run(E, commit, declare=_T10_DECL_PATH, name=name)
    assert rc == 0 and doc["per_stratum"] is None  # G20: the per-stratum block never leaves the seal
    sealed = [e for e in _t10_ledger(E).entries() if e["kind"] == "completed"][-1]["result"]
    assert set(sealed["per_stratum"]) == {"exact_identifier", "path", "semantic", "multi_hop"}
    assert sealed["per_stratum"]["path"]["n"] == 2 and sealed["per_stratum"]["path"]["mrr"] == 1.0
    assert doc["promotion"] == {"declared": True, "verdict": "PASS", "rules": {
        "non_inferiority_critical": True, "non_inferiority_overall": True, "improvement": True,
        "resource_budget": True, "resource_improved": False}}

def test_t10_a_blown_resource_budget_fails_the_declared_run(tmp_path):
    E = _t10_build(tmp_path)
    _t10_weak_baseline(E)
    commit, _p, name = _t10_declared(E, budgets={"latency_p95_ms": 100000, "peak_rss_mb": 1})
    doc = _t10_run(E, commit, declare=_T10_DECL_PATH, name=name)[2]
    assert doc["promotion"]["verdict"] == "FAIL" and doc["promotion"]["rules"]["resource_budget"] is False
    assert doc["promotion"]["rules"]["improvement"] is True

def test_t10_the_second_declared_run_is_compared_with_the_promoted_baseline(tmp_path):
    E = _t10_build(tmp_path)
    _t10_weak_baseline(E)
    c1, _p, n1 = _t10_declared(E)
    assert _t10_run(E, c1, declare=_T10_DECL_PATH, name=n1)[2]["promotion"]["verdict"] == "PASS"
    c2, _p2, n2 = _t10_declared(E, path=_T10_DECL_PATH2)
    doc = _t10_run(E, c2, declare=_T10_DECL_PATH2, name=n2)[2]
    rules = doc["promotion"]["rules"]
    # identical to the promoted run (not the weak baseline): non-inferior everywhere, no improvement beyond noise
    assert rules["non_inferiority_critical"] and rules["non_inferiority_overall"] and rules["resource_budget"]
    assert rules["improvement"] is False and doc["promotion"]["verdict"] == "FAIL"

def test_t10_a_candidate_worse_than_the_baseline_fails_non_inferiority(tmp_path):
    E = _t10_build(tmp_path)
    _t10_weak_baseline(E)
    c1, _p, n1 = _t10_declared(E)
    _t10_run(E, c1, declare=_T10_DECL_PATH, name=n1)
    c2, _p2, n2 = _t10_declared(E, path=_T10_DECL_PATH2)
    E.cand_cfg["oracle"] = {}
    rules = _t10_run(E, c2, declare=_T10_DECL_PATH2, name=n2)[2]["promotion"]["rules"]
    assert rules["non_inferiority_critical"] is False and rules["non_inferiority_overall"] is False

def test_t10_declaration_naming_the_wrong_commit_is_declaration_invalid(tmp_path):
    E = _t10_build(tmp_path)
    commit, _p, name = _t10_declared(E, decl={"candidate_commit": "f" * 40})
    _t10_refused(_t10_run(E, commit, declare=_T10_DECL_PATH, name=name), "DECLARATION_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_declaration_naming_an_unrelated_earlier_commit_is_declaration_invalid(tmp_path):
    # R20/R24: the commit binds the declaration; a candidate_commit field of any value is an unknown key
    E = _t10_build(tmp_path)
    commit, parent, name = _t10_declared(E, decl={"candidate_commit": E.commits[0]})
    assert E.commits[0] != parent
    _t10_refused(_t10_run(E, commit, declare=_T10_DECL_PATH, name=name), "DECLARATION_INVALID")

def test_t10_declaration_for_a_different_suite_is_declaration_invalid(tmp_path):
    E = _t10_build(tmp_path)
    commit, _p, name = _t10_declared(E, decl={"suite_id": "ffffffffffffffff"})
    _t10_refused(_t10_run(E, commit, declare=_T10_DECL_PATH, name=name), "DECLARATION_INVALID")


@pytest.mark.parametrize("bad", [
    {"primary_metric": "latency"}, {"critical_strata": []}, {"critical_strata": ["not_a_stratum"]},
    {"critical_strata": ["path", "path"]}, {"non_inferiority_margins": {"critical": 2, "overall": 0.1}},
    {"non_inferiority_margins": {"critical": 0.1}}, {"improvement_target": {"metric": "mrr", "min_delta": -1}},
    {"resource_budgets": {"latency_p95_ms": 5, "peak_rss_mb": 10}}, {"resource_budgets": {"latency_p95_ms": 100}},
    {"extra_field": 1}, {"improvement_target": {"metric": "x", "min_delta": 0.1}},
])
def test_t10_malformed_declarations_are_declaration_invalid(tmp_path, bad):
    E = _t10_build(tmp_path)
    commit, _p, name = _t10_declared(E, decl=bad)
    _t10_refused(_t10_run(E, commit, declare=_T10_DECL_PATH, name=name), "DECLARATION_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_a_declaration_file_that_is_not_in_the_commit_is_declaration_invalid(tmp_path):
    E = _t10_build(tmp_path)
    # _T10_DECL_PATH2 is not committed anywhere, so naming it is DECLARATION_INVALID
    _t10_refused(_t10_run(E, E.commits[0], declare=_T10_DECL_PATH2), "DECLARATION_INVALID")


def test_t10_reserve_after_a_passing_declared_holdout_run_is_accepted_once_per_commit(tmp_path):
    E = _t10_build(tmp_path)
    _t10_weak_baseline(E)
    commit, _p, name = _t10_declared(E)
    assert _t10_run(E, commit, declare=_T10_DECL_PATH, name=name)[2]["promotion"]["verdict"] == "PASS"
    # a different commit has no pass, so it is not eligible even with the flag
    other = _t10_commit(E, "other code\n")
    _t10_refused(_t10_run(E, other, suite="reserve", final=True), "RESERVE_NOT_ELIGIBLE")
    rc, _t, doc = _t10_run(E, commit, suite="reserve", final=True)
    assert rc == 0 and doc["suite"] == "reserve" and doc["n_items"] == len(E.reserve)
    assert doc["budget_remaining"] == 0 and doc["promotion"] is None
    again = _t10_run(E, commit, suite="reserve", final=True)[2]
    assert again["cached"] is True and again["budget_remaining"] == 0

def _t10_burned(E):
    return _t10_ledger(E).burned_ids()


def test_t10_sweep_finding_leftovers_burns_every_sent_item_and_blocks_the_next_run(tmp_path):
    E = _t10_build(tmp_path)
    resealed = []
    ctx = _t10_ctx(E, sweep_leftovers=lambda: ["/private/tmp/dropped-by-candidate"],
                   reseal=lambda paths, c, led: resealed.append(1) or {"resealed": True})
    _t10_refused(_t10_run(E, ctx=ctx), "SANDBOX_VIOLATION")
    assert _t10_burned(E) == {i["id"] for i in E.holdout}
    led = _t10_ledger(E)
    assert [e["incident_kind"] for e in led.open_incidents()] == ["sandbox_violation"]
    assert [e["code"] for e in led.entries() if e["kind"] == "failed"] == ["SANDBOX_VIOLATION"]
    assert resealed == [1]
    assert led.budget_remaining("0123456789abcdef", 8) == 7
    assert not list(E.paths.stage_dir.iterdir())
    # the next run, with a clean sweep, is still refused
    _t10_refused(_t10_run(E), "INTEGRITY_INCIDENT")
    files = [p.name for p in E.paths.incidents_dir.iterdir()]
    assert files == [led.open_incidents()[0]["incident_id"] + ".json"]


def test_t10_a_live_candidate_process_after_the_run_is_a_sandbox_violation(tmp_path):
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, sweep_processes=lambda: 2)), "SANDBOX_VIOLATION")
    assert _t10_burned(E) == {i["id"] for i in E.holdout}


def test_t10_a_sweep_that_cannot_tell_is_a_sandbox_violation_not_internal(tmp_path):
    E = _t10_build(tmp_path)

    def broken():
        raise SealRefusal("SANDBOX_VIOLATION")

    def exploding():
        raise OSError("ps vanished")

    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, sweep_processes=broken)), "SANDBOX_VIOLATION")
    E2 = _t10_build(tmp_path / "second")
    _t10_refused(_t10_run(E2, ctx=_t10_ctx(E2, sweep_leftovers=exploding)), "SANDBOX_VIOLATION")


def test_t10_a_candidate_that_fails_early_burns_only_what_it_was_sent(tmp_path):
    E = _t10_build(tmp_path)
    E.cand_cfg.update(mode="crash", at=3)
    _t10_refused(_t10_run(E, ctx=_t10_ctx(E, sweep_processes=lambda: 1)), "SANDBOX_VIOLATION")
    assert len(_t10_burned(E)) == 3
    sent = [json.loads(l)["q"] for l in E.rec.read_bytes().splitlines()]
    by_query = {i["query"]: i["id"] for i in E.holdout}
    assert _t10_burned(E) == {by_query[q] for q in sent}


def test_t10_a_failed_reseal_does_not_mask_the_violation(tmp_path):
    E = _t10_build(tmp_path)

    def bad_reseal(paths, ctx, led):
        raise RuntimeError("reseal exploded with " + E.holdout[0]["query"])

    res = _t10_run(E, ctx=_t10_ctx(E, sweep_processes=lambda: 1, reseal=bad_reseal))
    _t10_refused(res, "SANDBOX_VIOLATION")
    assert E.holdout[0]["query"] in (E.paths.runs_dir / "errors.log").read_text()
    assert E.holdout[0]["query"] not in res[1]


# ================================================================ concurrency

def _t10_child(E, commit, name, queue):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = B.broker_main(_t10_argv(commit, name, declare=E.default_decl), paths=E.paths, ctx=_t10_ctx(E))
    queue.put((rc, out.getvalue()))


def test_t10_concurrent_broker_runs_serialise_on_the_ledger_lock(tmp_path):
    E = _t10_build(tmp_path, commits=2)
    E.cand_cfg["delay"] = 0.4
    names = [_t10_drop(E, c) for c in E.commits]
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_t10_child, args=(E, c, n, queue)) for c, n in zip(E.commits, names)]
    for p in procs:
        p.start()
    results = [queue.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert [rc for rc, _t in results] == [0, 0]
    docs = [output.validate_broker_json(t) for _rc, t in results]
    assert sorted(d["budget_remaining"] for d in docs) == [6, 7]
    led = _t10_ledger(E)
    assert led.budget_remaining("0123456789abcdef", 8) == 6 and not led.unresolved_runs()
    events = [json.loads(l) for l in E.trace.read_text().splitlines()]
    launches = sorted((e["t"], e["run"]) for e in events if e["ev"] == "launch")
    wipes = {e["run"]: e["t"] for e in events if e["ev"] == "wipe"}
    assert len(launches) == 2
    assert launches[1][0] >= wipes[launches[0][1]]   # the second run launched only after the first finished


# ================================================================ status, scan, ack

def test_t10_status_reports_a_configured_seal(tmp_path):
    E = _t10_build(tmp_path)
    rc, _t, doc = _t10_call(E, ["status"])
    assert rc == 0 and doc["schema"] == "zurvan.z1b.status.v1"
    assert doc["configured"] is True and doc["manifest_valid"] is True and doc["corpus_match"] is True
    assert doc["installed_sha"] == _T10_SHA and doc["installed_tree"] == _T10_TREE and doc["stale"] is False
    assert doc["holdout_n"] == 10 and doc["reserve_n"] == 5 and doc["suite_id"] == "0123456789abcdef"
    assert doc["budget_remaining"] == 8 and doc["open_incidents"] == 0


def test_t10_status_of_an_unconfigured_root_is_a_closed_document_not_a_crash(tmp_path, capfd):
    paths = SealPaths.for_root(tmp_path / "empty")
    E = types.SimpleNamespace(paths=paths, spec_path=SPEC, tmp=tmp_path, launched=[], trace=tmp_path / "t",
                              rec=tmp_path / "r", script=tmp_path / "s", cand_cfg={})
    rc, _t, doc = _t10_call(E, ["status"], paths=paths, ctx=_t10_ctx(E))
    assert rc == 0 and doc["configured"] is False and doc["broker_sha"] == "0" * 40
    assert doc["manifest_valid"] is False and doc["holdout_n"] == 0 and doc["suite_id"] is None
    assert capfd.readouterr().err == ""


def test_t10_status_with_a_tampered_manifest_reports_invalid_not_a_refusal(tmp_path):
    E = _t10_build(tmp_path)
    sealed_store.manifest(E.paths).write_bytes(sealed_store.manifest(E.paths).read_bytes() + b" ")
    doc = _t10_call(E, ["status"])[2]
    assert doc["configured"] is True and doc["manifest_valid"] is False and doc["budget_remaining"] == 0


def test_t10_scan_subcommand_runs_the_scan_and_reports_status(tmp_path):
    E = _t10_build(tmp_path)
    calls = []
    ctx = _t10_ctx(E, scan=lambda paths, c, led: calls.append("scan") or {}, reseal=lambda p, c, led: calls.append("reseal") or {})
    rc, _t, doc = _t10_call(E, ["scan"], ctx=ctx)
    assert rc == 0 and doc["schema"] == "zurvan.z1b.status.v1" and calls == ["reseal", "scan"]


def test_t10_scan_subcommand_refuses_when_the_scan_leaves_an_open_incident(tmp_path):
    E = _t10_build(tmp_path)

    def scan(paths, c, led):
        led.record_incident("canary_hit", [])
        return {}

    _t10_refused(_t10_call(E, ["scan"], ctx=_t10_ctx(E, scan=scan)), "INTEGRITY_INCIDENT")


def test_t10_ack_incident_needs_a_terminal_and_a_real_incident_id(tmp_path):
    E = _t10_build(tmp_path)
    iid = _t10_ledger(E).record_incident("canary_hit", [E.holdout[0]["id"]])
    _t10_refused(_t10_call(E, ["ack-incident", iid], ctx=_t10_ctx(E, is_tty=lambda: False)), "BAD_ARGS")
    _t10_refused(_t10_call(E, ["ack-incident", "f" * 16]), "BAD_ARGS")
    assert len(_t10_ledger(E).open_incidents()) == 1
    rc, _t, doc = _t10_call(E, ["ack-incident", iid])
    assert rc == 0 and doc["open_incidents"] == 0
    _t10_refused(_t10_call(E, ["ack-incident", iid]), "BAD_ARGS")   # already acknowledged
    assert _t10_run(E)[2]["ok"] is True                              # the refusal is cleared


# ================================================================ seal-check

def _t10_layout(E):
    """Make the fake root look sealed: modes the real installer sets, owner = this test user."""
    P = E.paths
    P.runtime_dir.mkdir(parents=True, exist_ok=True)
    for d in (P.keys_dir, P.sets_dir, P.runs_dir):
        os.chmod(str(d), 0o700)
    for f in P.keys_dir.iterdir():
        os.chmod(str(f), 0o600)
    os.chmod(str(P.stage_dir), 0o750)
    os.chmod(str(P.drop_dir), 0o1733)
    lib = P.broker_lib / "scripts" / "eval_seal"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "x.py").write_text("x = 1\n")
    P.installed_tree_file.write_text(B.broker_tree_id(lib) + "\n")
    pin = P.broker_bin / "age"
    pin.write_text("#!/bin/sh\n")
    (P.keys_dir / "age.key").write_text("fake age identity\n")
    os.chmod(str(P.keys_dir / "age.key"), 0o600)
    fakepy = P.root / "fakebin" / "python3"
    fakepy.parent.mkdir(exist_ok=True)
    fakepy.write_text("#!/bin/sh\n")
    os.chmod(str(fakepy), 0o755)
    (P.broker_bin / "zurvan-broker").write_text("#!%s -I\nimport sys\n" % fakepy)
    P.pinned_file.write_text("%s  /Library/ZurvanEval/broker/bin/age\n" % hashlib.sha256(pin.read_bytes()).hexdigest())
    P.sudoers_file.parent.mkdir(parents=True, exist_ok=True)
    P.sudoers_file.write_text("x\n")
    os.chmod(str(P.sudoers_file), 0o440)
    P.pf_anchor.parent.mkdir(parents=True, exist_ok=True)
    P.pf_anchor.write_text("x\n")
    os.chmod(str(P.pf_anchor), 0o644)
    for p in P.corpus_dir.rglob("*"):
        os.chmod(str(p), 0o440 if p.is_file() else 0o550)
    os.chmod(str(P.corpus_dir), 0o550)
    for p in sorted(P.broker_dir.rglob("*"), reverse=True):
        os.chmod(str(p), 0o444 if p.is_file() else 0o555)
    os.chmod(str(P.broker_dir), 0o555)


def _t10_unlayout(E):
    for p in [E.paths.broker_dir, E.paths.corpus_dir, *E.paths.broker_dir.rglob("*"), *E.paths.corpus_dir.rglob("*")]:
        try:
            os.chmod(str(p), 0o755 if p.is_dir() else 0o644)
        except OSError:
            pass


def test_t10_seal_check_passes_on_a_compliant_layout_and_fails_each_violation(tmp_path):
    E = _t10_build(tmp_path)
    _t10_layout(E)
    try:
        rc, _t, doc = _t10_call(E, ["seal-check"])
        assert rc == 0 and doc["ok"] is True and doc["schema"] == "zurvan.z1b.sealcheck.v1"
        assert set(doc["checks"]) == {c for c in output.CHECK_IDS if c != "dev_cannot_read_sealed"}
        assert all(doc["checks"].values())

        def failing(check_id, only=True, **over):
            res = _t10_call(E, ["seal-check"], ctx=_t10_ctx(E, **over))
            assert res[0] == 2 and res[2]["ok"] is False and res[2]["checks"][check_id] is False
            if only:
                assert [k for k, v in res[2]["checks"].items() if not v] == [check_id]

        failing("candidate_cannot_read_sealed", probe=lambda: {"eval_home": "EACCES", "keys_dir": "READABLE",
                                                               "sets_dir": "EACCES", "runs_dir": "EACCES",
                                                               "incidents_dir": "EACCES",
                                                               "manifest_file": "EACCES"})
        failing("candidate_cannot_read_sealed", probe=lambda: {})
        failing("tm_excluded_keys", tmutil_isexcluded=lambda p: p != E.paths.keys_dir)
        failing("tm_excluded_private", tmutil_isexcluded=lambda p: p != E.paths.private_dir)  # R18
        failing("scan_agent_present", scan_agent_present=lambda: False)
        failing("broker_root_owned", only=False, root_uid=os.getuid() + 7)
    finally:
        _t10_unlayout(E)


@pytest.mark.parametrize("which", [
    "broker_not_writable", "keys_private", "sets_private", "runs_private", "corpus_read_only", "stage_private",
    "drop_dir_mode", "sudoers_valid", "pf_anchor_present", "pinned_binaries_match", "interpreter_root_owned",
    "interpreter_dir_writable", "broker_sha_matches", "ledger_valid", "manifest_valid",
])
def test_t10_seal_check_flags_each_file_level_violation(tmp_path, which):
    E = _t10_build(tmp_path)
    _t10_layout(E)
    P = E.paths
    try:
        if which == "broker_not_writable":
            os.chmod(str(P.broker_bin), 0o775)
        elif which == "keys_private":
            os.chmod(str(P.keys_dir), 0o750)
        elif which == "sets_private":
            os.chmod(str(P.sets_dir), 0o755)
        elif which == "runs_private":
            os.chmod(str(P.runs_dir), 0o770)
        elif which == "corpus_read_only":
            os.chmod(str(P.corpus_dir / "search.sqlite"), 0o640)
        elif which == "stage_private":
            os.chmod(str(P.stage_dir), 0o777)
        elif which == "drop_dir_mode":
            os.chmod(str(P.drop_dir), 0o777)
        elif which == "sudoers_valid":
            os.chmod(str(P.sudoers_file), 0o666)
        elif which == "pf_anchor_present":
            P.pf_anchor.unlink()
        elif which == "pinned_binaries_match":
            os.chmod(str(P.broker_bin), 0o755)
            os.chmod(str(P.broker_bin / "age"), 0o644)
            (P.broker_bin / "age").write_text("#!/bin/sh\necho swapped\n")
        elif which == "interpreter_dir_writable":
            os.chmod(str(P.root / "fakebin"), 0o777)
        elif which == "interpreter_root_owned":
            os.chmod(str(P.broker_bin), 0o755)
            os.chmod(str(P.broker_bin / "zurvan-broker"), 0o644)
            (P.broker_bin / "zurvan-broker").write_text("#!%s -I\n" % (P.root / "nowhere" / "python3"))
        elif which == "broker_sha_matches":
            os.chmod(str(P.broker_lib / "scripts" / "eval_seal"), 0o755)
            os.chmod(str(P.broker_lib / "scripts" / "eval_seal" / "x.py"), 0o644)
            (P.broker_lib / "scripts" / "eval_seal" / "x.py").write_text("x = 2\n")
        elif which == "ledger_valid":
            (P.runs_dir / "ledger.jsonl").write_text("garbage\n")
        elif which == "manifest_valid":
            sealed_store.manifest(P).write_bytes(sealed_store.manifest(P).read_bytes() + b" ")
        rc, _t, doc = _t10_call(E, ["seal-check"])
        check_id = "interpreter_root_owned" if which == "interpreter_dir_writable" else which
        assert rc == 2 and doc["ok"] is False and doc["checks"][check_id] is False
    finally:
        _t10_unlayout(E)


def test_t10_broker_tree_id_equals_the_git_tree_id_of_the_committed_sources(tmp_path):
    out = tmp_path / "export"
    out.mkdir()
    archive = subprocess.run([GIT, "-C", str(ROOT), "archive", "HEAD", "scripts/eval_seal"], env=_t10_genv(),
                             check=True, capture_output=True).stdout
    subprocess.run(["/usr/bin/tar", "-x", "-C", str(out)], input=archive, check=True)
    want = subprocess.run([GIT, "-C", str(ROOT), "rev-parse", "HEAD:scripts/eval_seal"], env=_t10_genv(),
                          check=True, capture_output=True).stdout.decode().strip()
    assert B.broker_tree_id(out / "scripts" / "eval_seal") == want


# ================================================================ sets, runtime digest, integration fixes

def test_t10_set_round_trip_and_every_malformed_set_is_manifest_invalid():
    spec = load_spec(SPEC)
    items = [_t10_item(i) for i in range(6)]
    raw = B.broker_dump_items(items)
    assert B.broker_parse_items(raw, spec) == sorted(items, key=lambda i: i["id"])
    assert B.broker_dump_items(B.broker_parse_items(raw, spec)) == raw
    bad_sets = [
        raw + raw.splitlines()[0] + b"\n",                                   # duplicate id
        b'{"id": 1}\n', b"not json\n", b'{"a":1,"a":2}\n', b"\xff\xfe\n", b"[]\n", b"null\n",
        raw.replace(b'"stratum":"path"', b'"stratum":"nope"'),
        raw.replace(b'"difficulty":"easy"', b'"difficulty":"easy","extra":1', 1),
        b'{"id":"' + b"0" * 32 + b'","query":NaN}\n',
    ]
    for data in bad_sets:
        with pytest.raises(SealRefusal) as ei:
            B.broker_parse_items(data, spec)
        assert ei.value.code == "MANIFEST_INVALID"


def test_t10_runtime_digest_changes_with_any_runtime_file_and_the_interpreter(tmp_path):
    paths = SealPaths.for_root(tmp_path)
    rt = paths.runtime_dir
    (rt / "bin").mkdir(parents=True)
    (rt / "venv" / "lib").mkdir(parents=True)
    (rt / "bin" / "python3").write_bytes(b"interp-1")
    (rt / "venv" / "lib" / "mod.py").write_text("a = 1\n")
    first = B.broker_runtime_sha256(paths)
    assert len(first) == 64 and first == B.broker_runtime_sha256(paths)
    (rt / "venv" / "lib" / "new.py").write_text("")
    second = B.broker_runtime_sha256(paths)
    assert second != first
    (rt / "bin" / "python3").write_bytes(b"interp-2")
    third = B.broker_runtime_sha256(paths)
    assert third not in (first, second)
    os.symlink("/usr/bin/python3", str(rt / "venv" / "link"))
    assert B.broker_runtime_sha256(paths) != third
    with pytest.raises(SealRefusal) as ei:
        B.broker_runtime_sha256(SealPaths.for_root(tmp_path / "none"))
    assert ei.value.code == "SEAL_NOT_CONFIGURED"


def test_t10_new_seal_path_properties_have_the_installer_locations(tmp_path):
    real = SealPaths.real()
    assert real.installed_tree_file == Path("/Library/ZurvanEval/broker/INSTALLED_TREE")
    assert real.scan_targets_file == Path("/Library/ZurvanEval/broker/SCAN_TARGETS.json")
    assert real.pinned_file == Path("/Library/ZurvanEval/broker/PINNED.sha256")
    assert real.empty_mcp_file == Path("/Library/ZurvanEval/broker/empty-mcp.json")
    fake = SealPaths.for_root(tmp_path)
    for name in ("installed_tree_file", "scan_targets_file", "pinned_file", "empty_mcp_file"):
        getattr(fake, name).relative_to(tmp_path)


def test_t10_sweep_roots_add_the_candidate_home_and_darwin_trees(tmp_path):
    paths = SealPaths.for_root(tmp_path / "root")
    base = tmp_path / "folders"
    mine = base / "zz" / "hash0123"
    mine.mkdir(parents=True)
    (base / "yy").mkdir()
    uid = os.getuid()
    assert sweep.sweep_darwin_roots(uid, str(base)) == [str(mine)]
    assert sweep.sweep_darwin_roots(uid + 1, str(base)) == []
    assert sweep.sweep_darwin_roots(uid, str(tmp_path / "missing")) == []
    roots = sweep.sweep_roots(paths, uid, str(base))
    assert roots[:4] == sweep.DEFAULT_ROOTS and str(paths.candidate_home) in roots and str(mine) in roots
    with pytest.raises(SealRefusal) as ei:
        sweep.sweep_roots(paths, 0, str(base))
    assert ei.value.code == "BAD_ARGS"


def test_t10_candidate_adapter_passes_source_path_through_unchanged_by_default():
    # ruling R6: the grader's path space is chunks.source_path exactly as indexed
    from scripts import z1b_candidate
    from scripts.eval_seal import protocol
    stdin = io.BytesIO(protocol.encode_query(1, "q"))
    stdout = io.BytesIO()
    chunk = {"source_path": "wiki/decisions/a.md", "heading": "H", "hybrid_score": 0.5}
    z1b_candidate.serve(stdin, stdout, lambda q, n: [chunk, dict(chunk, source_path="docs/guide.md")])
    got = protocol.decode_response(stdout.getvalue(), 1, 10)["results"]
    assert [r["page"] for r in got] == ["wiki/decisions/a.md", "docs/guide.md"]


def test_t10_ledger_burn_items_is_idempotent_irreversible_and_validated(tmp_path):
    led = Ledger(tmp_path / "runs")
    ids = ["a" * 32, "b" * 32]
    assert led.burn_items(ids + ids, "item_leak", "2026-10-02") == ids
    assert led.burn_items(ids, "canary_hit", "2026-10-03") == []
    assert led.burned_ids() == set(ids) and [e["kind"] for e in led.entries()] == ["burn", "burn"]
    for bad_ids, reason, date in ((["short"], "item_leak", "2026-10-02"), (["c" * 32], "nope", "2026-10-02"),
                                  (["c" * 32], "item_leak", "yesterday"), ("c" * 32, "item_leak", "2026-10-02")):
        with pytest.raises(SealRefusal) as ei:
            led.burn_items(bad_ids, reason, date)
        assert ei.value.code == "LEDGER_INVALID"
    assert led.burned_ids() == set(ids)


def test_t10_the_installed_wrapper_entry_name_exists():
    # the installed wrapper does `from scripts.eval_seal.broker import main`; ruling R11 names the function broker_main
    assert B.main is B.broker_main


def test_t10_a_declared_candidate_with_no_baseline_fails_and_is_never_compared_to_zero(tmp_path):
    # R25: no all-zero baseline. Without a baseline or a promoted run nothing is non-inferior or improved.
    E = _t10_build(tmp_path)
    commit, _p, name = _t10_declared(E)
    doc = _t10_run(E, commit, declare=_T10_DECL_PATH, name=name)[2]
    assert doc["promotion"]["verdict"] == "FAIL"
    assert doc["promotion"]["rules"] == {"non_inferiority_critical": False, "non_inferiority_overall": False,
                                         "improvement": False, "resource_budget": True, "resource_improved": False}


def test_t10_a_baseline_run_is_measured_but_never_judged_and_cannot_unlock_reserve(tmp_path):
    E = _t10_build(tmp_path)
    base = _t10_weak_baseline(E)
    _t10_refused(_t10_run(E, base, suite="reserve", final=True), "RESERVE_NOT_ELIGIBLE")


@pytest.mark.parametrize("role", ["promoted", "Candidate", None, 1])
def test_t10_a_declaration_with_an_unknown_role_is_declaration_invalid_and_charges_nothing(tmp_path, role):
    E = _t10_build(tmp_path)
    commit, _p, name = _t10_declared(E, decl={"role": role})
    _t10_refused(_t10_run(E, commit, declare=_T10_DECL_PATH, name=name), "DECLARATION_INVALID")
    assert "reserved" not in _t10_kinds(E)


def test_t10_a_candidate_role_run_never_becomes_the_baseline(tmp_path):
    # R25 / Rev 3: only a DECLARED baseline-role run or a PROMOTED run is a baseline. A completed
    # candidate-role run (verdict present) must not set the bar for the next candidate.
    E = _t10_build(tmp_path)
    assert _t10_run(E, E.commits[0])[0] == 0            # a candidate-role run completes (no baseline -> FAIL)
    commit, _p, name = _t10_declared(E)
    doc = _t10_run(E, commit, declare=_T10_DECL_PATH, name=name)[2]
    assert doc["promotion"]["verdict"] == "FAIL" and doc["promotion"]["rules"]["improvement"] is False


# ================================================================ Rev 3: mandatory declaration + key binding

def test_t10_rev3_holdout_without_a_declaration_is_refused_and_charges_nothing(tmp_path):
    # canonical §9 / P1 #8: the sealed HOLDOUT is only for a declared promotion candidate
    E = _t10_build(tmp_path)
    _t10_refused(_t10_run(E, declare=None), "DECLARATION_REQUIRED")
    assert "reserved" not in _t10_kinds(E) and "completed" not in _t10_kinds(E)


def test_t10_rev3_two_declarations_in_one_commit_do_not_share_a_cache_entry(tmp_path):
    # P1 #9: declaration_sha256 is in the run key, so declaration B is never served A's cached result
    E = _t10_build(tmp_path)
    base = _t10_weak_baseline(E)
    commit = _t10_commit(E, "one commit, two declarations\n", extra={
        _T10_DECL_PATH: json.dumps(_t10_decl_body(E)),
        _T10_DECL_PATH2: json.dumps(_t10_decl_body(E, primary_metric="recall_at_1")),
    })
    na = _t10_drop(E, commit)
    a = _t10_run(E, commit, declare=_T10_DECL_PATH, name=na)[2]
    assert a["cached"] is False
    nb = _t10_drop(E, commit)
    b = _t10_run(E, commit, declare=_T10_DECL_PATH2, name=nb)[2]
    assert b["cached"] is False  # different declaration digest => different key => not A's cache
    completed = [e for e in _t10_ledger(E).entries() if e["kind"] == "completed"
                 and e["result"]["suite"] == "holdout"]
    assert len(completed) == 3  # baseline + A + B, all distinct keys
    # re-running A with a fresh bundle IS cached (same declaration digest)
    na2 = _t10_drop(E, commit)
    a2 = _t10_run(E, commit, declare=_T10_DECL_PATH, name=na2)[2]
    assert a2["cached"] is True
