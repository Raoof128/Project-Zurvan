"""Z1b Task 9 tests: candidate protocol, sandbox profile, runner, sweep, default adapter (contract s3.8)."""
import errno
import io
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from scripts.eval_seal import candidate_run, protocol, sweep, sysenv
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.paths import SealPaths

_T9_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_T9_PROFILE = Path(candidate_run.__file__).parent / "sandbox" / "candidate.sb"
_t9_needs_sandbox = pytest.mark.skipif(
    not os.path.exists(_T9_SANDBOX_EXEC) or sys.platform != "darwin",
    reason="SKIPPED LOUDLY: sandbox-exec is absent, the sandbox profile is NOT exercised on this machine")
_t9_needs_nonroot = pytest.mark.skipif(os.geteuid() == 0, reason="chmod-based denial does not bind root")

_T9_RUN = "00112233445566ff"


# ================================================================ protocol

def _t9_resp(qid=1, results=None, abstain=False, **extra):
    d = {"qid": qid, "results": [] if results is None else results, "abstain": abstain}
    d.update(extra)
    return (json.dumps(d) + "\n").encode()


def _t9_hit(page="decisions/a.md", heading="H", score=0.5):
    return {"page": page, "heading": heading, "score": score}


def test_t9_encode_query_contains_only_qid_and_q():
    line = protocol.encode_query(7, "what is zurvan\nline2 \u00e9 \x00")
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    obj = json.loads(line)
    assert set(obj) == {"qid", "q"}
    assert obj == {"qid": 7, "q": "what is zurvan\nline2 \u00e9 \x00"}
    assert line.index(b'"qid"') < line.index(b'"q"')


@pytest.mark.parametrize("qid,q", [(True, "x"), (-1, "x"), ("1", "x"), (1.0, "x"), (None, "x"), (1, None), (1, b"x")])
def test_t9_encode_query_rejects_bad_types(qid, q):
    with pytest.raises(ValueError):
        protocol.encode_query(qid, q)


def test_t9_encode_query_rejects_oversize():
    with pytest.raises(ValueError):
        protocol.encode_query(1, "a" * protocol.MAX_LINE)


def test_t9_decode_accepts_a_wellformed_response_and_normalises():
    out = protocol.decode_response(_t9_resp(3, [_t9_hit(), _t9_hit("b/c.md", None, 1e-3)]), 3, 10)
    assert out == {"qid": 3, "results": [_t9_hit(), _t9_hit("b/c.md", None, 1e-3)], "abstain": False}


def test_t9_decode_accepts_empty_results_and_abstain_and_no_trailing_newline():
    out = protocol.decode_response(_t9_resp(2, [], True).rstrip(b"\n"), 2, 10)
    assert out == {"qid": 2, "results": [], "abstain": True}


def _t9_bad_lines():
    big = "a" * protocol.MAX_LINE
    good = {"qid": 1, "results": [_t9_hit()], "abstain": False}

    def j(**over):
        d = dict(good)
        d.update(over)
        return (json.dumps(d) + "\n").encode()

    return {
        "wrong-qid": _t9_resp(2),
        "qid-string": j(qid="1"),
        "qid-bool": b'{"qid":true,"results":[],"abstain":false}\n',
        "qid-float": b'{"qid":1.0,"results":[],"abstain":false}\n',
        "extra-top-key": j(extra=1),
        "missing-abstain": b'{"qid":1,"results":[]}\n',
        "missing-results": b'{"qid":1,"abstain":false}\n',
        "abstain-not-bool": j(abstain=0),
        "results-not-list": j(results={}),
        "too-many-results": j(results=[_t9_hit()] * 11),
        "extra-result-key": j(results=[dict(_t9_hit(), text="leak")]),
        "missing-result-key": j(results=[{"page": "a.md", "score": 0.1}]),
        "dotdot-page": j(results=[_t9_hit(page="../etc/passwd")]),
        "dotdot-mid-page": j(results=[_t9_hit(page="a/../../b.md")]),
        "absolute-page": j(results=[_t9_hit(page="/etc/passwd")]),
        "nul-page": j(results=[_t9_hit(page="a\x00b.md")]),
        "newline-page": j(results=[_t9_hit(page="a\nb.md")]),
        "empty-page": j(results=[_t9_hit(page="")]),
        "long-page": j(results=[_t9_hit(page="a" * 513)]),
        "page-not-str": j(results=[_t9_hit(page=5)]),
        "heading-not-str": j(results=[_t9_hit(heading=5)]),
        "heading-nul": j(results=[_t9_hit(heading="a\x00b")]),
        "heading-huge": j(results=[_t9_hit(heading="h" * 5000)]),
        "score-string": j(results=[_t9_hit(score="0.5")]),
        "score-int": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":1}],"abstain":false}\n',
        "score-bool": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":true}],"abstain":false}\n',
        "score-null": j(results=[_t9_hit(score=None)]),
        "score-nan": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":NaN}],"abstain":false}\n',
        "score-inf": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":Infinity}],"abstain":false}\n',
        "score-neg-inf": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":-Infinity}],"abstain":false}\n',
        "score-overflow": b'{"qid":1,"results":[{"page":"a.md","heading":null,"score":1e999}],"abstain":false}\n',
        "trailing-garbage": _t9_resp(1) + b"junk",
        "trailing-second-object": _t9_resp(1).rstrip(b"\n") + b' {"qid":1}\n',
        "two-lines": _t9_resp(1) + _t9_resp(1),
        "cr-in-middle": _t9_resp(1).rstrip(b"\n") + b"\r\n\n",
        "dup-top-key": b'{"qid":1,"qid":1,"results":[],"abstain":false}\n',
        "dup-abstain-key": b'{"qid":1,"results":[],"abstain":false,"abstain":true}\n',
        "dup-result-key": b'{"qid":1,"results":[{"page":"a.md","page":"b.md","heading":null,"score":0.1}],"abstain":false}\n',
        "oversize-line": b'{"qid":1,"results":[],"abstain":false,"pad":"' + big.encode() + b'"}\n',
        "oversize-valid-looking": _t9_resp(1, [_t9_hit(heading=None, score=0.1)]).rstrip(b"\n") + b" " * protocol.MAX_LINE + b"\n",
        "not-json": b"hello\n",
        "empty": b"",
        "only-newline": b"\n",
        "json-array": b"[]\n",
        "json-null": b"null\n",
        "invalid-utf8": b'{"qid":1,"results":[{"page":"\xff.md","heading":null,"score":0.1}],"abstain":false}\n',
        "bom": b"\xef\xbb\xbf" + _t9_resp(1),
        "deep-nesting": b"[" * 50000 + b"]" * 50000 + b"\n",
        "not-bytes": "str-not-bytes",
    }


@pytest.mark.parametrize("label", sorted(_t9_bad_lines()))
def test_t9_decode_rejects_every_attack_with_valueerror(label):
    with pytest.raises(ValueError):
        protocol.decode_response(_t9_bad_lines()[label], 1, 10)


def test_t9_decode_max_results_is_a_hard_bound():
    ten = [_t9_hit(page="p%d.md" % i) for i in range(10)]
    assert len(protocol.decode_response(_t9_resp(1, ten), 1, 10)["results"]) == 10
    with pytest.raises(ValueError):
        protocol.decode_response(_t9_resp(1, ten), 1, 9)
    with pytest.raises(ValueError):
        protocol.decode_response(_t9_resp(1, ten[:1]), 1, 0)


