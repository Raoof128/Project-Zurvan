"""The sealed evaluation broker (spec s5; contract s3.9; addendum A).

Runs as `zurvaneval` via the root-owned wrapper `bin/zurvan-broker`. It is the ONLY code that
sees the sealed items, the candidate's raw answers, and the grading. Everything it prints is one
JSON document produced by the closed serializers in output.py; stderr is never written.

Trust model, in code:
- The grammar is fixed (broker_parse_argv); anything outside it is BAD_ARGS before any file is read.
- Nothing is read from os.environ except SUDO_UID (sysenv.sudo_uid, validated). Every subprocess gets
  sysenv.clean_env(home_of_euid()) and an absolute binary path. Paths come from SealPaths, never env.
- A refusal carries a code and nothing else. An unexpected exception is written, with its traceback,
  ONLY to runs/errors.log; stdout gets the INTERNAL refusal.
- Budget is charged from the moment a run is reserved (before the candidate starts). A crashed run is
  failed and charged at the next startup, under the same lock.

Sealed-side code: stdlib only, Python 3.9 compatible. All module-level function names carry the
broker_ prefix (plan ruling R11).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import os
import pwd
import re
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import warnings
from pathlib import Path

from . import grade, intake, promotion, protocol, sealed_store, sweep, sysenv
from . import manifest as manifest_mod
from .codes import SealRefusal
from .corpus import corpus_pages, corpus_sha256
from .ledger import Ledger, run_key
from .output import (RESULT_SCHEMA_ID, SEALCHECK_SCHEMA_ID, STATUS_SCHEMA_ID, serialize_refusal,
                     serialize_public_result, serialize_result, serialize_sealcheck, serialize_status)
from .paths import CANDIDATE_USER, EVAL_USER, SealPaths
from .spec import STRATA, load_spec, validate_item

ZERO_SHA = "0" * 40
ZERO_SHA64 = "0" * 64
CHECK_TIMEOUT = 60
STARTUP_FACTOR = 10          # the first query also pays for loading the candidate's models
FINISH_TIMEOUT = 30          # after the last answer: EOF and a clean exit
MAX_DECLARATION_BYTES = 65536
EXEC_FILES = frozenset(("setup.sh", "install.sh", "install_corpus.sh", "tamper_drill.sh", "uninstall.sh", "refresh_acl.sh"))

_HEX16 = re.compile(r"[0-9a-f]{16}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BUNDLE = re.compile(intake.BUNDLE_NAME_RE)
_DECLARE = re.compile(r"eval/z1b/declarations/[a-z0-9-]{1,64}\.json")
_SUITES = ("holdout", "reserve")
_METRICS = ("recall_at_1", "recall_at_3", "recall_at_10", "mrr")
_RUN_FLAGS = ("--commit", "--bundle", "--suite", "--declare")


# ================================================================ grammar

def _broker_bad() -> SealRefusal:
    return SealRefusal("BAD_ARGS")


def broker_parse_argv(argv) -> dict:
    """The fixed grammar. Anything else is BAD_ARGS (no detail, nothing read yet).

    status | seal-check | scan | ack-incident <hex16>
    run --commit <hex40> --bundle <name> --suite holdout|reserve
        [--declare eval/z1b/declarations/<slug>.json] [--final-acceptance]
    """
    if type(argv) not in (list, tuple) or not argv:
        raise _broker_bad()
    for tok in argv:
        if type(tok) is not str or "\x00" in tok:
            raise _broker_bad()
    cmd, rest = argv[0], list(argv[1:])
    if cmd in ("status", "seal-check", "scan"):
        if rest:
            raise _broker_bad()
        return {"cmd": cmd}
    if cmd == "ack-incident":
        if len(rest) != 1 or not _HEX16.fullmatch(rest[0]):
            raise _broker_bad()
        return {"cmd": cmd, "incident_id": rest[0]}
    if cmd != "run":
        raise _broker_bad()
    seen = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--final-acceptance":
            if tok in seen:
                raise _broker_bad()
            seen[tok] = True
            i += 1
        elif tok in _RUN_FLAGS:
            if tok in seen or i + 1 >= len(rest):
                raise _broker_bad()
            seen[tok] = rest[i + 1]
            i += 2
        else:  # unknown flag, '--flag=value', a path, a stray word
            raise _broker_bad()
    commit, bundle, suite = seen.get("--commit"), seen.get("--bundle"), seen.get("--suite")
    declare, final = seen.get("--declare"), bool(seen.get("--final-acceptance"))
    if commit is None or bundle is None or suite is None:
        raise _broker_bad()
    if not _HEX40.fullmatch(commit) or not _BUNDLE.fullmatch(bundle) or suite not in _SUITES:
        raise _broker_bad()
    if bundle.split("-")[1] != commit:  # the name embeds the commit it claims to hold
        raise _broker_bad()
    if declare is not None and (suite != "holdout" or not _DECLARE.fullmatch(declare)):
        raise _broker_bad()
    if final and suite != "reserve":
        raise _broker_bad()
    return {"cmd": "run", "commit": commit, "bundle": bundle, "suite": suite,
            "declare": declare, "final_acceptance": final}


# ================================================================ context (seams)

@dataclasses.dataclass
class BrokerContext:
    """Every effect the broker has on the world, as an injectable seam. Production values come from
    production_context(paths); tests supply fakes for the privileged ones (age, sudo, tmutil, sweep)."""
    spec_path: object
    decrypt: object            # (Path) -> bytes
    encrypt: object            # (bytes, Path) -> None
    launch_candidate: object   # (run_id) -> Popen-like (stdin, stdout, kill, wait, poll)
    wipe: object               # (run_id) -> None
    probe: object              # () -> dict path_id -> "EACCES" | "ENOENT" | "READABLE"
    tmutil_isexcluded: object  # (Path) -> bool
    now: object                # () -> datetime
    sudo_uid: object           # () -> int
    sweep_leftovers: object    # () -> list
    sweep_processes: object    # () -> int
    runtime_sha256: object     # () -> 64-hex
    rss_mb: object             # () -> int
    scan: object               # (paths, ctx, ledger) -> dict   (Task 11: scan.scan_run)
    reseal: object             # (paths, ctx, ledger) -> dict   (Task 11: scan.reseal_after_burn)
    is_tty: object             # () -> bool
    scan_agent_present: object  # () -> bool
    git_env: dict
    root_uid: int
    eval_uid: int
    candidate_uid: int


# Rev 3 #1: the candidate executes inside a disposable VM. The in-guest candidate entry and the pinned
# guest image are part of the runtime identity, so a changed image/config is never served from cache.
GUEST_CANDIDATE_CMD = ("/opt/z1b/runtime/bin/python3", "-B", "-E", "-s", "/opt/z1b/candidate/z1b_candidate.py")


def broker_guest_image(paths: SealPaths) -> str:
    """The pinned guest image FILE under the broker asset tree (installed offline; the guest has no
    network, so it never downloads at boot). A file:// location for the VM config."""
    return "file://" + str(Path(paths.broker_dir) / "images" / "guest.qcow2")


