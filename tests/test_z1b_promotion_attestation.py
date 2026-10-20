"""Z1b Rev 3 P1 #16: the broker-signed promotion attestation (exportable proof of a holdout PASS).

The sealed ledger stays the authoritative anchor (R22); this covers the ADDED signed artifact: round-trip,
tamper rejection, wrong-key rejection, domain separation vs the manifest signature, and the closed schema.
Needs /usr/bin/ssh-keygen (the real signer); skips otherwise.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_seal import manifest as M            # noqa: E402
from scripts.eval_seal import promotion as P           # noqa: E402
from scripts.eval_seal import render as R              # noqa: E402
from scripts.eval_seal.codes import SealRefusal        # noqa: E402

SSH_KEYGEN = "/usr/bin/ssh-keygen"
pytestmark = pytest.mark.skipif(not os.path.exists(SSH_KEYGEN), reason="no /usr/bin/ssh-keygen")

_C40 = "a1" * 20          # 40 hex
_H16 = "0123456789abcdef"
_H64 = "b2" * 32          # 64 hex


def _keypair(d: Path, name="signing_ed25519"):
    d.mkdir(parents=True, exist_ok=True)
    key = d / name
    subprocess.run([SSH_KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    # derive allowed_signers exactly as the installer does (#7), so these two features are proven together
    inner = R.allowed_signers_derive_sh(str(d / (name + ".pub")), str(d / "allowed_signers"))
    assert subprocess.run(["/bin/sh", "-c", inner], timeout=30).returncode == 0
    return key, d / "allowed_signers"


def _att():
    return P.build_attestation(candidate_commit=_C40, suite="holdout", suite_id=_H16,
                               suite_manifest_sha256=_H64, run_id=_H16, result_sha256=_H64,
                               evaluator_version="1.0.0", sequence=3, created="2026-10-02T10:00:00Z")


def test_t16_round_trip_verifies_the_signed_attestation(tmp_path):
    key, signers = _keypair(tmp_path / "keys")
    att = tmp_path / "p.json"
    P.write(att, _att())
    sig = P.sign(att, key)
    assert sig.exists() and oct(att.stat().st_mode)[-3:] == "600"
    assert P.verify(att, sig, signers) == _att()


def test_t16_a_tampered_attestation_is_refused(tmp_path):
    key, signers = _keypair(tmp_path / "keys")
    att = tmp_path / "p.json"
    P.write(att, _att())
    sig = P.sign(att, key)
    att.write_bytes(att.read_bytes().replace(b'"holdout"', b'"reserve"'))  # flip a field after signing
    with pytest.raises(SealRefusal) as ei:
        P.verify(att, sig, signers)
    assert ei.value.code == "PROMOTION_INVALID"


def test_t16_a_signature_from_another_key_is_refused(tmp_path):
    key, _ = _keypair(tmp_path / "keys")
    _, other_signers = _keypair(tmp_path / "other")
    att = tmp_path / "p.json"
    P.write(att, _att())
    sig = P.sign(att, key)
    with pytest.raises(SealRefusal):
        P.verify(att, sig, other_signers)   # anchor names a different key


def test_t16_domain_separation_a_manifest_signature_never_verifies_as_a_promotion(tmp_path):
    # the SAME key signs identical bytes under the manifest namespace; the promotion verifier must reject it
    key, signers = _keypair(tmp_path / "keys")
    att = tmp_path / "p.json"
    P.write(att, _att())
    msig = M.sign(att, key)                  # namespace zurvan-z1b-manifest
    with pytest.raises(SealRefusal) as ei:
        P.verify(att, msig, signers)         # verifier uses zurvan-z1b-promotion
    assert ei.value.code == "PROMOTION_INVALID"


def test_t16_closed_schema_rejects_bad_or_extra_fields(tmp_path):
    key, signers = _keypair(tmp_path / "keys")
    with pytest.raises(SealRefusal):
        P.build_attestation(candidate_commit="nothex", suite="holdout", suite_id=_H16,
                            suite_manifest_sha256=_H64, run_id=_H16, result_sha256=_H64,
                            evaluator_version="1.0.0", sequence=3, created="now")
    # an extra key in a correctly-signed doc is refused on verify
    att = tmp_path / "p.json"
    doc = dict(_att(), smuggled=1)
    att.write_bytes((json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode())
    sig = P.sign(att, key)
    with pytest.raises(SealRefusal):
        P.verify(att, sig, signers)


def test_t16_a_pub_key_path_is_refused_for_signing(tmp_path):
    key, _ = _keypair(tmp_path / "keys")
    att = tmp_path / "p.json"
    P.write(att, _att())
    with pytest.raises(SealRefusal):
        P.sign(att, Path(str(key) + ".pub"))


def test_t16_broker_writes_and_signs_an_attestation_only_on_a_pass(tmp_path):
    import datetime
    from scripts.eval_seal import broker as B
    from scripts.eval_seal.paths import SealPaths
    paths = SealPaths.for_root(tmp_path / "root")
    keys = Path(paths.keys_dir)
    key, signers = _keypair(keys)
    paths.runs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime(2026, 10, 2, 10, 0, 0)
    result = {"candidate_commit": _C40, "suite": "holdout", "suite_id": _H16,
              "suite_manifest_sha256": _H64, "evaluator_version": "1.0.0",
              "metrics": {}, "promotion": {"verdict": "PASS", "rules": {}}}
    m = {"sequence": 4}
    dest = B.broker_write_promotion_attestation(paths, result, m, _H16, now)
    assert dest is not None and dest.exists()
    att = P.verify(dest, Path(str(dest) + ".sig"), signers)      # the signed artifact verifies
    assert att["candidate_commit"] == _C40 and att["run_id"] == _H16 and att["sequence"] == 4
    assert att["result_sha256"] == P.result_sha256(result)
    # a FAIL (or absent) promotion writes nothing
    result["promotion"] = {"verdict": "FAIL", "rules": {}}
    assert B.broker_write_promotion_attestation(paths, result, m, "0000000000000000", now) is None
    result["promotion"] = None
    assert B.broker_write_promotion_attestation(paths, result, m, "1111111111111111", now) is None
    assert sorted(p.name for p in (paths.runs_dir / "promotions").iterdir()) == [_H16 + ".json",
                                                                                 _H16 + ".json.sig"]
