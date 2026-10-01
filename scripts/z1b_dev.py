"""Z1b DEV set tooling (spec s6.5): validate, finalize, freeze and export the visible DEV pool.

DEV is the one pool that is PUBLIC and permanent, so nothing here is sealed. It still follows the
instrument rules: the file lives untracked at ~/.zurvan/instruments/z1-dev.jsonl, reports name
items by INDEX only, the tracked registry carries one whole-file digest and never a per-item digest,
and a frozen DEV set is only replaced by an explicit new suite version (a DEV change after sealed
authoring invalidates the contamination checks that were run against it).

The DEV authoring run itself spends model usage and is operator-gated; this module never calls a model.
"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from scripts import instruments
from scripts.config import PROJECT_ROOT
from scripts.eval_seal import spec as dev_seal_spec

DEV_NAME = "z1-dev.jsonl"
DEV_DATASET = "z1b_dev"
DEV_MAX_BYTES = 16 * 1024 * 1024
DEV_DEFAULT_SPEC = PROJECT_ROOT / "eval" / "z1b" / "spec.json"
DEV_PROVENANCE = {"author": "dev", "reviewer": "dev"}
_DEV_FIELDS = frozenset(("keys", "id", "stratum", "query", "targets", "grading", "difficulty",
                         "provenance", "snapshot_aliases", "item"))
_DEV_WS = re.compile(r"\s+")


class DevRefusal(Exception):
    """Carries a short code. The message never contains item content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def dev_path(*, must_exist: bool = False) -> Path:
    """The cleartext DEV file. Resolved at call time from instruments.INSTRUMENT_DIR (tests redirect it
    with ZURVAN_INSTRUMENTS); never the legacy eval/ fallback that `instruments.gold_path` allows."""
    p = Path(instruments.INSTRUMENT_DIR) / DEV_NAME
    if must_exist and not p.is_file():
        raise instruments.InstrumentMissing(
            "DEV set not found at %s. Author it first (eval/z1b/prompts/dev-author.md)." % p)
    return p


def _dev_spec(spec):
    return spec if spec is not None else dev_seal_spec.load_spec(DEV_DEFAULT_SPEC)


def _dev_read(path) -> bytes:
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > DEV_MAX_BYTES:
            raise DevRefusal("DEV_INVALID")
        return p.read_bytes()
    except OSError:
        raise DevRefusal("DEV_INVALID") from None