def broker_runtime_material(paths: SealPaths) -> str:
    """The text whose sha256 is the runtime identity (R14 + Rev 3 #1): the runtime tree listing, the
    interpreter bytes, and the pinned guest image digest + VM config."""
    from . import vm_runner as vmr
    root = paths.runtime_dir
    py = root / "bin" / "python3"
    if not root.is_dir() or not py.exists():
        raise SealRefusal("SEAL_NOT_CONFIGURED")
    lines = []
    for top, dirnames, filenames in os.walk(str(root), topdown=True, followlinks=False):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            full = os.path.join(top, name)
            st = os.lstat(full)
            rel = os.path.relpath(full, str(root))
            if stat.S_ISLNK(st.st_mode):
                detail = "l\0" + os.readlink(full)
            elif stat.S_ISDIR(st.st_mode):
                detail = "d\0"
            elif stat.S_ISREG(st.st_mode):
                detail = "f\0%d\0%d" % (st.st_size, st.st_mtime_ns)
            else:
                raise SealRefusal("SEAL_NOT_CONFIGURED")
            lines.append("%s\0%s\n" % (rel, detail))
    lines.append("interpreter:" + broker_file_sha256(Path(os.path.realpath(str(py)))) + "\n")
    lines.append("guest-image:" + vmr.GOLDEN_IMAGE_DIGEST + "\n")   # the image the broker actually boots
    lines.append("guest-cmd:" + "\0".join(GUEST_CANDIDATE_CMD) + "\n")
    lines.append("vm-config:" + hashlib.sha256(vmr.vm_config_text().encode()).hexdigest() + "\n")
    return "".join(lines)


def broker_runtime_sha256(paths: SealPaths) -> str:
    return hashlib.sha256(broker_runtime_material(paths).encode("utf-8", "surrogateescape")).hexdigest()


# The candidate reads its frozen search index straight from the READ-ONLY corpus (the corpus export is a
# coherent snapshot that already contains search.sqlite + graph.sqlite), so there is NO in-guest index
# build: the broker stages+mounts the corpus read-only and hands the candidate exactly these variables.
GUEST_HF_HOME = "/opt/z1b/runtime/hf"


def broker_candidate_env() -> dict:
    """The EXACT environment the in-guest candidate runs with (`env -i`): the frozen index + corpus on the
    read-only mount, the offline model cache baked into the golden image, and a writable TMPDIR for sqlite
    scratch. Nothing is inherited from the guest login."""
    from . import vm_runner as vmr
    return {
        "ZURVAN_SEARCH_DB": vmr.GUEST_CORPUS_MOUNT + "/search.sqlite",
        "ZURVAN_CORPUS": vmr.GUEST_CORPUS_MOUNT,
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": GUEST_HF_HOME,
        "TMPDIR": "/tmp",
    }


def broker_corpus_image(paths: SealPaths) -> Path:
    """The read-only corpus squashfs the operator install produced from the sealed corpus export."""
    return Path(paths.corpus_dir) / "corpus.squashfs"


def broker_corpus_expected_digest(paths: SealPaths) -> str:
    """The squashfs digest recorded beside it at install time (the tamper anchor the staged image is checked
    against). Missing/malformed => the corpus is not installed as a squashfs: SEAL_NOT_CONFIGURED."""
    try:
        txt = (Path(paths.corpus_dir) / "corpus.squashfs.sha256").read_text(encoding="ascii").strip()
    except OSError:
        raise SealRefusal("SEAL_NOT_CONFIGURED") from None
    if not re.fullmatch(r"[0-9a-f]{64}", txt):
        raise SealRefusal("SEAL_NOT_CONFIGURED")
    return txt


def broker_provision_corpus(run_id: str, paths: SealPaths, *, runner=None) -> None:
    """Verify the corpus squashfs against its recorded digest (CORPUS_MISMATCH on any mismatch or a non-
    squashfs), copy it into the running guest (scp over lima, never a host mount), and loop-mount it
    READ-ONLY at the fixed mount point. The guest never gets a writable view of the corpus."""
    from . import vm_runner as vmr
    img = broker_corpus_image(paths)
    vmr.verify_corpus_image(img, broker_corpus_expected_digest(paths))
    vmr.vm_copy_into_guest(run_id, paths, img, vmr.GUEST_CORPUS_SQUASHFS, runner=runner)
    vmr.vm_run(run_id, paths, ["sudo", "/bin/sh", "-c",
                               "mkdir -p %s && mount -o loop,ro %s %s"
                               % (vmr.GUEST_CORPUS_MOUNT, vmr.GUEST_CORPUS_SQUASHFS, vmr.GUEST_CORPUS_MOUNT)],
               runner=runner)


def broker_vm_launch(run_id: str, paths: SealPaths):
    """The ONE candidate-execution path: boot this run's disposable VM, stage + mount the read-only corpus,
    then return a shell Popen running the candidate on the protocol channel with the sealed env. There is no
    host-side execution fallback; a boot/provision/protocol failure propagates as a SealRefusal the broker
    charges and fails closed on."""
    from . import vm_runner as vmr
    vmr.vm_start(run_id, paths, image_location=broker_guest_image(paths))
    broker_provision_corpus(run_id, paths)
    return vmr.vm_launch(run_id, paths, GUEST_CANDIDATE_CMD, boot=False, guest_env=broker_candidate_env())


def broker_vm_wipe(run_id: str, paths: SealPaths) -> None:
    from . import vm_runner as vmr
    vmr.vm_teardown(run_id, paths)


def broker_vm_leftovers(paths: SealPaths) -> list:
    from . import vm_runner as vmr
    return vmr.vm_leftover_instances(paths)