def test_t9_decode_line_exactly_max_is_accepted_and_one_more_is_not():
    base = _t9_resp(1, [_t9_hit(heading=None, score=0.1)]).rstrip(b"\n")
    pad = protocol.MAX_LINE - len(base) - 1
    ok = base[:-1] + b',"x":1}'  # sanity: unknown key is refused regardless of size
    with pytest.raises(ValueError):
        protocol.decode_response(ok + b"\n", 1, 10)
    line = base + b" " * pad + b"\n"
    assert len(line) == protocol.MAX_LINE
    protocol.decode_response(line, 1, 10)
    with pytest.raises(ValueError):
        protocol.decode_response(line + b" ", 1, 10)


@pytest.mark.parametrize("bad", [True, -1, "10", None, 1.5])
def test_t9_decode_bad_max_results_argument(bad):
    with pytest.raises(ValueError):
        protocol.decode_response(_t9_resp(1), 1, bad)


def _t9_is_stdlib(module_name):
    """True when `module_name` ships with the RUNNING interpreter's standard library (works on 3.9, which has no
    sys.stdlib_module_names): built in, frozen, or located under sysconfig's stdlib dir and not in site-packages."""
    import importlib.util
    import os
    import sys
    import sysconfig
    if module_name in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    origin = os.path.realpath(spec.origin)
    stdlib = os.path.realpath(sysconfig.get_paths()["stdlib"])
    return origin.startswith(stdlib + os.sep) and "site-packages" not in origin


