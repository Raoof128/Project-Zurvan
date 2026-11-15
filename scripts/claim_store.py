#!/usr/bin/env python3
"""Z2 claim store: `data/claims.sqlite` (spec §7.1).

Three rules this module exists to keep:

1. Every byte that can name a person or a client - claim text, reasons, quotes,
   selectors, source paths, source fingerprints, tags, extraction keys - lives in
   table `content`, under a RANDOM cid. Nothing else holds a body or a hash of one.
2. `revisions` is append-only and hash-chained in insertion order (`seq`). The
   chain covers structure and cids, never bodies, so erasing `content.body`
   keeps every hash valid (spec §2.4: GDPR Art. 17, EDPB 02/2025 para 103-106).
3. A writer holds an exclusive lock from its first claim write until the
   knowledge operation that covers it commits or is abandoned, so per-claim
   `rev` order, chain order and commit order are one order (spec §7.1).

Record time is never stored here; it is derived from the registry (§7.2).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks

SCHEMA_VERSION = 1

EVENTS = ("assert", "revise", "supersede", "retract", "orphan", "restore", "erase")
STATUSES = ("asserted", "supported", "contested", "superseded", "retracted",
            "unknown", "orphaned", "erased")
METHODS = ("human", "agent", "model_extraction", "migration", "verifier")
CONFIDENCE = ("low", "medium", "high")
BASIS = ("primary_source", "independent_confirmation", "direct_measurement",
         "agent_inference", "user_assertion", "legacy_unstated")
RELATION_KINDS = ("supersedes", "supports", "contradicts")
LOCK_NAME = "claims.lock"

_GENESIS = "0" * 64


class ClaimRefused(Exception):
    """A refusal with a closed code (plan Global Constraints)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _q(values) -> str:
    return ",".join(f"'{v}'" for v in values)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS content (
    cid                TEXT PRIMARY KEY,
    kind               TEXT NOT NULL,
    body               TEXT,
    erased_at          TEXT,
    erase_reason_class TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id         TEXT PRIMARY KEY,
    created_mutation TEXT NOT NULL,
    legacy_id        TEXT UNIQUE,
    scope_project    TEXT
);
CREATE TABLE IF NOT EXISTS revisions (
    seq                       INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id                  TEXT NOT NULL REFERENCES claims(claim_id),
    rev                       INTEGER NOT NULL,
    event                     TEXT NOT NULL CHECK (event IN ({_q(EVENTS)})),
    status                    TEXT NOT NULL CHECK (status IN ({_q(STATUSES)})),
    text_cid                  TEXT REFERENCES content(cid),
    confidence                TEXT CHECK (confidence IS NULL OR confidence IN ({_q(CONFIDENCE)})),
    confidence_basis          TEXT NOT NULL DEFAULT '[]',
    valid_from                TEXT,
    valid_until               TEXT,
    valid_time_basis          TEXT NOT NULL CHECK (valid_time_basis IN ('stated','unknown')),
    actor                     TEXT NOT NULL,
    method                    TEXT NOT NULL CHECK (method IN ({_q(METHODS)})),
    reason_cid                TEXT REFERENCES content(cid),
    mutation_id               TEXT NOT NULL,
    recorded_at               TEXT NOT NULL,
    tags_cid                  TEXT REFERENCES content(cid),
    claim_type                TEXT,
    extraction_key_cid        TEXT REFERENCES content(cid),
    observed_at_legacy        TEXT,
    observed_at_legacy_source TEXT,
    subject_id                TEXT,
    predicate_id              TEXT,
    object_json               TEXT,
    evidence_digest           TEXT NOT NULL,
    relations_digest          TEXT NOT NULL,
    prev_hash                 TEXT NOT NULL,
    row_hash                  TEXT NOT NULL UNIQUE,
    UNIQUE (claim_id, rev)
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id          TEXT NOT NULL REFERENCES claims(claim_id),
    rev               INTEGER NOT NULL,
    source_ref_cid    TEXT NOT NULL REFERENCES content(cid),
    locator_kind      TEXT NOT NULL DEFAULT 'text_quote' CHECK (locator_kind IN ('text_quote')),
    selector_cid      TEXT NOT NULL REFERENCES content(cid),
    capsule_cid       TEXT REFERENCES content(cid),
    location_hint_cid TEXT REFERENCES content(cid),
    reader            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_claim  TEXT NOT NULL REFERENCES claims(claim_id),
    to_claim    TEXT NOT NULL REFERENCES claims(claim_id),
    kind        TEXT NOT NULL CHECK (kind IN ({_q(RELATION_KINDS)})),
    claim_id    TEXT NOT NULL,
    rev         INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS head (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    last_rev_hash    TEXT NOT NULL,
    last_mutation_id TEXT,
    schema_version   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS revisions_claim ON revisions(claim_id, rev);
CREATE INDEX IF NOT EXISTS revisions_mutation ON revisions(mutation_id);
CREATE INDEX IF NOT EXISTS evidence_claim ON evidence(claim_id, rev);
CREATE INDEX IF NOT EXISTS relations_from ON relations(from_claim);
CREATE INDEX IF NOT EXISTS relations_to ON relations(to_claim);
"""

#: Columns the chain hashes, in this order. Everything in `revisions` except
#: `seq` (storage order, recomputed by the walk) and `row_hash` itself.
_HASHED = ("claim_id", "rev", "event", "status", "text_cid", "confidence",
           "confidence_basis", "valid_from", "valid_until", "valid_time_basis",
           "actor", "method", "reason_cid", "mutation_id", "recorded_at", "tags_cid",
           "claim_type", "extraction_key_cid", "observed_at_legacy",
           "observed_at_legacy_source", "subject_id", "predicate_id", "object_json",
           "evidence_digest", "relations_digest", "prev_hash")

_EVIDENCE_COLS = ("source_ref_cid", "locator_kind", "selector_cid", "capsule_cid",
                  "location_hint_cid", "reader")
_RELATION_COLS = ("from_claim", "to_claim", "kind")


def db_path(root=None) -> Path:
    return ks.store_root(root).joinpath(*ks.CLAIMS_DB_REL)


def _pragmas(conn) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")


def _is_hot_journal(exc, path) -> bool:
    """A read-only connection cannot roll back a crashed writer's journal."""
    return (getattr(exc, "sqlite_errorname", "") == "SQLITE_READONLY_ROLLBACK"
            or Path(f"{path}-journal").exists() and "readonly" in str(exc))


def roll_back_hot_journal(path) -> bool:
    """Let SQLite roll back a journal a crashed writer left behind. SQLite does
    it only for a HOT journal (no live writer holds a lock), so this can never
    undo a transaction still in progress. True if a journal was there."""
    journal = Path(f"{path}-journal")
    if not journal.exists():
        return False
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        conn.close()
    return True


def connect(root=None, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the store. Read-only never creates it; read-write creates the schema.

    `isolation_level=None`: callers drive transactions with `BEGIN IMMEDIATE`,
    the same shape as `knowledge_revision` (an implicit BEGIN would collide).
    """
    path = db_path(root)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
        try:
            _pragmas(conn)
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.OperationalError as exc:
            conn.close()
            if _is_hot_journal(exc, path):
                raise ClaimRefused(
                    "CLAIMS_STORE_RECOVERY_NEEDED",
                    f"{path.name}-journal was left by a write that never finished; "
                    "run `zurvan knowledge reconcile` to roll it back") from None
            raise
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
    if str(mode).lower() != "delete":
        conn.close()
        raise sqlite3.OperationalError(f"claims.sqlite must be journal_mode=delete, got {mode}")
    _pragmas(conn)
    if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                # one statement at a time: `executescript` would COMMIT the
                # BEGIN IMMEDIATE first and leave the schema half-created on error
                for statement in (s.strip() for s in _SCHEMA.split(";")):
                    if statement:
                        conn.execute(statement)
                conn.execute("INSERT OR IGNORE INTO head (id, last_rev_hash, schema_version)"
                             " VALUES (1, ?, ?)", (_GENESIS, SCHEMA_VERSION))
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            conn.close()
            raise
    return conn


def new_claim_id() -> str:
    return "clm_" + secrets.token_hex(8)


def _new_cid() -> str:
    return "cnt_" + secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def put_content(conn, kind: str, body: str) -> str:
    """Store `body` under a fresh random cid. Equal bodies get distinct cids:
    a cid must never let anyone confirm a guessed body."""
    if not isinstance(body, str):
        raise TypeError("content body must be str")
    cid = _new_cid()
    conn.execute("INSERT INTO content (cid, kind, body) VALUES (?, ?, ?)", (cid, kind, body))
    return cid


def get_content(conn, cid) -> str | None:
    if cid is None:
        return None
    row = conn.execute("SELECT body FROM content WHERE cid = ?", (cid,)).fetchone()
    return row[0] if row else None


def insert_claim(conn, claim_id: str, created_mutation: str, legacy_id=None,
                 scope_project=None) -> None:
    conn.execute("INSERT INTO claims (claim_id, created_mutation, legacy_id, scope_project)"
                 " VALUES (?, ?, ?, ?)", (claim_id, created_mutation, legacy_id, scope_project))


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def _row_hash(fields: dict) -> str:
    return _digest({k: fields.get(k) for k in _HASHED})


def _rows_digest(rows, cols) -> str:
    return _digest([[r.get(c) for c in cols] for r in rows])


def append_revision(conn, row: dict) -> dict:
    """Append one revision (plus its evidence and relation rows) to the chain.

    `row` carries the revision's columns. Optional `tags` (list) and
    `extraction_key` (str) are stored as content; `evidence` and `relations`
    (lists of dicts) are inserted for this (claim_id, rev) and digested into the
    hashed row. Runs inside the caller's transaction; opens none of its own.
    """
    fields = {k: v for k, v in row.items() if k not in ("evidence", "relations", "tags",
                                                         "extraction_key")}
    claim_id = fields["claim_id"]
    if "tags" in row and row["tags"] is not None:
        fields["tags_cid"] = put_content(conn, "tags", json.dumps(list(row["tags"])))
    if row.get("extraction_key") is not None:
        fields["extraction_key_cid"] = put_content(conn, "extraction_key", row["extraction_key"])
    basis = fields.get("confidence_basis", [])
    fields["confidence_basis"] = json.dumps(list(basis)) if not isinstance(basis, str) else basis
    fields.setdefault("valid_time_basis", "stated" if fields.get("valid_from") else "unknown")
    fields.setdefault("recorded_at", _now())
    rev = conn.execute("SELECT COALESCE(MAX(rev), 0) + 1 FROM revisions WHERE claim_id = ?",
                       (claim_id,)).fetchone()[0]
    fields["rev"] = rev
    evidence = [dict(locator_kind="text_quote", **e) if "locator_kind" not in e else dict(e)
                for e in row.get("evidence") or []]
    relations = [dict(r) for r in row.get("relations") or []]
    fields["evidence_digest"] = _rows_digest(evidence, _EVIDENCE_COLS)
    fields["relations_digest"] = _rows_digest(relations, _RELATION_COLS)
    fields["prev_hash"] = conn.execute("SELECT last_rev_hash FROM head WHERE id = 1").fetchone()[0]
    fields["row_hash"] = _row_hash(fields)
    cols = list(fields)
    conn.execute(f"INSERT INTO revisions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                 [fields[c] for c in cols])
    for e in evidence:
        conn.execute("INSERT INTO evidence (claim_id, rev, source_ref_cid, locator_kind,"
                     " selector_cid, capsule_cid, location_hint_cid, reader)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (claim_id, rev, e["source_ref_cid"], e["locator_kind"], e["selector_cid"],
                      e.get("capsule_cid"), e.get("location_hint_cid"), e["reader"]))
    for r in relations:
        conn.execute("INSERT INTO relations (from_claim, to_claim, kind, claim_id, rev)"
                     " VALUES (?, ?, ?, ?, ?)", (r["from_claim"], r["to_claim"], r["kind"],
                                                 claim_id, rev))
    conn.execute("UPDATE head SET last_rev_hash = ?, last_mutation_id = ? WHERE id = 1",
                 (fields["row_hash"], fields["mutation_id"]))
    return fields


def chain_ok(conn) -> tuple:
    """Walk `revisions` in `seq` order and recompute every link. (ok, problem)."""
    conn.row_factory = sqlite3.Row
    try:
        prev = _GENESIS
        for r in conn.execute("SELECT * FROM revisions ORDER BY seq"):
            fields = dict(r)
            where = f"claim {fields['claim_id']} rev {fields['rev']}"
            if fields["prev_hash"] != prev:
                return False, f"{where}: prev_hash does not link to the previous row"
            ev = [dict(e) for e in conn.execute(
                f"SELECT {', '.join(_EVIDENCE_COLS)} FROM evidence WHERE claim_id = ? AND rev = ?"
                " ORDER BY evidence_id", (fields["claim_id"], fields["rev"]))]
            rel = [dict(x) for x in conn.execute(
                f"SELECT {', '.join(_RELATION_COLS)} FROM relations WHERE claim_id = ? AND rev = ?"
                " ORDER BY relation_id", (fields["claim_id"], fields["rev"]))]
            if _rows_digest(ev, _EVIDENCE_COLS) != fields["evidence_digest"]:
                return False, f"{where}: evidence rows do not match evidence_digest"
            if _rows_digest(rel, _RELATION_COLS) != fields["relations_digest"]:
                return False, f"{where}: relation rows do not match relations_digest"
            if _row_hash(fields) != fields["row_hash"]:
                return False, f"{where}: row_hash does not match its fields"
            prev = fields["row_hash"]
        head = conn.execute("SELECT last_rev_hash FROM head WHERE id = 1").fetchone()
        if head is None or head[0] != prev:
            return False, "head.last_rev_hash is not the last row's hash"
        return True, None
    finally:
        conn.row_factory = None


_AUTHORITY_RE = re.compile(r"^authority:\s*\"?structured\"?\s*$", re.M)


def _is_structured_page(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return False
    if not head.startswith("---"):
        return False
    end = head.find("\n---", 3)
    front = head[3:end] if end != -1 else head
    return bool(_AUTHORITY_RE.search(front))


def require_migrated(root=None) -> None:
    """Refuse claim writes on a store that still has legacy pages and no ledger."""
    if db_path(root).exists():
        return
    claims_dir = ks.store_root(root) / "wiki" / "claims"
    if not claims_dir.is_dir():
        return
    for page in sorted(claims_dir.glob("*.md")):
        if not _is_structured_page(page):
            raise ClaimRefused("MIGRATION_REQUIRED",
                               f"{page.name} is a legacy claim page; run `zurvan claim migrate`")


def acquire_for_operation(root=None, timeout_s: float = 30.0) -> None:
    """Hold `data/claims.lock` until the outermost knowledge operation ends."""
    base = ks.store_root(root)
    op = kr.active_operation()
    if op is None or op.root != base:
        raise kr.KnowledgeOperationRequired(
            kr.operation_required_message("a claim write (claim_store.acquire_for_operation)"))
    if getattr(op, "_claims_lock_fd", None) is not None:
        return
    lock_path = base.joinpath(*ks.CLAIMS_DB_REL).with_name(LOCK_NAME)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise ClaimRefused("CLAIMS_BUSY",
                                   f"another process holds {lock_path.name} for {timeout_s:g}s")
            time.sleep(0.05)
    op._claims_lock_fd = fd

    def _release():
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            op._claims_lock_fd = None

    kr.at_operation_end(_release)


def reconcile_ok(path, planned_pages=()) -> bool:
    """Is a claims.sqlite written by a crashed operation coherent? (spec §7.2)

    Integrity check, the hash chain, and every claim page the mutation planned
    exists. Used by `knowledge_reconcile`, whose text parser cannot read SQLite.
    """
    try:
        roll_back_hot_journal(path)        # a crash mid-transaction leaves one
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    except sqlite3.Error:
        return False
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False
        ok, _ = chain_ok(conn)
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return ok and all(Path(p).exists() for p in planned_pages)


def doctor(root=None) -> dict:
    """Health of the claim store: chain, integrity, invisible rows, dangling cids,
    hand-edited pages. Read-only."""
    from scripts import knowledge_revision as _kr
    out = {"chain_ok": True, "chain_problem": None, "integrity": "absent",
           "invisible_rows": [], "dangling_cids": [], "page_mismatches": []}
    if not db_path(root).exists():
        return out
    conn = connect(root, readonly=True)
    try:
        out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        out["chain_ok"], out["chain_problem"] = chain_ok(conn)
        visible = _kr.belief_revisions(root)
        states = {}
        try:
            reg = sqlite3.connect(f"file:{_kr.db_path(root)}?mode=ro", uri=True)
            states = dict(reg.execute("SELECT mutation_id, state FROM mutations"))
            reg.close()
        except sqlite3.Error:
            pass
        for claim_id, rev, mutation_id in conn.execute(
                "SELECT claim_id, rev, mutation_id FROM revisions ORDER BY seq"):
            if mutation_id not in visible:
                out["invisible_rows"].append((claim_id, rev, states.get(mutation_id, "UNKNOWN")))
        cols = (("revisions", ("text_cid", "reason_cid", "tags_cid", "extraction_key_cid")),
                ("evidence", ("source_ref_cid", "selector_cid", "capsule_cid",
                              "location_hint_cid")))
        for table, names in cols:
            for name in names:
                for (cid,) in conn.execute(
                        f"SELECT {name} FROM {table} WHERE {name} IS NOT NULL AND {name} NOT IN"
                        " (SELECT cid FROM content)"):
                    out["dangling_cids"].append(cid)
    finally:
        conn.close()
    from scripts import claim_ledger, claim_pages
    from scripts.claim_visibility import _front
    mismatches = set(claim_pages.conflicts(root))
    # Final review 2026-11-14 (I5): page/ledger agreement, not only self-consistency.
    # Derived views trust the page's `status`, so a stale or missing page matters.
    for cid, state in claim_ledger.current(root).items():
        page = claim_pages.page_path(state, root)
        if not page.exists():
            mismatches.add(cid)
            continue
        fm = _front(page)
        if (fm.get("status") != state.status
                or fm.get("generated_from_mutation") != state.mutation_id):
            mismatches.add(cid)
    out["page_mismatches"] = sorted(mismatches)
    return out
