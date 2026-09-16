# Implementation plan — Zurvan Boundary, detection core (Plan 1 of 3)

**Goal:** Implement the Boundary invariants — no protected span reaches a
forbidden sink, and no scan returns `clean` while the store cannot prove its own
integrity. The gauntlet is **evidence**, not the target: a plan aimed at "make the
tests pass" invites test-fitting, and these tests are worth more than that.

**Architecture.** A local, git-invisible store under `~/.zurvan/boundary/` holds a
**content-addressed blob store** of protected material, an approved public-origin
corpus, and one SQLite database. Build copies each protected file into an
immutable blob keyed by its digest, indexes the blob under an **opaque origin
id**, and writes a canonical manifest **into the temp database before** the
atomic swap. A scan opens exactly one database snapshot, verifies the manifest
against the store, and confirms candidates against **blobs it has already
verified** — never against a mutable filesystem path.

**Why content-addressed.** The first draft read source files at scan time, which
created a TOCTOU window (validate, then the file changes, then confirmation reads
the changed bytes), leaked local paths into `Verdict.source_id`, and re-read the
same file once per candidate. One design change closes all three.

**Verified rejection of the previous draft (2026-09-16).** Its modules were
extracted and executed:

```
baseline              : detects=True   validate=CLEAN
after DELETE all fps  : detects=False  validate=CLEAN   <- false clean
after init() k/w swap : detects=False  validate=CLEAN   <- false clean
instruments/ in digest: NO - unchanged
```

`content_digest` also collided without any SHA-256 weakness, because path and
bytes were concatenated with no length delimiter: `sources/a = b"bc"` and
`sources/ab = b"c"` hash identically. Every fix below traces to one of these.

**Tech stack.** Python 3.10+ (matching this project's existing floor — an
earlier draft said 3.11+ and would have bumped the whole project silently),
stdlib `sqlite3`, stdlib `hashlib`, `pytest`.
No new third-party dependency. Follows this repo's existing module style
(`scripts/*.py`, tests in `tests/`).

**Spec:** `docs/superpowers/specs/2026-09-15-zurvan-boundary-design.md`

**Decomposition — this plan is 1 of 3. Do not start 2 or 3 from this document.**

| Plan | Covers | Gauntlet rows |
| --- | --- | --- |
| **1 (this)** | detection core | 5, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17 + verdict hygiene |

**Acceptance criteria and who discharges them.** 7/7 transfer-leak recall: Plan 1
(row 15 covers INSTRUMENT; the PRIVATE cases are rows 5/8/9). FP ceiling: Plan 1,
row 17. **Latency p95 ≤ 400 ms: Plan 2** — it needs the real corpus, and note
that per-scan full-corpus revalidation is the first thing that budget will
reject, which is why validation is manifest-based rather than a re-walk.
| 2 | git integration: staged post-image scanning, renames, CLI, `check.sh` gate | 12, 13 |
| 3 | instrument migration (§15c) — moves `eval/search_gold.jsonl` out of git | — |

**Plan 3 is a PREREQUISITE for enabling the gate, not for building this core.**
Build and test this plan against synthetic fixtures; do not wire Boundary into
`check.sh` until Plan 3 lands, or its first honest run blocks this repository.

---

## Global Constraints

Copied verbatim from the spec. **Every task below implicitly includes this
section.**

1. **Three blocked (source class, sink) pairs across two protected source
   classes:** `PRIVATE → PUBLIC_ARTIFACT`, `INSTRUMENT → PUBLIC_ARTIFACT`,
   `INSTRUMENT → RETRIEVAL_CORPUS`. Not two.
2. **Sink labels are NON-EXCLUSIVE, and deny overrides allow.** Every applicable
   sink label is evaluated and any BLOCK wins.
3. **`T` is defined in terms of `k` and `w`, not chosen independently.** For
   standard winnowing every shared substring of length ≥ `k + w - 1` is detected,
   so `T = k + w - 1`.
4. **Postings are one-to-many.** A fingerprint maps to a LIST of
   `(source_id, offset)`. All postings resolve, then byte-compare.
5. **A fingerprint hit is a candidate only.** The verdict always comes from a
   byte-exact comparison against the original source.
6. **D3 subtracts at span level, never source level**, and is **source-class and
   sink aware**: public-origin subtraction applies to `PRIVATE`, never to
   `INSTRUMENT → RETRIEVAL_CORPUS`.
7. **The trusted public corpus is never seeded from "everything currently
   tracked."** Only approved, reviewed, known-clean provenance, recording
   accession provenance and digest per entry.
8. **Stale, missing, partial or mismatched Boundary state is a hard failure,
   never a "clean" result.** Index replacement is atomic.
9. **D2 matching uses Unicode casefold (not `lower()`)**, and the casefold
   version is recorded in each release.
10. **A release binds five fields:** span digest, source class + origin id,
    permitted sink(s), detector normalisation version, timestamp. Never the
    private source path, title, or quoted text.
11. **A Verdict never carries protected cleartext.** `span_digest` only.
12. **The store lives outside the git tree** at `~/.zurvan/boundary/`.
13. **No gold query text and no private project name in any tracked file.**
14. Acceptance: **7/7 recall** on the transfer leaks, **≤ 1 blocked artefact per
    200 clean**, **added scan latency p95 ≤ 400 ms**.

---

## Task 1 — Content-addressed store, canonical manifest, real validation

Discharges gauntlet rows **11** (stale index) and **16** (gate self-protection).
The previous draft discharged **neither**: deleting every fingerprint row left
`validate()` reporting clean.

**Files**
- Create: `scripts/boundary/store.py`
- Test: `tests/test_boundary_store.py`

**Interfaces PROVIDED**