def test_t9_protocol_is_stdlib_only():
    import ast
    tree = ast.parse(Path(protocol.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not node.level:
            assert _t9_is_stdlib((node.module or "").split(".")[0]), node.module
        if isinstance(node, ast.Import):
            assert all(_t9_is_stdlib(a.name.split(".")[0]) for a in node.names)


# ================================================================ sandbox profile (REAL sandbox-exec)

@pytest.fixture
def t9sb(tmp_path):
    real = Path(os.path.realpath(tmp_path))
    d = {"scratch": real / "scratch", "sealed": real / "sealed", "devhome": real / "devhome", "root": real}
    for k in ("scratch", "sealed", "devhome"):
        d[k].mkdir()
    (d["sealed"] / "age.key").write_bytes(b"SEALED-KEY-BYTES")
    (d["devhome"] / "notes.txt").write_bytes(b"DEV-HOME-BYTES")
    created = []
    d["created"] = created
    yield d
    for p in created:
        try:
            os.unlink(p)
        except OSError:
            pass


def _t9_unique_tmp(t9sb, tag):
    p = "/private/tmp/zurvan-z1b-t9-%s-%s" % (tag, uuid.uuid4().hex)
    t9sb["created"].append(p)
    return p


def _t9_sb_run(t9sb, code, timeout=60):
    """Run `code` in python under the real profile; returns the decoded JSON the code printed."""
    cmd = [_T9_SANDBOX_EXEC, "-f", str(_T9_PROFILE), "-D", "SCRATCH=%s" % t9sb["scratch"],
           "-D", "SEALED=%s" % t9sb["sealed"], "-D", "DEVHOME=%s" % t9sb["devhome"],
           sys.executable, "-B", "-E", "-s", "-c", code]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(t9sb["scratch"]), "TMPDIR": str(t9sb["scratch"])}
    res = subprocess.run(cmd, cwd=str(t9sb["scratch"]), env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    assert res.returncode == 0, res.stderr.decode()[-800:]
    return json.loads(res.stdout.decode().strip().splitlines()[-1])


_T9_PROBE_PRELUDE = '''
import json, os, subprocess, socket, sys
def _t9_attempt(fn):
    try:
        fn()
        return "OK"
    except OSError as e:
        return "ERR%s" % (e.errno,)
    except Exception as e:
        return "EXC:" + type(e).__name__
'''


@_t9_needs_sandbox
def test_t9_sandbox_write_inside_scratch_is_allowed(t9sb):
    out = _t9_sb_run(t9sb, _T9_PROBE_PRELUDE + '''
p = os.path.join(%r, "ok.txt")
r = {}
def _t9_w_inside():
    open(p, "w").write("hi")
r["write"] = _t9_attempt(_t9_w_inside)
r["read_back"] = open(p).read() if os.path.exists(p) else None
os.makedirs(os.path.join(%r, "sub", "deep"))
r["mkdir"] = "OK"
print(json.dumps(r))
''' % (str(t9sb["scratch"]), str(t9sb["scratch"])))
    assert out == {"write": "OK", "read_back": "hi", "mkdir": "OK"}


@_t9_needs_sandbox
def test_t9_sandbox_write_outside_scratch_is_denied(t9sb):
    target = _t9_unique_tmp(t9sb, "direct")
    sibling = str(t9sb["root"] / "sibling.txt")
    devfile = str(t9sb["devhome"] / "planted.txt")
    out = _t9_sb_run(t9sb, _T9_PROBE_PRELUDE + '''
r = {}
def _t9_w_outside(p):
    return lambda: open(p, "w").write("x")
r["tmp"] = _t9_attempt(_t9_w_outside(%r))
r["sibling"] = _t9_attempt(_t9_w_outside(%r))
r["devhome"] = _t9_attempt(_t9_w_outside(%r))
r["rename_out"] = _t9_attempt(lambda: os.rename(os.path.join(%r, "ok.txt"), %r))
print(json.dumps(r))
''' % (target, sibling, devfile, str(t9sb["scratch"]), target))
    assert out["tmp"] == "ERR%d" % errno.EPERM
    assert out["sibling"] == "ERR%d" % errno.EPERM
    assert out["devhome"] == "ERR%d" % errno.EPERM
    assert out["rename_out"].startswith("ERR")
    assert not os.path.exists(target)
    assert not os.path.exists(sibling)
    assert not os.path.exists(devfile)


@_t9_needs_sandbox
def test_t9_sandbox_child_processes_inherit_the_denial(t9sb):
    sh_target = _t9_unique_tmp(t9sb, "sh")
    py_target = _t9_unique_tmp(t9sb, "py")
    out = _t9_sb_run(t9sb, _T9_PROBE_PRELUDE + '''
r = {}
r["sh_rc"] = subprocess.run(["/bin/sh", "-c", "echo x > %s"], stderr=subprocess.DEVNULL).returncode
r["py_rc"] = subprocess.run([sys.executable, "-B", "-c", "open(%r, 'w').write('x')"],
                            stderr=subprocess.DEVNULL).returncode
r["touch_rc"] = subprocess.run(["/usr/bin/touch", %r], stderr=subprocess.DEVNULL).returncode
r["sh_in_scratch_rc"] = subprocess.run(["/bin/sh", "-c", "echo x > %s"], stderr=subprocess.DEVNULL).returncode
print(json.dumps(r))
''' % (sh_target, py_target, py_target, str(t9sb["scratch"] / "child_ok")))
    assert out["sh_rc"] != 0 and out["py_rc"] != 0 and out["touch_rc"] != 0
    assert out["sh_in_scratch_rc"] == 0
    assert not os.path.exists(sh_target) and not os.path.exists(py_target)


@_t9_needs_sandbox
def test_t9_sandbox_network_is_denied_for_tcp_udp_dns_and_loopback(t9sb):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        out = _t9_sb_run(t9sb, _T9_PROBE_PRELUDE + '''
r = {}
r["tcp"] = _t9_attempt(lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
def _t9_udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b"x", ("1.1.1.1", 53))
r["udp"] = _t9_attempt(_t9_udp)
r["dns"] = _t9_attempt(lambda: socket.getaddrinfo("example.com", 443))
r["loopback"] = _t9_attempt(lambda: socket.create_connection(("127.0.0.1", %d), timeout=3))
r["curl_rc"] = subprocess.run(["/usr/bin/curl", "-s", "-m", "5", "http://1.1.1.1/"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
print(json.dumps(r))
''' % port)
    finally:
        srv.close()
    assert out["tcp"] == "ERR%d" % errno.EPERM  # EPERM = the sandbox said no, not "timed out"/"unreachable"
    assert out["udp"] == "ERR%d" % errno.EPERM
    assert out["dns"] != "OK"
    assert out["loopback"] == "ERR%d" % errno.EPERM
    assert out["curl_rc"] != 0


@_t9_needs_sandbox
def test_t9_sandbox_denies_reading_sealed_and_dev_home_but_control_reads_work(t9sb):
    out = _t9_sb_run(t9sb, _T9_PROBE_PRELUDE + '''
r = {}
r["sealed_read"] = _t9_attempt(lambda: open(%r, "rb").read())
r["sealed_list"] = _t9_attempt(lambda: os.listdir(%r))
r["devhome_param_read"] = _t9_attempt(lambda: open(%r, "rb").read())
r["real_sealed_list"] = _t9_attempt(lambda: os.listdir("/Users/zurvaneval"))
r["control_usr"] = _t9_attempt(lambda: open("/usr/bin/true", "rb").read(4))
r["control_stdlib"] = _t9_attempt(lambda: __import__("json").dumps({}))
r["child_cat_sealed_rc"] = subprocess.run(["/bin/cat", %r], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL).returncode
print(json.dumps(r))
''' % (str(t9sb["sealed"] / "age.key"), str(t9sb["sealed"]), str(t9sb["devhome"] / "notes.txt"),
       str(t9sb["sealed"] / "age.key")))
    assert out["sealed_read"] == "ERR%d" % errno.EPERM
    assert out["sealed_list"] == "ERR%d" % errno.EPERM
    assert out["devhome_param_read"] == "ERR%d" % errno.EPERM
    assert out["real_sealed_list"] != "OK"
    assert out["control_usr"] == "OK" and out["control_stdlib"] == "OK"
    assert out["child_cat_sealed_rc"] != 0
    # the same reads work WITHOUT the sandbox, so the denials above are the profile's doing
    assert (t9sb["sealed"] / "age.key").read_bytes() == b"SEALED-KEY-BYTES"


def test_t9_profile_text_has_the_required_rules():
    text = _T9_PROFILE.read_text()
    assert "(deny network*)" in text
    assert "(deny file-write*)" in text
    assert '(param "SCRATCH")' in text and '(param "SEALED")' in text
    assert '(subpath "/Users/zurvaneval")' in text and '(param "DEVHOME")' in text
    assert '"/Users/dev"' not in text  # R12: the dev home arrives only as a parameter
    deny_write = text.index("(deny file-write*)")
    allow_write = text.index("(allow file-write*")
    assert deny_write < allow_write  # later rules win in SBPL: the allow must come after the deny
    assert text.count("(allow file-write*") == 1


# ================================================================ runner: wipe

def _t9_paths(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    for d in (p.scratch_dir, p.candidate_home, p.eval_home, p.stage_dir, p.runtime_dir, p.corpus_dir):
        d.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.parametrize("bad", ["", "../x", "..", ".", "/abs", "00112233445566F0", "00112233445566f", "00112233445566ff0",
                                 "00112233445566ff\n", "0011223344556\x006f", "00112233445566fg", "a/b", None, 5])
def test_t9_wipe_refuses_bad_run_ids_and_deletes_nothing(tmp_path, bad):
    p = _t9_paths(tmp_path)
    decoy = p.scratch_dir.parent / "x"
    decoy.mkdir()
    (decoy / "keep").write_text("keep")
    (p.scratch_dir / _T9_RUN).mkdir()
    with pytest.raises(SealRefusal) as ei:
        candidate_run.wipe(p, bad, killer=lambda: None)
    assert ei.value.code == "BAD_ARGS"
    assert (decoy / "keep").read_text() == "keep"
    assert (p.scratch_dir / _T9_RUN).is_dir()


def test_t9_wipe_removes_the_run_tree_and_only_that(tmp_path):
    p = _t9_paths(tmp_path)
    mine = p.scratch_dir / _T9_RUN
    other = p.scratch_dir / "ffeeddccbbaa0099"
    for d in (mine, other):
        (d / "repo" / "sub").mkdir(parents=True)
        (d / "repo" / "sub" / "f").write_text("x")
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)
    assert not mine.exists()
    assert (other / "repo" / "sub" / "f").exists()


def test_t9_wipe_is_idempotent_when_nothing_exists(tmp_path):
    p = _t9_paths(tmp_path)
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)


def test_t9_wipe_does_not_follow_a_symlink_planted_at_the_run_dir(tmp_path):
    p = _t9_paths(tmp_path)
    victim = tmp_path / "victim"
    (victim / "inner").mkdir(parents=True)
    (victim / "inner" / "precious").write_text("precious")
    link = p.scratch_dir / _T9_RUN
    os.symlink(str(victim), str(link))
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)
    assert not os.path.lexists(link)
    assert (victim / "inner" / "precious").read_text() == "precious"


def test_t9_wipe_does_not_follow_symlinks_inside_the_run_tree(tmp_path):
    p = _t9_paths(tmp_path)
    victim = tmp_path / "victim2"
    victim.mkdir()
    os.chmod(victim, 0o755)
    (victim / "precious").write_text("precious")
    run = p.scratch_dir / _T9_RUN
    (run / "repo").mkdir(parents=True)
    os.symlink(str(victim), str(run / "repo" / "dirlink"))
    os.symlink(str(victim / "precious"), str(run / "filelink"))
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)
    assert not run.exists()
    assert (victim / "precious").read_text() == "precious"
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o755  # wipe never chmods through a link


@_t9_needs_nonroot
def test_t9_wipe_removes_unreadable_and_readonly_subtrees(tmp_path):
    p = _t9_paths(tmp_path)
    run = p.scratch_dir / _T9_RUN
    locked = run / "locked" / "deeper"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    os.chmod(locked, 0o000)
    os.chmod(run / "locked", 0o500)
    candidate_run.wipe(p, _T9_RUN, killer=lambda: None)
    assert not run.exists()


def test_t9_wipe_invokes_the_kill_seam_before_removing(tmp_path):
    p = _t9_paths(tmp_path)
    run = p.scratch_dir / _T9_RUN
    run.mkdir()
    seen = []
    candidate_run.wipe(p, _T9_RUN, killer=lambda: seen.append(run.exists()))
    assert seen == [True]
    assert not run.exists()


