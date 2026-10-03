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

    def fake_vm_start(run_id, paths, *, image_location=None, **kw):
        seen["started"] = run_id
        seen["image"] = image_location

    def fake_provision(run_id, paths, **kw):
        seen["provisioned"] = run_id

    def fake_vm_launch(run_id, paths, guest_cmd, *, boot=True, guest_env=None, image_location=None, **kw):
        seen["run_id"] = run_id
        seen["guest_cmd"] = list(guest_cmd)
        seen["boot"] = boot
        seen["guest_env"] = guest_env
        return types.SimpleNamespace(stdin=object(), stdout=object(), pid=99)
    monkeypatch.setattr(V, "vm_start", fake_vm_start)
    monkeypatch.setattr(B, "broker_provision_corpus", fake_provision)
    monkeypatch.setattr(V, "vm_launch", fake_vm_launch)
    proc = B.broker_vm_launch("0123456789abcdef", p)
    assert proc.pid == 99 and seen["run_id"] == "0123456789abcdef"
    # boot happens once (vm_start), the corpus is provisioned, then the candidate launches on the booted VM
    assert seen["started"] == "0123456789abcdef" and seen["provisioned"] == "0123456789abcdef"
    assert seen["boot"] is False                                   # vm_launch must NOT boot a second time
    assert seen["image"].startswith("file://") and "images/guest" in seen["image"]
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


def test_t1wire_provision_corpus_verifies_then_stages_then_mounts_read_only(tmp_path, monkeypatch):
    import hashlib
    p = _bvm_paths(tmp_path)
    p.corpus_dir.mkdir(parents=True, exist_ok=True)
    img = p.corpus_dir / "corpus.squashfs"
    img.write_bytes(b"hsqs" + b"\x00" * 32)                       # squashfs magic + bytes
    digest = hashlib.sha256(img.read_bytes()).hexdigest()
    (p.corpus_dir / "corpus.squashfs.sha256").write_text(digest + "\n")
    calls = []
    monkeypatch.setattr(V, "vm_copy_into_guest", lambda rid, paths, src, dst, **kw: calls.append(("copy", str(src), dst)))
    monkeypatch.setattr(V, "vm_run", lambda rid, paths, argv, **kw: calls.append(("run", " ".join(argv))))
    B.broker_provision_corpus("0123456789abcdef", p)
    assert calls[0] == ("copy", str(img), V.GUEST_CORPUS_SQUASHFS)  # staged (not a host mount)
    assert calls[1][0] == "run" and "mount -o loop,ro" in calls[1][1] and V.GUEST_CORPUS_MOUNT in calls[1][1]


def test_t1wire_provision_corpus_refuses_a_wrong_corpus_digest(tmp_path):
    from scripts.eval_seal.codes import SealRefusal
    p = _bvm_paths(tmp_path)
    p.corpus_dir.mkdir(parents=True, exist_ok=True)
    (p.corpus_dir / "corpus.squashfs").write_bytes(b"hsqs" + b"\x00" * 32)
    (p.corpus_dir / "corpus.squashfs.sha256").write_text("0" * 64 + "\n")   # does not match the file
    with pytest.raises(SealRefusal) as ei:
        B.broker_provision_corpus("0123456789abcdef", p)
    assert ei.value.code == "CORPUS_MISMATCH"


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
    with pytest.raises(SealRefusal) as ei:
        B.broker_vm_launch("0123456789abcdef", p)
    assert ei.value.code == "CANDIDATE_FAILED"  # charged by the broker; no other path is tried


def test_t1wire_run_identity_binds_the_guest_image_digest(tmp_path):
    # the image/config is part of the runtime identity, so a changed guest image is never served from cache
    p = _bvm_paths(tmp_path)
    (p.runtime_dir).mkdir(parents=True, exist_ok=True)
    (p.runtime_dir / "bin").mkdir(parents=True, exist_ok=True)
    (p.runtime_dir / "bin" / "python3").write_text("#!/bin/sh\n")
    base = B.broker_runtime_sha256(p)
    assert V.GOLDEN_IMAGE_DIGEST[-16:] in B.broker_runtime_material(p)  # the digest is in the identity material