def _dev_no_dups(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _dev_lines(data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DevRefusal("DEV_INVALID") from None
    return [ln for ln in text.splitlines() if ln.strip()]


def _dev_norm_query(q) -> str:
    return _DEV_WS.sub(" ", str(q)).strip().casefold()


def _dev_scan(data: bytes, spec) -> dict:
    """Counts only. `invalid_indexes` are 0-based positions among non-blank lines."""
    strata = [e["id"] for e in spec["strata"]]
    per = {s: 0 for s in strata}
    invalid, indexes, fields = 0, [], {}
    ids, queries, dup_ids, dup_queries = set(), set(), 0, 0
    lines = _dev_lines(data)
    for idx, line in enumerate(lines):
        try:
            item = json.loads(line, object_pairs_hook=_dev_no_dups)
            if type(item) is not dict:
                raise ValueError("item")
        except ValueError:
            invalid += 1
            indexes.append(idx)
            fields["json"] = fields.get("json", 0) + 1
            continue
        probe = dict(item)
        if "provenance" not in probe:
            probe["provenance"] = dict(DEV_PROVENANCE)   # DEV carries no provenance requirement
        try:
            dev_seal_spec.validate_item(probe, spec)
        except ValueError as exc:
            name = str(exc.args[0]) if exc.args else "item"
            name = name if name in _DEV_FIELDS else "other"
            invalid += 1
            indexes.append(idx)
            fields[name] = fields.get(name, 0) + 1
            continue
        per[item["stratum"]] += 1
        if item["id"] in ids:
            dup_ids += 1
        ids.add(item["id"])
        nq = _dev_norm_query(item["query"])
        if nq in queries:
            dup_queries += 1
        queries.add(nq)
    need = {e["id"]: e["min_holdout"] for e in spec["strata"]}
    short = [{"stratum": s, "have": per[s], "need": need[s]} for s in strata if per[s] < need[s]]
    ok = bool(lines) and invalid == 0 and dup_ids == 0 and dup_queries == 0 and not short
    return {"n_items": len(lines), "per_stratum": per, "invalid": invalid, "invalid_indexes": indexes,
            "invalid_fields": fields, "duplicate_ids": dup_ids, "duplicate_queries": dup_queries,
            "short": short, "ok": ok}


def dev_validate(path, spec) -> dict:
    """Validate a DEV file against the sealed item schema minus provenance. Counts and indexes only."""
    return _dev_scan(_dev_read(path), spec)


def _dev_clean(res: dict) -> bool:
    return res["invalid"] == 0 and res["duplicate_ids"] == 0 and res["duplicate_queries"] == 0


def _dev_atomic_write(dest: Path, data: bytes, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".dev-", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _dev_load_registry(registry_path) -> dict:
    try:
        reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DevRefusal("REGISTRY_INVALID") from None
    if type(reg) is not dict or type(reg.get("datasets")) is not dict:
        raise DevRefusal("REGISTRY_INVALID")
    return reg


def dev_freeze(path, registry_path, *, spec=None, new_suite_version: bool = False) -> str:
    """Register the DEV file's whole-file sha256 as `z1b_dev` (state DEV). Returns the digest.

    Same bytes: no-op. Different bytes while `z1b_dev` exists: refused unless `new_suite_version`
    (A2: DEV may not change after sealed authoring without a new suite), and the old digest is kept
    under `superseded`. A digest already registered under another name is refused, since
    `eval_datasets.dataset_for_digest` would otherwise raise on the duplicate."""
    data = _dev_read(path)
    res = _dev_scan(data, _dev_spec(spec))
    if not res["ok"]:
        raise DevRefusal("DEV_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    reg = _dev_load_registry(registry_path)
    datasets = reg["datasets"]
    for name, entry in datasets.items():
        if name != DEV_DATASET and type(entry) is dict and entry.get("sha256") == digest:
            raise DevRefusal("DEV_DIGEST_CONFLICT")
    current = datasets.get(DEV_DATASET)
    entry = {"state": "DEV", "sha256": digest, "n_items": res["n_items"]}
    if current is not None:
        if type(current) is not dict or current.get("state") != "DEV":
            raise DevRefusal("REGISTRY_INVALID")
        if current.get("sha256") == digest:
            return digest
        if not new_suite_version:
            raise DevRefusal("DEV_ALREADY_FROZEN")
        entry["superseded"] = list(current.get("superseded", [])) + [current.get("sha256")]
    datasets[DEV_DATASET] = entry
    mode = os.stat(registry_path).st_mode & 0o777
    _dev_atomic_write(Path(registry_path), (json.dumps(reg, indent=2) + "\n").encode("utf-8"), mode)
    return digest


def dev_ref_export(path, dest, *, spec=None, registry_path=None) -> str:
    """Copy the DEV file to `dest` (the sealed side's sets/dev-ref.jsonl). DEV is public, so the copy
    leaks nothing. With `registry_path`, the bytes must equal the frozen `z1b_dev` digest."""
    src, dst = Path(path), Path(dest)
    if dst.exists() and os.path.samefile(src, dst):
        raise DevRefusal("DEV_SAME_FILE")
    data = _dev_read(src)
    res = _dev_scan(data, _dev_spec(spec))
    if not _dev_clean(res):
        raise DevRefusal("DEV_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    if registry_path is not None:
        frozen = _dev_load_registry(registry_path)["datasets"].get(DEV_DATASET)
        if type(frozen) is not dict or frozen.get("sha256") != digest:
            raise DevRefusal("DEV_NOT_FROZEN")
    _dev_atomic_write(dst, data, 0o644)
    return digest


def dev_finalize(raw_path, dest, *, spec=None) -> str:
    """Turn the authoring agent's raw output (stratum, query, difficulty, targets) into DEV items:
    deterministic id, grading from the spec, null source_id, default provenance. Never overwrites."""
    spec = _dev_spec(spec)
    grading = {e["id"]: e["grading"] for e in spec["strata"]}
    out = []
    for idx, line in enumerate(_dev_lines(_dev_read(raw_path))):
        try:
            raw = json.loads(line, object_pairs_hook=_dev_no_dups)
            item = {
                "id": hashlib.sha256(("z1b-dev\x00%d\x00%s" % (idx, raw["query"])).encode("utf-8")).hexdigest()[:32],
                "stratum": raw["stratum"],
                "query": raw["query"],
                "targets": [dict({"source_id": None}, **t) for t in raw["targets"]],
                "grading": grading[raw["stratum"]],
                "difficulty": raw["difficulty"],
                "provenance": dict(raw.get("provenance", DEV_PROVENANCE)),
            }
        except (ValueError, KeyError, TypeError):
            raise DevRefusal("DEV_INVALID") from None
        out.append(item)
    data = "".join(json.dumps(i, sort_keys=True) + "\n" for i in out).encode("utf-8")
    if not _dev_clean(_dev_scan(data, spec)):
        raise DevRefusal("DEV_INVALID")
    dst = Path(dest)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        raise DevRefusal("DEV_EXISTS") from None
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return hashlib.sha256(data).hexdigest()