def test_t9_default_killer_never_signals_unless_running_as_the_candidate_user(monkeypatch):
    calls = []
    monkeypatch.setattr(candidate_run.os, "kill", lambda pid, sig: calls.append((pid, sig)))  # never a real kill(-1)
    assert candidate_run._euid_user() != "zurvancandidate"
    assert candidate_run._default_killer() is False
    assert calls == []


def test_t9_default_killer_signals_only_for_the_candidate_user(monkeypatch):
    calls = []
    monkeypatch.setattr(candidate_run.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(candidate_run, "_euid_user", lambda: "zurvancandidate")
    assert candidate_run._default_killer() is True
    assert calls == [(-1, signal.SIGKILL)]


# ================================================================ runner: probe

@_t9_needs_nonroot
def test_t9_probe_reports_eacces_readable_enoent(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    p.eval_home.mkdir(parents=True)
    os.chmod(p.eval_home, 0o755)
    p.keys_dir.mkdir()
    os.chmod(p.keys_dir, 0o000)
    p.sets_dir.mkdir()
    os.chmod(p.sets_dir, 0o755)
    p.incidents_dir.mkdir()
    os.chmod(p.incidents_dir, 0o111)  # traversable but not listable: children are still reachable by name
    p.manifest_file.write_text("{}")
    try:
        got = candidate_run.probe(p)
    finally:
        os.chmod(p.keys_dir, 0o755)
        os.chmod(p.incidents_dir, 0o755)
    assert got == {
        "eval_home": "READABLE", "keys_dir": "EACCES", "sets_dir": "READABLE",
        "runs_dir": "ENOENT", "incidents_dir": "READABLE", "manifest_file": "READABLE",
    }


@_t9_needs_nonroot
def test_t9_probe_denied_parent_hides_every_child(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    p.eval_home.mkdir(parents=True)
    for d in (p.keys_dir, p.sets_dir, p.runs_dir, p.incidents_dir):
        d.mkdir()
    p.manifest_file.write_text("{}")
    os.chmod(p.eval_home, 0o000)
    try:
        got = candidate_run.probe(p)
    finally:
        os.chmod(p.eval_home, 0o755)
    assert set(got.values()) == {"EACCES"}
    assert set(got) == {"eval_home", "keys_dir", "sets_dir", "runs_dir", "incidents_dir", "manifest_file"}


def test_t9_probe_unknown_open_failure_counts_as_exposure_not_as_denial(tmp_path, monkeypatch):
    p = SealPaths.for_root(tmp_path / "root")
    p.eval_home.mkdir(parents=True)
    real_open = os.open

    def odd(path, flags, *a, **kw):
        if str(path) == str(p.keys_dir):
            raise OSError(errno.ELOOP, "loop")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(candidate_run.os, "open", odd)
    assert candidate_run.probe(p)["keys_dir"] == "READABLE"


def test_t9_probe_missing_everything_is_enoent(tmp_path):
    p = SealPaths.for_root(tmp_path / "nothing")
    assert set(candidate_run.probe(p).values()) == {"ENOENT"}


class _T9Hang(BaseException):
    """BaseException: a blanket `except Exception` in the code under test cannot swallow a hang."""


def test_t9_probe_does_not_hang_on_a_fifo(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    p.eval_home.mkdir(parents=True)
    os.mkfifo(str(p.manifest_file))

    def _boom(signum, frame):
        raise _T9Hang("probe blocked on a FIFO")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(10)
    try:
        got = candidate_run.probe(p)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert got["manifest_file"] == "READABLE"


# ================================================================ runner: run()

def _t9_make_repo(tmp_path, script_body, name="cand", extra_files=None):
    env = sysenv.clean_env(tmp_path)
    repo = tmp_path / name
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "z1b_candidate.py").write_text(script_body)
    for rel, text in (extra_files or {}).items():
        (repo / rel).write_text(text)
    git = "/usr/bin/git"
    base = [git, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(base + ["init", "-q"], cwd=repo, env=env, check=True)
    subprocess.run(base + ["add", "."], cwd=repo, env=env, check=True)
    subprocess.run(base + ["commit", "-q", "-m", "c"], cwd=repo, env=env, check=True)
    bundle = tmp_path / (name + ".bundle")
    subprocess.run(base + ["bundle", "create", str(bundle), "HEAD"], cwd=repo, env=env, check=True,
                   stderr=subprocess.DEVNULL)
    return bundle


def _t9_stage_bundle(p, bundle, run_id=_T9_RUN):
    shutil.copy(str(bundle), str(p.stage_dir / (run_id + ".bundle")))


class _T9Launch:
    def __init__(self, rc=0, hook=None):
        self.rc, self.hook, self.calls = rc, hook, []

    def __call__(self, cmd, env, cwd, timeout=None):
        self.calls.append({"cmd": list(cmd), "env": dict(env), "cwd": cwd, "timeout": timeout})
        if self.hook:
            self.hook(cmd, env, cwd)
        return self.rc


def test_t9_run_clones_then_launches_with_the_pinned_command_and_env(tmp_path):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "print('hi')\n"))
    launch = _T9Launch(rc=0)
    assert candidate_run.run(_T9_RUN, p, launcher=launch, python="/opt/rt/bin/python3") == 0
    (call,) = launch.calls
    run_dir = Path(os.path.realpath(p.scratch_dir)) / _T9_RUN
    assert call["cwd"] == str(run_dir)
    assert (run_dir / "repo" / "scripts" / "z1b_candidate.py").read_text() == "print('hi')\n"
    cmd = call["cmd"]
    assert cmd[:3] == [_T9_SANDBOX_EXEC, "-f", str(_T9_PROFILE)]
    assert "SCRATCH=%s" % run_dir == cmd[cmd.index("-D") + 1]
    assert ["-D", "SEALED=%s" % os.path.realpath(p.eval_home)] == cmd[cmd.index("-D") + 2:cmd.index("-D") + 4] or \
        "SEALED=%s" % os.path.realpath(p.eval_home) in cmd
    assert cmd[-5:] == ["/opt/rt/bin/python3", "-B", "-E", "-s", str(run_dir / "repo" / "scripts" / "z1b_candidate.py")]
    env = call["env"]
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["HF_HOME"] == str(p.runtime_dir / "hf")
    assert env["ZURVAN_SEARCH_DB"] == str(p.corpus_dir / "search.sqlite")
    assert env["ZURVAN_CORPUS"] == str(p.corpus_dir)
    assert env["ZURVAN_ROOT"] == str(run_dir / "repo")
    assert env["TMPDIR"] == str(run_dir / "tmp")
    assert env["HOME"] == str(p.candidate_home)
    assert (run_dir / "tmp").is_dir()
    assert set(env) == set(sysenv.clean_env(p.candidate_home)) | {
        "HF_HUB_OFFLINE", "HF_HOME", "ZURVAN_SEARCH_DB", "ZURVAN_CORPUS", "ZURVAN_ROOT", "TMPDIR"}


def test_t9_run_without_sandbox_has_no_sandbox_exec_prefix(tmp_path):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "pass\n"))
    launch = _T9Launch()
    candidate_run.run(_T9_RUN, p, sandbox=False, launcher=launch, python="/opt/rt/bin/python3")
    assert launch.calls[0]["cmd"][0] == "/opt/rt/bin/python3"
    assert _T9_SANDBOX_EXEC not in launch.calls[0]["cmd"]


