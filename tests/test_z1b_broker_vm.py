"""Z1b Rev 3 #1 broker->VM wiring. In sealed mode the broker's ONLY way to execute the candidate is the
disposable VM: there is no host-side candidate-execution path to fall back to, and a VM launch/teardown
failure is a hard sealed-eval failure, never host execution (canonical: a component must not silently
substitute another backend when it fails)."""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import broker as B  # noqa: E402
from scripts.eval_seal import vm_runner as V  # noqa: E402
from scripts.eval_seal.paths import SealPaths  # noqa: E402


def _bvm_paths(tmp_path):
    p = SealPaths.for_root(tmp_path / "root")
    for d in (p.broker_bin, p.scratch_dir):
        d.mkdir(parents=True, exist_ok=True)
    return p


def test_t1wire_candidate_executes_only_through_vm_launch(tmp_path, monkeypatch):
    p = _bvm_paths(tmp_path)
    seen = {}

    def fake_vm_start(run_id, paths, **kw):
        seen["started"] = run_id
        seen["start_kw"] = kw

    def fake_provision(run_id, paths, corpus_sha, **kw):
        seen["provisioned"] = run_id
        seen["corpus_sha"] = corpus_sha

    def fake_vm_launch(run_id, paths, guest_cmd, *, boot=True, guest_env=None, guest_user=None, **kw):
        seen["run_id"] = run_id
        seen["guest_cmd"] = list(guest_cmd)
        seen["boot"] = boot
        seen["guest_env"] = guest_env
        seen["guest_user"] = guest_user
        return types.SimpleNamespace(stdin=object(), stdout=object(), pid=99)
    monkeypatch.setattr(V, "vm_start", fake_vm_start)
    monkeypatch.setattr(B, "broker_provision_corpus", fake_provision)
    monkeypatch.setattr(V, "vm_launch", fake_vm_launch)
    proc = B.broker_vm_launch("0123456789abcdef", p, "a" * 64)
    assert proc.pid == 99 and seen["run_id"] == "0123456789abcdef"
    # boot happens once (vm_start), the corpus is provisioned (bound to the run's corpus_sha), then launch
    assert seen["started"] == "0123456789abcdef" and seen["provisioned"] == "0123456789abcdef"
    assert seen["corpus_sha"] == "a" * 64
    assert seen["boot"] is False                                   # vm_launch must NOT boot a second time
    # the broker does NOT choose the image: the door renders the pinned golden config itself
    assert seen["start_kw"] == {}
    # the candidate runs as the no-sudo guest user (the login user is root-equivalent in the guest)
    assert seen["guest_user"] == B.GUEST_CANDIDATE_USER == "z1bcand"
    # the candidate entry is the fixed in-guest path, not a host clone, and runs with the sealed env
    assert seen["guest_cmd"] == list(B.GUEST_CANDIDATE_CMD)
    assert seen["guest_env"] == B.broker_candidate_env()


def test_t1wire_candidate_env_is_the_frozen_index_on_the_read_only_corpus(tmp_path):
    env = B.broker_candidate_env()
    assert env["ZURVAN_CORPUS"] == V.GUEST_CORPUS_MOUNT
    assert env["ZURVAN_SEARCH_DB"] == V.GUEST_CORPUS_MOUNT + "/search.sqlite"   # frozen index IN the corpus
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HOME"].startswith("/opt/z1b")
    assert set(env) == {"ZURVAN_SEARCH_DB", "ZURVAN_CORPUS", "HF_HUB_OFFLINE", "HF_HOME", "TMPDIR"}


def test_t1wire_guest_env_is_forced_with_env_minus_i(tmp_path):
    p = _bvm_paths(tmp_path)
    argv = V.vm_shell_argv("0123456789abcdef", p, ["/opt/z1b/x"], guest_env={"B": "2", "A": "1"})
    i = argv.index("--")
    # after `-- ` the candidate runs under `/usr/bin/env -i A=1 B=2` (sorted, nothing inherited)
    assert argv[i + 1:] == ["/usr/bin/env", "-i", "A=1", "B=2", "/opt/z1b/x"]


