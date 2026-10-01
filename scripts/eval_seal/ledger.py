"""Run ledger, budget, result cache and incidents (contract s3.6).

One append-only JSONL file, hash-chained: every line carries seq, prev_sha256 and
entry_sha256 (sha256 of the line's canonical JSON without that field). Verification is
total and strict: the file must end in a newline, every line must be byte-identical to
its canonical re-serialisation, the chain must be unbroken, every kind has a CLOSED field
set, and the run/incident state machine must be consistent. Any defect is
SealRefusal("LEDGER_INVALID") - there is no repair path in this module.

Budget is counted from "reserved" entries, written BEFORE the candidate starts and never
removed, so a crashed or failed run still costs one (fail closed). A cache hit appends
nothing and so costs nothing. Burn entries are append-only and burned_ids() is the union
of every burn ever written; the kind set has no inverse of "burn", and any hand-crafted
"unburn"-shaped line fails the closed schema.

Limit (documented, not hidden): a hash chain without an external anchor cannot detect
removal of whole lines from the END of the file, nor a tamper that also recomputes every
following hash. The ledger lives in the sealed identity's 0700 home; the chain defends
against accident and partial writes, the filesystem boundary defends against the dev
identity.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path

from .codes import ALL_CODES, SealRefusal

SCHEMA = "zurvan.z1b.ledger.v1"
GENESIS = "0" * 64
MAX_LINE_BYTES = 4 * 1024 * 1024
BURN_REASONS = ("canary_hit", "item_leak", "sandbox_violation", "operator")
SUITES = ("holdout", "reserve")

_HEX16 = re.compile(r"[0-9a-f]{16}")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX40 = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_ITEM = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_CHAIN = ("seq", "prev_sha256", "entry_sha256")
_KIND_FIELDS = {
    "reserved": ("run_id", "key", "suite_id"),
    "completed": ("run_id", "result", "rows_sha256"),
    "failed": ("run_id", "code"),
    "incident": ("incident_id", "incident_kind", "item_ids"),
    "ack": ("incident_id",),
    "burn": ("id", "reason", "date"),
    "manifest_seen": ("sequence", "manifest_sha256"),
}


def _bad():
    raise SealRefusal("LEDGER_INVALID")


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=True).encode("ascii")


def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_sha256"}
    return hashlib.sha256(_canon(body)).hexdigest()


def run_key(candidate_commit: str, corpus_sha: str, suite_manifest_sha: str, evaluator_version: str,
            suite: str) -> str:
    ok = (
        type(candidate_commit) is str and _HEX40.fullmatch(candidate_commit)
        and type(corpus_sha) is str and _HEX64.fullmatch(corpus_sha)
        and type(suite_manifest_sha) is str and _HEX64.fullmatch(suite_manifest_sha)
        and type(evaluator_version) is str and _VERSION.fullmatch(evaluator_version)
        and suite in SUITES
    )
    if not ok:
        raise SealRefusal("BAD_ARGS")
    material = "\x00".join(("zurvan.z1b.runkey.v1", candidate_commit, corpus_sha, suite_manifest_sha,
                            evaluator_version, suite))
    return hashlib.sha256(material.encode("ascii")).hexdigest()


def _no_dups(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key")
        out[k] = v
    return out


def _no_const(name):
    raise ValueError("non-finite constant")


def _is_nat(v) -> bool:
    return type(v) is int and v >= 0


def _check_fields(entry: dict) -> None:
    """Closed per-kind schema (excluding the chain fields)."""
    kind = entry.get("kind")
    if type(kind) is not str or kind not in _KIND_FIELDS:
        _bad()
    if entry.get("schema") != SCHEMA:
        _bad()
    allowed = {"schema", "kind"} | set(_KIND_FIELDS[kind])
    keys = set(entry) - set(_CHAIN)
    if keys != allowed:
        _bad()
    g = entry.get
    if kind == "reserved":
        if not (type(g("run_id")) is str and _HEX16.fullmatch(g("run_id"))):
            _bad()
        if not (type(g("key")) is str and _HEX64.fullmatch(g("key"))):
            _bad()
        if not (type(g("suite_id")) is str and _HEX16.fullmatch(g("suite_id"))):
            _bad()
    elif kind == "completed":
        if not (type(g("run_id")) is str and _HEX16.fullmatch(g("run_id"))):
            _bad()
        if not isinstance(g("result"), dict):
            _bad()
        if not (type(g("rows_sha256")) is str and _HEX64.fullmatch(g("rows_sha256"))):
            _bad()
    elif kind == "failed":
        if not (type(g("run_id")) is str and _HEX16.fullmatch(g("run_id"))):
            _bad()
        if type(g("code")) is not str or g("code") not in ALL_CODES:
            _bad()
    elif kind == "incident":
        if not (type(g("incident_id")) is str and _HEX16.fullmatch(g("incident_id"))):
            _bad()
        if not (type(g("incident_kind")) is str and _NAME.fullmatch(g("incident_kind"))):
            _bad()
        ids = g("item_ids")
        if not isinstance(ids, list) or len(ids) > 100000:
            _bad()
        for i in ids:
            if not (type(i) is str and _ITEM.fullmatch(i)):
                _bad()
    elif kind == "ack":
        if not (type(g("incident_id")) is str and _HEX16.fullmatch(g("incident_id"))):
            _bad()
    elif kind == "burn":
        if not (type(g("id")) is str and _HEX32.fullmatch(g("id"))):
            _bad()
        if g("reason") not in BURN_REASONS:
            _bad()
        if not (type(g("date")) is str and _DATE.fullmatch(g("date"))):
            _bad()
    elif kind == "manifest_seen":
        if not _is_nat(g("sequence")):
            _bad()
        if not (type(g("manifest_sha256")) is str and _HEX64.fullmatch(g("manifest_sha256"))):
            _bad()


class _State:
    """Replays entries; every transition must be legal. Raises LEDGER_INVALID."""

    def __init__(self):
        self.reserved = {}      # run_id -> entry
        self.resolved = {}      # run_id -> "completed" | "failed"
        self.completed_by_key = {}  # key -> result (latest)
        self.incidents = {}     # incident_id -> acked bool
        self.burned = set()
        self.manifest_seq = 0
        self.budget_used = {}   # suite_id -> n

    def apply(self, e: dict) -> None:
        kind = e["kind"]
        if kind == "reserved":
            if e["run_id"] in self.reserved:
                _bad()
            self.reserved[e["run_id"]] = e
            self.budget_used[e["suite_id"]] = self.budget_used.get(e["suite_id"], 0) + 1
        elif kind in ("completed", "failed"):
            if e["run_id"] not in self.reserved or e["run_id"] in self.resolved:
                _bad()
            self.resolved[e["run_id"]] = kind
            if kind == "completed":
                self.completed_by_key[self.reserved[e["run_id"]]["key"]] = (e["run_id"], e["result"])
        elif kind == "incident":
            if e["incident_id"] in self.incidents:
                _bad()
            self.incidents[e["incident_id"]] = False
        elif kind == "ack":
            if e["incident_id"] not in self.incidents or self.incidents[e["incident_id"]]:
                _bad()
            self.incidents[e["incident_id"]] = True
        elif kind == "burn":
            self.burned.add(e["id"])
        elif kind == "manifest_seen":
            if e["sequence"] < self.manifest_seq:
                _bad()
            self.manifest_seq = e["sequence"]


class Ledger:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.path = self.runs_dir / "ledger.jsonl"
        self.lock_path = self.runs_dir / "ledger.lock"
        self._tlock = threading.RLock()
        self._depth = 0
        self._lock_fd = None

    # ---------------------------------------------------------------- locking

    @contextlib.contextmanager
    def lock(self):
        """Exclusive flock on runs/ledger.lock. Re-entrant within this object."""
        with self._tlock:
            if self._depth == 0:
                self.runs_dir.mkdir(parents=True, exist_ok=True)
                try:
                    fd = os.open(str(self.lock_path),
                                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                except OSError:
                    _bad()
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except OSError:
                    os.close(fd)
                    _bad()
                self._lock_fd = fd
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
                if self._depth == 0:
                    fd, self._lock_fd = self._lock_fd, None
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)

    # ---------------------------------------------------------------- reading

    def _read_raw(self) -> bytes:
        try:
            fd = os.open(str(self.path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return b""
        except OSError:
            _bad()
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                _bad()
            chunks = []
            while True:
                c = os.read(fd, 1 << 20)
                if not c:
                    break
                chunks.append(c)
            return b"".join(chunks)
        except OSError:
            _bad()
        finally:
            os.close(fd)

    def _verified(self):
        raw = self._read_raw()
        entries, state = [], _State()
        if not raw:
            return entries, state, GENESIS
        if not raw.endswith(b"\n"):
            _bad()
        prev = GENESIS
        for index, line in enumerate(raw[:-1].split(b"\n")):
            if not line or len(line) > MAX_LINE_BYTES:
                _bad()
            try:
                e = json.loads(line.decode("ascii"), object_pairs_hook=_no_dups,
                               parse_constant=_no_const)
            except (ValueError, UnicodeDecodeError, RecursionError):
                _bad()
            if not isinstance(e, dict) or _canon(e) != line:
                _bad()
            if e.get("seq") != index or type(e.get("seq")) is not int:
                _bad()
            if e.get("prev_sha256") != prev:
                _bad()
            if e.get("entry_sha256") != _entry_hash(e):
                _bad()
            _check_fields(e)
            state.apply(e)
            entries.append(e)
            prev = e["entry_sha256"]
        return entries, state, prev

    def entries(self) -> list:
        with self.lock():
            return copy.deepcopy(self._verified()[0])

    # ---------------------------------------------------------------- writing

    def _fsync_dir(self) -> None:
        dfd = os.open(str(self.runs_dir), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def _append_locked(self, entry: dict):
        _entries, state, prev = self._verified()
        if not isinstance(entry, dict) or set(entry) & set(_CHAIN) or "schema" in entry:
            _bad()
        full = dict(entry)
        full["schema"] = SCHEMA
        full["seq"] = len(_entries)
        full["prev_sha256"] = prev
        try:
            _check_fields(dict(full, entry_sha256="0" * 64))
            full["entry_sha256"] = _entry_hash(full)
            line = _canon(full) + b"\n"
        except (TypeError, ValueError):
            _bad()
        if len(line) > MAX_LINE_BYTES:
            _bad()
        state.apply(full)  # raises on an illegal transition BEFORE any byte is written
        fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            written = os.write(fd, line)
            if written != len(line):
                _bad()
            os.fsync(fd)
        finally:
            os.close(fd)
        self._fsync_dir()
        return full

    def append(self, entry: dict) -> dict:
        with self.lock():
            return self._append_locked(entry)

    # ---------------------------------------------------------------- queries

    def cached(self, key: str):
        with self.lock():
            hit = self._verified()[1].completed_by_key.get(key)
            return copy.deepcopy(hit[1]) if hit else None

    def budget_remaining(self, suite_id: str, budget: int) -> int:
        if not _is_nat(budget):
            raise SealRefusal("BAD_ARGS")
        with self.lock():
            used = self._verified()[1].budget_used.get(suite_id, 0)
        return max(0, budget - used)

    def unresolved_runs(self) -> list:
        """run_ids reserved but neither completed nor failed (a crashed or still-running run)."""
        with self.lock():
            st = self._verified()[1]
            return [r for r in st.reserved if r not in st.resolved]

    def open_incidents(self) -> list:
        with self.lock():
            entries, st, _p = self._verified()
            return [copy.deepcopy(e) for e in entries
                    if e["kind"] == "incident" and not st.incidents[e["incident_id"]]]

    def burned_ids(self) -> set:
        with self.lock():
            return set(self._verified()[1].burned)

    def last_manifest_sequence(self) -> int:
        with self.lock():
            return self._verified()[1].manifest_seq

    # ---------------------------------------------------------------- runs

    def reserve_run(self, key: str, suite_id: str, budget: int) -> str:
        """Under the lock: reserve one run for `key`.

        - key already completed: returns that run's run_id and appends NOTHING (no budget used);
          the caller must call cached(key) first - complete_run on a finished run is refused.
        - key reserved but unresolved (in flight, or a crashed run): LEDGER_INVALID (contract
          deviation: no dedicated code exists; this is the most fail-closed existing one).
        - no budget left: SUITE_RENEWAL_REQUIRED.
        """
        if not (type(key) is str and _HEX64.fullmatch(key)):
            raise SealRefusal("BAD_ARGS")
        if not (type(suite_id) is str and _HEX16.fullmatch(suite_id)) or not _is_nat(budget):
            raise SealRefusal("BAD_ARGS")
        with self.lock():
            entries, st, prev = self._verified()
            done = st.completed_by_key.get(key)
            if done:
                return done[0]
            for rid, e in st.reserved.items():
                if e["key"] == key and rid not in st.resolved:
                    _bad()
            if st.budget_used.get(suite_id, 0) >= budget:
                raise SealRefusal("SUITE_RENEWAL_REQUIRED")
            run_id = hashlib.sha256((prev + key).encode("ascii")).hexdigest()[:16]
            self._append_locked({"kind": "reserved", "run_id": run_id, "key": key, "suite_id": suite_id})
            return run_id

    def complete_run(self, run_id: str, result: dict, rows_path: Path) -> None:
        if not isinstance(result, dict):
            _bad()
        try:
            fd = os.open(str(rows_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            _bad()
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                _bad()
            h = hashlib.sha256()
            while True:
                c = os.read(fd, 1 << 20)
                if not c:
                    break
                h.update(c)
        finally:
            os.close(fd)
        self.append({"kind": "completed", "run_id": run_id, "result": result, "rows_sha256": h.hexdigest()})

    def fail_run(self, run_id: str, code: str) -> None:
        self.append({"kind": "failed", "run_id": run_id, "code": code})

    # ---------------------------------------------------------------- incidents

    def record_incident(self, kind: str, item_ids: list) -> str:
        """Appends an incident and returns its id (contract says None; a superset return)."""
        with self.lock():
            _e, _s, prev = self._verified()
            if type(kind) is not str or not isinstance(item_ids, list):
                _bad()
            material = prev + "\x00" + kind + "\x00" + "\x00".join(str(i) for i in item_ids)
            incident_id = hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:16]
            self._append_locked({"kind": "incident", "incident_id": incident_id,
                                 "incident_kind": kind, "item_ids": list(item_ids)})
            return incident_id

    def acknowledge_incident(self, incident_id: str) -> None:
        self.append({"kind": "ack", "incident_id": incident_id})