```python
SCHEMA_VERSION = 1
D1_VERSION = "d1-winnow-v1"
D2_VERSION = "d2-casefold-v1"       # bound to sys.version_info for Unicode data
POLICY_VERSION = "policy-v1"
DB_NAME = "boundary.sqlite"

class StaleStateError(RuntimeError): ...
class StateIntegrityError(RuntimeError): ...

class Config:
    k: int
    w: int
    threshold_bytes: int             # derived: k + w - 1

def init(root, k: int, w: int) -> None          # REFUSES an existing built store
def open_db(root) -> sqlite3.Connection         # schema + version + quick_check
def read_config(conn) -> Config                      # takes the OPEN CONNECTION
def blob_path(root, digest: str) -> Path
def put_blob(root, data: bytes) -> str          # returns content digest
def get_blob(root, digest: str) -> bytes        # re-verifies digest on read
def manifest_digest(conn, root) -> str          # canonical, length-prefixed
def validate(conn, root) -> None                # raises; never returns a verdict
def atomic_replace(root, build_fn) -> None      # build_fn(tmp_db_path) -> None
```

**Every function that reads state takes the OPEN CONNECTION**, not a root. The
previous draft opened the database three times per scan (`validate`, `config`,
`open_db`), so an atomic rebuild between those opens could hand the scanner a
different store mid-scan.

### Steps

**1.1 — Failing tests: the three false-cleans that rejected the last draft.**

```python
# tests/test_boundary_store.py
import pytest
from scripts.boundary import store

K, W = 8, 9

def _built(tmp_path, data=b"PROTECTED-SENTENCE-LONGER-THAN-THE-THRESHOLD-BY-FAR"):
    root = tmp_path / "b"
    store.init(root, k=K, w=W)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources" / "a.md").write_bytes(data)
    from scripts.boundary import scan as pipeline
    pipeline.build(root)
    return root

def test_deleting_every_fingerprint_is_detected(tmp_path):
    """The attack that rejected the previous draft: validate() said CLEAN."""
    root = _built(tmp_path)
    conn = store.open_db(root)
    conn.execute("DELETE FROM fingerprints"); conn.commit()
    with pytest.raises(store.StateIntegrityError):
        store.validate(conn, root)

def test_changing_k_or_w_after_build_is_refused_not_silent(tmp_path):
    """init() on a built store previously mutated k/w and left validate() clean."""
    root = _built(tmp_path)
    with pytest.raises(store.StateIntegrityError):
        store.init(root, k=32, w=33)

def test_instrument_tree_is_covered_by_the_manifest(tmp_path):
    """The previous manifest walked sources/ only, so INSTRUMENT edits were invisible."""
    root = _built(tmp_path)
    conn = store.open_db(root)
    store.validate(conn, root)
    (root / "instruments").mkdir(exist_ok=True)
    (root / "instruments" / "gold.jsonl").write_text("added after build", encoding="utf-8")
    with pytest.raises(StaleStateError := store.StaleStateError):
        store.validate(conn, root)

def test_manifest_is_unambiguous_under_path_boundary_shifts(tmp_path):
    """`sources/a`+b"bc" and `sources/ab`+b"c" previously hashed identically.

    No SHA-256 weakness was needed — the digest concatenated path and bytes with
    no length delimiter.
    """
    a = store.canonical_manifest_bytes([("sources/a", b"bc")])
    b = store.canonical_manifest_bytes([("sources/ab", b"c")])
    assert a != b
```

**1.2 — Run, watch them fail.**

**1.3 — Code.**

```python
# scripts/boundary/store.py
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


def validate(conn, root) -> None:
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
    for origin_id, expected in conn.execute("SELECT origin_id, n_fingerprints FROM origins"):
        actual = conn.execute(
            "SELECT count(*) FROM fingerprints WHERE origin_id = ?", (origin_id,)).fetchone()[0]
        if actual != expected:
            raise StateIntegrityError(
                f"fingerprint count for origin {origin_id[:12]}: {actual} present, "
                f"{expected} committed")
    orphans = conn.execute(
        "SELECT count(*) FROM fingerprints f LEFT JOIN origins o USING(origin_id) "
        "WHERE o.origin_id IS NULL").fetchone()[0]
    if orphans:
        raise StateIntegrityError(f"{orphans} fingerprint rows reference no origin")

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
```

**1.4 — Run the tests, watch them pass.** If any fails, fix `store.py`. Never the
test: these four are the reproductions that rejected the previous draft.

**1.5 — Commit.** `zurvan: boundary content-addressed store and real validation`

---

## Task 2 — D1: winnowed fingerprints with byte-exact confirmation

Discharges gauntlet rows **5** (T edges) and **6** (one-to-many postings).

**Files**
- Create: `scripts/boundary/d1.py`
- Test: `tests/test_boundary_d1.py`

**Interfaces CONSUMED from Task 1:** `store.Config`, `store.open_db`,
`store.config`.

**Interfaces PROVIDED**

```python
# scripts/boundary/d1.py
def kgram_hashes(data: bytes, k: int) -> list[int]
def winnow(hashes: list[int], w: int) -> list[tuple[int, int]]   # (hash, offset)
def index_source(conn, origin_id: str, data: bytes, cfg) -> int  # rows written
def candidates(conn, data: bytes, cfg) -> list[tuple[str, int, int]]
                                          # (origin_id, src_offset, cand_offset)
def confirm(source_bytes, src_offset, data, cand_offset) -> tuple[int, int]
                                          # (match_start_in_data, match_length)
                                          # takes the SOURCE BYTES, not a root+id:
                                          # the caller already loaded them, and a
                                          # second read here would be a second
                                          # chance to read the wrong source
```

### Steps

**2.1 — Failing test for the winnowing guarantee.**

```python
# tests/test_boundary_d1.py
import pytest
from scripts.boundary import d1, store

K, W = 32, 33          # T = 64

def test_winnowing_detects_every_shared_substring_of_length_k_plus_w_minus_1():
    """The guarantee, stated as a property rather than an example."""
    import random
    random.seed(4)
    corpus = bytes(random.randrange(256) for _ in range(5000))
    T = K + W - 1
    hs = d1.winnow(d1.kgram_hashes(corpus, K), W)
    selected = {off for _, off in hs}
    for start in range(0, len(corpus) - T, 97):
        window = range(start, start + T - K + 1)
        assert selected & set(window), f"no fingerprint inside a {T}-byte span at {start}"

def test_a_span_one_byte_short_of_T_may_be_missed_and_that_is_the_stated_bound():
    """Honest bound: < T is NOT guaranteed. Asserting otherwise would overclaim."""
    assert (K + W - 1) == 64
```

**2.2 — Run, watch it fail** (`ModuleNotFoundError: scripts.boundary.d1`).