def _write_corpus(p):
    """A small frozen corpus CONTENT tree on the host (no squashfs, no packaging artifact) + its digest."""
    import shutil
    from scripts.eval_seal.corpus import corpus_sha256
    shutil.rmtree(str(p.corpus_dir), ignore_errors=True)
    p.corpus_dir.mkdir(parents=True, exist_ok=True)
    (p.corpus_dir / "search.sqlite").write_bytes(b"SQLite format 3\x00frozen-index")
    (p.corpus_dir / "graph.sqlite").write_bytes(b"SQLite format 3\x00frozen-graph")
    sub = p.corpus_dir / "pages"
    sub.mkdir()
    (sub / "a.md").write_text("# A\nalpha\n")
    (sub / "b.md").write_text("# B\nbeta\n")
    return corpus_sha256(Path(p.corpus_dir))


def test_t1wire_provision_stages_content_then_builds_and_mounts_ro_in_guest(tmp_path, monkeypatch):
    # The host supplies CONTENT only: it tars corpus/ and copies the tar in; the GUEST extracts, re-derives
    # the digest, builds the squashfs and loop-mounts it read-only. The host never makes a mountable image.
    p = _bvm_paths(tmp_path)
    sha = _write_corpus(p)
    calls = []
    staged = {}
    def fake_copy(rid, paths, src, dst, **kw):
        staged["bytes"] = Path(src).read_bytes()                  # capture what crossed the boundary
        calls.append(("copy", Path(src).name, dst))
    monkeypatch.setattr(V, "vm_copy_into_guest", fake_copy)
    monkeypatch.setattr(V, "vm_run", lambda rid, paths, argv, **kw: calls.append(("run", argv[-1])))
    B.broker_provision_corpus("0123456789abcdef", p, sha)
    # (a) exactly one host-side copy of a TAR to the tar path, then one in-guest build script -- nothing else
    assert [c[0] for c in calls] == ["copy", "run"]
    assert calls[0][2] == V.GUEST_CORPUS_TAR                       # a tar, never a host-built squashfs
    script = calls[1][1]
    assert "tar xf %s" % V.GUEST_CORPUS_TAR in script              # guest extracts
    assert "mksquashfs %s %s" % (V.GUEST_CORPUS_CONTENT, V.GUEST_CORPUS_SQUASHFS) in script  # guest builds
    assert "mount -o loop,ro %s %s" % (V.GUEST_CORPUS_SQUASHFS, V.GUEST_CORPUS_MOUNT) in script  # guest mounts ro
    assert sha in script and "exit 3" in script                   # digest bound + fail-closed on mismatch
    assert "rm -rf %s %s %s" % (V.GUEST_CORPUS_CONTENT, V.GUEST_CORPUS_TAR, V.GUEST_CORPUS_SQUASHFS) in script
    # the unprivileged candidate can read the corpus (modes are outside corpus_sha256), set before squashing
    assert script.index("chmod -R a+rX %s" % V.GUEST_CORPUS_CONTENT) < script.index("mksquashfs")
    assert script.index("chmod -R a+rX") > script.index("exit 3")                # only after the digest check
    # the candidate's guest user exists and every non-loopback packet it sends is REJECTed (v4 + v6, incl. DNS)
    u = B.GUEST_CANDIDATE_USER
    assert "useradd --system --no-create-home" in script and "--shell /usr/sbin/nologin %s" % u in script
    assert "iptables -I OUTPUT 1 -m owner --uid-owner %s ! -o lo -j REJECT" % u in script
    assert "ip6tables -I OUTPUT 1 -m owner --uid-owner %s ! -o lo -j REJECT" % u in script
    assert script.startswith("set -e;")                                          # any failure: no run, fail closed
    # (b) the staged tar carries EXACTLY the frozen corpus content for that digest
    import io, tarfile, tempfile
    from scripts.eval_seal.corpus import corpus_sha256
    dd = Path(tempfile.mkdtemp())
    tarfile.open(fileobj=io.BytesIO(staged["bytes"])).extractall(str(dd))
    assert corpus_sha256(dd) == sha


