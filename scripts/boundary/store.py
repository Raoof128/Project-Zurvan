"""Content-addressed store, canonical manifest, and validation that can fail.

Three properties the previous draft lacked, each proven missing by execution:

1. Deleting every fingerprint row left `validate()` reporting clean. Validation
   now compares the LIVE index against counts committed in the manifest.
2. `init()` on a built store silently changed k/w, invalidating every
   fingerprint while validation stayed clean. `init()` now refuses.
3. The manifest walked `sources/` only, so the INSTRUMENT tree could change
   invisibly. It now covers every protected class, the config, and the policy.

Protected bytes are copied into immutable content-addressed blobs at build time.
The scanner therefore never reads a mutable path, which removes the TOCTOU window
between validation and confirmation and keeps local paths out of Verdicts.
"""
import hashlib
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1
D1_VERSION = "d1-winnow-v1"
# Unicode data ships with CPython, so casefold results are version-bound. A DB
# built under a different Unicode version must not be trusted silently.
D2_VERSION = f"d2-casefold-v1+py{sys.version_info.major}.{sys.version_info.minor}"
POLICY_VERSION = "policy-v1"
DB_NAME = "boundary.sqlite"
# Default winnowing parameters. T = k + w - 1 = 64, the threshold the spec's
# coincidence measurement was taken at. `build()` uses these when asked to build
# a store that has not been explicitly initialised.
DEFAULT_K = 32
DEFAULT_W = 33
PROTECTED_TREES = (("sources", "PRIVATE"), ("instruments", "INSTRUMENT"))


class StaleStateError(RuntimeError):
    """Protected material or configuration changed after the last build."""


class StateIntegrityError(RuntimeError):
    """Boundary's own state is missing, truncated, inconsistent or incompatible."""


