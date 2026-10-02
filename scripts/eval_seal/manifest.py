"""Signed public manifest (contract s3.5).

The manifest carries counts and whole-file digests only. It is signed with
`ssh-keygen -Y sign` (namespace zurvan-z1b-manifest) by the sealed identity and verified
on the dev side with a tracked allowed_signers file. Every failure on the verify path is
SealRefusal("MANIFEST_INVALID"): the caller learns that it failed, never why.

assert_no_per_item_digests is the structural guard that keeps per-item digests out of the
tracked manifest (a per-item digest is a confirmation oracle for the sealed items). Only
five TOP-LEVEL keys may carry a 64-hex value; any hex run of 33+ characters anywhere else
(keys and values, any nesting depth) is refused. 32-hex burn ids are shorter and allowed.
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

SSH_KEYGEN = "/usr/bin/ssh-keygen"
NAMESPACE = "zurvan-z1b-manifest"
PRINCIPAL = "zurvaneval"
SCHEMA = "zurvan.z1b.manifest.v1"
MAX_MANIFEST_BYTES = 1024 * 1024
BURN_REASONS = ("canary_hit", "item_leak", "target_leak", "sandbox_violation", "operator")

DIGEST_KEYS = ("corpus_sha256", "holdout_age_sha256", "reserve_age_sha256", "dev_sha256", "prompt_sha256")
_FIELDS = (
    "schema", "suite_id", "sequence", "created", "models", "strata_counts", "accepted", "rejected",
    "short", "evaluator_version", "burn_ledger",
) + DIGEST_KEYS

_HEX64 = re.compile(r"[0-9a-f]{64}")
_LONG_HEX = re.compile(r"[0-9a-fA-F]{33,}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_CREATED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_MODEL = re.compile(r"[A-Za-z0-9._:/-]{1,80}")


def _bad():
    raise SealRefusal("MANIFEST_INVALID")


def _manifest_is_int(v) -> bool:
    return type(v) is int and v >= 0


def _walk_strings(node, depth=0):
    if depth > 12:
        _bad()
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                _bad()
            yield k
            for s in _walk_strings(v, depth + 1):
                yield s
    elif isinstance(node, (list, tuple)):
        for v in node:
            for s in _walk_strings(v, depth + 1):
                yield s


def assert_no_per_item_digests(manifest: dict) -> None:
    if not isinstance(manifest, dict):
        _bad()
    for key, value in manifest.items():
        if not isinstance(key, str):
            _bad()
        if key in DIGEST_KEYS:
            if type(value) is not str or not _HEX64.fullmatch(value):
                _bad()
            continue
        if _LONG_HEX.search(key):
            _bad()
        for s in _walk_strings(value):
            if _LONG_HEX.search(s):
                _bad()


def _validate(m) -> None:
    """Closed schema. Raises SealRefusal(MANIFEST_INVALID) on any deviation."""
    if not isinstance(m, dict) or set(m) != set(_FIELDS):
        _bad()
    if m["schema"] != SCHEMA:
        _bad()
    if type(m["suite_id"]) is not str or not _HEX16.fullmatch(m["suite_id"]):
        _bad()
    if not _manifest_is_int(m["sequence"]) or not _manifest_is_int(m["accepted"]):
        _bad()
    if type(m["created"]) is not str or not _CREATED.fullmatch(m["created"]):
        _bad()
    if type(m["evaluator_version"]) is not str or not _VERSION.fullmatch(m["evaluator_version"]):
        _bad()
    models = m["models"]
    if not isinstance(models, dict) or not models:
        _bad()
    for k, v in models.items():
        if not isinstance(k, str) or not _NAME.fullmatch(k) or type(v) is not str or not _MODEL.fullmatch(v):
            _bad()
    sc = m["strata_counts"]
    if not isinstance(sc, dict):
        _bad()
    for pool, per in sc.items():
        if not isinstance(pool, str) or not _NAME.fullmatch(pool) or not isinstance(per, dict):
            _bad()
        for stratum, n in per.items():
            if not isinstance(stratum, str) or not _NAME.fullmatch(stratum) or not _manifest_is_int(n):
                _bad()
    rej = m["rejected"]
    if not isinstance(rej, dict):
        _bad()
    for reason, n in rej.items():
        if not isinstance(reason, str) or not _NAME.fullmatch(reason) or not _manifest_is_int(n):
            _bad()
    short = m["short"]
    if not isinstance(short, list):
        _bad()
    for row in short:
        if not isinstance(row, dict) or set(row) != {"pool", "stratum", "have", "need"}:
            _bad()
        for k in ("pool", "stratum"):
            if type(row[k]) is not str or not _NAME.fullmatch(row[k]):
                _bad()
        if not _manifest_is_int(row["have"]) or not _manifest_is_int(row["need"]):
            _bad()
    ledger = m["burn_ledger"]
    if not isinstance(ledger, list):
        _bad()
    for e in ledger:
        if not isinstance(e, dict) or set(e) != {"id", "reason", "date"}:
            _bad()
        if type(e["id"]) is not str or not _HEX32.fullmatch(e["id"]):
            _bad()
        if e["reason"] not in BURN_REASONS:
            _bad()
        if type(e["date"]) is not str or not _DATE.fullmatch(e["date"]):
            _bad()
    assert_no_per_item_digests(m)


def build_manifest(*, suite_id, sequence, created, models, prompt_sha256, corpus_sha256, holdout_age_sha256,
                   reserve_age_sha256, dev_sha256, strata_counts, accepted, rejected, short, evaluator_version,
                   burn_ledger) -> dict:
    m = {
        "schema": SCHEMA,
        "suite_id": suite_id,
        "sequence": sequence,
        "created": created,
        "models": dict(models),
        "prompt_sha256": prompt_sha256,
        "corpus_sha256": corpus_sha256,
        "holdout_age_sha256": holdout_age_sha256,
        "reserve_age_sha256": reserve_age_sha256,
        "dev_sha256": dev_sha256,
        "strata_counts": {p: dict(c) for p, c in strata_counts.items()},
        "accepted": accepted,
        "rejected": dict(rejected),
        "short": [dict(r) for r in short],
        "evaluator_version": evaluator_version,
        "burn_ledger": [dict(e) for e in burn_ledger],
    }
    _validate(m)
    return m


def dump_manifest(manifest: dict) -> bytes:
    _validate(manifest)
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_manifest(path: Path, manifest: dict) -> None:
    Path(path).write_bytes(dump_manifest(manifest))


def _env() -> dict:
    return sysenv.clean_env(sysenv.home_of_euid())


def sign(manifest_path: Path, key_path: Path) -> Path:
    manifest_path, key_path = Path(manifest_path), Path(key_path)
    if key_path.name.endswith(".pub") or not key_path.is_file() or not manifest_path.is_file():
        _bad()  # a .pub path would make ssh-keygen consult an agent
    sig = Path(str(manifest_path) + ".sig")
    try:
        sig.unlink()
    except FileNotFoundError:
        pass
    try:
        proc = subprocess.run(
            [SSH_KEYGEN, "-Y", "sign", "-f", str(key_path), "-n", NAMESPACE, str(manifest_path)],
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
            if total > MAX_MANIFEST_BYTES:
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


def _no_const(name):
    raise ValueError("non-finite constant")


def verify(manifest_path: Path, sig_path: Path, allowed_signers: Path) -> dict:
    data = _read_bounded(Path(manifest_path))
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
        # parse the exact bytes that were verified (no second read, no TOCTOU)
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_no_dups, parse_constant=_no_const)
    except (ValueError, UnicodeDecodeError, RecursionError):
        _bad()
    _validate(parsed)
    return parsed


def check_sequence(manifest: dict, last_seen: int) -> None:
    if not isinstance(manifest, dict) or not _manifest_is_int(manifest.get("sequence")) or not _manifest_is_int(last_seen):
        _bad()
    if manifest["sequence"] < last_seen:
        _bad()


def suite_manifest_sha256(manifest_path: Path) -> str:
    return hashlib.sha256(_read_bounded(Path(manifest_path))).hexdigest()