def test_t1wire_provision_stages_a_tar_not_a_host_squashfs_and_leaves_corpus_untouched(tmp_path, monkeypatch):
    # The host NEVER runs mksquashfs and NEVER writes a packaging artifact into the corpus tree; the source
    # corpus is byte-identical afterwards, and the staged tar lands in the eval runs dir, not in corpus/.
    import hashlib
    p = _bvm_paths(tmp_path)
    sha = _write_corpus(p)
    before = {q.name: hashlib.sha256(q.read_bytes()).hexdigest()
              for q in sorted(Path(p.corpus_dir).rglob("*")) if q.is_file()}
    import tarfile
    seen = {}
    def grab(rid, paths, src, dst, **kw):
        seen["src"] = Path(src)
        seen["mode"] = Path(src).stat().st_mode & 0o777
        seen["is_tar"] = tarfile.is_tarfile(str(src))             # inspect it WHILE it exists, before transport
        seen["dst"] = dst
    monkeypatch.setattr(V, "vm_copy_into_guest", grab)
    monkeypatch.setattr(V, "vm_run", lambda rid, paths, argv, **kw: None)
    B.broker_provision_corpus("0123456789abcdef", p, sha)
    after = {q.name: hashlib.sha256(q.read_bytes()).hexdigest()
             for q in sorted(Path(p.corpus_dir).rglob("*")) if q.is_file()}
    assert after == before                                        # source corpus unchanged, nothing added
    assert not list(Path(p.corpus_dir).glob("*.squashfs"))        # no packaging artifact inside corpus/
    assert seen["is_tar"] and seen["dst"] == V.GUEST_CORPUS_TAR    # host stages a TAR, never a built image
    assert not seen["src"].exists()                               # staged tar removed after transport
    assert Path(p.stage_dir) in seen["src"].parents               # staged where the door may copy it, not corpus/
    assert seen["mode"] == 0o640                                   # group-readable for the candidate, no world bits


def test_t1wire_provision_refuses_a_malformed_corpus_sha_before_touching_the_guest(tmp_path, monkeypatch):
    from scripts.eval_seal.codes import SealRefusal
    p = _bvm_paths(tmp_path)
    _write_corpus(p)
    touched = []
    monkeypatch.setattr(V, "vm_copy_into_guest", lambda *a, **k: touched.append("copy"))
    monkeypatch.setattr(V, "vm_run", lambda *a, **k: touched.append("run"))
    for bad in ("not-hex", "0" * 63, "ABCDEF" + "0" * 58):
        with pytest.raises(SealRefusal) as ei:
            B.broker_provision_corpus("0123456789abcdef", p, bad)
        assert ei.value.code == "CORPUS_MISMATCH"
    assert touched == []                                          # fail-closed before anything is staged


