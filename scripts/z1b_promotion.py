"""Z1b promotion workflow (contract s4.2): the production caller of the Z1a dataset gate.

A candidate is promoted only by a HOLDOUT run made with a predeclaration committed at the candidate
commit. This module refuses, in this order and before any broker call:

1. a manifest whose HOLDOUT digest is not registered as HOLDOUT (RETIRED, RESERVE, DEV and
   unregistered files all raise DatasetNotEligible("NOT_PROMOTION_ELIGIBLE"));
2. a declaration that is not committed at the candidate commit, or does not satisfy the spec's
   predeclaration schema, or names a different commit or suite.

A PASS verdict is appended to `eval/z1b/promotions.jsonl`; a FAIL records nothing. Final acceptance
(the RESERVE run) requires a recorded PASS for the same commit and suite_id and is refused here before
the broker is asked; the broker enforces the same rule independently.

Every refusal is a code, never text.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scripts import eval_datasets as ed
from scripts import z1b_client
from scripts.eval_seal import manifest as seal_manifest
from scripts.eval_seal import sysenv
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.spec import load_spec
from scripts.z1b_client import ClientRefusal

DECLARATION_RE = r"^eval/z1b/declarations/[a-z0-9-]{1,64}\.json$"
REPO = Path(__file__).resolve().parent.parent

_DECL = re.compile(DECLARATION_RE)
_COMMIT = re.compile(r"[0-9a-f]{40}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CREATED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_RECORD_KEYS = ("commit", "suite_id", "suite_manifest_sha256", "verdict", "broker_sha", "created")
_MAX_DECLARATION_BYTES = 64 * 1024
_GIT = "/usr/bin/git"


def _promotion_refuse(code: str = "DECLARATION_INVALID"):
    return ClientRefusal(code)


# ---------------------------------------------------------------------------- declaration

def _promotion_no_dups(pairs):
    out = {}
    for key, val in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = val
    return out


def _promotion_no_const(_name):
    raise ValueError("non-finite constant")


def _promotion_is_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _promotion_check(value, schema, depth: int = 0) -> None:
    """Validate `value` against one predeclaration-schema node. Raises DECLARATION_INVALID."""
    if depth > 6 or type(schema) is not dict:
        raise _promotion_refuse()
    kind = schema.get("type")
    if kind == "string":
        if type(value) is not str:
            raise _promotion_refuse()
        if "enum" in schema and value not in schema["enum"]:
            raise _promotion_refuse()
        if "pattern" in schema and not re.fullmatch(schema["pattern"].strip("^$"), value):
            raise _promotion_refuse()
    elif kind in ("number", "integer"):
        ok = (type(value) is int) if kind == "integer" else _promotion_is_number(value)
        if not ok or isinstance(value, bool):
            raise _promotion_refuse()
        if "minimum" in schema and value < schema["minimum"]:
            raise _promotion_refuse()
        if "maximum" in schema and value > schema["maximum"]:
            raise _promotion_refuse()
    elif kind == "array":
        if type(value) is not list or len(value) < schema.get("min_length", 0):
            raise _promotion_refuse()
        for element in value:
            _promotion_check(element, schema["element"], depth + 1)
        if len({json.dumps(e, sort_keys=True) for e in value}) != len(value):
            raise _promotion_refuse()  # duplicate strata would let a critical set be padded
    elif kind == "object":
        props = schema.get("properties", {})
        if type(value) is not dict or set(value) != set(schema.get("required", [])) or set(value) != set(props):
            raise _promotion_refuse()
        for key, sub in props.items():
            _promotion_check(value[key], sub, depth + 1)
    else:
        raise _promotion_refuse()


def validate_declaration(decl: dict, spec: dict) -> None:
    """The five canonical s9.5 fields plus the commit and suite bindings, exactly and nothing else."""
    try:
        schema = spec["predeclaration_schema"]
    except (KeyError, TypeError):
        raise _promotion_refuse() from None
    if type(decl) is not dict:
        raise _promotion_refuse()
    _promotion_check(decl, {"type": "object", "required": schema["required"],
                            "properties": schema["properties"]})


# ---------------------------------------------------------------------------- dataset gate

def promotion_dataset(manifest: dict) -> "ed.DatasetInfo":
    """The registry entry for the manifest's HOLDOUT file. Unregistered digests come back as DEV."""
    try:
        digest = manifest["holdout_age_sha256"]
    except (KeyError, TypeError):
        raise _promotion_refuse("MANIFEST_INVALID") from None
    return ed.dataset_for_digest(digest)


@contextlib.contextmanager
def _promotion_registry(registry):
    """Use `registry` (a path) as the dataset registry for the duration of one call."""
    if registry is None:
        yield
        return
    saved = ed.REGISTRY
    ed.REGISTRY = Path(registry)
    try:
        yield
    finally:
        ed.REGISTRY = saved


def promotion_load_manifest():
    """(manifest, sha256 of the signed file): the public manifest, signature-verified or refused."""
    base = REPO / "eval" / "z1b"
    try:
        parsed = seal_manifest.verify(base / "manifest.json", base / "manifest.json.sig",
                                      base / "allowed_signers")
        return parsed, seal_manifest.suite_manifest_sha256(base / "manifest.json")
    except SealRefusal as exc:
        raise ClientRefusal(exc.code) from None


def promotion_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- records (append-only)

def _promotion_records_path() -> Path:
    return REPO / "eval" / "z1b" / "promotions.jsonl"


