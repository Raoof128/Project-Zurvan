#!/usr/bin/env python3
"""The knowledge revision ledger and the logical mutation barrier.

A revision names a coherent logical state of writable knowledge. It is advanced
by a logical mutation — one CLI command, one agent operation — never by an
individual file write, because `decision add` writes a page and then appends
`wiki/log.md`, and a revision minted between those two is a half-operation
wearing a valid-looking number.

The *barrier* is the ACTIVE mutation row: that is the durable fact another
process reads, and it outlives any connection. `BEGIN IMMEDIATE` is narrower —
it serialises transitions of the ledger itself so two processes cannot both
believe they advanced the revision. The transaction is held for the transition
only, never for the duration of an evaluation: locking a shared brain for the
length of a test run gets routed around within a week, and then removed.

**This module fails closed.** `writelog` is telemetry and may swallow its own
errors; a write that landed while the coherence ledger failed to record it is a
correctness incident, not a lost log line.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts import knowledge_scope as ks

CLEAN = "CLEAN"
SUSPECT = "SUSPECT"
MANAGED = "MANAGED"
EXTERNAL_OBSERVED = "EXTERNAL_OBSERVED"

ACTIVE = "ACTIVE"
COMMITTED = "COMMITTED"
ABORTED = "ABORTED"
ORPHANED = "ORPHANED"
RESOLVED = "RESOLVED"

_BUSY_MS = 5000


class LedgerError(RuntimeError):
    """The coherence ledger could not do what was asked. Never swallowed."""


class MutationStateError(LedgerError):
    """A transition was attempted from a state that does not allow it."""


class NestedMutationError(LedgerError):
    """A second operation was opened inside one that already owns this store."""


def db_path(root=None) -> Path:
    return ks.store_root(root) / "data" / "registry.sqlite"


def _connect(root=None) -> sqlite3.Connection:
    """`isolation_level=None` — this module drives its own transactions.

    Under Python's default isolation level sqlite3 auto-begins before DML, and
    a later explicit `BEGIN IMMEDIATE` then raises `cannot start a transaction
    within a transaction` (measured). Autocommit removes the class: explicit
    `BEGIN IMMEDIATE` ... `commit()` is the only transaction shape here.
    """
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=_BUSY_MS / 1000, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout={_BUSY_MS}")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Revision:
    revision: int
    state: str
    atomicity: str
    manifest_digest: str


def ensure_schema(root=None) -> None:
    """Create the three tables if absent. Additive: `sources` is untouched."""
    conn = _connect(root)
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_state (
                id               INTEGER PRIMARY KEY CHECK (id = 1),
                current_revision INTEGER NOT NULL,
                state            TEXT NOT NULL
                                 CHECK (state IN ('{CLEAN}','{SUSPECT}')),
                atomicity        TEXT NOT NULL
                                 CHECK (atomicity IN ('{MANAGED}','{EXTERNAL_OBSERVED}')),
                manifest_digest  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mutations (
                mutation_id      TEXT PRIMARY KEY,
                base_revision    INTEGER NOT NULL,
                state            TEXT NOT NULL
                                 CHECK (state IN ('{ACTIVE}','{COMMITTED}',
                                                  '{ABORTED}','{ORPHANED}','{RESOLVED}')),
                project          TEXT,
                writer           TEXT,
                implicit         INTEGER NOT NULL DEFAULT 0,
                caller           TEXT,
                owner_pid        INTEGER,
                owner_identity   TEXT NOT NULL,
                started_at       TEXT NOT NULL,
                committed_at     TEXT,
                last_heartbeat   TEXT,
                planned_paths    TEXT NOT NULL DEFAULT '[]',
                completed_paths  TEXT NOT NULL DEFAULT '[]',
                orphan_reason    TEXT,
                orphaned_at      TEXT,
                resolved_at      TEXT,
                resolution_actor TEXT,
                resolution_reason TEXT,
                resolution_revision INTEGER
            );
            CREATE INDEX IF NOT EXISTS mutations_state ON mutations(state);
            CREATE TABLE IF NOT EXISTS reconciliations (
                reconciliation_id TEXT PRIMARY KEY,
                at                TEXT NOT NULL,
                actor             TEXT NOT NULL,
                reason            TEXT,
                mutation_ids      TEXT NOT NULL,
                manifest_before   TEXT,
                manifest_after    TEXT NOT NULL,
                from_revision     INTEGER NOT NULL,
                to_revision       INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO knowledge_state"
            " (id, current_revision, state, atomicity, manifest_digest)"
            " VALUES (1, 0, ?, ?, '')",
            (CLEAN, MANAGED),
        )
    finally:
        conn.close()


def current(root=None) -> Revision:
    conn = _connect(root)
    try:
        row = conn.execute(
            "SELECT current_revision, state, atomicity, manifest_digest"
            " FROM knowledge_state WHERE id = 1").fetchone()
    finally:
        conn.close()
    if row is None:
        raise LedgerError(
            f"no knowledge_state row in {db_path(root)}; run ensure_schema first")
    return Revision(revision=row[0], state=row[1],
                    atomicity=row[2], manifest_digest=row[3])


def ledger_fingerprint(root=None) -> str:
    """A digest of everything the ledger asserts.

    The session guard needs to detect ANY production-ledger mutation, not only
    a revision bump. `CLEAN -> SUSPECT`, an atomicity change, a mutation row
    reaching ORPHANED and a reconciliation record all leave the revision
    counter untouched.
    """
    path = db_path(root)
    if not path.is_file():
        return "absent"
    conn = _connect(root)
    try:
        h = hashlib.sha256()
        for table, cols in (
            ("knowledge_state",
             "id, current_revision, state, atomicity, manifest_digest"),
            ("mutations",
             "mutation_id, state, planned_paths, completed_paths,"
             " orphan_reason, resolved_at, resolution_revision"),
            ("reconciliations",
             "reconciliation_id, at, actor, from_revision, to_revision"),
        ):
            try:
                rows = conn.execute(
                    f"SELECT {cols} FROM {table} ORDER BY 1").fetchall()
            except sqlite3.Error:
                rows = [("<missing>",)]
            for row in rows:
                h.update(repr(row).encode())
                h.update(b"\x00")
        return h.hexdigest()
    finally:
        conn.close()


ALIVE = "ALIVE"
DEAD = "DEAD"
UNKNOWN = "UNKNOWN"

#: (pid, identity). Keyed by pid because a forked child inherits module state
#: and would otherwise answer with its parent's identity. `ps` costs 3.7 ms per
#: call (measured), and this runs on every mutation, so it is worth caching.
_OWN_IDENTITY: tuple | None = None


def process_identity(pid: int) -> str | None:
    """`pid:start-time`, `None` when the process is absent, raises on failure.

    Three outcomes, not two: a missing process is evidence, an unusable `ps` is
    not. `psutil` is not a dependency of this project; `ps -o lstart=` works on
    macOS and Linux.
    """
    out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                         capture_output=True, text=True, timeout=5)
    started = out.stdout.strip()
    return f"{pid}:{started}" if started else None


def own_identity() -> str:
    global _OWN_IDENTITY
    pid = os.getpid()
    if _OWN_IDENTITY is None or _OWN_IDENTITY[0] != pid:
        try:
            identity = process_identity(pid)
        except (OSError, subprocess.SubprocessError):
            identity = None
        _OWN_IDENTITY = (pid, identity or f"{pid}:unknown")
    return _OWN_IDENTITY[1]


def process_state(pid, identity) -> str:
    """ALIVE, DEAD, or UNKNOWN. Only DEAD may orphan a mutation."""
    if not pid:
        return UNKNOWN
    try:
        live = process_identity(int(pid))
    except (OSError, ValueError, subprocess.SubprocessError):
        return UNKNOWN
    if live is None:
        return DEAD
    return ALIVE if live == identity else DEAD