def test_t1wire_provision_refuses_a_byte_tampered_after_the_digest_was_established(tmp_path, monkeypatch):
    # THE tamper gate. The run's corpus_sha256 is declared from corpus/. An attacker then flips one byte in
    # corpus/ before the VM is provisioned. The guest re-derives the digest of the bytes it actually received
    # and REFUSES: the real provisioning path (vm_run) surfaces the guest's nonzero exit as CANDIDATE_FAILED.
    # We run the broker's ACTUAL guest script on the host, rewriting only guest-only tokens (python path,
    # /opt/z1b import root, the fixed /tmp paths) and stubbing mksquashfs/mount -- the digest comparison,
    # the embedded declared sha and the exit code are the broker's own.
    import subprocess
    from scripts.eval_seal.codes import SealRefusal
    p = _bvm_paths(tmp_path)
    declared = _write_corpus(p)                                   # digest established HERE
    (Path(p.corpus_dir) / "pages" / "a.md").write_text("# A\nTAMPERED\n")  # ...then one byte changes

    host = {"content": str(tmp_path / "guest-content"),
            "sq": str(tmp_path / "guest.squashfs"), "mount": str(tmp_path / "guest-mount")}
    captured = {}
    def fake_copy(rid, paths, src, dst, **kw):
        captured["tar"] = Path(src).read_bytes()                  # the bytes that "reach the VM"
    def run_guest_on_host(rid, paths, argv, *, runner=None, timeout=None):
        tarf = tmp_path / "reached.tar"
        tarf.write_bytes(captured["tar"])
        script = argv[-1]
        script = (script
                  .replace(B.GUEST_CANDIDATE_CMD[0], sys.executable)
                  .replace('"/opt/z1b"', '"%s"' % ROOT)          # keep it double-quoted inside the -c
                  .replace(V.GUEST_CORPUS_CONTENT, host["content"])
                  .replace(V.GUEST_CORPUS_TAR, str(tarf))
                  .replace(V.GUEST_CORPUS_SQUASHFS, host["sq"])
                  .replace(V.GUEST_CORPUS_MOUNT, host["mount"])
                  .replace("mksquashfs ", ": mksquashfs ").replace("mount -o", ": mount -o")
                  .replace("useradd ", ": useradd ").replace("ip6tables ", ": ip6tables ")
                  .replace("iptables ", ": iptables "))
        done = subprocess.run(["/bin/sh", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if done.returncode != 0:
            raise SealRefusal("CANDIDATE_FAILED")
    monkeypatch.setattr(V, "vm_copy_into_guest", fake_copy)
    monkeypatch.setattr(V, "vm_run", run_guest_on_host)
    with pytest.raises(SealRefusal) as ei:
        B.broker_provision_corpus("0123456789abcdef", p, declared)
    assert ei.value.code == "CANDIDATE_FAILED"                    # the tampered corpus cannot be mounted

    # control: the SAME harness accepts the untampered corpus (proves the gate is the digest, not the harness)
    declared2 = _write_corpus(p)                                  # rewrite clean content + its true digest
    B.broker_provision_corpus("0123456789abcdef", p, declared2)   # no raise


def test_t1wire_vm_run_fails_closed_on_a_nonzero_guest_command(tmp_path):
    from scripts.eval_seal.codes import SealRefusal
    p = _bvm_paths(tmp_path)
    with pytest.raises(SealRefusal) as ei:
        V.vm_run("0123456789abcdef", p, ["sh", "-c", "exit 1"],
                 runner=lambda *a, **k: types.SimpleNamespace(returncode=1))
    assert ei.value.code == "CANDIDATE_FAILED"


def test_t1wire_broker_has_no_host_candidate_run_execution_path():
    # anti-escape-hatch: the broker must never invoke the candidate wrapper's `run` subcommand (the old
    # host clone + sandbox-exec path). A future refactor cannot quietly re-add a host executor.
    src = Path(B.__file__).read_text()
    assert '["run"' not in src and '"run", run_id' not in src and "+ [\"run\"" not in src


def test_t1wire_vm_teardown_is_the_wipe_path_and_leftovers_feed_the_sweep(tmp_path, monkeypatch):
    p = _bvm_paths(tmp_path)
    torn, swept = {}, {}
    monkeypatch.setattr(V, "vm_teardown", lambda run_id, paths, **kw: torn.setdefault("id", run_id))
    monkeypatch.setattr(V, "vm_leftover_instances", lambda paths, **kw: swept.setdefault("v", ["zdead"]) or ["zdead"])
    B.broker_vm_wipe("0123456789abcdef", p)
    assert torn["id"] == "0123456789abcdef"
    assert B.broker_vm_leftovers(p) == ["zdead"]


def test_t1wire_a_failed_vm_launch_is_a_hard_failure_not_a_host_fallback(tmp_path, monkeypatch):
    from scripts.eval_seal.codes import SealRefusal
    p = _bvm_paths(tmp_path)

    def boom(run_id, paths, guest_cmd, *, image_location=None, **kw):
        raise SealRefusal("CANDIDATE_FAILED")
    monkeypatch.setattr(V, "vm_launch", boom)
    monkeypatch.setattr(B, "broker_provision_corpus", lambda *a, **k: None)
    with pytest.raises(SealRefusal) as ei:
        B.broker_vm_launch("0123456789abcdef", p, "a" * 64)
    assert ei.value.code == "CANDIDATE_FAILED"  # charged by the broker; no other path is tried


def test_t1wire_run_identity_binds_the_guest_image_digest(tmp_path):
    # the image/config is part of the runtime identity, so a changed guest image is never served from cache
    p = _bvm_paths(tmp_path)
    (p.runtime_dir).mkdir(parents=True, exist_ok=True)
    (p.runtime_dir / "bin").mkdir(parents=True, exist_ok=True)
    (p.runtime_dir / "bin" / "python3").write_text("#!/bin/sh\n")
    base = B.broker_runtime_sha256(p)
    assert V.GOLDEN_IMAGE_DIGEST[-16:] in B.broker_runtime_material(p)  # the digest is in the identity material