**2.3 — Minimal code.**

```python
# scripts/boundary/d1.py
"""D1 — long-span verbatim matcher.

Winnowed k-gram fingerprints locate CANDIDATES; the verdict always comes from a
byte-exact comparison against the original source (Global Constraint 5). Hashes
never decide, so a collision cannot produce a false block.

Reused primitive, not a new invention: Project Simurgh's Stage 4X/4Y (VDR,
Verifiable Document Residue) already builds residue detection over a corpus. VDR
signs "coverage geometry, not leakage detection" and "the Map Is Not a Verdict";
this module points the same primitive at an egress sink and supplies the verdict
VDR declines to render.
"""
BASE = 257
MOD = (1 << 61) - 1


def kgram_hashes(data: bytes, k: int) -> list[int]:
    """Rabin-Karp rolling hashes of every k-gram. O(len(data))."""
    if len(data) < k:
        return []
    high = pow(BASE, k - 1, MOD)
    h = 0
    for b in data[:k]:
        h = (h * BASE + b) % MOD
    out = [h]
    for i in range(k, len(data)):
        h = ((h - data[i - k] * high) * BASE + data[i]) % MOD
        out.append(h)
    return out


def winnow(hashes: list[int], w: int) -> list[tuple[int, int]]:
    """Schleimer winnowing: in each window of w hashes select the minimum.

    Ties break on the RIGHTMOST occurrence, consistently, so two identical texts
    always select identical fingerprints regardless of surrounding context.
    """
    if len(hashes) < w:
        return []
    out, prev = [], -1
    for start in range(len(hashes) - w + 1):
        window = hashes[start:start + w]
        m = min(window)
        idx = start + (len(window) - 1 - window[::-1].index(m))
        if idx != prev:
            out.append((m, idx))
            prev = idx
    return out


def index_source(conn, origin_id: str, data: bytes, cfg) -> int:
    """Index one origin's blob. The origin row must already exist (FK)."""
    rows = [(h.to_bytes(8, "big"), origin_id, off)
            for h, off in winnow(kgram_hashes(data, cfg.k), cfg.w)]
    conn.executemany("INSERT INTO fingerprints VALUES (?,?,?)", rows)
    return len(rows)


def candidates(conn, data: bytes, cfg):
    """Every posting for every matching fingerprint — Global Constraint 4.

    Returning only the first posting is the defect this exists to avoid: a span
    duplicated across a public and a protected source would resolve to the public
    origin and the leak would read clean.
    """
    out = []
    for h, cand_off in winnow(kgram_hashes(data, cfg.k), cfg.w):
        for origin_id, src_off in conn.execute(
            "SELECT origin_id, offset FROM fingerprints WHERE fingerprint = ?",
            (h.to_bytes(8, "big"),),
        ):
            out.append((origin_id, src_off, cand_off))
    return out


def confirm(source_bytes: bytes, src_offset: int,
            data: bytes, cand_offset: int) -> tuple[int, int]:
    """Byte-exact confirmation. Returns (match_start_in_data, match_length).

    Expands the anchor both ways for as long as the bytes agree, then reports the
    true span — the fingerprint only located it.
    """
    lo_s, lo_d = src_offset, cand_offset
    while lo_s > 0 and lo_d > 0 and source_bytes[lo_s - 1] == data[lo_d - 1]:
        lo_s -= 1
        lo_d -= 1
    hi_s, hi_d = src_offset, cand_offset
    while (hi_s < len(source_bytes) and hi_d < len(data)
           and source_bytes[hi_s] == data[hi_d]):
        hi_s += 1
        hi_d += 1
    return lo_d, hi_d - lo_d
```

**2.4 — Run, watch pass.**

**2.5 — Failing test for one-to-many postings.**

```python
def test_a_fingerprint_with_two_origins_returns_both_postings(tmp_path):
    root = tmp_path / "b"; store.init(root, k=K, w=W)
    cfg = store.config(root); conn = store.open_db(root)
    shared = b"A" * 40 + bytes(range(60)) + b"B" * 40
    d1.index_source(conn, "public-1", shared, cfg)
    d1.index_source(conn, "private-1", shared, cfg)
    conn.commit()
    origins = {src for src, _, _ in d1.candidates(conn, shared, cfg)}
    assert origins == {"public-1", "private-1"}
```

**2.6 — Run, watch pass.** No code change needed; if it fails, `candidates` is
keeping one posting — fix `d1.py`.

**2.7 — Commit.** `zurvan: boundary D1, winnowed fingerprints, byte-exact confirm`

---

## Task 3 — D2: salient-literal registry with Unicode casefold

Discharges gauntlet row **7**.

**Files**
- Create: `scripts/boundary/d2.py`
- Test: `tests/test_boundary_d2.py`

**Interfaces CONSUMED:** `store.open_db`, `store.NORMALISATION_VERSION`.

**Interfaces PROVIDED**

```python
def register(conn, literal: str, source_class: str, origin_id: str) -> str
         # returns an opaque literal_id (HMAC over literal+origin)
def match(conn, text: str) -> list[tuple[str, str, str]]
         # (literal_id, source_class, origin_id) — EVERY matching origin
```

### Steps

**3.1 — Failing test, all four casings.**

```python
# tests/test_boundary_d2.py
import pytest
from scripts.boundary import d2, store

@pytest.mark.parametrize("form", ["Vantablack", "vantablack", "VANTABLACK", "vAnTaBlAcK"])
def test_every_casing_matches(tmp_path, form):
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    d2.register(conn, "Vantablack", "PRIVATE", ["PUBLIC_ARTIFACT"])
    assert d2.match(conn, f"a note about {form} today")

def test_german_sharp_s_needs_casefold_not_lower(tmp_path):
    """`lower()` maps neither 'ß'->'ss' nor 'SS'->'ss'; casefold does both.

    This is why Global Constraint 9 names casefold specifically: a `lower()`
    implementation passes the four ASCII cases above and silently fails here.
    """
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    d2.register(conn, "Straße", "PRIVATE", ["PUBLIC_ARTIFACT"])
    assert d2.match(conn, "the STRASSE project")
```

**3.2 — Run, watch fail.**

**3.3 — Minimal code.**