def test_t9_run_returns_the_candidate_exit_code(tmp_path):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "pass\n"))
    assert candidate_run.run(_T9_RUN, p, launcher=_T9Launch(rc=7), python="/x/py") == 7


@pytest.mark.parametrize("bad", ["", "../x", "00112233445566F0", "xyz", None])
def test_t9_run_refuses_bad_run_id_before_touching_disk(tmp_path, bad):
    p = _t9_paths(tmp_path)
    launch = _T9Launch()
    with pytest.raises(SealRefusal) as ei:
        candidate_run.run(bad, p, launcher=launch, python="/x/py")
    assert ei.value.code == "BAD_ARGS"
    assert launch.calls == []
    assert list(p.scratch_dir.iterdir()) == []


def test_t9_run_missing_staged_bundle_is_bundle_invalid(tmp_path):
    p = _t9_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        candidate_run.run(_T9_RUN, p, launcher=_T9Launch(), python="/x/py")
    assert ei.value.code == "BUNDLE_INVALID"


def test_t9_run_refuses_a_stale_run_directory(tmp_path):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "pass\n"))
    (p.scratch_dir / _T9_RUN).mkdir()
    launch = _T9Launch()
    with pytest.raises(SealRefusal) as ei:
        candidate_run.run(_T9_RUN, p, launcher=launch, python="/x/py")
    assert ei.value.code == "SANDBOX_VIOLATION"
    assert launch.calls == []


def test_t9_run_bundle_without_the_candidate_entry_is_candidate_failed(tmp_path):
    p = _t9_paths(tmp_path)
    env = sysenv.clean_env(tmp_path)
    repo = tmp_path / "noentry"
    repo.mkdir()
    (repo / "x.txt").write_text("x")
    base = ["/usr/bin/git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(base + ["init", "-q"], cwd=repo, env=env, check=True)
    subprocess.run(base + ["add", "."], cwd=repo, env=env, check=True)
    subprocess.run(base + ["commit", "-q", "-m", "c"], cwd=repo, env=env, check=True)
    subprocess.run(base + ["bundle", "create", str(tmp_path / "n.bundle"), "HEAD"], cwd=repo, env=env, check=True,
                   stderr=subprocess.DEVNULL)
    _t9_stage_bundle(p, tmp_path / "n.bundle")
    launch = _T9Launch()
    with pytest.raises(SealRefusal) as ei:
        candidate_run.run(_T9_RUN, p, launcher=launch, python="/x/py")
    assert ei.value.code == "CANDIDATE_FAILED"
    assert launch.calls == []


def test_t9_run_clone_ignores_hostile_ambient_git_configuration(tmp_path, monkeypatch):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "pass\n"))
    marker = tmp_path / "hook-ran"
    tpl = tmp_path / "tpl"
    (tpl / "hooks").mkdir(parents=True)
    for hook in ("post-checkout", "post-merge", "pre-commit"):
        h = tpl / "hooks" / hook
        h.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
        h.chmod(0o755)
    fsm = tmp_path / "fsm.sh"
    fsm.write_text("#!/bin/sh\ntouch '%s'\nexit 0\n" % marker)
    fsm.chmod(0o755)
    cfg = tmp_path / "evil.gitconfig"
    cfg.write_text("[init]\n\ttemplateDir = %s\n[core]\n\tfsmonitor = %s\n" % (tpl, fsm))
    home = tmp_path / "evilhome"
    home.mkdir()
    (home / ".gitconfig").write_text(cfg.read_text())
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(cfg))
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tpl))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    candidate_run.run(_T9_RUN, p, launcher=_T9Launch(), python="/x/py")
    assert not marker.exists()
    assert not (Path(os.path.realpath(p.scratch_dir)) / _T9_RUN / "repo" / ".git" / "hooks" / "post-checkout").exists()


def test_t9_run_clone_does_not_run_config_defined_filters(tmp_path, monkeypatch):
    p = _t9_paths(tmp_path)
    bundle = _t9_make_repo(tmp_path, "pass\n", extra_files={".gitattributes": "* filter=evil\n", "a.txt": "a\n"})
    _t9_stage_bundle(p, bundle)
    marker = tmp_path / "smudge-ran"
    cfg = tmp_path / "filter.gitconfig"
    cfg.write_text("[filter \"evil\"]\n\tsmudge = touch '%s' && cat\n\tclean = cat\n\trequired = false\n" % marker)
    home = tmp_path / "fhome"
    home.mkdir()
    (home / ".gitconfig").write_text(cfg.read_text())
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("HOME", str(home))
    candidate_run.run(_T9_RUN, p, launcher=_T9Launch(), python="/x/py")
    assert not marker.exists()


def test_t9_run_clone_ignores_hostile_exec_path_and_path_helpers(tmp_path, monkeypatch):
    p = _t9_paths(tmp_path)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, "pass\n"))
    marker = tmp_path / "helper-ran"
    evil = tmp_path / "evilbin"
    evil.mkdir()
    for helper in ("git-index-pack", "git-unpack-objects", "git-bundle", "git-fetch-pack", "git-rev-list",
                   "git-remote-bundle", "git-checkout", "git-read-tree", "git-update-ref", "git-symbolic-ref",
                   "git-config", "git-init", "git-remote", "git-fetch", "git-pack-objects"):
        f = evil / helper
        f.write_text("#!/bin/sh\ntouch '%s'\nexit 1\n" % marker)
        f.chmod(0o755)
    monkeypatch.setenv("GIT_EXEC_PATH", str(evil))
    monkeypatch.setenv("PATH", str(evil) + os.pathsep + os.environ["PATH"])
    candidate_run.run(_T9_RUN, p, launcher=_T9Launch(), python="/x/py")
    assert not marker.exists()


def test_t9_run_default_launcher_inherits_stdio_and_silences_stderr(tmp_path, capfd):
    p = _t9_paths(tmp_path)
    body = "import sys\nprint('PROTOCOL-OUT')\nsys.stderr.write('LEAKY-ERR')\nsys.stdout.flush()\n"
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, body))
    rc = candidate_run.run(_T9_RUN, p, sandbox=False, python=sys.executable)
    cap = capfd.readouterr()
    assert rc == 0
    assert "PROTOCOL-OUT" in cap.out
    assert "LEAKY-ERR" not in cap.out and "LEAKY-ERR" not in cap.err


def test_t9_run_default_launcher_kills_the_process_group_on_timeout(tmp_path):
    p = _t9_paths(tmp_path)
    pidfile = tmp_path / "pids"
    body = ("import os, subprocess, sys, time\n"
            "kid = subprocess.Popen(['/bin/sleep', '60'])\n"
            "open(%r, 'w').write('%%d %%d' %% (os.getpid(), kid.pid))\n"
            "time.sleep(60)\n") % str(pidfile)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, body))
    with pytest.raises(SealRefusal) as ei:
        candidate_run.run(_T9_RUN, p, sandbox=False, python=sys.executable, timeout=3)
    assert ei.value.code == "CANDIDATE_FAILED"
    pids = [int(x) for x in pidfile.read_text().split()]
    deadline = time.time() + 5
    alive = pids
    while alive and time.time() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                try:
                    if os.waitpid(pid, os.WNOHANG) != (0, 0):
                        continue
                except ChildProcessError:
                    pass
                alive.append(pid)
            except ProcessLookupError:
                pass
        time.sleep(0.1)
    assert not alive


