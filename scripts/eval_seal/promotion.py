"""Z1b Rev 3 P1 #16: a broker-signed PROMOTION ATTESTATION.

The sealed ledger (a hash-chained, eval-owned log of completed holdout runs whose promotion verdict is
PASS) stays the AUTHORITATIVE internal anchor for promotion (ruling R22): `broker_holdout_passed` and
`broker_baseline` consult ONLY it. This module adds an EXPORTABLE proof on top: when a holdout run passes,
the broker signs a closed-schema attestation with the eval signing key, so an outside party can verify
"commit X passed holdout suite S at run R" WITHOUT trusting the eval-owned filesystem.

Domain separation: a DISTINCT ssh-keygen namespace (`zurvan-z1b-promotion`), so a manifest signature can
never verify as a promotion attestation and vice versa. Any failure on the verify path is PROMOTION_INVALID
and reveals nothing. Sealed-side code: stdlib only, Python 3.9 compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from . import sysenv
from .codes import SealRefusal
from .manifest import PRINCIPAL, SSH_KEYGEN   # reuse the pinned tool + the eval principal

NAMESPACE = "zurvan-z1b-promotion"            # DISTINCT from the manifest namespace (domain separation)
SCHEMA = "z1b-promotion-attestation-1"
MAX_BYTES = 64 * 1024
SUITES = ("holdout", "reserve")

_HEX16 = re.compile(r"[0-9a-f]{16}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_FIELDS = ("schema", "candidate_commit", "suite", "suite_id", "suite_manifest_sha256", "run_id",
           "result_sha256", "evaluator_version", "verdict", "sequence", "created")


def _bad():
    raise SealRefusal("PROMOTION_INVALID")


def _env() -> dict:
    return sysenv.clean_env(sysenv.home_of_euid())


def result_sha256(result: dict) -> str:
    """The digest the attestation binds: the canonical bytes of the run result the ledger recorded."""
    return hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False,
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def _validate(att) -> None:
    if type(att) is not dict or set(att) != set(_FIELDS):
        _bad()
    if att["schema"] != SCHEMA or att["verdict"] != "PASS":
        _bad()
    for k, rx in (("candidate_commit", _HEX40), ("suite_id", _HEX16), ("run_id", _HEX16),
                  ("suite_manifest_sha256", _HEX64), ("result_sha256", _HEX64),
                  ("evaluator_version", _VERSION)):
        if type(att[k]) is not str or not rx.fullmatch(att[k]):
            _bad()
    if att["suite"] not in SUITES:
        _bad()
    if type(att["sequence"]) is not int or isinstance(att["sequence"], bool) or att["sequence"] < 1:
        _bad()
    if type(att["created"]) is not str or not att["created"] or len(att["created"]) > 64:
        _bad()


def build_attestation(*, candidate_commit, suite, suite_id, suite_manifest_sha256, run_id,
                      result_sha256, evaluator_version, sequence, created) -> dict:
    att = {"schema": SCHEMA, "candidate_commit": candidate_commit, "suite": suite, "suite_id": suite_id,
           "suite_manifest_sha256": suite_manifest_sha256, "run_id": run_id, "result_sha256": result_sha256,
           "evaluator_version": evaluator_version, "verdict": "PASS", "sequence": sequence,
           "created": created}
    _validate(att)
    return att


def dump(att: dict) -> bytes:
    return (json.dumps(att, sort_keys=True, separators=(",", ":"), allow_nan=False,
                       ensure_ascii=True) + "\n").encode("ascii")


def write(path: Path, att: dict) -> Path:
    """Write the attestation 0600, eval-owned, never following a symlink. Caller signs it next."""
    _validate(att)
    path = Path(path)
    body = dump(att)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def sign(att_path: Path, key_path: Path) -> Path:
    """Detached ssh signature over the attestation, namespace zurvan-z1b-promotion. Mirrors manifest.sign:
    a `.pub` path is refused (it would make ssh-keygen consult an agent)."""
    att_path, key_path = Path(att_path), Path(key_path)
    if key_path.name.endswith(".pub") or not key_path.is_file() or not att_path.is_file():
        _bad()
    sig = Path(str(att_path) + ".sig")
    try:
        sig.unlink()
    except FileNotFoundError:
        pass
    try:
        proc = subprocess.run(
            [SSH_KEYGEN, "-Y", "sign", "-f", str(key_path), "-n", NAMESPACE, str(att_path)],
            env=_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        _bad()
    if proc.returncode != 0 or not sig.is_file():
        _bad()
    return sig


def _read_bounded(path: Path) -> bytes:
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _bad()
    try:
        chunks, total = [], 0
        while True:
            c = os.read(fd, 65536)
            if not c:
                break
            total += len(c)
            if total > MAX_BYTES:
                _bad()
            chunks.append(c)
        return b"".join(chunks)
    except OSError:
        _bad()
    finally:
        os.close(fd)


def _no_dups(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def verify(att_path: Path, sig_path: Path, allowed_signers: Path) -> dict:
    """Verify the signature (eval principal, promotion namespace) against the trust anchor, then parse the
    exact bytes that were verified and validate the closed schema. PROMOTION_INVALID on any failure. The
    namespace guarantees a manifest signature cannot be replayed here."""
    data = _read_bounded(Path(att_path))
    sig_path, allowed_signers = Path(sig_path), Path(allowed_signers)
    if not sig_path.is_file() or not allowed_signers.is_file():
        _bad()
    try:
        proc = subprocess.run(
            [SSH_KEYGEN, "-Y", "verify", "-f", str(allowed_signers), "-I", PRINCIPAL, "-n", NAMESPACE,
             "-s", str(sig_path)],
            input=data, env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        _bad()
    if proc.returncode != 0:
        _bad()
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_no_dups)
    except (ValueError, UnicodeDecodeError, RecursionError):
        _bad()
    _validate(parsed)
    return parsed