```python
# scripts/boundary/d2.py
"""D2 — salient-literal registry.

Named "literal", not "identifier": a complete evaluation item is neither short
nor an identifier, and it belongs in this detector because no span rule can reach
a string below T.

Cleartext lives ONLY here, in the local store (Global Constraint 10/13). Tracked
metadata carries the hash. A SHA-256 cannot locate an arbitrary case-insensitive
substring, so a searchable representation must exist locally — that is a
deliberate trade, not an oversight.
"""
import hashlib
import hmac
import secrets


def _registry_key(conn) -> bytes:
    """Per-store HMAC key, generated once at first use and never exported."""
    row = conn.execute("SELECT value FROM meta WHERE key='literal_hmac_key'").fetchone()
    if row is None:
        key = secrets.token_hex(32)
        conn.execute("INSERT INTO meta VALUES ('literal_hmac_key', ?)", (key,))
        conn.commit()
    else:
        key = row[0]
    return bytes.fromhex(key)


def _fold(s: str) -> str:
    # Global Constraint 9. On 2026-09-15 a redaction pass covering lower and
    # upper case missed a title-cased occurrence, which survived into a later
    # commit.
    #
    # casefold() is required rather than lower() because `"ß".lower()` is still
    # `"ß"` while `"ß".casefold()` is `"ss"` — so lower() cannot match "Straße"
    # against "STRASSE". (An earlier comment here also claimed lower() fails on
    # "SS"; it does not — `"SS".lower()` is `"ss"`. The asymmetry is entirely on
    # the ß side.)
    return s.casefold()


def register(conn, literal: str, source_class: str, origin_id: str) -> str:
    """Register one literal FOR ONE ORIGIN. Returns the opaque literal_id.

    The previous draft made the casefolded literal the primary key with INSERT OR
    REPLACE, so registering the same folded string for a second origin DELETED
    the first. Two projects sharing a codename, or the same string protected as
    both PRIVATE and INSTRUMENT, silently lost one registration — and with it,
    one class of block.

    `literal_id` is an HMAC over (literal, origin), not a bare SHA-256 of the
    literal: a 5-byte project name has far too little entropy to survive a bare
    digest appearing in a Verdict that reaches a CI log.
    """
    if not literal or not literal.strip():
        raise ValueError("refusing to register an empty literal: it matches everything")
    if source_class not in ("PRIVATE", "INSTRUMENT"):
        raise ValueError(f"unknown source class {source_class!r}")
    literal_id = hmac.new(_registry_key(conn),
                          f"{origin_id}\x00{literal}".encode("utf-8"),
                          hashlib.sha256).hexdigest()
    conn.execute("INSERT OR REPLACE INTO literals VALUES (?,?,?,?)",
                 (literal_id, _fold(literal), source_class, origin_id))
    conn.commit()
    return literal_id


def match(conn, text: str):
    """[(literal_id, source_class, origin_id)] — every matching origin, not one."""
    folded = _fold(text)
    return [(literal_id, source_class, origin_id)
            for literal_id, lit, source_class, origin_id in conn.execute(
                "SELECT literal_id, literal_casefold, source_class, origin_id FROM literals")
            if lit in folded]
```

**3.4 — Run, watch pass.** If the `ß` test fails, the implementation used
`lower()` — fix `d2.py`, never the test.

**3.5 — Commit.** `zurvan: boundary D2, salient literals, Unicode casefold`

---

## Task 4 — D3: span-level, class-aware public-origin subtraction

Discharges gauntlet rows **8** (laundering) and **9** (partial overlap).

**Files**
- Create: `scripts/boundary/d3.py`
- Test: `tests/test_boundary_d3.py`

**Interfaces CONSUMED:** `store.open_db`.

**Interfaces PROVIDED**

```python
def accede(root, conn, document: bytes, provenance: str) -> str
         # accessions an approved public DOCUMENT; returns accession_id
def is_exempt(root, conn, span_bytes: bytes, source_class: str, sink: str) -> bool
         # containment inside an approved document, not digest equality
```

### Steps

**4.1 — Failing tests: class-awareness and the laundering guard.**

```python
# tests/test_boundary_d3.py
from scripts.boundary import d3, store

SPAN = b"the quarterly containment review concluded that the threshold moved"

def test_public_origin_exempts_PRIVATE(tmp_path):
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    d3.accede(conn, SPAN.decode(), accession="reviewed-release-v1")
    assert d3.is_exempt(conn, SPAN, "PRIVATE", "PUBLIC_ARTIFACT")

def test_public_origin_does_NOT_exempt_INSTRUMENT_into_the_retrieval_corpus(tmp_path):
    """Global Constraint 6. Measurement integrity is not confidentiality.

    An evaluation item whose words also occur publicly still matches its own
    answer and still contaminates. Exempting it would reintroduce the exact
    failure of 2026-09-15 under a rule that looks principled.
    """
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    d3.accede(conn, SPAN.decode(), accession="reviewed-release-v1")
    assert not d3.is_exempt(conn, SPAN, "INSTRUMENT", "RETRIEVAL_CORPUS")

def test_accession_requires_an_explicit_provenance_label(tmp_path):
    """Global Constraint 7: never seed from 'everything currently tracked'."""
    import pytest
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    with pytest.raises(ValueError):
        d3.accede(conn, SPAN.decode(), accession="")
```

**4.2 — Run, watch fail. 4.3 — Minimal code.**