@_t9_needs_sandbox
def test_t9_run_end_to_end_under_the_real_sandbox_confines_the_candidate(tmp_path, capfd):
    # Legacy host-sandbox executor, exercised directly. It is NOT reachable from candidate_run.main in VM
    # mode (see test_t9_candidate_run_main_grammar_is_strict); the production candidate runs in a VM.
    p = _t9_paths(tmp_path)
    (p.keys_dir).mkdir(parents=True)
    (p.keys_dir / "age.key").write_bytes(b"SEALED-KEY-BYTES")
    target = "/private/tmp/zurvan-z1b-t9-e2e-%s" % uuid.uuid4().hex
    body = ('import json, os, socket\n'
            'r = {}\n'
            'def t(fn):\n'
            '    try:\n        fn(); return "OK"\n'
            '    except OSError as e:\n        return "ERR%%d" %% e.errno\n'
            'r["sealed"] = t(lambda: open(%r, "rb").read())\n'
            'r["tmpwrite"] = t(lambda: open(%r, "w").write("x"))\n'
            'r["net"] = t(lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))\n'
            'r["scratch"] = t(lambda: open(os.path.join(os.environ["TMPDIR"], "ok"), "w").write("x"))\n'
            'print(json.dumps(r))\n') % (str(p.keys_dir / "age.key"), target)
    _t9_stage_bundle(p, _t9_make_repo(tmp_path, body))
    try:
        rc = candidate_run.run(_T9_RUN, p, python=sys.executable, dev_home=tmp_path / "devhome")
        out = json.loads(capfd.readouterr().out.strip().splitlines()[-1])
        assert rc == 0
        assert out == {"sealed": "ERR%d" % errno.EPERM, "tmpwrite": "ERR%d" % errno.EPERM,
                       "net": "ERR%d" % errno.EPERM, "scratch": "OK"}
        assert not os.path.exists(target)
    finally:
        try:
            os.unlink(target)
        except OSError:
            pass


def test_t9_candidate_run_main_grammar_is_strict(tmp_path, capsys):
    p = _t9_paths(tmp_path)
    # Rev 3 #1: `run` is refused in every form - the wrapper cannot execute candidate code on the host.
    for argv in ([], ["bogus"], ["run"], ["run", _T9_RUN], ["run", "../x"], ["run", _T9_RUN, "extra"],
                 ["probe", "x"], ["wipe"], ["wipe", "../x"], ["wipe", _T9_RUN, "y"], ["RUN", _T9_RUN]):
        assert candidate_run.main(argv, paths=p) == 2, argv
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_t9_candidate_run_main_never_invokes_the_host_executor(tmp_path, monkeypatch):
    # Rev 3 #1 anti-escape-hatch: `run` must be refused WITHOUT ever calling run(). This distinguishes a
    # real refusal from run() happening to fail, so re-wiring main to the host executor is caught.
    p = _t9_paths(tmp_path)
    called = []
    monkeypatch.setattr(candidate_run, "run", lambda *a, **k: called.append(a) or 0)
    assert candidate_run.main(["run", _T9_RUN], paths=p) == 2
    assert called == []


def test_t9_candidate_run_main_probe_prints_json_and_wipe_works(tmp_path, capsys):
    p = _t9_paths(tmp_path)
    (p.scratch_dir / _T9_RUN).mkdir()
    assert candidate_run.main(["probe"], paths=p, killer=lambda: None) == 0
    got = json.loads(capsys.readouterr().out)
    assert set(got) == {"eval_home", "keys_dir", "sets_dir", "runs_dir", "incidents_dir", "manifest_file"}
    assert candidate_run.main(["wipe", _T9_RUN], paths=p, killer=lambda: None) == 0
    assert not (p.scratch_dir / _T9_RUN).exists()
    assert capsys.readouterr().out == ""


# ================================================================ sweep

def test_t9_sweep_finds_files_owned_by_uid_and_ignores_other_uids(tmp_path):
    p = _t9_paths(tmp_path)
    root = tmp_path / "droproot"
    (root / "d" / "e").mkdir(parents=True)
    (root / "d" / "e" / "leak.txt").write_text("x")
    (root / "top.txt").write_text("x")
    mine = os.getuid()
    got = sweep.find_candidate_leftovers(p, mine, roots=[str(root)])
    assert str(root / "top.txt") in got
    assert str(root / "d" / "e" / "leak.txt") in got
    assert str(root / "d") in got
    assert got == sorted(got)
    assert sweep.find_candidate_leftovers(p, mine + 1, roots=[str(root)]) == []


def test_t9_sweep_does_not_follow_symlinks(tmp_path):
    p = _t9_paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inner.txt").write_text("x")
    root = tmp_path / "root2"
    root.mkdir()
    os.symlink(str(outside), str(root / "dirlink"))
    os.symlink(str(outside / "inner.txt"), str(root / "filelink"))
    got = sweep.find_candidate_leftovers(p, os.getuid(), roots=[str(root)])
    assert str(root / "dirlink") in got  # the link itself is owned by uid: reported via lstat
    assert str(root / "filelink") in got
    assert not any("inner.txt" in g and "outside" in g for g in got)
    assert str(outside / "inner.txt") not in got
    assert str(root / "dirlink" / "inner.txt") not in got


def test_t9_sweep_skips_the_scratch_tree_missing_roots_and_bad_uid(tmp_path):
    p = _t9_paths(tmp_path)
    (p.scratch_dir / _T9_RUN).mkdir()
    (p.scratch_dir / _T9_RUN / "f").write_text("x")
    got = sweep.find_candidate_leftovers(p, os.getuid(), roots=[str(p.candidate_home), str(tmp_path / "nonexistent")])
    assert not any(g.startswith(str(p.scratch_dir)) for g in got)
    for bad in (True, -1, "5", None, 0):
        with pytest.raises(SealRefusal) as ei:
            sweep.find_candidate_leftovers(p, bad, roots=[])
        assert ei.value.code == "BAD_ARGS"


def test_t9_sweep_default_roots_are_the_contract_roots():
    assert tuple(sweep.DEFAULT_ROOTS) == ("/tmp", "/private/tmp", "/private/var/tmp", "/Users/Shared")


def test_t9_candidate_processes_parses_ps_output_via_the_runner_seam():
    seen = []

    def runner(cmd):
        seen.append(cmd)
        return 0, "  101\n 202\n\n  303 \n"

    assert sweep.candidate_processes(501, runner=runner) == 3
    assert seen == [["/bin/ps", "-U", "501", "-o", "pid="]]


def test_t9_candidate_processes_no_match_is_zero_and_garbage_fails_closed():
    assert sweep.candidate_processes(501, runner=lambda c: (1, "")) == 0
    assert sweep.candidate_processes(501, runner=lambda c: (0, "")) == 0
    for rc, out in ((0, "abc\n"), (0, "12 34\n"), (2, ""), (1, "101\n"), (127, ""), (0, "-5\n"), (0, "1e3\n")):
        with pytest.raises(SealRefusal) as ei:
            sweep.candidate_processes(501, runner=lambda c, rc=rc, out=out: (rc, out))
        assert ei.value.code == "SANDBOX_VIOLATION"  # ruling R13: assume the worst, never INTERNAL
    def boom(cmd):
        raise OSError("no ps")
    with pytest.raises(SealRefusal) as ei:
        sweep.candidate_processes(501, runner=boom)
    assert ei.value.code == "SANDBOX_VIOLATION"


