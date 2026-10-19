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


class DriftUnobservable(LedgerError):
    """Drift may exist but cannot be attributed right now: the store is still
    changing, another operation is in flight, or the ledger moved."""


class KnowledgeOperationRequired(LedgerError):
    """An authoritative write was attempted with no operation above it.

    Raised before any byte lands and before any ledger row exists. Until
    2026-09-28 such a write was accepted and recorded as an implicit mutation;
    on 2026-09-23 an agent in another repository imported `scripts.memory`
    directly and turned three logical writes into six revisions, each page and
    its log line committing separately. Implicit mode was always a migration
    mechanism, and one reachable by importing a module is the production path.
    """


def operation_required_message(what: str) -> str:
    return (
        f"{what} cannot change knowledge outside an operation: every "
        "authoritative write must belong to one logical operation, or a "
        "snapshot can observe half of it.\n"
        "Use the CLI, which opens one per command:\n"
        "  scripts/zurvan decision add --title ... --reason ... --status "
        "accepted --project <slug>\n"
        "  (also: remember / claim add / question add)\n"
        "or, from Python, declare the boundary yourself:\n"
        "  from scripts import knowledge_revision as kr\n"
        "  with kr.operation(writer=\"<who>\"):\n"
        "      ...\n"
        "Implicit writes are reserved for migrations: "
        "kr.allow_implicit_for_migration(reason=...).")


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