```python
# scripts/boundary/d3.py
"""D3 — known-public origin resolver.

Subtraction happens at the MATCHED SPAN level, never by exempting a whole source
(Global Constraint 6). Exempting a source permits laundering: drop protected text
into a source marked public and everything it contains becomes quotable.

Measured justification (spec §8): against 11.16 MB of real sources and 1.69 MB of
tracked code, 98 spans still collide at T=128, and the samples are the project's
own ingested documentation. Without D3, D1's first real run blocks a commit for
quoting the repository's own README.
"""
import hashlib

# Global Constraint 6: which (class, sink) pairs may ever be exempted by public
# origin. INSTRUMENT -> RETRIEVAL_CORPUS is deliberately absent.
# Only pairs the policy actually blocks can be exempted; listing others implies
# a check that never fires. INSTRUMENT is absent by design (Global Constraint 6):
# an evaluation item whose words also occur publicly still matches its own answer.
_EXEMPTIBLE = {("PRIVATE", "PUBLIC_ARTIFACT")}


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def accede(root, conn, document: bytes, provenance: str) -> str:
    """Admit one approved public DOCUMENT, storing its bytes as a blob.

    The previous draft accessioned an exact span digest, which made this an
    exact-span whitelist rather than a public corpus: an approved public document
    did NOT exempt a legitimate substring of itself, so a correctly-quoted public
    sentence still blocked. Reproduced before rewriting.

    `provenance` is metadata, not a trust boundary — "not empty" proves nothing
    about approval. The caller is responsible for only acceding reviewed material
    (Global Constraint 7); this records WHAT was acceded and WHY so that a later
    audit can challenge it.
    """
    if not provenance or not provenance.strip():
        raise ValueError(
            "accession provenance is required; the public corpus is never seeded "
            "from 'everything currently tracked' (Global Constraint 7)")
    from scripts.boundary import store
    blob_digest = store.put_blob(root, document)
    accession_id = _digest(f"{blob_digest}\x00{provenance}".encode())
    conn.execute("INSERT OR REPLACE INTO public_origins VALUES (?,?,?)",
                 (accession_id, blob_digest, provenance))
    conn.commit()
    return accession_id


def is_exempt(root, conn, span_bytes: bytes, source_class: str, sink: str) -> bool:
    """True when this exact span occurs INSIDE an approved public document.

    Containment, not digest equality. A public document exempts its own
    substrings; it does not exempt anything longer that merely starts with one
    (the partial-overlap escape, which the residual test pins).
    """
    if (source_class, sink) not in _EXEMPTIBLE:
        return False
    if len(span_bytes) == 0:
        return False
    from scripts.boundary import store
    for (blob_digest,) in conn.execute("SELECT blob_digest FROM public_origins"):
        if span_bytes in store.get_blob(root, blob_digest):
            return True
    return False
```

**4.4 — Run, watch pass.**

**4.5 — Failing test: partial overlap must not exempt the residual.**

```python
def test_a_public_subspan_does_not_exempt_the_longer_protected_span(tmp_path):
    root = tmp_path / "b"; store.init(root, k=32, w=33); conn = store.open_db(root)
    d3.accede(conn, "the quarterly containment review concluded that",
              accession="reviewed-release-v1")
    assert not d3.is_exempt(conn, SPAN, "PRIVATE", "PUBLIC_ARTIFACT")
```

**4.6 — Run, watch pass.** Exemption keys on the digest of the *confirmed span*,
so a shorter public prefix never matches the longer residual.

**4.7 — Commit.** `zurvan: boundary D3, span-level class-aware subtraction`

---

## Task 5 — Policy engine, Verdict, and release ledger

Discharges rows **10** (deny-overrides-allow), **14** (release isolation),
**15** (instrument), **17** (FP ceiling) and verdict hygiene.

**Files**
- Create: `scripts/boundary/policy.py`
- Create: `scripts/boundary/scan.py`
- Test: `tests/test_boundary_policy.py`

**Interfaces CONSUMED:** `store`, `d1`, `d2`, `d3` as above.

**Interfaces PROVIDED — these are the names `tests/test_boundary_gauntlet.py`
already calls, and they must match exactly:**

```python
# scripts/boundary/__init__.py re-exports:
build(root) -> BuildResult
scan(root, text: str, sink: str | list[str]) -> Verdict
scan_blob(root, path: str, blob: str, sink, change_type: str = "modify") -> Verdict
    # `root` is EXPLICIT on every entry point. The first draft omitted it from
    # scan/scan_blob while every other function took it, which forces an implicit
    # process-global store set by build(). For a gate that is a correctness
    # hazard, not a style one: a scan against the wrong store reports a false
    # "clean", which is the single worst output this system can produce.
config(root) -> Config
register_literal(root, literal, source_class, forbidden_sinks) -> None
accede_public(root, span, reason) -> None
release(root, span, sink, reason) -> str
StaleStateError, StateIntegrityError

class Match:  source_id, source_class, detector, span_digest, sink
class Verdict: blocked: bool, matches: list[Match]
```

### Steps

**5.1 — Failing test: the three blocked pairs, and only three.**

```python
# tests/test_boundary_policy.py
import pytest
from scripts.boundary import policy

BLOCKED = {("PRIVATE", "PUBLIC_ARTIFACT"),
           ("INSTRUMENT", "PUBLIC_ARTIFACT"),
           ("INSTRUMENT", "RETRIEVAL_CORPUS")}

def test_exactly_three_blocked_pairs():
    """Global Constraint 1. An earlier spec draft said 'two', which an
    implementer could satisfy by omitting a policy entirely."""
    assert policy.blocked_pairs() == BLOCKED

@pytest.mark.parametrize("cls,sink", sorted(BLOCKED))
def test_each_blocked_pair_blocks(cls, sink):
    assert policy.is_blocked(cls, [sink])

def test_deny_overrides_allow_across_applicable_labels():
    """Global Constraint 2. PRIVATE->RETRIEVAL is allow, PRIVATE->PUBLIC is
    block; a file carrying both labels must block."""
    assert policy.is_blocked("PRIVATE", ["RETRIEVAL_CORPUS", "PUBLIC_ARTIFACT"])
    assert not policy.is_blocked("PRIVATE", ["RETRIEVAL_CORPUS"])
```

**5.2 — Run, watch fail. 5.3 — Minimal code.**