@dataclass(frozen=True)
class Config:
    k: int
    w: int

    def __post_init__(self):
        if self.k < 1 or self.w < 1:
            raise StateIntegrityError(f"invalid winnowing config k={self.k} w={self.w}")

    @property
    def threshold_bytes(self) -> int:
        return self.k + self.w - 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS origins (
    origin_id     TEXT PRIMARY KEY,          -- OPAQUE. never a filesystem path
    source_class  TEXT NOT NULL CHECK (source_class IN ('PRIVATE','INSTRUMENT')),
    blob_digest   TEXT NOT NULL,
    n_fingerprints INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS fingerprints (
    fingerprint BLOB NOT NULL,
    origin_id   TEXT NOT NULL REFERENCES origins(origin_id),
    offset      INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS fp_idx ON fingerprints(fingerprint);
-- validate() aggregates per origin on every scan; without this it degrades to
-- one full table scan PER ORIGIN (301 scans of 796k rows = ~17 s, measured).
CREATE INDEX IF NOT EXISTS fp_origin_idx ON fingerprints(origin_id);
-- one-to-many: two origins may register the same casefolded literal
CREATE TABLE IF NOT EXISTS literals (
    literal_id       TEXT PRIMARY KEY,
    literal_casefold TEXT NOT NULL,
    source_class     TEXT NOT NULL CHECK (source_class IN ('PRIVATE','INSTRUMENT')),
    origin_id        TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lit_idx ON literals(literal_casefold);
CREATE TABLE IF NOT EXISTS public_origins (
    accession_id TEXT PRIMARY KEY,
    blob_digest  TEXT NOT NULL,
    provenance   TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases (
    release_id   TEXT PRIMARY KEY,
    match_id     TEXT NOT NULL,              -- opaque: binds span+class+origin
    detector     TEXT NOT NULL,
    normalisation TEXT NOT NULL,
    reason       TEXT NOT NULL,
    created_at   TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS release_sinks (   -- one row per sink; no LIKE matching
    release_id TEXT NOT NULL REFERENCES releases(release_id),
    sink       TEXT NOT NULL,
    PRIMARY KEY (release_id, sink));
"""

_REQUIRED_TABLES = {"meta", "origins", "fingerprints", "literals",
                    "public_origins", "releases", "release_sinks"}


def _is_built(root) -> bool:
    db = Path(root) / DB_NAME
    if not db.is_file():
        return False
    try:
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT value FROM meta WHERE key='manifest_digest'").fetchone()
        conn.close()
        return row is not None
    except sqlite3.DatabaseError:
        return False


def init(root, k: int, w: int) -> None:
    """Create an empty store. REFUSES to touch one that has been built.

    The previous draft allowed `init()` on a built store, which rewrote k and w
    while leaving every fingerprint — computed under the old k — in place, and
    validation reported clean. Changing the configuration is a rebuild, not an
    edit.
    """
    Config(k=k, w=w)                       # raises on k<1 / w<1 before touching disk
    root = Path(root)
    if _is_built(root):
        raise StateIntegrityError(
            "store already built; changing k/w invalidates every fingerprint. "
            "Delete the store and rebuild deliberately."
        )
    for sub, _cls in PROTECTED_TREES:
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "public").mkdir(exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / DB_NAME)
    conn.executescript(SCHEMA)
    for key, value in (("k", k), ("w", w), ("schema_version", SCHEMA_VERSION),
                       ("d1_version", D1_VERSION), ("d2_version", D2_VERSION),
                       ("policy_version", POLICY_VERSION)):
        conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))
    conn.commit()
    conn.close()


def open_db(root) -> sqlite3.Connection:
    """Open and prove the database is structurally and semantically compatible."""
    db = Path(root) / DB_NAME
    if not db.is_file():
        raise StateIntegrityError(f"Boundary state missing: {db}")
    try:
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise StateIntegrityError(f"SQLite quick_check failed: {db}")
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = _REQUIRED_TABLES - present
        if missing:
            raise StateIntegrityError(f"Boundary schema incomplete, missing {sorted(missing)}")
    except sqlite3.DatabaseError as exc:
        raise StateIntegrityError(f"Boundary state unreadable: {db}: {exc}") from exc
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    for key, expected in (("schema_version", str(SCHEMA_VERSION)),
                          ("d1_version", D1_VERSION),
                          ("d2_version", D2_VERSION),
                          ("policy_version", POLICY_VERSION)):
        if meta.get(key) != expected:
            # A store built by incompatible detector code must never be trusted:
            # its fingerprints and casefolds mean something different.
            raise StateIntegrityError(
                f"incompatible store: {key}={meta.get(key)!r}, this build expects {expected!r}")
    return conn


def read_config(conn) -> Config:
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    try:
        return Config(k=int(meta["k"]), w=int(meta["w"]))
    except (KeyError, ValueError) as exc:
        # int("corrupt") raises ValueError; the promise is StateIntegrityError.
        raise StateIntegrityError(f"Boundary meta malformed: {exc}") from exc


# ── content-addressed blobs ───────────────────────────────────────────────────
def blob_path(root, digest: str) -> Path:
    return Path(root) / "blobs" / digest[:2] / digest


def put_blob(root, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    p = blob_path(root, digest)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
    return digest


def get_blob(root, digest: str) -> bytes:
    """Read a blob and RE-VERIFY it. A blob store that trusts its own filenames
    is a filesystem, not an integrity mechanism."""
    p = blob_path(root, digest)
    if not p.is_file():
        raise StateIntegrityError(f"missing blob {digest[:12]}")
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise StateIntegrityError(f"blob {digest[:12]} does not match its digest")
    return data


# ── canonical manifest ────────────────────────────────────────────────────────
def canonical_manifest_bytes(entries) -> bytes:
    """Length-prefixed, sorted, unambiguous.

    `path + bytes` with no delimiter let `sources/a`+b"bc" and `sources/ab`+b"c"
    produce the same digest. Every field is now length-prefixed, so no boundary
    shift can forge an equal manifest.
    """
    out = bytearray()
    for name, data in sorted(entries):
        nb = name.encode("utf-8")
        out += len(nb).to_bytes(8, "big") + nb
        out += len(data).to_bytes(8, "big") + data
    return bytes(out)


def _protected_entries(root):
    root = Path(root)
    for sub, cls in PROTECTED_TREES:
        d = root / sub
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    yield f"{cls}:{p.relative_to(root)}", p.read_bytes()


def stat_digest(root) -> str:
    """Cheap staleness probe: path + size + mtime, never file contents.

    Re-hashing the protected tree on every scan was measured at ~14.6 s on a
    13 MB corpus — flat regardless of the artefact being scanned, and 36x the
    400 ms budget. This walks the same files but reads no bytes.

    It is an OPTIMISATION, not the authority: it assumes an edit changes size or
    mtime, which holds for the careless authorised agent v1 defends against and
    NOT for a hostile one. `validate(deep=True)` always does the full content
    comparison, and any stat change escalates to it automatically.
    """
    h = hashlib.sha256()
    root = Path(root)
    for sub, cls in PROTECTED_TREES:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(x for x in d.rglob("*") if x.is_file()):
            st = p.stat()
            rec = f"{cls}:{p.relative_to(root)}\x00{st.st_size}\x00{st.st_mtime_ns}".encode()
            h.update(len(rec).to_bytes(8, "big") + rec)
    return h.hexdigest()


def manifest_digest(conn, root) -> str:
    """Covers protected material, configuration, versions AND index shape."""
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    entries = list(_protected_entries(root))
    for key in ("k", "w", "schema_version", "d1_version", "d2_version", "policy_version"):
        entries.append((f"meta:{key}", str(meta.get(key)).encode()))
    for origin_id, n in conn.execute(
            "SELECT origin_id, n_fingerprints FROM origins ORDER BY origin_id"):
        entries.append((f"origin:{origin_id}", str(n).encode()))
    return hashlib.sha256(canonical_manifest_bytes(entries)).hexdigest()


def validate(conn, root, deep: bool = False) -> None:
    """Fail closed. Raises; never returns a verdict.

    Two distinct failures, deliberately distinguished:
      StaleStateError     - the world changed; rebuild
      StateIntegrityError - the store contradicts itself; do not trust it
    """
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    committed = meta.get("manifest_digest")
    if committed is None:
        raise StaleStateError("Boundary index has never been built")

    # Logical index integrity: the count the manifest committed must still be
    # there. Deleting every fingerprint row previously validated CLEAN.
    # Per-scan: ONE total count against the committed total. Any deletion of any
    # fingerprint changes it, which is the false-clean this guards against.
    #
    # Profiled 2026-09-16 on 301 origins / 796k fingerprints: one COUNT per
    # origin cost ~17 s, a GROUP BY ~400 ms, a single total count ~1 ms. The
    # per-origin breakdown moved to deep=True and `boundary status`.
    #
    # Stated bound: a total-preserving delete-and-insert would pass this and be
    # caught only by deep verification. That is a HOSTILE agent, which v1
    # explicitly does not defend against.
    committed_total = meta.get("total_fingerprints")
    if committed_total is None:
        # Fail CLOSED. Skipping the check when the key is absent would make
        # `DELETE FROM meta WHERE key='total_fingerprints'` a one-row bypass of
        # the whole integrity guarantee. A store with a manifest must have a
        # committed total.
        raise StateIntegrityError(
            "store has a manifest but no committed fingerprint total; rebuild")
    # TWO cheap aggregates, because one alone is escapable:
    #   count(fingerprints) catches rows deleted from the index
    #   sum(origins.n_fingerprints) catches the ORIGINS table being tampered with
    # The gauntlet found this: bumping n_fingerprints changed no fingerprint row,
    # so a total-only check passed while the store contradicted itself.
    actual_total = conn.execute("SELECT count(*) FROM fingerprints").fetchone()[0]
    claimed_total = conn.execute(
        "SELECT coalesce(sum(n_fingerprints), 0) FROM origins").fetchone()[0]
    if actual_total != int(committed_total) or claimed_total != int(committed_total):
        raise StateIntegrityError(
            f"fingerprint index disagrees with its manifest: {actual_total} rows "
            f"present, origins claim {claimed_total}, {committed_total} committed")
    if deep:
        actual_counts = dict(conn.execute(
            "SELECT origin_id, count(*) FROM fingerprints GROUP BY origin_id"))
        for origin_id, expected in conn.execute(
                "SELECT origin_id, n_fingerprints FROM origins"):
            if actual_counts.get(origin_id, 0) != expected:
                raise StateIntegrityError(
                    f"fingerprint count for origin {origin_id[:12]}: "
                    f"{actual_counts.get(origin_id, 0)} present, {expected} committed")
    # EXISTS, not COUNT: one orphan is enough to fail, and stopping at the first
    # avoids a full join on every scan.
    if conn.execute(
        "SELECT 1 FROM fingerprints f LEFT JOIN origins o USING(origin_id) "
        "WHERE o.origin_id IS NULL LIMIT 1").fetchone():
        raise StateIntegrityError("fingerprint rows reference an unknown origin")

    # Cheap probe first. Only when it disagrees — or when deep=True — do we pay
    # for reading every protected byte.
    committed_stat = meta.get("stat_digest")
    if not deep and committed_stat is not None and stat_digest(root) == committed_stat:
        return
    if manifest_digest(conn, root) != committed:
        raise StaleStateError(
            "protected material or configuration changed since the last build; "
            "run `zurvan boundary build`")


def atomic_replace(root, build_fn) -> None:
    """build_fn(tmp_db_path) populates a temp DB INCLUDING its manifest digest.

    The manifest is committed inside the temp database before the swap, so the
    published state is never index-from-one-snapshot plus digest-from-another.
    """
    root = Path(root)
    tmp = root / (DB_NAME + ".building")
    if tmp.exists():
        tmp.unlink()
    build_fn(tmp)
    check = sqlite3.connect(tmp)
    if check.execute("SELECT value FROM meta WHERE key='manifest_digest'").fetchone() is None:
        check.close()
        tmp.unlink()
        raise StateIntegrityError("refusing to publish a build with no manifest")
    check.close()
    os.replace(tmp, root / DB_NAME)