def broker_rss_mb() -> int:
    """Peak RSS of waited-for descendants, in MiB (ru_maxrss is bytes on macOS, KiB elsewhere)."""
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return max(0, int(peak // (1024 * 1024 if sys.platform == "darwin" else 1024)))


def _broker_run_tool(cmd, env, *, data=None, timeout=CHECK_TIMEOUT):
    return subprocess.run(cmd, env=env, input=data, stdin=None if data is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=False)


def production_context(paths: SealPaths) -> BrokerContext:
    """The real seams. Built only by main(); nothing here is consulted from the environment."""
    from . import scan as scan_mod  # lazy: scan imports this module
    from . import vm_runner as _vmr  # noqa: F841 (imported for monkeypatch visibility in tests)

    env = sysenv.clean_env(sysenv.home_of_euid())
    try:
        cand_uid = pwd.getpwnam(CANDIDATE_USER).pw_uid
        eval_uid = pwd.getpwnam(EVAL_USER).pw_uid
    except KeyError:
        raise SealRefusal("SEAL_NOT_CONFIGURED") from None
    age = str(paths.broker_bin / "age")
    keygen = str(paths.broker_bin / "age-keygen")
    age_key = str(paths.keys_dir / "age.key")
    wrapper = str(paths.broker_bin / "zurvan-candidate-run")
    # The candidate wrapper is used ONLY for the read-denial probe (seal-check), never to execute candidate
    # code: execution is the VM (Rev 3 #1). There is deliberately no `run` subcommand invocation here.
    sudo = ["/usr/bin/sudo", "-n", "-u", CANDIDATE_USER, wrapper]

    def decrypt(path):
        try:
            done = _broker_run_tool([age, "-d", "-i", age_key, str(path)], env)
        except (OSError, subprocess.SubprocessError):
            raise SealRefusal("MANIFEST_INVALID") from None
        if done.returncode != 0:
            raise SealRefusal("MANIFEST_INVALID")
        return done.stdout

    def encrypt(plain, dest):
        try:
            rec = _broker_run_tool([keygen, "-y", age_key], env)
            recipient = rec.stdout.decode("ascii").strip()
            if rec.returncode != 0 or not recipient.startswith("age1"):
                raise SealRefusal("INTERNAL")
            done = _broker_run_tool([age, "-r", recipient, "-o", str(dest)], env, data=plain)
        except SealRefusal:
            raise
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            raise SealRefusal("INTERNAL") from None
        if done.returncode != 0:
            raise SealRefusal("INTERNAL")
        os.chmod(str(dest), 0o600)

    def launch(run_id):
        return broker_vm_launch(run_id, paths)   # the ONLY candidate executor: a disposable VM

    def wipe(run_id):
        broker_vm_wipe(run_id, paths)            # destroy the guest; never raises (sweep then checks)

    def probe():
        try:
            done = _broker_run_tool(sudo + ["probe"], env)
            got = json.loads(done.stdout.decode("ascii"))
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeDecodeError):
            return {}
        return got if done.returncode == 0 and isinstance(got, dict) else {}

    def tmutil(path):
        try:
            done = _broker_run_tool(["/usr/bin/tmutil", "isexcluded", str(path)], env)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0 and b"[Excluded]" in done.stdout

    def agent_present():
        from .render import SCAN_LABEL
        return (paths.root / "Library" / "LaunchDaemons" / (SCAN_LABEL + ".plist")).is_file()

    return BrokerContext(
        spec_path=Path(__file__).resolve().parents[2] / "eval" / "z1b" / "spec.json",
        decrypt=decrypt, encrypt=encrypt, launch_candidate=launch, wipe=wipe, probe=probe,
        tmutil_isexcluded=tmutil,
        now=lambda: datetime.datetime.now(datetime.timezone.utc),
        sudo_uid=sysenv.sudo_uid,
        sweep_leftovers=lambda: (sweep.find_candidate_leftovers(
            paths, cand_uid, roots=sweep.sweep_roots(paths, cand_uid)) + broker_vm_leftovers(paths)),
        sweep_processes=lambda: sweep.candidate_processes(cand_uid),
        runtime_sha256=lambda: broker_runtime_sha256(paths),
        rss_mb=broker_rss_mb,
        scan=scan_mod.scan_run, reseal=scan_mod.reseal_after_burn,
        is_tty=lambda: sys.stdin.isatty(),
        scan_agent_present=agent_present,
        git_env=env, root_uid=0, eval_uid=eval_uid, candidate_uid=cand_uid)


# ================================================================ small sealed helpers

def broker_file_sha256(path: Path) -> str:
    """Whole-file sha256 through one O_NOFOLLOW descriptor; any problem is MANIFEST_INVALID."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SealRefusal("MANIFEST_INVALID") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SealRefusal("MANIFEST_INVALID")
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                return h.hexdigest()
            h.update(chunk)
    except OSError:
        raise SealRefusal("MANIFEST_INVALID") from None
    finally:
        os.close(fd)


def broker_read_hex_file(path: Path, pattern):
    """The single hex token in a small installed metadata file, or None."""
    try:
        text = Path(path).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    text = text.strip()
    return text if pattern.fullmatch(text) else None


def broker_installed_sha(paths: SealPaths):
    return broker_read_hex_file(paths.installed_sha_file, _HEX40)


def broker_log_error(paths: SealPaths, text: str) -> None:
    """Append a traceback to runs/errors.log (0600). The ONLY place unexpected-exception detail goes."""
    try:
        paths.runs_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(paths.runs_dir / "errors.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, text.encode("utf-8", "replace") + b"\n")
        finally:
            os.close(fd)
    except OSError:
        pass


def broker_today(ctx) -> str:
    return ctx.now().strftime("%Y-%m-%d")


def broker_iso_now(ctx) -> str:
    return ctx.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _broker_no_dups(pairs):
    out = {}
    for key, val in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = val
    return out


def _broker_no_const(_name):
    raise ValueError("non-finite constant")


def broker_parse_items(raw, spec) -> list:
    """A sealed set's plaintext: canonical JSONL, one item per line, every item schema-valid, ids unique.
    Returns the items sorted by id. Any defect is MANIFEST_INVALID."""
    try:
        text = raw.decode("utf-8")
        items, seen = [], set()
        for line in text.split("\n"):
            if not line:
                continue
            item = json.loads(line, object_pairs_hook=_broker_no_dups, parse_constant=_broker_no_const)
            validate_item(item, spec)
            if item["id"] in seen:
                raise ValueError("duplicate id")
            seen.add(item["id"])
            items.append(item)
    except (ValueError, UnicodeDecodeError, RecursionError, AttributeError, TypeError):
        raise SealRefusal("MANIFEST_INVALID") from None
    return sorted(items, key=lambda i: i["id"])


def broker_dump_items(items) -> bytes:
    """Inverse of broker_parse_items: canonical JSONL sorted by id."""
    lines = [json.dumps(i, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
             for i in sorted(items, key=lambda i: i["id"])]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("ascii")


def broker_record_review(paths: SealPaths, kind: str, item_ids: list, now) -> None:
    """A NON-blocking, non-destructive review note (e.g. a SUSPECT target association, #13): a file the
    operator can read, but NOT a ledger incident, so it never burns an item and never blocks the next run.
    Best-effort: a write failure is swallowed, since nothing depends on the note for correctness."""
    try:
        paths.incidents_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        body = json.dumps({"kind": kind, "verdict": "suspect", "n_items": len(item_ids),
                           "item_ids": sorted(item_ids),
                           "created": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, sort_keys=True) + "\n"
        fd = os.open(str(paths.incidents_dir / ("review-%s-%s.json" % (kind, stamp))),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, body.encode("ascii"))
        finally:
            os.close(fd)
    except OSError:
        pass


def broker_record_incident(paths: SealPaths, ledger: Ledger, kind: str, item_ids: list, now) -> str:
    """Ledger incident + a private incidents/<id>.json the operator reads to acknowledge it."""
    incident_id = ledger.record_incident(kind, list(item_ids))
    try:
        paths.incidents_dir.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"incident_id": incident_id, "kind": kind, "n_items": len(item_ids),
                           "created": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, sort_keys=True) + "\n"
        fd = os.open(str(paths.incidents_dir / (incident_id + ".json")),
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, body.encode("ascii"))
        finally:
            os.close(fd)
    except OSError:
        pass  # the ledger entry is the authority; the file is a convenience
    return incident_id


# ================================================================ manifest

def broker_verified_manifest(paths: SealPaths, ledger: Ledger, *, record: bool = True):
    """Verify the SEALED manifest (signature, closed schema, rollback, same-sequence replay).
    Returns (manifest, whole_file_sha256). Records manifest_seen when the sequence is new."""
    signers = paths.keys_dir / "allowed_signers"
    mf = sealed_store.manifest(paths)
    sig = sealed_store.manifest_sig(paths)
    before = manifest_mod.suite_manifest_sha256(mf)
    m = manifest_mod.verify(mf, sig, signers)
    if manifest_mod.suite_manifest_sha256(mf) != before:  # changed during verification
        raise SealRefusal("MANIFEST_INVALID")
    manifest_mod.check_sequence(m, ledger.last_manifest_sequence())
    last = None
    for entry in ledger.entries():
        if entry["kind"] == "manifest_seen":
            last = entry
    if last is not None and last["sequence"] == m["sequence"] and last["manifest_sha256"] != before:
        raise SealRefusal("MANIFEST_INVALID")  # a different manifest under an already-seen sequence
    if record and (last is None or last["sequence"] < m["sequence"]):
        ledger.append({"kind": "manifest_seen", "sequence": m["sequence"], "manifest_sha256": before})
    return m, before


# ================================================================ declaration

def _broker_number(value, lo, hi) -> bool:
    return (type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi)


def broker_check_declaration(decl, spec, *, suite_id: str) -> None:
    """Predeclaration (spec s5.4 / canonical s9.5): the five fields, the suite binding and the role (R20/R24).

    The commit binding is the commit itself: the declaration is read from the evaluated commit, so it
    cannot name that commit (a file cannot contain the hash of the commit that holds it) and has no
    candidate_commit field. Changing a declaration needs a new commit, hence a new run key and a new charge.
    """
    bad = SealRefusal("DECLARATION_INVALID")
    if type(decl) is not dict:
        raise bad
    required = spec["predeclaration_schema"]["required"]
    if set(decl) != set(required):
        raise bad
    if decl["suite_id"] != suite_id or decl["role"] not in ("baseline", "candidate") or type(decl["role"]) is not str:
        raise bad
    if decl["primary_metric"] not in _METRICS or type(decl["primary_metric"]) is not str:
        raise bad
    crit = decl["critical_strata"]
    if (type(crit) is not list or not crit or len(set(crit)) != len(crit)
            or not all(type(s) is str and s in STRATA for s in crit)):
        raise bad
    margins = decl["non_inferiority_margins"]
    if (type(margins) is not dict or set(margins) != {"critical", "overall"}
            or not all(_broker_number(v, 0, 1) for v in margins.values())):
        raise bad
    target = decl["improvement_target"]
    if (type(target) is not dict or set(target) != {"metric", "min_delta"}
            or target["metric"] not in _METRICS or type(target["metric"]) is not str
            or not _broker_number(target["min_delta"], 0, 1)):
        raise bad
    budgets = decl["resource_budgets"]
    if type(budgets) is not dict or set(budgets) != {"latency_p95_ms", "peak_rss_mb"}:
        raise bad
    for key, floor in (("latency_p95_ms", 10), ("peak_rss_mb", 1)):
        if type(budgets[key]) is not int or budgets[key] < floor:
            raise bad
    # #12 (canonical s9.5): the resource-only promotion route. null disables it (quality-improvement route
    # only); otherwise a predeclared resource metric must improve by at least `min_delta` (a fraction of the
    # baseline) while quality stays non-inferior.
    ri = decl["resource_improvement"]
    if ri is not None:
        if (type(ri) is not dict or set(ri) != {"metric", "min_delta"}
                or ri["metric"] not in ("latency_p95_ms", "peak_rss_mb") or type(ri["metric"]) is not str
                or not (_broker_number(ri["min_delta"], 0, 1) and ri["min_delta"] > 0)):
            raise bad


def _broker_git(args, env, git_dir):
    return intake._intake_git(args, env, git_dir)


def broker_declaration_digest(decl: dict) -> str:
    """Canonical digest of a predeclaration (Rev 3 P1 #9), bound into the run key, the result and the
    promotion record so a cached result can never be served under a different declaration."""
    body = json.dumps(decl, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(body.encode("ascii")).hexdigest()


def broker_load_declaration(paths: SealPaths, ctx, staged: Path, commit: str, decl_path: str) -> dict:
    """Read `decl_path` out of `commit` in the staged (already verified) bundle (R20/R24). A regular
    100644 blob, at most MAX_DECLARATION_BYTES, strict JSON without duplicate keys or NaN/Infinity."""
    bad = SealRefusal("DECLARATION_INVALID")
    work = None
    try:
        intake._intake_check_env(ctx.git_env)
        work = tempfile.mkdtemp(prefix=".decl-", dir=str(paths.stage_dir))
        repo = os.path.join(work, "r.git")
        env = ctx.git_env
        if _broker_git(["init", "--bare", "--quiet", "--template=", repo], env, None).returncode != 0:
            raise bad
        if _broker_git(["bundle", "unbundle", str(staged)], env, repo).returncode != 0:
            raise bad
        spec_arg = commit + ":" + decl_path
        tree = _broker_git(["ls-tree", commit, "--", decl_path], env, repo)
        if tree.returncode != 0 or not tree.stdout.startswith(b"100644 blob "):
            raise bad
        size = _broker_git(["cat-file", "-s", spec_arg], env, repo)
        if size.returncode != 0 or not size.stdout.strip().isdigit() or int(size.stdout) > MAX_DECLARATION_BYTES:
            raise bad
        blob = _broker_git(["cat-file", "blob", spec_arg], env, repo)
        if blob.returncode != 0:
            raise bad
        return json.loads(blob.stdout.decode("utf-8"), object_pairs_hook=_broker_no_dups,
                          parse_constant=_broker_no_const)
    except SealRefusal:
        raise
    except Exception:  # noqa: BLE001 - nothing but a code crosses this boundary
        raise bad from None
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


# ================================================================ candidate session

def _broker_fail() -> SealRefusal:
    return SealRefusal("CANDIDATE_FAILED")


def _broker_write_all(fd: int, data: bytes, deadline: float) -> None:
    view = memoryview(data)
    while view:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _broker_fail()
        _r, writable, _x = select.select([], [fd], [], left)
        if not writable:
            raise _broker_fail()
        try:
            written = os.write(fd, view)
        except BlockingIOError:
            continue
        except OSError:
            raise _broker_fail() from None
        view = view[written:]


def _broker_read_line(fd: int, buf: bytearray, deadline: float) -> bytes:
    """One newline-terminated line from the candidate. EOF, oversize or timeout is a violation."""
    while True:
        cut = buf.find(b"\n")
        if cut >= 0:
            line = bytes(buf[:cut + 1])
            del buf[:cut + 1]
            if buf:  # the candidate sent more than it was asked for
                raise _broker_fail()
            return line
        if len(buf) > protocol.MAX_LINE:
            raise _broker_fail()
        left = deadline - time.monotonic()
        if left <= 0:
            raise _broker_fail()
        readable, _w, _x = select.select([fd], [], [], left)
        if not readable:
            raise _broker_fail()
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            raise _broker_fail() from None
        if not chunk:
            raise _broker_fail()
        buf.extend(chunk)


def broker_drive(proc, items, spec, sent: list):
    """Send one query at a time, decode each answer strictly. Returns (results per item, latencies ms).
    `sent` is appended BEFORE each send so a burn covers exactly what the candidate may have seen."""
    policy = spec["run_policy"]
    qt, max_results = policy["query_timeout_s"], policy["max_results"]
    run_deadline = time.monotonic() + policy["run_timeout_s"]
    in_fd, out_fd = proc.stdin.fileno(), proc.stdout.fileno()
    os.set_blocking(in_fd, False)
    buf = bytearray()
    results, latencies = [], []
    for qid, item in enumerate(items):
        try:
            line = protocol.encode_query(qid, item["query"])
        except ValueError:
            raise _broker_fail() from None
        sent.append(item["id"])
        t0 = time.monotonic()
        limit = min(run_deadline, t0 + (qt * STARTUP_FACTOR if qid == 0 else qt))
        _broker_write_all(in_fd, line, limit)
        raw = _broker_read_line(out_fd, buf, limit)
        latencies.append((time.monotonic() - t0) * 1000.0)
        try:
            results.append(protocol.decode_response(raw, qid, max_results)["results"])
        except ValueError:
            raise _broker_fail() from None
    # a clean end: close the channel, expect EOF with no extra bytes, then a zero exit
    try:
        proc.stdin.close()
    except OSError:
        pass
    end = time.monotonic() + FINISH_TIMEOUT
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise _broker_fail()
        readable, _w, _x = select.select([out_fd], [], [], left)
        if not readable:
            raise _broker_fail()
        chunk = os.read(out_fd, 65536)
        if not chunk:
            break
        raise _broker_fail()  # unsolicited output after the last answer
    try:
        rc = proc.wait(timeout=FINISH_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _broker_fail() from None
    if rc != 0:
        raise _broker_fail()
    return results, latencies


def broker_kill(proc) -> None:
    if proc is None:
        return
    for fn in (proc.kill,):
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass
    for pipe in (getattr(proc, "stdin", None), getattr(proc, "stdout", None)):
        try:
            if pipe is not None:
                pipe.close()
        except Exception:  # noqa: BLE001
            pass


def broker_post_run_violation(ctx, run_id: str) -> bool:
    """Wipe the candidate's scratch, then sweep. Any leftover, any live process, or ANY failure to find out
    is a violation (assume the worst)."""
    try:
        ctx.wipe(run_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(ctx.sweep_leftovers()) or int(ctx.sweep_processes()) > 0
    except Exception:  # noqa: BLE001
        return True


# ================================================================ metrics and promotion

def _broker_percentile(sorted_vals: list, p: float) -> float:
    idx = max(0, int(math.ceil(p * len(sorted_vals))) - 1)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def broker_latency_block(latencies: list) -> dict:
    lat = sorted(latencies)
    out = {}
    for key, p in (("p50", 0.5), ("p95", 0.95)):
        ms = int(round(_broker_percentile(lat, p) / 10.0)) * 10
        out[key] = min(ms, 3600000)
    return out


def broker_row_values(rows: list, spec) -> dict:
    """id -> {metric: value} for the scored rows (abstain and TARGET_MISSING rows have no values)."""
    out = {}
    for row in rows:
        vec = grade.metric_vectors([row], spec)
        if vec["mrr"]:
            out[row["id"]] = {m: vec[m][0] for m in _METRICS}
    return out


def broker_ci(vals: list, base, *, resamples: int, seed: int):
    if not vals:
        return (0.0, 0.0)
    return grade.paired_bootstrap_ci(vals, base, resamples=resamples, seed=seed)


def broker_baseline(ledger: Ledger, paths: SealPaths, suite_id: str):
    """The comparison point for a declared candidate (R25): the most recent PROMOTED holdout run of this
    suite (verdict PASS); before any promotion, the most recent declared BASELINE run (per_stratum present,
    promotion null). (result, rows), or (None, None) when neither exists. There is no all-zero baseline."""
    promoted = baseline = None
    for entry in ledger.entries():
        if entry["kind"] != "completed":
            continue
        res = entry["result"]
        if res.get("suite") != "holdout" or res.get("suite_id") != suite_id:
            continue
        promo = res.get("promotion")
        if isinstance(promo, dict) and promo.get("verdict") == "PASS":
            promoted = entry
        elif promo is None and res.get("per_stratum") is not None:
            baseline = entry
    last = promoted if promoted is not None else baseline
    if last is None:
        return None, None
    try:
        with open(str(paths.runs_dir / "rows" / (last["run_id"] + ".json")), "rb") as fh:
            rows = json.loads(fh.read().decode("ascii"))["rows"]
    except (OSError, ValueError, KeyError, UnicodeDecodeError):
        raise SealRefusal("LEDGER_INVALID") from None
    return last["result"], rows


def _broker_resource_value(res: dict, metric: str):
    return res["latency_ms"]["p95"] if metric == "latency_p95_ms" else res["peak_rss_mb"]


def broker_verdict(decl: dict, rows: list, base_rows, result: dict, spec, seed: int, *, base_result=None) -> dict:
    """Spec s9.5 evaluated from the predeclaration. Every interval is a paired bootstrap over the items
    both runs scored (a lower bound, not a point estimate: small strata report uncertainty honestly).
    With no baseline (R25) nothing can be shown non-inferior or improved: both quality rules are False and
    the verdict is FAIL. There is no all-zero baseline.

    PASS needs the critical + overall non-inferiority rules AND the resource budget AND at least one
    promotion route (s9.5 rule 3): the QUALITY route (a predeclared target metric improves beyond noise)
    OR, when `resource_improvement` was predeclared, the RESOURCE route (quality stays non-inferior and the
    named resource metric improves by at least the declared fraction of the baseline). `resource_improved`
    is reported for transparency; it only grants PASS through the resource route.
    """
    budgets = decl["resource_budgets"]
    resource_ok = (result["latency_ms"]["p95"] <= budgets["latency_p95_ms"]
                   and result["peak_rss_mb"] <= budgets["peak_rss_mb"])
    ri = decl["resource_improvement"]
    resource_improved = False
    if ri is not None and base_result is not None:
        base_v = _broker_resource_value(base_result, ri["metric"])
        cand_v = _broker_resource_value(result, ri["metric"])
        # a material DECREASE: the candidate uses at most (1 - min_delta) of the baseline's resource
        resource_improved = base_v > 0 and cand_v <= base_v * (1.0 - ri["min_delta"])
    if base_rows is None:
        rules = {"non_inferiority_critical": False, "non_inferiority_overall": False, "improvement": False,
                 "resource_budget": resource_ok, "resource_improved": resource_improved}
        return {"declared": True, "rules": rules, "verdict": "FAIL"}
    resamples = spec["run_policy"]["bootstrap_resamples"]
    cand_vals = broker_row_values(rows, spec)
    base_vals = broker_row_values(base_rows, spec)
    strata_of = {r["id"]: r["stratum"] for r in rows}

    def diff_lo(metric, stratum=None):
        cand, base = [], []
        for item_id in sorted(cand_vals):
            if stratum is not None and strata_of[item_id] != stratum:
                continue
            if item_id in base_vals:
                cand.append(cand_vals[item_id][metric])
                base.append(base_vals[item_id][metric])
        if not cand:
            return -math.inf
        return grade.paired_bootstrap_ci(cand, base, resamples=resamples, seed=seed)[0]

    primary = decl["primary_metric"]
    margins = decl["non_inferiority_margins"]
    target = decl["improvement_target"]
    rules = {
        "non_inferiority_critical": all(diff_lo(primary, s) >= -margins["critical"]
                                        for s in decl["critical_strata"]),
        "non_inferiority_overall": diff_lo(primary) >= -margins["overall"],
        "improvement": (lambda lo: lo > 0 and lo >= target["min_delta"])(diff_lo(target["metric"])),
        "resource_budget": resource_ok,
        "resource_improved": resource_improved,
    }
    quality_non_inferior = rules["non_inferiority_critical"] and rules["non_inferiority_overall"]
    route = rules["improvement"] or rules["resource_improved"]   # s9.5 rule 3: quality OR resource route
    verdict = "PASS" if (quality_non_inferior and rules["resource_budget"] and route) else "FAIL"
    return {"declared": True, "rules": rules, "verdict": verdict}


def broker_holdout_passed(ledger: Ledger, commit: str, suite_id: str) -> bool:
    for entry in ledger.entries():
        if entry["kind"] != "completed":
            continue
        res = entry["result"]
        promo = res.get("promotion")
        if (res.get("suite") == "holdout" and res.get("candidate_commit") == commit
                and res.get("suite_id") == suite_id and isinstance(promo, dict)
                and promo.get("verdict") == "PASS"):
            return True
    return False


def broker_reserve_budget_id(suite_id: str, commit: str) -> str:
    """Reserve runs have their own budget (1 per commit per suite): a derived budget key."""
    return hashlib.sha256(("reserve\0" + suite_id + "\0" + commit).encode("ascii")).hexdigest()[:16]


def broker_write_promotion_attestation(paths: SealPaths, result: dict, m: dict, run_id: str, now) -> Path:
    """#16: when a holdout run's promotion verdict is PASS, emit an EXPORTABLE broker-signed attestation
    (the ledger stays the authoritative anchor, R22). Signed with the eval key under a DISTINCT namespace.
    Returns the attestation path, or None when the run is not a PASS promotion."""
    promo = result.get("promotion")
    if not (isinstance(promo, dict) and promo.get("verdict") == "PASS"):
        return None
    folder = paths.runs_dir / "promotions"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(str(folder), 0o700)
    att = promotion.build_attestation(
        candidate_commit=result["candidate_commit"], suite=result["suite"], suite_id=result["suite_id"],
        suite_manifest_sha256=result["suite_manifest_sha256"], run_id=run_id,
        result_sha256=promotion.result_sha256(result), evaluator_version=result["evaluator_version"],
        sequence=m["sequence"], created=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    dest = folder / (run_id + ".json")
    promotion.write(dest, att)
    promotion.sign(dest, paths.keys_dir / "signing_ed25519")
    return dest


def broker_write_rows(paths: SealPaths, run_id: str, rows: list) -> Path:
    """Opaque ids, ranks and flags only; NEVER query or target text. 0600, never overwritten."""
    folder = paths.runs_dir / "rows"
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(str(folder), 0o700)
    dest = folder / (run_id + ".json")
    body = json.dumps({"run_id": run_id, "rows": rows}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, body.encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return dest


# ================================================================ run

def _broker_fail_unresolved(ledger: Ledger, run_id: str, code: str) -> None:
    try:
        if run_id in ledger.unresolved_runs():
            ledger.fail_run(run_id, code)
    except SealRefusal:
        pass


def broker_run(args: dict, paths: SealPaths, ctx: BrokerContext) -> dict:
    spec = load_spec(ctx.spec_path)
    policy = spec["run_policy"]
    sha = broker_installed_sha(paths)
    if sha is None:
        raise SealRefusal("SEAL_NOT_CONFIGURED")
    expected_uid = ctx.sudo_uid()
    suite, commit = args["suite"], args["commit"]
    declared = args["declare"] is not None
    staged = None
    led = Ledger(paths.runs_dir)
    try:
        with led.lock():
            # 1. a crashed or still-reserved run is failed AND charged before anything else
            for rid in led.unresolved_runs():
                led.fail_run(rid, "INTERNAL")
            # 2. an unacknowledged incident refuses everything
            if led.open_incidents():
                raise SealRefusal("INTEGRITY_INCIDENT")
            # 3. leak scan (takes no input from the dev side)
            ctx.scan(paths, ctx, led)
            if led.open_incidents():
                raise SealRefusal("INTEGRITY_INCIDENT")
            # 4-6. sealed identity of what is about to be measured
            m, smsha = broker_verified_manifest(paths, led)
            corpus_sha = corpus_sha256(paths.corpus_dir)
            if corpus_sha != m["corpus_sha256"]:
                raise SealRefusal("CORPUS_MISMATCH")
            set_path = sealed_store.age_path(paths, suite)
            if broker_file_sha256(set_path) != m[suite + "_age_sha256"]:
                raise SealRefusal("MANIFEST_INVALID")
            # 7. bundle: staged through one validated fd, then verified (provisional id; renamed once reserved)
            provisional = os.urandom(8).hex()
            staged = intake.stage_bundle(args["bundle"], paths, expected_uid=expected_uid, run_id=provisional)
            intake.verify_bundle(staged, commit, env=ctx.git_env)
            # 8. declaration. Rev 3 P1 #8: HOLDOUT is only for a declared promotion candidate (canonical §9).
            decl = None
            decl_sha = ZERO_SHA64
            if suite == "holdout" and not declared:
                raise SealRefusal("DECLARATION_REQUIRED")
            if declared:
                decl = broker_load_declaration(paths, ctx, staged, commit, args["declare"])
                broker_check_declaration(decl, spec, suite_id=m["suite_id"])
                decl_sha = broker_declaration_digest(decl)
            # 9. RESERVE is reachable only as a final acceptance of a candidate that passed HOLDOUT
            if suite == "reserve":
                if not args["final_acceptance"] or not broker_holdout_passed(led, commit, m["suite_id"]):
                    raise SealRefusal("RESERVE_NOT_ELIGIBLE")
            # 10. cache or charge
            key = run_key(commit, corpus_sha, smsha, grade.EVALUATOR_VERSION, suite, ctx.runtime_sha256(),
                          decl_sha)
            if suite == "holdout":
                budget_id, budget_n = m["suite_id"], policy["holdout_budget"]
            else:
                budget_id, budget_n = broker_reserve_budget_id(m["suite_id"], commit), 1
            cached = led.cached(key)
            if cached is not None:
                return dict(cached, cached=True, budget_remaining=led.budget_remaining(budget_id, budget_n))
            # 11. decrypt in memory; a burned item sitting in a set is refused BEFORE any charge
            items = broker_parse_items(ctx.decrypt(set_path), spec)
            burned = led.burned_ids()
            if not items or any(i["id"] in burned for i in items):
                raise SealRefusal("MANIFEST_INVALID")
            baseline_result, baseline_rows = (broker_baseline(led, paths, m["suite_id"]) if declared
                                              else (None, None))
            pages = corpus_pages(paths.corpus_dir)
            run_id = led.reserve_run(key, budget_id, budget_n)
            final_stage = paths.stage_dir / (run_id + ".bundle")
            os.rename(str(staged), str(final_stage))
            staged = final_stage
            return _broker_run_charged(args, paths, ctx, led, spec, items, pages, m, smsha, corpus_sha,
                                       sha, key, run_id, budget_id, budget_n, decl, baseline_rows,
                                       baseline_result)
    finally:
        if staged is not None:
            try:
                os.unlink(str(staged))
            except OSError:
                pass


def _broker_run_charged(args, paths, ctx, led, spec, items, pages, m, smsha, corpus_sha, sha, key, run_id,
                        budget_id, budget_n, decl, baseline_rows, baseline_result) -> dict:
    """Everything after the budget is charged. The run ends completed or failed, never unresolved."""
    policy = spec["run_policy"]
    declared = decl is not None
    try:
        sent, proc, failure, results, latencies = [], None, None, None, None
        try:
            proc = ctx.launch_candidate(run_id)
            results, latencies = broker_drive(proc, items, spec, sent)
        except Exception:  # noqa: BLE001 - any candidate-side failure, however it surfaces
            failure = "CANDIDATE_FAILED"
        finally:
            broker_kill(proc)
        if broker_post_run_violation(ctx, run_id):
            led.burn_items(list(sent), "sandbox_violation", broker_today(ctx))
            broker_record_incident(paths, led, "sandbox_violation", list(sent), ctx.now())
            try:
                ctx.reseal(paths, ctx, led)
            except Exception:  # noqa: BLE001 - the violation outranks a failed reseal; scan self-heals later
                broker_log_error(paths, traceback.format_exc())
            led.fail_run(run_id, "SANDBOX_VIOLATION")
            raise SealRefusal("SANDBOX_VIOLATION")
        if failure is not None:
            led.fail_run(run_id, failure)
            raise SealRefusal(failure)
        # grade (the candidate's own abstain flag is ignored: abstention is decided from the scores)
        rows = []
        for item, res in zip(items, results):
            row = grade.grade_item(item, res, spec, corpus_pages=pages)
            row["id"] = item["id"]
            rows.append(row)
        agg = grade.aggregate(rows, spec, per_stratum=declared)
        seed = int(key[:16], 16)
        resamples = policy["bootstrap_resamples"]
        vectors = grade.metric_vectors(rows, spec)
        ci = {name: list(broker_ci(vectors[name], None, resamples=resamples, seed=seed)) for name in _METRICS}
        result = {
            "ok": True, "schema": RESULT_SCHEMA_ID, "suite": args["suite"], "suite_id": m["suite_id"],
            "n_items": agg["n_items"], "corpus_sha256": corpus_sha, "suite_manifest_sha256": smsha,
            "evaluator_version": grade.EVALUATOR_VERSION, "broker_sha": sha,
            "candidate_commit": args["commit"],
            "metrics": dict(agg["metrics"], ci=ci),
            "per_stratum": agg["per_stratum"] if declared else None,
            "target_missing": agg["target_missing"], "abstain": agg["abstain"],
            "latency_ms": broker_latency_block(latencies), "peak_rss_mb": int(ctx.rss_mb()),
            "seal_valid": True, "cached": False,
            "budget_remaining": led.budget_remaining(budget_id, budget_n),
            "promotion": None,
        }
        if declared and decl["role"] == "candidate":  # R25: a baseline run is measured, never judged
            result["promotion"] = broker_verdict(decl, rows, baseline_rows, result, spec, seed,
                                                 base_result=baseline_result)
        serialize_result(result)  # validate BEFORE anything is committed to the ledger
        rows_path = broker_write_rows(paths, run_id, rows)
        led.complete_run(run_id, result, rows_path)
        # #16: a PASS holdout promotion also gets an exportable broker-signed attestation (ledger stays
        # authoritative). Best-effort: a signing failure must not undo a recorded, verified run.
        try:
            broker_write_promotion_attestation(paths, result, m, run_id, ctx.now())
        except Exception:  # noqa: BLE001
            broker_log_error(paths, traceback.format_exc())
        return result
    except SealRefusal:
        _broker_fail_unresolved(led, run_id, "INTERNAL")
        raise
    except Exception:
        _broker_fail_unresolved(led, run_id, "INTERNAL")
        raise


# ================================================================ status, scan, ack

def broker_status(paths: SealPaths, ctx: BrokerContext, led: Ledger = None) -> dict:
    """Read-only summary. Never refuses on staleness (the client compares installed_tree with the repo).
    `led` is passed by callers that already hold the ledger lock (a second Ledger object would deadlock)."""
    sha = broker_installed_sha(paths)
    tree = broker_read_hex_file(paths.installed_tree_file, _HEX40)
    doc = {"ok": True, "schema": STATUS_SCHEMA_ID, "configured": False, "broker_sha": sha or ZERO_SHA,
           "installed_sha": sha, "installed_tree": tree, "stale": False, "manifest_valid": False,
           "corpus_match": False, "budget_remaining": 0, "open_incidents": 0, "holdout_n": 0,
           "reserve_n": 0, "suite_id": None}
    if sha is None or not sealed_store.manifest(paths).exists():
        return doc
    doc["configured"] = True
    spec = load_spec(ctx.spec_path)
    led = led if led is not None else Ledger(paths.runs_dir)
    doc["open_incidents"] = len(led.open_incidents())
    try:
        m, _sha = broker_verified_manifest(paths, led, record=False)
    except SealRefusal:
        return doc
    doc["manifest_valid"] = True
    doc["suite_id"] = m["suite_id"]
    doc["holdout_n"] = sum(m["strata_counts"].get("holdout", {}).values())
    doc["reserve_n"] = sum(m["strata_counts"].get("reserve", {}).values())
    doc["budget_remaining"] = led.budget_remaining(m["suite_id"], spec["run_policy"]["holdout_budget"])
    try:
        doc["corpus_match"] = corpus_sha256(paths.corpus_dir) == m["corpus_sha256"]
    except SealRefusal:
        doc["corpus_match"] = False
    return doc


def broker_scan(paths: SealPaths, ctx: BrokerContext) -> dict:
    """The `scan` subcommand (launchd runs it): heal a pending reseal, scan, report status.
    A hit or an unreadable target is a refusal, exactly as in `run`."""
    led = Ledger(paths.runs_dir)
    with led.lock():
        for rid in led.unresolved_runs():
            led.fail_run(rid, "INTERNAL")
        ctx.reseal(paths, ctx, led)
        ctx.scan(paths, ctx, led)
        if led.open_incidents():
            raise SealRefusal("INTEGRITY_INCIDENT")
        return broker_status(paths, ctx, led)


def broker_ack(args: dict, paths: SealPaths, ctx: BrokerContext) -> dict:
    """Operator-only: needs a terminal AND the incident id (which the dev side never sees)."""
    if not ctx.is_tty():
        raise SealRefusal("BAD_ARGS")
    led = Ledger(paths.runs_dir)
    with led.lock():
        if args["incident_id"] not in [e["incident_id"] for e in led.open_incidents()]:
            raise SealRefusal("BAD_ARGS")
        led.acknowledge_incident(args["incident_id"])
        return broker_status(paths, ctx, led)


# ================================================================ seal-check

def _broker_entries(root: Path):
    """root and every descendant, lstat only (a symlink is reported, never followed)."""
    yield root, os.lstat(str(root))
    for top, dirnames, filenames in os.walk(str(root), topdown=True, followlinks=False):
        for name in sorted(dirnames + filenames):
            full = os.path.join(top, name)
            yield Path(full), os.lstat(full)


def broker_tree_id(directory: Path) -> str:
    """git tree id of a directory, computed from file bytes. Installed files lose their exec bit (0444), so
    the mode of each entry is the one the repository records (EXEC_FILES), pinned by a test that compares
    this function with `git rev-parse HEAD:scripts/eval_seal`."""
    entries = []
    for name in os.listdir(str(directory)):
        full = os.path.join(str(directory), name)
        st = os.lstat(full)
        if stat.S_ISDIR(st.st_mode):
            sub = broker_tree_id(Path(full))
            if sub == "4b825dc642cb6eb9a060e54bf8d69288fbee4904":  # git does not store empty trees
                continue
            entries.append((name.encode() + b"/", b"40000", name.encode(), bytes.fromhex(sub)))
        elif stat.S_ISREG(st.st_mode):
            with open(full, "rb") as fh:
                data = fh.read()
            blob = hashlib.sha1(b"blob %d\0" % len(data) + data).digest()
            mode = b"100755" if name in EXEC_FILES else b"100644"
            entries.append((name.encode(), mode, name.encode(), blob))
        else:
            raise ValueError("special file")
    entries.sort(key=lambda e: e[0])
    body = b"".join(mode + b" " + name + b"\0" + oid for _k, mode, name, oid in entries)
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def _broker_mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(str(path)).st_mode)


def _broker_private_dir(path: Path, uid: int, names=()) -> bool:
    st = os.lstat(str(path))
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or stat.S_IMODE(st.st_mode) & 0o077:
        return False
    for name in names:
        child = os.lstat(str(path / name))
        if not stat.S_ISREG(child.st_mode) or child.st_uid != uid or stat.S_IMODE(child.st_mode) & 0o077:
            return False
    return True


def _broker_check_broker_owned(paths, ctx) -> bool:
    return all(st.st_uid == ctx.root_uid for _p, st in _broker_entries(paths.broker_dir))


def _broker_check_broker_unwritable(paths, ctx) -> bool:
    for _p, st in _broker_entries(paths.broker_dir):
        if stat.S_ISLNK(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o022:
            return False
        if st.st_uid in (ctx.eval_uid, ctx.candidate_uid) and st.st_uid != ctx.root_uid:
            return False
    return True


def _broker_check_sha(paths, ctx) -> bool:
    sha = broker_installed_sha(paths)
    tree = broker_read_hex_file(paths.installed_tree_file, _HEX40)
    if sha is None or tree is None:
        return False
    return broker_tree_id(paths.broker_lib / "scripts" / "eval_seal") == tree


def _broker_check_sudoers(paths, ctx) -> bool:
    st = os.lstat(str(paths.sudoers_file))
    return (stat.S_ISREG(st.st_mode) and st.st_uid == ctx.root_uid and stat.S_IMODE(st.st_mode) == 0o440)


def _broker_check_pf(paths, ctx) -> bool:
    st = os.lstat(str(paths.pf_anchor))
    return (stat.S_ISREG(st.st_mode) and st.st_uid == ctx.root_uid and not stat.S_IMODE(st.st_mode) & 0o022)


def _broker_check_keys(paths, ctx) -> bool:
    return _broker_private_dir(paths.keys_dir, ctx.eval_uid, ("age.key", "signing_ed25519"))


def _broker_check_sets(paths, ctx) -> bool:
    return _broker_private_dir(paths.sets_dir, ctx.eval_uid)


def _broker_check_runs(paths, ctx) -> bool:
    return _broker_private_dir(paths.runs_dir, ctx.eval_uid)


def _broker_check_corpus(paths, ctx) -> bool:
    for _p, st in _broker_entries(paths.corpus_dir):
        if stat.S_ISLNK(st.st_mode) or st.st_uid != ctx.root_uid or stat.S_IMODE(st.st_mode) & 0o222:
            return False
        if stat.S_IMODE(st.st_mode) & 0o007:
            return False
    return True


def _broker_check_stage(paths, ctx) -> bool:
    st = os.lstat(str(paths.stage_dir))
    return stat.S_ISDIR(st.st_mode) and st.st_uid == ctx.eval_uid and not stat.S_IMODE(st.st_mode) & 0o027


def _broker_check_probe(paths, ctx) -> bool:
    from .candidate_run import SEALED_IDS
    got = ctx.probe()
    return (isinstance(got, dict) and set(got) == set(SEALED_IDS)
            and all(v in ("EACCES", "ENOENT") for v in got.values()))


def _broker_check_tm(paths, ctx) -> bool:
    return bool(ctx.tmutil_isexcluded(paths.keys_dir))


def _broker_check_tm_private(paths, ctx) -> bool:
    """R18: private/ holds plaintext authoring state; it must be excluded from Time Machine like keys/."""
    return bool(ctx.tmutil_isexcluded(paths.private_dir))


def _broker_check_drop(paths, ctx) -> bool:
    st = os.lstat(str(paths.drop_dir))
    return stat.S_ISDIR(st.st_mode) and (st.st_mode & 0o7777) == 0o1733 and st.st_uid == ctx.root_uid


def _broker_check_agent(paths, ctx) -> bool:
    return bool(ctx.scan_agent_present())


def _broker_check_pinned(paths, ctx) -> bool:
    lines = [ln for ln in paths.pinned_file.read_text(encoding="ascii").splitlines() if ln.strip()]
    if not lines:
        return False
    for line in lines:
        digest, _sep, path = line.partition("  ")
        if not _HEX64.fullmatch(digest) or not path:
            return False
        actual = paths.broker_bin / os.path.basename(path)
        if broker_file_sha256(actual) != digest:
            return False
    return True


def _broker_check_interpreter(paths, ctx) -> bool:
    shebang = (paths.broker_bin / "zurvan-broker").read_bytes().split(b"\n", 1)[0].decode("ascii")
    if not shebang.startswith("#!"):
        return False
    cur = os.path.realpath(shebang[2:].split()[0])
    top = os.path.realpath(str(paths.root))  # "/" for real; the tmp root in tests
    while True:
        st = os.stat(cur)
        if st.st_uid != ctx.root_uid or stat.S_IMODE(st.st_mode) & 0o022:
            return False
        parent = os.path.dirname(cur)
        if cur == top or parent == cur:
            return True
        cur = parent


def _broker_check_ledger(paths, ctx) -> bool:
    Ledger(paths.runs_dir).entries()
    return True


def _broker_check_manifest(paths, ctx) -> bool:
    broker_verified_manifest(paths, Ledger(paths.runs_dir), record=False)
    return True


_SEAL_CHECKS = (
    ("broker_root_owned", _broker_check_broker_owned),
    ("broker_not_writable", _broker_check_broker_unwritable),
    ("broker_sha_matches", _broker_check_sha),
    ("sudoers_valid", _broker_check_sudoers),
    ("pf_anchor_present", _broker_check_pf),
    ("keys_private", _broker_check_keys),
    ("sets_private", _broker_check_sets),
    ("runs_private", _broker_check_runs),
    ("corpus_read_only", _broker_check_corpus),
    ("stage_private", _broker_check_stage),
    ("candidate_cannot_read_sealed", _broker_check_probe),
    ("tm_excluded_keys", _broker_check_tm),
    ("drop_dir_mode", _broker_check_drop),
    ("scan_agent_present", _broker_check_agent),
    ("pinned_binaries_match", _broker_check_pinned),
    ("interpreter_root_owned", _broker_check_interpreter),
    ("ledger_valid", _broker_check_ledger),
    ("manifest_valid", _broker_check_manifest),
    ("tm_excluded_private", _broker_check_tm_private),
)


def broker_seal_check(paths: SealPaths, ctx: BrokerContext) -> dict:
    """Every check is a bool; a check that cannot run is False. dev_cannot_read_sealed is the CLIENT's half
    (the broker cannot test the dev identity) and is merged in on the dev side."""
    checks = {}
    for check_id, fn in _SEAL_CHECKS:
        try:
            checks[check_id] = fn(paths, ctx) is True
        except Exception:  # noqa: BLE001
            checks[check_id] = False
    return {"ok": all(checks.values()), "schema": SEALCHECK_SCHEMA_ID,
            "broker_sha": broker_installed_sha(paths) or ZERO_SHA, "checks": checks}


# ================================================================ entry point

def broker_main(argv=None, *, paths: SealPaths = None, ctx: BrokerContext = None) -> int:
    """Prints exactly one JSON document on stdout; stderr is never written. Exit 0 ok, 2 refusal, 3 INTERNAL.

    Python-level stderr, sys.excepthook and warnings.showwarning are replaced for the duration, so no
    library can write to the terminal the dev session reads. Unexpected exceptions go to runs/errors.log.
    """
    argv = sys.argv[1:] if argv is None else argv
    saved = (sys.stderr, sys.excepthook, warnings.showwarning)
    sink = open(os.devnull, "w")
    sys.stderr = sink
    sys.excepthook = lambda *_a: None
    warnings.showwarning = lambda *_a, **_k: None
    sha = ZERO_SHA
    try:
        try:
            paths = paths or SealPaths.real()
            sha = broker_installed_sha(paths) or ZERO_SHA
            args = broker_parse_argv(argv)
            ctx = ctx or production_context(paths)
            cmd = args["cmd"]
            rc = 0
            if cmd == "run":
                text = serialize_public_result(broker_run(args, paths, ctx))  # G20: projection only
            elif cmd == "status":
                text = serialize_status(broker_status(paths, ctx))
            elif cmd == "seal-check":
                doc = broker_seal_check(paths, ctx)
                text = serialize_sealcheck(doc)
                rc = 0 if doc["ok"] else 2
            elif cmd == "scan":
                text = serialize_status(broker_scan(paths, ctx))
            else:
                text = serialize_status(broker_ack(args, paths, ctx))
        except SealRefusal as exc:
            text = serialize_refusal(exc.code, sha)
            rc = 3 if exc.code == "INTERNAL" else 2
        except Exception:  # noqa: BLE001
            if paths is not None:
                broker_log_error(paths, traceback.format_exc())
            text = serialize_refusal("INTERNAL", sha)
            rc = 3
    finally:
        sys.stderr, sys.excepthook, warnings.showwarning = saved
        sink.close()
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    return rc


main = broker_main  # the installed wrapper does `from scripts.eval_seal.broker import main`