```python
# scripts/boundary/policy.py
"""The source-class x sink-class matrix.

Organised around sinks rather than "public" because of leak #1 of 2026-09-15: an
evaluation item quoted into a page that was never tracked and never pushed still
outranked that item's own answer once indexed. Contamination with zero
publication — a failure no confidentiality-shaped model describes.
"""
SINKS = ("PUBLIC_ARTIFACT", "RETRIEVAL_CORPUS", "EVAL_OUTPUT", "PRIVATE_LOCAL")

_BLOCKED = frozenset({
    ("PRIVATE", "PUBLIC_ARTIFACT"),
    ("INSTRUMENT", "PUBLIC_ARTIFACT"),
    ("INSTRUMENT", "RETRIEVAL_CORPUS"),
})


def blocked_pairs() -> set:
    return set(_BLOCKED)


SOURCE_CLASSES = ("PRIVATE", "INSTRUMENT", "PUBLIC")


class PolicyError(ValueError):
    """An unrecognised class or an empty sink set. Always fatal, never 'allow'."""


def is_blocked(source_class: str, sinks) -> bool:
    """Deny overrides allow (Global Constraint 2), and unknown input FAILS CLOSED.

    The previous draft returned `any(...)` over an unrecognised class, so a typo
    produced ALLOW: `is_blocked("PRVATE", ["PUBLIC_ARTIFACT"])` was False. An
    empty sink list did the same. For a gate, a misspelling must never be the
    difference between blocked and published.
    """
    if isinstance(sinks, str):
        sinks = [sinks]
    sinks = list(sinks)
    if not sinks:
        raise PolicyError("empty sink set: a scan must name at least one sink")
    if source_class not in SOURCE_CLASSES:
        raise PolicyError(
            f"unknown source class {source_class!r}; expected one of {SOURCE_CLASSES}")
    unknown = set(sinks) - set(SINKS)
    if unknown:
        raise PolicyError(f"unknown sink label(s): {sorted(unknown)}")
    return any((source_class, s) in _BLOCKED for s in sinks)
```

**5.4 — Run, watch pass.**

**5.5 — Failing test: a Verdict must never echo cleartext.**

```python
def test_verdict_never_carries_protected_cleartext(tmp_path, monkeypatch):
    """A Verdict is printed into CI logs. If it echoes the matched span, the
    detector becomes the exfiltration path it exists to prevent."""
    from scripts.boundary import scan as scan_mod
    m = scan_mod.Match(source_id="s1", source_class="PRIVATE", detector="D1",
                       span_digest="a" * 64, sink="PUBLIC_ARTIFACT")
    v = scan_mod.Verdict(blocked=True, matches=[m])
    text = repr(v) + repr(m) + str(v) + str(m)
    assert "quarterly containment" not in text
    assert len(m.span_digest) == 64
    assert not hasattr(m, "span")        # no cleartext attribute at all
```

**5.6 — Run, watch fail. 5.7 — Minimal code.**

```python
# scripts/boundary/scan.py
"""Verdict types and the scan pipeline.

`Match` carries a digest and never the span (Global Constraint 11). `__slots__`
makes that structural rather than a convention — a later contributor cannot
attach cleartext to a Match without changing this line.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Match:
    """One protected span reaching one forbidden sink.

    `origin_id` is OPAQUE — never a filesystem path. The previous draft put
    `path.relative_to(root)` here, so a Verdict printed into a CI log disclosed
    the directory layout of the protected store.

    `match_id` binds span + class + origin + detector, and is what a release
    references. Releasing "these bytes" alone would declassify the same text
    arriving from a different protected origin.
    """
    __slots__ = ("match_id", "origin_id", "source_class", "detector", "span_digest", "sink")
    match_id: str
    origin_id: str
    source_class: str
    detector: str
    span_digest: str
    sink: str


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    matches: tuple = ()          # tuple, not list: a frozen dataclass holding a
                                 # list is not frozen — the previous draft's
                                 # Verdict could be mutated after construction.
```

**5.8 — Run, watch pass. 5.9 — Commit.**
`zurvan: boundary policy matrix, verdict hygiene, release ledger`

---

## Task 6 — The scan pipeline

Turns the remaining gauntlet rows green. **This task shows its code**; an earlier
draft said "implement until the gauntlet is green", which is a placeholder
wearing a task's clothes.

**Files**
- Modify: `scripts/boundary/scan.py`
- Modify: `scripts/boundary/__init__.py`
- Test: `tests/test_boundary_gauntlet.py` — **already written; do not edit it**

**Interfaces CONSUMED:** everything from Tasks 1–5.

### Steps

**6.1 — Failing test.** Run `PYTHONPATH=. python -m pytest -q
tests/test_boundary_gauntlet.py -rs`. It skips on `importorskip` until
`scripts/boundary` exists with the full API; after Task 5 it will fail on
`AttributeError: module 'scripts.boundary' has no attribute 'build'`. That is the
right failure.

**6.2 — Code.**

