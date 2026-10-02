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

    def fake_vm_launch(run_id, paths, guest_cmd, *, image_location=None, **kw):
        seen["run_id"] = run_id
        seen["guest_cmd"] = list(guest_cmd)
        seen["image"] = image_location
        return types.SimpleNamespace(stdin=object(), stdout=object(), pid=99)
    monkeypatch.setattr(V, "vm_launch", fake_vm_launch)
    proc = B.broker_vm_launch("0123456789abcdef", p)
    assert proc.pid == 99
    assert seen["run_id"] == "0123456789abcdef"
    # the candidate entry is the fixed in-guest path, not a host clone
    assert seen["guest_cmd"] == list(B.GUEST_CANDIDATE_CMD)
    # boots from the pinned guest image FILE (guest has no network), under the broker asset tree
    assert seen["image"].startswith("file://") and "images/guest" in seen["image"]


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
    assert V.GUEST_IMAGE_DIGEST[-16:] in B.broker_runtime_material(p)  # the digest is in the identity material