def _promotion_valid_record(rec) -> bool:
    return (type(rec) is dict and set(rec) == set(_RECORD_KEYS)
            and all(type(rec[k]) is str for k in _RECORD_KEYS)
            and bool(_COMMIT.fullmatch(rec["commit"])) and bool(_HEX16.fullmatch(rec["suite_id"]))
            and bool(_HEX64.fullmatch(rec["suite_manifest_sha256"]))
            and rec["verdict"] == "PASS" and bool(_COMMIT.fullmatch(rec["broker_sha"]))
            and bool(_CREATED.fullmatch(rec["created"])))


def promotion_records() -> list:
    """Every recorded promotion. A malformed line makes the whole file untrustworthy: LEDGER_INVALID."""
    path = _promotion_records_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError:
        raise _promotion_refuse("LEDGER_INVALID") from None
    if data and not data.endswith(b"\n"):
        raise _promotion_refuse("LEDGER_INVALID")
    out = []
    for line in data.decode("utf-8", "replace").splitlines():
        try:
            rec = json.loads(line, object_pairs_hook=_promotion_no_dups, parse_constant=_promotion_no_const)
        except (ValueError, RecursionError):
            raise _promotion_refuse("LEDGER_INVALID") from None
        if not _promotion_valid_record(rec):
            raise _promotion_refuse("LEDGER_INVALID")
        out.append(rec)
    return out


def _promotion_append(record: dict) -> bool:
    """Append one record under an exclusive lock. False when an identical PASS is already recorded."""
    path = _promotion_records_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)
    except OSError:
        raise _promotion_refuse("LEDGER_INVALID") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        key = (record["commit"], record["suite_id"], record["suite_manifest_sha256"])
        for existing in promotion_records():
            if (existing["commit"], existing["suite_id"], existing["suite_manifest_sha256"]) == key:
                return False
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        os.write(fd, line.encode("ascii"))
        os.fsync(fd)
        return True
    finally:
        os.close(fd)  # closing releases the flock


# ---------------------------------------------------------------------------- declaration source

def _promotion_read_declaration(commit: str, declaration: str) -> dict:
    """`git show <commit>:<declaration>`: the predeclaration must be committed AT the candidate."""
    work = tempfile.mkdtemp(prefix="z1b-decl-")
    try:
        env = sysenv.clean_env(Path(work), {"TMPDIR": work})
        cp = subprocess.run([_GIT, "-c", "core.hooksPath=/dev/null", "-C", str(REPO), "show",
                             "%s:%s" % (commit, declaration)], env=env, cwd="/", stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise _promotion_refuse() from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if cp.returncode != 0 or not cp.stdout or len(cp.stdout) > _MAX_DECLARATION_BYTES:
        raise _promotion_refuse()
    try:
        return json.loads(cp.stdout.decode("utf-8"), object_pairs_hook=_promotion_no_dups,
                          parse_constant=_promotion_no_const)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise _promotion_refuse() from None


# ---------------------------------------------------------------------------- the workflow

def promote(commit: str, declaration: str, *, client=z1b_client, registry=None) -> dict:
    """HOLDOUT run with a committed predeclaration; a PASS verdict is recorded, a FAIL is not."""
    with _promotion_registry(registry):
        manifest, manifest_sha = promotion_load_manifest()
        ed.require_promotion_eligible(promotion_dataset(manifest))  # BEFORE anything else, incl. the broker
    if type(commit) is not str or not _COMMIT.fullmatch(commit):
        raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    if type(declaration) is not str or not _DECL.fullmatch(declaration):
        raise _promotion_refuse()
    try:
        spec = load_spec(REPO / "eval" / "z1b" / "spec.json")
    except SealRefusal as exc:
        raise ClientRefusal(exc.code) from None
    decl = _promotion_read_declaration(commit, declaration)
    validate_declaration(decl, spec)
    if decl["candidate_commit"] != commit or decl["suite_id"] != manifest["suite_id"]:
        raise _promotion_refuse()

    result = client.run(commit, "holdout", declare=declaration)

    promo = result.get("promotion") if type(result) is dict else None
    if type(promo) is not dict or promo.get("declared") is not True or promo.get("verdict") not in ("PASS", "FAIL"):
        raise ClientRefusal("INTERNAL")
    if result.get("candidate_commit") != commit or result.get("suite_id") != manifest["suite_id"]:
        raise ClientRefusal("INTERNAL")
    if result.get("suite_manifest_sha256") != manifest_sha:
        raise ClientRefusal("MANIFEST_INVALID")  # the public manifest is not the one that was run against
    verdict = promo["verdict"]
    recorded = False
    if verdict == "PASS":
        recorded = _promotion_append({
            "commit": commit, "suite_id": result["suite_id"],
            "suite_manifest_sha256": result["suite_manifest_sha256"], "verdict": "PASS",
            "broker_sha": result["broker_sha"], "created": promotion_now()})
    return {"verdict": verdict, "recorded": recorded, "result": result}


def final_acceptance(commit: str, *, client=z1b_client) -> dict:
    """RESERVE run, only after a recorded HOLDOUT PASS for this commit under this suite_id."""
    if type(commit) is not str or not _COMMIT.fullmatch(commit):
        raise ClientRefusal("CANDIDATE_NOT_FROZEN")
    manifest, _sha = promotion_load_manifest()
    passed = [r for r in promotion_records()
              if r["commit"] == commit and r["suite_id"] == manifest["suite_id"] and r["verdict"] == "PASS"]
    if not passed:
        raise ClientRefusal("RESERVE_NOT_ELIGIBLE")
    return client.run(commit, "reserve", final_acceptance=True)