```python
# scripts/boundary/scan.py  (append below Match/Verdict from Task 5)
import hashlib
import hmac
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.boundary import d1, d2, d3, policy, store


@dataclass(frozen=True)
class BuildResult:
    manifest_digest: str
    n_origins: int
    n_fingerprints: int


def _opaque_origin_id(conn, rel_path: str, source_class: str) -> str:
    """Stable, opaque, and NOT a filesystem path.

    Derived by HMAC so the same file yields the same id across rebuilds (releases
    keep referring to the right origin) while a Verdict never discloses where the
    protected store keeps its files.
    """
    return hmac.new(d2._registry_key(conn),
                    f"{source_class}\x00{rel_path}".encode("utf-8"),
                    hashlib.sha256).hexdigest()


def build(root) -> BuildResult:
    """Index every protected file from an IMMUTABLE snapshot, then publish.

    Order matters and the previous draft had it wrong: it indexed from mutable
    files, replaced the database, and only then re-read the disk to record a
    digest — so bytes indexed and bytes digested could differ. Here each file is
    copied into a content-addressed blob FIRST, everything downstream reads the
    blob, and the manifest is committed inside the temp database before the swap.
    """
    root = Path(root)
    counts = {"origins": 0, "fingerprints": 0}

    def populate(tmp_db: Path) -> None:
        with closing(store.open_db(root)) as live:
            live.execute("VACUUM INTO ?", (str(tmp_db),))   # exact copy, sqlite >= 3.27
        with closing(sqlite3.connect(tmp_db)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            cfg = store.read_config(conn)
            conn.execute("DELETE FROM fingerprints")
            conn.execute("DELETE FROM origins")
            for sub, cls in store.PROTECTED_TREES:
                d = root / sub
                if not d.is_dir():
                    continue
                for path in sorted(p for p in d.rglob("*") if p.is_file()):
                    data = path.read_bytes()
                    blob_digest = store.put_blob(root, data)      # immutable snapshot
                    rel = str(path.relative_to(root))
                    origin_id = _opaque_origin_id(conn, rel, cls)
                    # The origin row MUST exist before its fingerprints: the
                    # foreign key rejects the reverse order. Found by executing
                    # this plan, not by reading it — the count is then corrected
                    # in place so `origins.n_fingerprints` is what validate()
                    # compares against.
                    conn.execute("INSERT OR REPLACE INTO origins VALUES (?,?,?,0)",
                                 (origin_id, cls, blob_digest))
                    n = d1.index_source(conn, origin_id, data, cfg)
                    conn.execute("UPDATE origins SET n_fingerprints=? WHERE origin_id=?",
                                 (n, origin_id))
                    counts["origins"] += 1
                    counts["fingerprints"] += n
            conn.commit()
            digest = store.manifest_digest(conn, root)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('manifest_digest', ?)",
                         (digest,))
            conn.commit()
            counts["digest"] = digest

    store.atomic_replace(root, populate)
    return BuildResult(manifest_digest=counts["digest"],
                       n_origins=counts["origins"],
                       n_fingerprints=counts["fingerprints"])


def _match_id(conn, span_digest: str, source_class: str, origin_id: str, detector: str) -> str:
    """Binds span + class + origin + detector (Global Constraint 10).

    A release references THIS, so releasing one occurrence cannot declassify the
    same bytes arriving from a different protected origin or via another detector.
    """
    return hmac.new(d2._registry_key(conn),
                    f"{span_digest}\x00{source_class}\x00{origin_id}\x00{detector}".encode(),
                    hashlib.sha256).hexdigest()


def _released(conn, match_id: str, sink: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM releases r JOIN release_sinks s USING(release_id) "
        "WHERE r.match_id=? AND s.sink=? AND r.normalisation=?",
        (match_id, sink, store.D1_VERSION),
    ).fetchone()
    return row is not None


def release(root, match_id: str, sinks, reason: str) -> str:
    """Declassify ONE match for ONE OR MORE named sinks.

    Takes an opaque `match_id` from a Verdict — never arbitrary text. The
    previous draft declared `release(root, span, sink, reason)` and never
    implemented it; a signature over raw bytes would also have released the same
    text from every origin at once.
    """
    if not reason or not reason.strip():
        raise ValueError("a release requires a stated reason")
    sinks = [sinks] if isinstance(sinks, str) else list(sinks)
    unknown = set(sinks) - set(policy.SINKS)
    if unknown:
        raise policy.PolicyError(f"unknown sink label(s): {sorted(unknown)}")
    release_id = uuid.uuid4().hex
    with closing(store.open_db(root)) as conn:
        conn.execute("INSERT INTO releases VALUES (?,?,?,?,?,?)",
                     (release_id, match_id, "any", store.D1_VERSION, reason,
                      datetime.now(timezone.utc).isoformat()))
        conn.executemany("INSERT INTO release_sinks VALUES (?,?)",
                         [(release_id, s) for s in sinks])
        conn.commit()
    return release_id


def scan_bytes(root, data: bytes, sink) -> Verdict:
    """The core scanner. Operates on BYTES, because the guarantee is over bytes.

    One connection is opened for the whole scan and validation runs against that
    same connection: the previous draft opened the database three separate times,
    so an atomic rebuild between opens could validate one store and scan another.
    """
    root = Path(root)
    sinks = [sink] if isinstance(sink, str) else list(sink)
    with closing(store.open_db(root)) as conn:
        store.validate(conn, root)                  # Stale / Integrity — never a verdict
        cfg = store.read_config(conn)
        origins = {oid: (cls, blob) for oid, cls, blob in
                   conn.execute("SELECT origin_id, source_class, blob_digest FROM origins")}
        blob_cache: dict[str, bytes] = {}
        seen, matches = set(), []

        # ── D1 ───────────────────────────────────────────────────────────────
        anchors = set(d1.candidates(conn, data, cfg))      # dedup before any blob read
        for origin_id, src_off, cand_off in sorted(anchors):
            meta = origins.get(origin_id)
            if meta is None:
                raise store.StateIntegrityError(
                    f"fingerprint references unknown origin {origin_id[:12]}")
            src_class, blob_digest = meta
            if blob_digest not in blob_cache:
                # get_blob re-verifies the digest, so confirmation compares against
                # bytes whose integrity has been proven this scan.
                blob_cache[blob_digest] = store.get_blob(root, blob_digest)
            start, length = d1.confirm(blob_cache[blob_digest], src_off, data, cand_off)
            if length < cfg.threshold_bytes:
                continue
            span = data[start:start + length]
            span_digest = hashlib.sha256(span).hexdigest()
            mid = _match_id(conn, span_digest, src_class, origin_id, "D1")
            for s in sinks:
                if not policy.is_blocked(src_class, [s]):
                    continue
                if d3.is_exempt(root, conn, span, src_class, s):
                    continue
                if _released(conn, mid, s):
                    continue
                if (mid, s) in seen:
                    continue
                seen.add((mid, s))
                matches.append(Match(match_id=mid, origin_id=origin_id,
                                     source_class=src_class, detector="D1",
                                     span_digest=span_digest, sink=s))

        # ── D2 ───────────────────────────────────────────────────────────────
        # Release semantics apply here too. The previous draft checked releases
        # for D1 only, so a literal could never be declassified at all.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
        for literal_id, src_class, origin_id in d2.match(conn, text):
            mid = _match_id(conn, literal_id, src_class, origin_id, "D2")
            for s in sinks:
                if not policy.is_blocked(src_class, [s]):
                    continue
                if _released(conn, mid, s):
                    continue
                if (mid, s) in seen:
                    continue
                seen.add((mid, s))
                matches.append(Match(match_id=mid, origin_id=origin_id,
                                     source_class=src_class, detector="D2",
                                     span_digest=literal_id, sink=s))

    return Verdict(blocked=bool(matches), matches=tuple(matches))


def scan(root, text: str, sink) -> Verdict:
    """Text convenience wrapper. `scan_bytes` is the contract."""
    return scan_bytes(root, text.encode("utf-8"), sink)


def scan_blob(root, path: str, blob: bytes, sink, change_type: str = "modify") -> Verdict:
    """Scan a complete post-image blob.

    `blob` is BYTES: a git blob is not necessarily valid UTF-8, and the previous
    draft's `str` signature could not represent one.

    The scan covers the WHOLE blob, never diff hunks, because a protected span
    can be assembled across two hunks or moved by a rename producing no textual
    diff at all. `path` and `change_type` are carried for the caller's reporting;
    they do not influence the verdict, and the previous draft claimed they were
    "recorded" while discarding them.
    """
    if isinstance(blob, str):
        raise TypeError("scan_blob takes bytes; a git blob may not be valid UTF-8")
    return scan_bytes(root, blob, sink)
```