@pytest.mark.parametrize("bad", [True, -1, 0, "501", None, 1.0])
def test_t9_candidate_processes_bad_uid(bad):
    with pytest.raises(SealRefusal) as ei:
        sweep.candidate_processes(bad, runner=lambda c: (0, ""))
    assert ei.value.code == "BAD_ARGS"


def test_t9_candidate_processes_real_ps_sees_own_processes_and_none_for_an_unknown_uid():
    assert sweep.candidate_processes(os.getuid()) >= 1
    assert sweep.candidate_processes(2147480000) == 0


# ================================================================ default adapter (dev side)

from scripts import z1b_candidate  # noqa: E402
from scripts.trace_schema import TraceRecord  # noqa: E402
from scripts.trace_writer import ProtectedTraceError, TraceStore, in_protected_eval  # noqa: E402


def _t9_chunk(path="wiki/decisions/a.md", heading="Why", score=0.5):
    return {"chunk_id": "c", "source_path": path, "heading": heading, "text": "SECRET CORPUS TEXT",
            "keyword_score": 0.1, "semantic_score": 0.2, "hybrid_score": score}


def _t9_serve(requests, search_fn, limit=10):
    stdin = io.BytesIO(b"".join(requests))
    stdout = io.BytesIO()
    rc = z1b_candidate.serve(stdin, stdout, search_fn, limit=limit)  # default wiki_root: ruling R6
    return rc, stdout.getvalue()


def test_t9_adapter_maps_chunks_to_relative_pages_and_roundtrips_the_protocol():
    calls = []

    def fake(query, limit):
        calls.append((query, limit))
        return [_t9_chunk("wiki/decisions/a.md", "H1", 0.9), _t9_chunk("wiki/x.md", None, 1),
                _t9_chunk("docs/readme.md", "D", 0.1)]

    rc, out = _t9_serve([protocol.encode_query(1, "q one"), protocol.encode_query(2, "q two")], fake)
    assert rc == 0
    lines = out.splitlines(keepends=True)
    assert len(lines) == 2
    first = protocol.decode_response(lines[0], 1, 10)
    assert first["results"] == [
        {"page": "wiki/decisions/a.md", "heading": "H1", "score": 0.9},
        {"page": "wiki/x.md", "heading": None, "score": 1.0},
        {"page": "docs/readme.md", "heading": "D", "score": 0.1},
    ]
    assert isinstance(first["results"][1]["score"], float)
    assert first["abstain"] is False
    assert protocol.decode_response(lines[1], 2, 10)["qid"] == 2
    assert calls == [("q one", 10), ("q two", 10)]
    assert b"SECRET CORPUS TEXT" not in out


def test_t9_adapter_enforces_top_10_even_if_search_returns_more():
    rc, out = _t9_serve([protocol.encode_query(1, "q")],
                        lambda q, n: [_t9_chunk("wiki/p%d.md" % i, "h", 1.0 - i / 100) for i in range(25)])
    assert rc == 0
    assert len(protocol.decode_response(out, 1, 10)["results"]) == 10


def test_t9_adapter_drops_chunks_whose_path_could_escape():
    rc, out = _t9_serve([protocol.encode_query(1, "q")], lambda q, n: [
        _t9_chunk("../../etc/passwd"), _t9_chunk("/etc/passwd"), _t9_chunk("wiki/../../x.md"),
        _t9_chunk("wiki/ok.md"), _t9_chunk("a\x00b.md"), _t9_chunk(None), _t9_chunk("")])
    assert rc == 0
    assert [r["page"] for r in protocol.decode_response(out, 1, 10)["results"]] == ["wiki/ok.md"]


def test_t9_adapter_strips_absolute_corpus_prefix():
    rc, out = _t9_serve([protocol.encode_query(1, "q")],
                        lambda q, n: [_t9_chunk("/Library/ZurvanEval/corpus/wiki/decisions/z.md")])
    # an absolute path outside the relative wiki root is not guessed at: dropped, never passed through
    assert rc == 0
    assert protocol.decode_response(out, 1, 10)["results"] == []


def test_t9_adapter_search_returning_nothing_is_an_empty_answer_not_abstain():
    rc, out = _t9_serve([protocol.encode_query(4, "q")], lambda q, n: [])
    assert rc == 0
    assert protocol.decode_response(out, 4, 10) == {"qid": 4, "results": [], "abstain": False}


@pytest.mark.parametrize("line", [
    b"not json\n", b'{"qid":1}\n', b'{"q":"x"}\n', b'{"qid":1,"q":"x","extra":1}\n', b'{"qid":true,"q":"x"}\n',
    b'{"qid":-1,"q":"x"}\n', b'{"qid":1,"q":5}\n', b'{"qid":1,"q":"x"}\n{"qid":2,"q":"y"}\n' + b"x" * 5,
    b'{"qid":1,"qid":2,"q":"x"}\n', b"[]\n", b"\xff\xfe\n", b'{"qid":1,"q":"' + b"a" * 70000 + b'"}\n',
])
def test_t9_adapter_refuses_malformed_requests_without_calling_search_or_answering(line):
    called = []
    rc, out = _t9_serve([line], lambda q, n: called.append(q) or [])
    if line.startswith(b'{"qid":1,"q":"x"}\n{"qid"'):
        assert rc == 2  # second line is garbage: first is answered, then refusal
    else:
        assert rc == 2
        assert out == b""
        assert called == []


def test_t9_adapter_empty_stdin_is_a_clean_exit():
    rc, out = _t9_serve([], lambda q, n: [])
    assert (rc, out) == (0, b"")


def test_t9_adapter_runs_inside_protected_eval_and_resets_after():
    seen = []
    rc, _ = _t9_serve([protocol.encode_query(1, "q")], lambda q, n: seen.append(in_protected_eval()) or [])
    assert rc == 0
    assert seen == [True]
    assert in_protected_eval() is False


def _t9_record(mode, tid):
    return TraceRecord(trace_id=tid, title="t", summary="s", trace_mode=mode)


def test_t9_adapter_non_protected_trace_write_inside_the_loop_raises_and_writes_nothing(tmp_path):
    store = TraceStore(tmp_path)

    def sneaky(query, limit):
        store.write(_t9_record("summary", "trace-20261002T000000Z-sneaky01"))
        return []

    with pytest.raises(ProtectedTraceError):
        _t9_serve([protocol.encode_query(1, "q")], sneaky)
    assert not (tmp_path / "data" / "traces").exists() or list((tmp_path / "data" / "traces").iterdir()) == []
    assert in_protected_eval() is False

    def sneaky_full(query, limit):
        store.write(_t9_record("full", "trace-20261002T000000Z-sneaky02"))
        return []

    with pytest.raises(ProtectedTraceError):
        _t9_serve([protocol.encode_query(1, "q")], sneaky_full)


def test_t9_adapter_protected_eval_trace_is_still_allowed_inside_the_loop(tmp_path):
    store = TraceStore(tmp_path)

    def ok(query, limit):
        store.write(_t9_record("protected_eval", "trace-20261002T000000Z-allowed1"))
        return []

    rc, _ = _t9_serve([protocol.encode_query(1, "q")], ok)
    assert rc == 0
    assert (tmp_path / "data" / "traces" / "trace-20261002T000000Z-allowed1.json").exists()