def _connect_ro(root=None) -> sqlite3.Connection:
    """A reader. Opens `mode=ro` so it cannot create the file.

    `sqlite3.connect` creates the database it cannot find — the hazard this
    repository already records for the search index, where a 0-byte file reads
    as "unreadable" rather than "missing". A read of the ledger must leave a
    store without one exactly as it found it.
    """
    path = db_path(root)
    if not path.is_file():
        raise LedgerError(
            f"no ledger at {path}. It is created on the first write, or by "
            "`zurvan knowledge bootstrap`.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                           timeout=_BUSY_MS / 1000)
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


def _migrate_attribution(conn) -> None:
    """Split the overloaded `project` column into four named facts.

    The shipped column was filled from `project_registry.get_current_project()`
    — the registry's `current`, one file shared by every concurrent session on
    the machine. It was therefore never a subject and never a caller, and on
    2026-09-19 a `decision add` run inside the Zurvan repo recorded `otherproj`
    while the page it wrote stamped `zurvan`.

    The recorded values ARE selections, so they move to `selected_project`
    verbatim. `store_project`, `execution_project` and `caller_project` stay
    NULL on historical rows: nothing in the old row records those facts, and a
    backfill would be a guess wearing the authoritative field's name.
    """
    def _columns():
        return {row[1] for row in conn.execute("PRAGMA table_info(mutations)")}

    if "project" not in _columns():
        return                       # fresh, or another process already did it
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-read INSIDE the transaction. `ensure_schema` runs on every mutation
        # insert, so two processes can both see the old shape, and the loser of
        # the `BEGIN IMMEDIATE` race would otherwise RENAME a column the winner
        # had already renamed — failing the write that triggered it.
        columns = _columns()
        if "project" in columns:
            conn.execute("ALTER TABLE mutations RENAME COLUMN project"
                         " TO selected_project")
            for name in ("store_project", "execution_project", "caller_project"):
                if name not in columns:
                    conn.execute(
                        f"ALTER TABLE mutations ADD COLUMN {name} TEXT")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_implicit_reason(conn) -> None:
    """Add `implicit_reason` to a ledger that predates it. Rows are untouched.

    Historical implicit rows keep NULL: nobody gave a reason for them, and the
    column exists to record reasons, not to reconstruct them.
    """
    def _columns():
        return {row[1] for row in conn.execute("PRAGMA table_info(mutations)")}

    cols = _columns()
    if not cols or "implicit_reason" in cols:
        return                       # fresh store, or already migrated
    conn.execute("BEGIN IMMEDIATE")
    try:
        if "implicit_reason" not in _columns():   # re-read inside, as above
            conn.execute("ALTER TABLE mutations ADD COLUMN implicit_reason TEXT")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def ensure_schema(root=None) -> None:
    """Create the three tables if absent, and migrate an older shape in place.

    Additive: `sources` is untouched.
    """
    conn = _connect(root)
    try:
        _migrate_attribution(conn)
        _migrate_implicit_reason(conn)
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
                store_project       TEXT,
                execution_project   TEXT,
                selected_project    TEXT,
                caller_project      TEXT,
                writer              TEXT,
                implicit            INTEGER NOT NULL DEFAULT 0,
                caller              TEXT,
                owner_pid           INTEGER,
                owner_identity      TEXT NOT NULL,
                started_at          TEXT NOT NULL,
                committed_at        TEXT,
                last_heartbeat      TEXT,
                planned_paths       TEXT NOT NULL DEFAULT '[]',
                completed_paths     TEXT NOT NULL DEFAULT '[]',
                orphan_reason       TEXT,
                orphaned_at         TEXT,
                resolved_at         TEXT,
                resolution_actor    TEXT,
                resolution_reason TEXT,
                resolution_revision INTEGER,
                implicit_reason     TEXT
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
            CREATE TABLE IF NOT EXISTS external_observations (
                revision        INTEGER PRIMARY KEY,
                trigger         TEXT NOT NULL,
                manifest_before TEXT NOT NULL,
                manifest_after  TEXT NOT NULL,
                observed_at     TEXT NOT NULL
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
    """Read the ledger. Never creates it.

    A read that created tables would be a write on a read path, and would fail
    differently on a read-only store. A store whose ledger does not exist yet
    is a legitimate state — it is created on the first write — so this reports
    it rather than tracebacking with `no such table`.
    """
    conn = _connect_ro(root)
    try:
        try:
            row = conn.execute(
                "SELECT current_revision, state, atomicity, manifest_digest"
                " FROM knowledge_state WHERE id = 1").fetchone()
        except sqlite3.OperationalError as exc:
            raise LedgerError(
                f"no ledger in {db_path(root)}: {exc}. It is created on the "
                "first write, or by `zurvan knowledge bootstrap`.") from exc
    finally:
        conn.close()
    if row is None:
        raise LedgerError(
            f"no ledger row in {db_path(root)}; run ensure_schema first")
    return Revision(revision=row[0], state=row[1],
                    atomicity=row[2], manifest_digest=row[3])


def ledger_fingerprint(root=None) -> str:
    """A digest of everything the ledger asserts.

    The session guard needs to detect ANY production-ledger mutation, not only
    a revision bump. `CLEAN -> SUSPECT`, an atomicity change, a mutation row
    reaching ORPHANED and a reconciliation record all leave the revision
    counter untouched.
    """
    if not db_path(root).is_file():
        return "absent"
    conn = _connect_ro(root)
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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None                      # ESRCH: the kernel says it is absent
    except PermissionError:
        pass                             # EPERM: it exists, another user's
    out = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                         capture_output=True, text=True, timeout=5)
    started = out.stdout.strip()
    if started:
        return f"{pid}:{started}"
    # Measured 2026-09-28: a pid that exited gives rc 1 and EMPTY stderr. A ps
    # that fails says why on stderr, and that is not evidence of absence
    # (final review I2): reading it as "absent" turned a broken ps into DEAD.
    if out.returncode == 1 and not out.stderr.strip():
        return None
    raise subprocess.SubprocessError(
        f"ps failed for pid {pid}: rc={out.returncode} {out.stderr.strip()!r}")


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


@dataclass
class Mutation:
    mutation_id: str
    base_revision: int
    state: str
    #: The SUBJECT written: the `project:` field stamped on the page, which is
    #: the publication control `public_repo_guard` reads. Whose knowledge moved.
    store_project: str | None
    #: The registered project containing the working directory. WHERE execution
    #: happened — for an MCP server that is its own workspace, which is why this
    #: is not called `caller_project`.
    execution_project: str | None
    #: The registry's `current`. Diagnostic only: never consulted for ownership,
    #: orphan attribution or routing. Recording it makes a stale selection
    #: visible instead of letting it masquerade as an attribution.
    selected_project: str | None
    #: Reserved. Populated only from a caller that supplies a trustworthy
    #: project identity of its own; nothing does yet, so it is always NULL.
    caller_project: str | None
    writer: str | None
    implicit: bool
    caller: str | None
    owner_pid: int | None
    owner_identity: str
    started_at: str
    committed_at: str | None
    planned_paths: list
    completed_paths: list
    orphan_reason: str | None
    orphaned_at: str | None
    resolved_at: str | None
    resolution_revision: int | None
    #: Why an implicit write was permitted — the argument to
    #: `allow_implicit_for_migration`. NULL on every row written before
    #: 2026-09-28, when implicit writes needed no permission.
    implicit_reason: str | None = None


_MUTATION_COLS = (
    "mutation_id, base_revision, state, store_project, execution_project,"
    " selected_project, caller_project, writer, implicit, caller,"
    " owner_pid, owner_identity, started_at, committed_at,"
    " planned_paths, completed_paths, orphan_reason, orphaned_at,"
    " resolved_at, resolution_revision, implicit_reason"
)


def _row_to_mutation(row) -> Mutation:
    return Mutation(
        mutation_id=row[0], base_revision=row[1], state=row[2],
        store_project=row[3], execution_project=row[4],
        selected_project=row[5], caller_project=row[6],
        writer=row[7], implicit=bool(row[8]), caller=row[9], owner_pid=row[10],
        owner_identity=row[11], started_at=row[12], committed_at=row[13],
        planned_paths=json.loads(row[14] or "[]"),
        completed_paths=json.loads(row[15] or "[]"),
        orphan_reason=row[16], orphaned_at=row[17],
        resolved_at=row[18], resolution_revision=row[19],
        implicit_reason=row[20],
    )


def _transition(conn, mutation_id, want, new, **fields) -> None:
    """Move a mutation from `want` to `new`, or raise.

    Compare-and-set, inside the caller's transaction. Updating by id alone let
    `commit_mutation` run twice and advance the revision twice, and let orphan
    detection reclassify a mutation that had committed between the liveness
    check and the update.
    """
    assignments = ["state = ?"]
    values = [new]
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        values.append(value)
    values.extend([mutation_id, want])
    cur = conn.execute(
        f"UPDATE mutations SET {', '.join(assignments)}"
        " WHERE mutation_id = ? AND state = ?", values)
    if cur.rowcount != 1:
        row = conn.execute(
            "SELECT state FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        found = row[0] if row else "<no such mutation>"
        raise MutationStateError(
            f"{mutation_id}: expected {want} to become {new}, found {found}")


def mutations_in(state, root=None) -> list:
    """Reader. A store with no ledger has no mutations, which is true.

    "No ledger" has THREE shapes, and only two are reachable from a fixture:
    no registry file at all, a registry with the full schema, and — the
    production store's actual state — a registry that exists holding `sources`
    from ingestion and has never taken a write through the ledger. In the third
    the connection opens fine and the QUERY is what fails.
    """
    try:
        conn = _connect_ro(root)
    except LedgerError:
        return []
    try:
        try:
            rows = conn.execute(
                f"SELECT {_MUTATION_COLS} FROM mutations WHERE state = ?"
                " ORDER BY started_at", (state,)).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]


@dataclass
class Operation:
    root: Path
    #: May be None at open and declared later: `main()` wraps before argparse
    #: runs, so the CLI cannot know the subject until the page resolves it.
    store_project: str | None = None
    execution_project: str | None = None
    selected_project: str | None = None
    caller_project: str | None = None
    writer: str | None = None
    caller: str | None = None
    implicit: bool = False
    implicit_reason: str | None = None
    #: Activate at open instead of at the first planned write. For writers that
    #: write authoritative files directly before (or instead of) reaching
    #: `safe_write` - ingest, extract. Lazily activated, their own direct
    #: writes were minted as an external edit underneath them (final review,
    #: 2026-09-28).
    eager: bool = False
    mutation_id: str | None = None
    depth: int = 0          # nesting depth; join-only, never used to commit
    #: The revision this operation committed, set when it finishes; None
    #: when it planned nothing (or was abandoned). Callers that report a
    #: revision report THIS, not `current()` read later (another session's).
    minted_revision: int | None = None


@dataclass
class WriteTicket:
    mutation_id: str
    owns: bool          # True when this single write must commit the mutation


#: One operation per context. A module global let an inner operation clear the
#: active id on exit, after which the outer operation's remaining writes became
#: implicit — splitting one logical operation without anyone knowing.
#:
#: Deliberately NOT an environment variable. An earlier design exported the
#: mutation id so subprocesses could join, and `zurvan --project X` then
#: re-invoked the CLI against a DIFFERENT store with the id still set — writes
#: there matched no row and were silently dropped. A subprocess now opens its
#: own operation in its own store, which is the honest answer.
_CTX: ContextVar = ContextVar("zurvan_operation", default=None)

#: The reason an implicit write is permitted, or None. A context variable for
#: the same reason as `_CTX`, and deliberately not an environment variable
#: either: an exported permission would follow a subprocess into whatever it
#: ran, which is how a migration's licence becomes everybody's.
_IMPLICIT_OK: ContextVar = ContextVar("zurvan_implicit_ok", default=None)


def active_operation():
    return _CTX.get()


def require_active_operation(what: str, root=None) -> None:
    """Refuse up front, in a library writer, with a message naming `what`.

    `plan_write` is the backstop and would refuse anyway; this makes the error
    name the function the caller actually called, and lets a writer refuse
    before doing any work — `add_claim` reads its source file first.
    """
    op = _CTX.get()
    if op is not None and op.root == ks.store_root(root):
        return
    if op is None and _IMPLICIT_OK.get() is not None:
        return
    raise KnowledgeOperationRequired(operation_required_message(what))


@contextmanager
def allow_implicit_for_migration(reason):
    """Permit writes outside an operation, for a migration, with a reason.

    Each such write is its own implicit mutation — the old behaviour, with the
    split it implies — and `reason` is recorded on its row. There is no boolean
    form and no default: a reason is the price of the exemption, and it is what
    makes the exemption greppable in code and auditable in the ledger.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("allow_implicit_for_migration needs a non-empty reason")
    token = _IMPLICIT_OK.set(reason.strip())
    try:
        yield
    finally:
        _IMPLICIT_OK.reset(token)


def _describe_caller() -> str:
    """`module:function` of the first frame outside this module and safe_write.

    `writer="implicit"` told a migration nothing about WHAT to migrate. The
    inventory exists to be actioned, so it records something actionable. Names
    only — never arguments, which on this store can be private content.
    """
    import sys
    frame = sys._getframe(1)
    skip = {__name__, "scripts.safe_write"}
    while frame is not None:
        module = frame.f_globals.get("__name__", "?")
        if module not in skip:
            return f"{module}:{frame.f_code.co_name}"
        frame = frame.f_back
    return "unknown"


def _clean(value):
    """`"unknown"` is not a project name; NULL is how a column says so.

    `memory.resolve_project` answers `"unknown"` deliberately — it is a
    publication control and fails closed, and `"unknown"` is not in
    `PUBLIC_PROJECTS`. A ledger column has a real representation for absence,
    and carrying two spellings of it would mean every reader has to know both.

    Stated bound: a project actually REGISTERED as `unknown` would be erased to
    NULL here. `is_safe_slug` permits that name, and `resolve_project` already
    cannot distinguish the two either — this inherits that collision rather
    than introducing it.
    """
    return None if value in (None, "", "unknown") else value


def _ambient_attribution() -> tuple:
    """(execution_project, selected_project) for the process, best effort.

    Deliberately NOT fail-closed, unlike everything else in this module. These
    two are diagnostics: an absent one is honestly NULL, and a coherence ledger
    that refused a write because the project registry was unreadable would be
    trading a real guarantee for a label.
    """
    execution = selected = None
    try:
        from scripts.memory import resolve_project
        execution = resolve_project()
    except Exception:
        pass
    try:
        from scripts.project_registry import get_current_project
        selected = get_current_project()[0]
    except Exception:
        pass
    return _clean(execution), _clean(selected)


def declare_store_project(name, op=None) -> None:
    """Record the SUBJECT this operation is writing, from the write path.

    Called where a page stamps its `project:` field, with the value that page
    stamped, so the ledger and the page cannot disagree — which is the entire
    defect this replaces. Declaring works before or after the mutation row
    exists, because the row is created lazily on the first planned write while
    `add_decision` resolves its subject in the middle of writing.

    A second, DIFFERENT subject raises: one mutation, one subject. If a command
    ever writes two projects' pages then the operation boundary is drawn in the
    wrong place, and mis-attributing that quietly is worse than stopping.
    Outside any operation this is a no-op; the write that follows is refused
    by `plan_write` unless a migration has permitted it.
    """
    name = _clean(name)
    if name is None:
        return
    if op is None:
        op = _CTX.get()
    if op is None:
        return
    if op.store_project is not None and op.store_project != name:
        raise LedgerError(
            f"operation already writing {op.store_project!r}; refusing to also "
            f"attribute it to {name!r}. One mutation has one subject — split "
            "the operation instead.")
    op.store_project = name
    if op.mutation_id is not None:
        conn = _connect(op.root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE mutations SET store_project = ? WHERE mutation_id = ?",
                (name, op.mutation_id))
            conn.execute("COMMIT")
        finally:
            conn.close()


def _absorb_external_drift(base):
    """Record an external edit as its own revision before a managed write.

    Without this, `commit_mutation` records the whole store's manifest and
    the edit is laundered into the managed revision (reproduced 2026-09-28).
    Skipped - and bounded, not fixed - when drift cannot be attributed: no
    baseline, SUSPECT (reconcile owns it), another operation in flight, or
    the store still changing.
    """
    try:
        state = current(base)
    except LedgerError:
        return None
    if not state.manifest_digest or state.state != CLEAN:
        return None
    # A crashed writer's ACTIVE row is not an operation in flight. Without
    # this, one SIGKILL disabled absorption until someone ran reconcile
    # (final review, 2026-09-28). DEAD owners only; a torn one makes the
    # store SUSPECT, which is loud, instead of silently absorbing.
    detect_orphans(base)
    if current(base).state != CLEAN:
        return None
    if mutations_in(ACTIVE, base):
        return None
    try:
        return observe_external_drift(base, trigger="activation")
    except DriftUnobservable:
        return None


def _insert_mutation(op: Operation) -> str:
    mutation_id = f"m{uuid.uuid4().hex[:12]}"
    pid = os.getpid()
    ensure_schema(op.root)
    _absorb_external_drift(op.root)
    conn = _connect(op.root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        base = conn.execute(
            "SELECT current_revision FROM knowledge_state WHERE id = 1"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO mutations (mutation_id, base_revision, state,"
            " store_project, execution_project, selected_project,"
            " caller_project, writer, implicit, caller, owner_pid,"
            " owner_identity, started_at, last_heartbeat, implicit_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mutation_id, base, ACTIVE, _clean(op.store_project),
             _clean(op.execution_project), _clean(op.selected_project),
             _clean(op.caller_project), op.writer,
             int(bool(op.implicit)), op.caller, pid, own_identity(),
             _now(), _now(), op.implicit_reason))
        conn.execute("COMMIT")
    finally:
        conn.close()
    return mutation_id


def _append_path(mutation_id, column, path, root) -> None:
    """Append one path to `planned_paths` or `completed_paths`. Fails closed."""
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"SELECT {column}, state FROM mutations WHERE mutation_id = ?",
            (mutation_id,)).fetchone()
        if row is None or row[1] != ACTIVE:
            conn.execute("ROLLBACK")
            raise MutationStateError(
                f"{mutation_id}: cannot record {column} in state "
                f"{row[1] if row else '<missing>'}")
        paths = json.loads(row[0] or "[]")
        if str(path) not in paths:
            paths.append(str(path))
        conn.execute(
            f"UPDATE mutations SET {column} = ?, last_heartbeat = ?"
            " WHERE mutation_id = ? AND state = ?",
            (json.dumps(paths), _now(), mutation_id, ACTIVE))
        conn.execute("COMMIT")
    finally:
        conn.close()


def plan_write(path, root=None):
    """Record the intent to write BEFORE the bytes land.

    Returns a `WriteTicket`, or `None` when the path is outside the
    authoritative scope. Activates the operation's mutation row lazily, so an
    operation that never writes leaves no trace in the ledger.

    Raises rather than returning quietly: a write whose intent could not be
    recorded must not proceed. `writelog` may be best-effort; this may not.
    """
    base = ks.store_root(root)
    if not ks.is_authoritative(path, base):
        return None
    op = _CTX.get()
    owns = False
    if op is None or op.root != base:
        reason = _IMPLICIT_OK.get()
        if reason is None:
            # Before the row, before the bytes: a refused write leaves no trace.
            raise KnowledgeOperationRequired(operation_required_message(
                f"a write to {path} (from {_describe_caller()})"))
        execution_project, selected_project = _ambient_attribution()
        op = Operation(root=base, writer="implicit", implicit=True,
                       implicit_reason=reason,
                       caller=_describe_caller(),
                       execution_project=execution_project,
                       selected_project=selected_project)
        owns = True
    if op.mutation_id is None:
        op.mutation_id = _insert_mutation(op)
    _append_path(op.mutation_id, "planned_paths", path, base)
    return WriteTicket(mutation_id=op.mutation_id, owns=owns)


def complete_write(ticket, path, root=None) -> None:
    """Record that the bytes landed. Fails closed, loudly.

    If this raises, the file is already published and the ledger does not know
    it — a correctness incident. The store is marked SUSPECT before the error
    propagates, because the alternative is a knowledge base that silently
    forgot a write it performed.
    """
    if ticket is None:
        return
    base = ks.store_root(root)
    try:
        _append_path(ticket.mutation_id, "completed_paths", path, base)
    except Exception:
        _mark_suspect(base)
        raise
    if ticket.owns:
        commit_mutation(ticket.mutation_id, root=base)


def _mark_suspect(root) -> None:
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE knowledge_state SET state = ? WHERE id = 1",
                     (SUSPECT,))
        conn.execute("COMMIT")
    finally:
        conn.close()


def commit_mutation(mutation_id, manifest_digest=None, root=None) -> int:
    """Commit one mutation and advance the revision by exactly one."""
    base = ks.store_root(root)
    if manifest_digest is None:
        from scripts.knowledge_manifest import authoritative_manifest
        manifest_digest = authoritative_manifest(base)
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rev = conn.execute(
                "SELECT current_revision FROM knowledge_state WHERE id = 1"
            ).fetchone()[0]
            new_revision = rev + 1
            _transition(conn, mutation_id, want=ACTIVE, new=COMMITTED,
                        committed_at=_now())
            conn.execute(
                "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
                " manifest_digest = ? WHERE id = 1",
                (new_revision, MANAGED, manifest_digest))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision


def abandon_mutation(mutation_id, reason, root=None) -> str:
    """End a mutation that did not complete.

    Nothing PLANNED -> ABORTED, knowledge state untouched: a command that
    failed on its arguments tore nothing. Anything planned -> ORPHANED and the
    revision becomes SUSPECT. An earlier design keyed this on `completed_paths`,
    which is empty precisely in the crash this is meant to catch.
    """
    base = ks.store_root(root)
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT planned_paths FROM mutations WHERE mutation_id = ?",
                (mutation_id,)).fetchone()
            planned = json.loads((row[0] if row else None) or "[]")
            state = ORPHANED if planned else ABORTED
            _transition(conn, mutation_id, want=ACTIVE, new=state,
                        orphan_reason=reason, orphaned_at=_now(),
                        committed_at=_now())
            if state == ORPHANED:
                conn.execute(
                    "UPDATE knowledge_state SET state = ? WHERE id = 1",
                    (SUSPECT,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return state


def _finish(op: Operation, ok: bool, reason: str = "") -> None:
    if op.mutation_id is None:
        return                       # nothing was ever planned; no row exists
    if ok and op.eager and not _mutation_planned(op):
        # An eager operation that wrote nothing through safe_write: commit
        # only if its direct writes changed the store, else it minted nothing.
        from scripts.knowledge_manifest import authoritative_manifest
        live = authoritative_manifest(op.root)
        if live == current(op.root).manifest_digest:
            abandon_mutation(op.mutation_id, "eager operation changed nothing",
                             root=op.root)
            return
        op.minted_revision = commit_mutation(op.mutation_id, manifest_digest=live, root=op.root)
        return
    if ok:
        op.minted_revision = commit_mutation(op.mutation_id, root=op.root)
    else:
        abandon_mutation(op.mutation_id, reason, root=op.root)


def _mutation_planned(op: Operation) -> bool:
    conn = _connect_ro(op.root)
    try:
        row = conn.execute("SELECT planned_paths FROM mutations"
                           " WHERE mutation_id = ?", (op.mutation_id,)).fetchone()
    finally:
        conn.close()
    return bool(json.loads((row[0] if row else None) or "[]"))


@contextmanager
def operation(writer=None, caller=None, root=None, store_project=None,
              execution_project=None, selected_project=None, eager=False):
    """Scope one logical operation. Joins an enclosing one on the same store.

    No database row is created here. The mutation activates on the first
    planned write, so a read-only command leaves the ledger untouched and
    cannot make a snapshot capture report BUSY.

    There is no `project=` parameter. The one this replaces was filled from the
    registry's globally selected workspace, which is neither the subject of the
    write nor an identity of the caller; passing it recorded `otherproj` for work
    done in the Zurvan repo. A boundary that knows its subject passes
    `store_project`; one that does not leaves it for `declare_store_project`.
    `execution_project` and `selected_project` are captured from the process
    unless given, so every boundary gets them without repeating itself.
    """
    base = ks.store_root(root)
    existing = _CTX.get()
    if existing is not None:
        if existing.root != base:
            raise NestedMutationError(
                f"operation on {base} opened inside one on {existing.root}")
        existing.depth += 1
        try:
            yield existing
        finally:
            existing.depth -= 1
        return

    if execution_project is None and selected_project is None:
        execution_project, selected_project = _ambient_attribution()
    op = Operation(root=base, store_project=store_project,
                   execution_project=execution_project,
                   selected_project=selected_project,
                   writer=writer, caller=caller, eager=eager)
    token = _CTX.set(op)
    try:
        try:
            if eager:
                op.mutation_id = _insert_mutation(op)
            yield op
        except SystemExit as exc:
            # `sys.exit` is control flow, not a crash: `scripts/cli.py` uses it
            # on ~20 dispatch branches. Treating it as a crash marked the
            # PRODUCTION store SUSPECT on every non-zero exit — including
            # `zurvan knowledge reconcile`, which exits 1 on NEEDS_REVIEW and
            # would SUSPECT the store it had just cleared.
            _finish(op, ok=exc.code in (0, None), reason=f"exit {exc.code}")
            raise
        except BaseException as exc:
            _finish(op, ok=False, reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            _finish(op, ok=True)
    finally:
        _CTX.reset(token)


def implicit_mutations(root=None, unresolved_only=True) -> list:
    """Implicit mutations, unresolved by default.

    The migration gate is "no unresolved implicit callers", not "no historical
    implicit rows": counting history made closure reachable only by deleting
    evidence, which this project does not do.
    """
    clause = " AND resolved_at IS NULL" if unresolved_only else ""
    try:
        conn = _connect_ro(root)
    except LedgerError:
        return []
    try:
        try:
            rows = conn.execute(
                f"SELECT {_MUTATION_COLS} FROM mutations"
                f" WHERE implicit = 1{clause} ORDER BY started_at").fetchall()
        except sqlite3.OperationalError:
            return []          # a registry with no ledger tables; see above
    finally:
        conn.close()
    return [_row_to_mutation(r) for r in rows]


def implicit_callers(root=None) -> dict:
    """Unresolved implicit callers and how often each one wrote."""
    summary: dict = {}
    for m in implicit_mutations(root):
        key = m.caller or "unknown"
        summary[key] = summary.get(key, 0) + 1
    return summary


def resolve_implicit(caller, actor, root=None,
                     reason="migrated to an explicit operation") -> int:
    """Mark every unresolved implicit mutation from `caller` as resolved.

    A claim about a caller, which a later implicit write from the same caller
    falsifies by simply appearing in the inventory again. `reason` says what
    made it true; the default was the only answer until 2026-09-28, when the
    honest one for an external caller was "the path was closed", not
    "the caller was migrated".
    """
    conn = _connect(root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE mutations SET resolved_at = ?, resolution_actor = ?,"
            " resolution_reason = ?"
            " WHERE implicit = 1 AND resolved_at IS NULL AND caller = ?",
            (_now(), actor, reason, caller))
        count = cur.rowcount
        conn.execute("COMMIT")
    finally:
        conn.close()
    return count


def _set_revision(conn, new_revision, atomicity, digest) -> None:
    conn.execute(
        "UPDATE knowledge_state SET current_revision = ?, atomicity = ?,"
        " manifest_digest = ? WHERE id = 1",
        (new_revision, atomicity, digest))


def bootstrap(root=None, actor="bootstrap") -> int:
    """Record the stable authoritative manifest of an existing store.

    A fresh ledger has no digest, so it can neither agree nor disagree with the
    filesystem. Treating that as "nothing to contradict" was safe for a
    diagnostic and is not safe for a correctness mechanism: it makes the first
    drift check silently vacuous. Bootstrapping is deliberate, and the revision
    it produces is honestly labelled EXTERNAL_OBSERVED, because Zurvan did not
    witness the operations that produced those bytes.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    ensure_schema(base)
    first = authoritative_manifest(base)
    second = authoritative_manifest(base)
    if first != second:
        raise LedgerError(
            "the store is changing while being bootstrapped; retry when quiet")
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise LedgerError("a mutation is in flight; bootstrap when quiet")
            rev = conn.execute(
                "SELECT current_revision FROM knowledge_state WHERE id = 1"
            ).fetchone()[0]
            new_revision = rev + 1
            _set_revision(conn, new_revision, EXTERNAL_OBSERVED, first)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision


def observe_external_drift(root=None, trigger="manual"):
    """Detect a completed unmanaged edit and record it, in one operation.

    Two different questions, both required:

        M_before == M_after                  these bytes were STABLE
        live == revision.manifest_digest     Zurvan KNEW about them

    A finished Obsidian edit passes stability perfectly and fails the second.
    Detection and minting are one call because splitting them leaves a window
    in which the filesystem can move between what was observed and what is
    recorded.

    Returns the new revision, or `None` when Zurvan's view is already current.
    Refuses on an unbootstrapped store, a SUSPECT store, or an in-flight
    mutation — minting underneath a running operation would attribute its
    half-written files to an external observation.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    state = current(base)
    if not state.manifest_digest:
        raise LedgerError(
            "no manifest recorded for this store; run `zurvan knowledge "
            "bootstrap` once so drift has a baseline to contradict")
    if state.state != CLEAN:
        raise LedgerError(
            f"store is {state.state}; reconcile before observing drift")

    first = authoritative_manifest(base)
    if first == state.manifest_digest:
        return None
    second = authoritative_manifest(base)
    if first != second:
        raise DriftUnobservable("the store is still changing; retry when quiet")

    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise DriftUnobservable("a mutation is in flight; not external drift")
            rev, recorded, st = conn.execute(
                "SELECT current_revision, manifest_digest, state"
                " FROM knowledge_state WHERE id = 1").fetchone()
            if st != CLEAN or recorded != state.manifest_digest:
                raise DriftUnobservable("the ledger moved during observation; retry")
            new_revision = rev + 1
            _set_revision(conn, new_revision, EXTERNAL_OBSERVED, second)
            # Recorded in the same transaction: the managed commit that usually
            # follows overwrites `atomicity`, and without this row the
            # observation would leave no trace (2026-09-28).
            conn.execute(
                "INSERT INTO external_observations (revision, trigger,"
                " manifest_before, manifest_after, observed_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (new_revision, trigger, recorded, second, _now()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return new_revision


def external_observations(root=None) -> list:
    """Every minted EXTERNAL_OBSERVED revision, oldest first. The record
    outlives the managed commit that follows, which overwrites atomicity."""
    conn = _connect_ro(root)
    try:
        return conn.execute(
            "SELECT revision, trigger, manifest_before, manifest_after,"
            " observed_at FROM external_observations ORDER BY revision"
        ).fetchall()
    finally:
        conn.close()


def detect_orphans(root=None) -> list:
    """Transition ACTIVE mutations whose owner is provably dead.

    Only `DEAD` orphans. `UNKNOWN` — a `ps` failure, a timeout, a permission
    problem — leaves the row alone: absence of proof of life is not proof of
    death, and marking a shared store SUSPECT because a subprocess timed out
    would make the flag worthless.

    The decision is keyed on `planned_paths`, not `completed_paths`. A crash
    between publication and bookkeeping leaves `completed_paths` empty while
    the bytes are live, which is exactly the case this exists to catch.

    Candidates are re-read under the lock and transitioned with compare-and-set,
    so a mutation that commits between the liveness check and the update is not
    reclassified from stale information.
    """
    base = ks.store_root(root)
    candidates = [m for m in mutations_in(ACTIVE, base)
                  if process_state(m.owner_pid, m.owner_identity) == DEAD]
    if not candidates:
        return []
    orphaned = []
    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            torn = False
            for m in candidates:
                row = conn.execute(
                    "SELECT state, planned_paths FROM mutations"
                    " WHERE mutation_id = ?", (m.mutation_id,)).fetchone()
                if row is None or row[0] != ACTIVE:
                    continue                    # it finished; leave it alone
                planned = json.loads(row[1] or "[]")
                new = ORPHANED if planned else ABORTED
                _transition(conn, m.mutation_id, want=ACTIVE, new=new,
                            orphan_reason="owner_process_dead",
                            orphaned_at=_now())
                if planned:
                    torn = True
                    orphaned.append(m.mutation_id)
            if torn:
                conn.execute(
                    "UPDATE knowledge_state SET state = ? WHERE id = 1",
                    (SUSPECT,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    found = {m.mutation_id: m for m in mutations_in(ORPHANED, base)}
    return [found[i] for i in orphaned if i in found]


def reconcile_revision(mutation_ids, actor, reason=None, root=None) -> dict:
    """Accept the authoritative state and mint a NEW CLEAN revision.

    The approved design requires `SUSPECT R -> ... -> CLEAN R'`. Flipping
    `state = CLEAN` in place leaves the pre-orphan manifest attached to a
    revision now declared coherent — the laundering the design forbids.

    The new revision is EXTERNAL_OBSERVED: Zurvan did not witness the operation
    that produced these bytes, so it may not claim they were one managed
    transaction. A durable `reconciliations` row records who accepted what.
    """
    from scripts.knowledge_manifest import authoritative_manifest
    base = ks.store_root(root)
    first = authoritative_manifest(base)
    second = authoritative_manifest(base)
    if first != second:
        raise LedgerError("the store is changing; reconcile when quiet")

    conn = _connect(base)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT COUNT(*) FROM mutations WHERE state = ?",
                            (ACTIVE,)).fetchone()[0]:
                raise LedgerError("a mutation is in flight; reconcile when quiet")
            rev, before = conn.execute(
                "SELECT current_revision, manifest_digest"
                " FROM knowledge_state WHERE id = 1").fetchone()
            new_revision = rev + 1
            for mutation_id in mutation_ids:
                _transition(conn, mutation_id, want=ORPHANED, new=RESOLVED,
                            resolved_at=_now(), resolution_actor=actor,
                            resolution_reason=reason,
                            resolution_revision=new_revision)
            conn.execute(
                "UPDATE knowledge_state SET current_revision = ?, state = ?,"
                " atomicity = ?, manifest_digest = ? WHERE id = 1",
                (new_revision, CLEAN, EXTERNAL_OBSERVED, second))
            conn.execute(
                "INSERT INTO reconciliations (reconciliation_id, at, actor,"
                " reason, mutation_ids, manifest_before, manifest_after,"
                " from_revision, to_revision) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"r{uuid.uuid4().hex[:12]}", _now(), actor, reason,
                 json.dumps(list(mutation_ids)), before, second,
                 rev, new_revision))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return {"from_revision": rev, "to_revision": new_revision,
            "manifest_after": second}