```python
# scripts/boundary/__init__.py  (replace)
"""Zurvan Boundary — provenance-aware egress and contamination control."""
from scripts.boundary import store, d1, d2, d3, policy      # noqa: F401  submodules
from scripts.boundary.store import (                        # noqa: F401
    Config, StaleStateError, StateIntegrityError, init,
)
from scripts.boundary.policy import PolicyError             # noqa: F401
from scripts.boundary.scan import (                         # noqa: F401
    BuildResult, Match, Verdict, build, release, scan, scan_blob, scan_bytes,
)


def config(root):
    """Config for a store, by root. `store.config` takes an open connection."""
    from contextlib import closing
    with closing(store.open_db(root)) as conn:
        return store.read_config(conn)


def register_literal(root, literal, source_class, forbidden_sinks=None, origin_id="manual"):
    from contextlib import closing
    with closing(store.open_db(root)) as conn:
        return d2.register(conn, literal, source_class, origin_id)


def accede_public(root, span, reason):
    from contextlib import closing
    data = span.encode("utf-8") if isinstance(span, str) else span
    with closing(store.open_db(root)) as conn:
        return d3.accede(root, conn, data, provenance=reason)
```

> **Import regression fixed.** Task 5's test used
> `from scripts.boundary import scan as scan_mod`, which resolved to the MODULE
> before this file re-exported a function of the same name and to the FUNCTION
> after — so a test introduced in Task 5 broke in Task 6, with
> `AttributeError: 'function' object has no attribute 'Match'`. Task 5's test
> must use `import scripts.boundary.scan as scan_mod`.

**6.3 — Run, watch the rows turn green one at a time.** **Do not modify
`tests/test_boundary_gauntlet.py`.** If a contract looks wrong, stop and raise it:
editing a gauntlet test so an implementation passes is loosening a check to make
evidence pass, which is the cardinal sin this whole subsystem exists to prevent.

**6.4 — Record the FP number** from the row-17 contract. If it exceeds
≤ 1 per 200, **diagnose before prescribing**: candidate causes are insufficient
D3 accession coverage, a threshold set too low, a class-model error, or a test
corpus that is not actually clean. The one thing that is never the fix is raising
the ceiling.

**6.5 — Full suite.** `PYTHONPATH=. python -m pytest -q`.

**Expected skips after Plan 1, stated precisely** — an earlier draft said "the 4
bounded-FTS rows stay skipped until Plan 2" while also defining Plan 2 as git
integration, which contradicted itself and made the skip count useless as
evidence:

| Still skipped after Plan 1 | Why | Lands in |
| --- | --- | --- |
| 4 bounded-FTS rows | `fts_limit` does not exist | Phase 1B-FTS (a different project) |
| rows 12, 13 (git boundary, rename) | need staged-blob plumbing | Boundary Plan 2 |

The 11 remaining boundary rows must convert from skip to PASS. Do not treat a
drop in total skips as success on its own; name which rows moved.

**6.6 — Commit.** `zurvan: boundary detection core — gauntlet green`

---

## Task 7 — The repository's mandatory change protocol

**This project's own rules require it, and the previous draft omitted it
entirely.** `CLAUDE.md` and `AGENTS.md` mandate: read `AGENTS.md` + `CHANGELOG.md`
before edits; after edits run `pytest`, `scripts/public_repo_guard.py` and
`git diff --check`, and append a dated `Raouf:` entry to BOTH change files. A plan
that ends at "gauntlet green" ships work that violates the repository it ships
into.

**Files**
- Modify: `AGENTS.md`, `CHANGELOG.md`
- Test: none — this task is a gate, not a feature

### Steps

```bash
# 7.1 - 7.4, run in order; every one must pass before the entry is written
PYTHONPATH=. python -m pytest -q                       # 0 failed; record the counts
python scripts/public_repo_guard.py                    # must print a pass
git status --short | grep -c boundary                  # must be 0: store stays untracked
git diff --check                                       # clean
git diff --stat tests/test_boundary_gauntlet.py        # MUST BE EMPTY
```

**7.1** `PYTHONPATH=. python -m pytest -q` — 0 failed. Record the exact counts.

**7.2** `python scripts/public_repo_guard.py` — must print a pass. Boundary adds
a store under `~/.zurvan/`, so confirm nothing under it became tracked:
`git status --short | grep -c boundary` must be 0.

**7.3** `git diff --check` — clean.

**7.4** Confirm the gauntlet was not weakened:
`git diff --stat tests/test_boundary_gauntlet.py` must be EMPTY for this plan.
Any change to that file during implementation is the cardinal sin unless it was
raised and approved as a tightening first.

**7.5** Append the dated entry to `AGENTS.md` and `CHANGELOG.md`: scope, what was
built, the measured FP number, which gauntlet rows converted, which remain
skipped and why, and the verification output above.

**7.6** Commit.

---

## Plan self-review

**Spec coverage.** Constraints map as: 1,2→T5 · 3,8,12→T1 · 4,5→T2 · 6,7→T4 ·
9→T3 · 10→T5+T6 (`release` is now implemented, not merely declared) · 11→T5 ·
13→enforced by `test_gauntlet_completeness.py` and `test_plan_integrity.py`,
which already run · 14→split: recall and FP in T6, **latency in Plan 2**.

**This section previously overclaimed** and is corrected: it asserted constraint
8 was discharged by Task 1 when Task 1 could not detect a deleted index, and
constraint 10 by Task 5 when no release implementation existed anywhere. Both
were verified false by executing the plan's own code.

**Known gap, stated.** Rows 12 and 13 (git boundary, rename) are Plan 2. The
gauntlet will still skip them after this plan; that is expected and must not be
papered over by weakening those tests.