def test_t9_adapter_chunk_to_page_unit():
    f = z1b_candidate.chunk_to_page
    assert f("wiki/a/b.md", "wiki") == "a/b.md"
    assert f("wiki/a/b.md", "wiki/") == "a/b.md"
    assert f("docs/x.md", "wiki") == "docs/x.md"
    assert f("wikipedia/x.md", "wiki") == "wikipedia/x.md"
    for bad in ("", None, "/abs.md", "../x", "a/../b", "a\x00", 5, "x" * 600):
        assert f(bad, "wiki") is None


def test_t9_adapter_default_search_requires_the_db_env_and_never_guesses(monkeypatch):
    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)
    with pytest.raises(SystemExit) as ei:
        z1b_candidate.adapter_main([])
    assert ei.value.code == 2


# ------------------------------------------------------------------ the VM door (operator gate 2026-10-05)
#
# The broker (zurvaneval) runs every VM operation through this door AS zurvancandidate, so the VM's host
# processes belong to the candidate identity (pf blocks the guest's egress; a hypervisor escape holds no key).
# The door re-validates every argument and renders the VM config itself.

def _t9_vm(monkeypatch):
    from scripts.eval_seal import vm_runner as V
    calls = []
    monkeypatch.setattr(V, "local_start", lambda rid, paths, **kw: calls.append(("start", rid, kw)))
    monkeypatch.setattr(V, "local_copy", lambda rid, paths, src, dst, **kw: calls.append(("copy", rid, src, dst)))
    monkeypatch.setattr(V, "local_teardown", lambda rid, paths, **kw: calls.append(("delete", rid)))
    monkeypatch.setattr(V, "local_leftovers", lambda paths, **kw: ["z" + _T9_RUN])
    return V, calls


def _t9_golden(p):
    img = p.broker_dir / "images" / "guest.qcow2"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"QFI\xfb")
    return img


def test_t9_door_vm_start_boots_the_pinned_golden_image_as_the_candidate(tmp_path, monkeypatch):
    p = _t9_paths(tmp_path)
    V, calls = _t9_vm(monkeypatch)
    assert candidate_run.main(["vm-start", _T9_RUN], paths=p) == 2          # no golden image installed: refused
    assert calls == []
    img = _t9_golden(p)
    assert candidate_run.main(["vm-start", _T9_RUN], paths=p) == 0
    assert calls == [("start", _T9_RUN, {"image_location": "file://" + str(img), "profile": "golden"})]


def test_t9_door_vm_start_reports_a_leftover_instance_distinctly(tmp_path, monkeypatch):
    from scripts.eval_seal import vm_runner as V
    p = _t9_paths(tmp_path)
    _t9_golden(p)

    def leftover(*a, **k):
        raise candidate_run.SealRefusal("SANDBOX_VIOLATION")
    monkeypatch.setattr(V, "local_start", leftover)
    assert candidate_run.main(["vm-start", _T9_RUN], paths=p) == V.DOOR_RC_LEFTOVER

    def failed(*a, **k):
        raise candidate_run.SealRefusal("CANDIDATE_FAILED")
    monkeypatch.setattr(V, "local_start", failed)
    assert candidate_run.main(["vm-start", _T9_RUN], paths=p) == 2


def test_t9_door_vm_shell_execs_limactl_as_the_candidate_with_the_guest_argv_verbatim(tmp_path, monkeypatch):
    from scripts.eval_seal import vm_runner as V
    p = _t9_paths(tmp_path)
    seen = {}

    def fake_execve(exe, argv, env):
        seen.update(exe=exe, argv=argv, env=env)
    rc = candidate_run.main(["vm-shell", _T9_RUN, "--", "/usr/bin/sudo", "-n", "-u", "z1bcand", "--", "/x"],
                            paths=p, execve=fake_execve)
    assert seen["exe"] == str(p.broker_bin / "limactl")
    assert seen["argv"] == V.local_shell_argv(_T9_RUN, p, ["/usr/bin/sudo", "-n", "-u", "z1bcand", "--", "/x"])
    assert seen["env"]["LIMA_HOME"] == str(p.candidate_home / "l" / _T9_RUN) and "PYTHONPATH" not in seen["env"]
    assert rc == 3                                                          # execve never returns in production


def test_t9_door_vm_copy_accepts_only_a_regular_file_under_the_stage_dir(tmp_path, monkeypatch):
    p = _t9_paths(tmp_path)
    V, calls = _t9_vm(monkeypatch)
    good = p.stage_dir / "corpus-stage.x.tar"
    good.write_bytes(b"tar")
    outside = tmp_path / "secret.tar"
    outside.write_bytes(b"s")
    link = p.stage_dir / "link.tar"
    link.symlink_to(outside)
    sub = p.stage_dir / "d"
    sub.mkdir()
    for src, dst in ((str(outside), "/tmp/z1b-corpus.tar"),          # outside the stage dir
                     (str(link), "/tmp/z1b-corpus.tar"),             # a symlink planted in the stage dir
                     (str(sub), "/tmp/z1b-corpus.tar"),              # not a regular file
                     ("relative.tar", "/tmp/z1b-corpus.tar"),        # not absolute
                     (str(good), "relative"),                        # guest dst not absolute
                     (str(good), "/tmp/../etc/shadow"),              # guest dst traversal
                     (str(good), "/tmp/a b")):                       # guest dst charset
        assert candidate_run.main(["vm-copy", _T9_RUN, src, dst], paths=p) == 2, (src, dst)
    assert calls == []
    assert candidate_run.main(["vm-copy", _T9_RUN, str(good), "/tmp/z1b-corpus.tar"], paths=p) == 0
    assert calls == [("copy", _T9_RUN, os.path.realpath(str(good)), "/tmp/z1b-corpus.tar")]


def test_t9_door_vm_delete_and_list(tmp_path, monkeypatch, capsys):
    p = _t9_paths(tmp_path)
    V, calls = _t9_vm(monkeypatch)
    assert candidate_run.main(["vm-delete", _T9_RUN], paths=p) == 0 and calls == [("delete", _T9_RUN)]
    assert candidate_run.main(["vm-list"], paths=p) == 0
    assert capsys.readouterr().out == "z" + _T9_RUN + "\n"


def test_t9_door_vm_grammar_is_strict(tmp_path, monkeypatch, capsys):
    p = _t9_paths(tmp_path)
    V, calls = _t9_vm(monkeypatch)
    _t9_golden(p)
    for argv in (["vm-start"], ["vm-start", "../x"], ["vm-start", _T9_RUN, "extra"], ["vm-start", _T9_RUN.upper()],
                 ["vm-shell", _T9_RUN], ["vm-shell", _T9_RUN, "/x"], ["vm-shell", _T9_RUN, "--"],
                 ["vm-copy", _T9_RUN, "/a"], ["vm-delete"], ["vm-delete", "x"], ["vm-list", "x"],
                 ["vm-exec", _T9_RUN], ["vm-", _T9_RUN]):
        assert candidate_run.main(argv, paths=p, execve=lambda *a: None) == 2, argv
    assert calls == []
    assert capsys.readouterr().out == ""
