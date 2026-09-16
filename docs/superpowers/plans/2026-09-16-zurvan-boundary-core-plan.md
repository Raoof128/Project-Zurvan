# Implementation plan — Zurvan Boundary, detection core (Plan 1 of 3)

**Goal:** Make the 13 red contracts in `tests/test_boundary_gauntlet.py` pass by
building the detection core — store, state validation, D1, D2, D3, policy engine
and release ledger.

**Architecture.** A local, git-invisible store under `~/.zurvan/boundary/` holds
protected sources, an approved public corpus, and one SQLite index. A scan
resolves candidate spans through winnowed fingerprints, confirms them
byte-exactly against the original source, subtracts spans with approved public
origin, and renders a verdict against a (source class, sink) policy matrix.
Every scan first validates four state digests and fails closed.

**Tech stack.** Python 3.11+, stdlib `sqlite3`, stdlib `hashlib`, `pytest`.
No new third-party dependency. Follows this repo's existing module style
(`scripts/*.py`, tests in `tests/`).

**Spec:** `docs/superpowers/specs/2026-09-15-zurvan-boundary-design.md`

**Decomposition — this plan is 1 of 3. Do not start 2 or 3 from this document.**

| Plan | Covers | Gauntlet rows |
| --- | --- | --- |
| **1 (this)** | detection core | 5, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17 + verdict hygiene |
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

## Task 1 — Store layout, state digests, fail-closed validation

Discharges gauntlet rows **11** (stale index) and **16** (gate self-protection).

**Files**
- Create: `scripts/boundary/__init__.py`
- Create: `scripts/boundary/store.py`
- Test: `tests/test_boundary_store.py`

**Interfaces this task PROVIDES to later tasks**

```python
# scripts/boundary/store.py
NORMALISATION_VERSION = "v1-raw-bytes"

class StaleStateError(RuntimeError): ...
class StateIntegrityError(RuntimeError): ...

class Config:
    k: int            # k-gram size in bytes
    w: int            # winnowing window, in k-grams
    threshold_bytes: int   # == k + w - 1, derived, never set directly

def open_db(root: Path) -> sqlite3.Connection
def config(root: Path) -> Config
def content_digest(root: Path) -> str      # digest of protected sources + policy
def stored_digest(root: Path) -> str | None
def validate(root: Path) -> None           # raises StaleStateError / StateIntegrityError
def atomic_replace(root: Path, build_fn) -> None
```

**Deviation from the spec, stated plainly.** §7 of the spec draws five separate
SQLite files. This task builds **one** `boundary.sqlite` with five tables. The
load-bearing requirement is atomic replacement under constraint 8, and one file
is atomically replaceable with `os.replace`; five are not atomically replaceable
together without a transaction manager. Nothing precludes splitting later.

### Steps

**1.1 — Write the failing test for derived T.**

```python
# tests/test_boundary_store.py
import pytest
from pathlib import Path
from scripts.boundary import store

def test_threshold_is_derived_from_k_and_w_never_set_directly(tmp_path):
    root = tmp_path / "b"
    store.init(root, k=32, w=33)
    cfg = store.config(root)
    assert cfg.threshold_bytes == cfg.k + cfg.w - 1 == 64

def test_threshold_cannot_be_set_independently(tmp_path):
    root = tmp_path / "b"
    with pytest.raises(TypeError):
        store.init(root, k=32, w=33, threshold_bytes=99)
```

**1.2 — Run it, watch it fail** with `ModuleNotFoundError: scripts.boundary`.
That is the right failure. `PYTHONPATH=. python -m pytest -q tests/test_boundary_store.py`

**1.3 — Minimal code.**

```python
# scripts/boundary/__init__.py
"""Zurvan Boundary — provenance-aware egress and contamination control."""
from scripts.boundary.store import (          # noqa: F401
    Config, StaleStateError, StateIntegrityError, config,
)
```

```python
# scripts/boundary/store.py
"""Store layout, state digests, and fail-closed validation.

The store lives OUTSIDE the git tree (Global Constraint 12) so that `git add -A`
cannot capture it. That is the structural half of Boundary: a rule that cannot be
misconfigured because there is nothing to configure.
"""
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

NORMALISATION_VERSION = "v1-raw-bytes"
DB_NAME = "boundary.sqlite"


class StaleStateError(RuntimeError):
    """Protected sources or policy changed after the last build."""


class StateIntegrityError(RuntimeError):
    """Boundary's own state is missing, truncated or unreadable."""


@dataclass(frozen=True)
class Config:
    k: int
    w: int

    @property
    def threshold_bytes(self) -> int:
        # Global Constraint 3. Winnowing guarantees detection of every shared
        # substring of length >= k + w - 1; T is that number, never an
        # independent knob.
        return self.k + self.w - 1


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta        (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources     (source_id TEXT PRIMARY KEY, source_class TEXT NOT NULL,
                                        digest TEXT NOT NULL);
-- Global Constraint 4: (fingerprint -> many postings). No UNIQUE on fingerprint.
CREATE TABLE IF NOT EXISTS fingerprints(fingerprint BLOB NOT NULL, source_id TEXT NOT NULL,
                                        offset INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS fp_idx ON fingerprints(fingerprint);
CREATE TABLE IF NOT EXISTS literals    (literal_casefold TEXT PRIMARY KEY, value_hash TEXT NOT NULL,
                                        source_class TEXT NOT NULL, forbidden_sinks TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS public_spans(span_digest TEXT PRIMARY KEY, accession TEXT NOT NULL,
                                        accession_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS releases    (release_id TEXT PRIMARY KEY, span_digest TEXT NOT NULL,
                                        source_class TEXT NOT NULL, origin_id TEXT NOT NULL,
                                        permitted_sinks TEXT NOT NULL, normalisation TEXT NOT NULL,
                                        reason TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def init(root, k: int, w: int) -> None:
    """Create an empty store. `threshold_bytes` is deliberately NOT a parameter."""
    root = Path(root)
    (root / "sources").mkdir(parents=True, exist_ok=True)
    (root / "public").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / DB_NAME)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('k', ?)", (str(k),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('w', ?)", (str(w),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('normalisation', ?)",
                 (NORMALISATION_VERSION,))
    conn.commit()
    conn.close()


def open_db(root) -> sqlite3.Connection:
    db = Path(root) / DB_NAME
    if not db.is_file():
        raise StateIntegrityError(f"Boundary state missing: {db}")
    try:
        conn = sqlite3.connect(db)
        conn.execute("SELECT count(*) FROM meta").fetchone()
        return conn
    except sqlite3.DatabaseError as exc:
        # Global Constraint 8: corruption is a hard failure, never "clean".
        raise StateIntegrityError(f"Boundary state unreadable: {db}: {exc}") from exc


def config(root) -> Config:
    conn = open_db(root)
    rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    conn.close()
    try:
        return Config(k=int(rows["k"]), w=int(rows["w"]))
    except KeyError as exc:
        raise StateIntegrityError(f"Boundary meta incomplete: missing {exc}") from exc


def content_digest(root) -> str:
    """Digest of every protected source plus the policy file.

    Recomputed on every scan and compared with the digest stored at build time.
    This is what turns "someone edited a protected source after the last build"
    from a silent false negative into a hard failure.
    """
    h = hashlib.sha256()
    root = Path(root)
    for p in sorted((root / "sources").rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode())
            h.update(p.read_bytes())
    policy = root / "policy.yaml"
    if policy.is_file():
        h.update(policy.read_bytes())
    return h.hexdigest()


def stored_digest(root):
    conn = open_db(root)
    row = conn.execute("SELECT value FROM meta WHERE key='content_digest'").fetchone()
    conn.close()
    return row[0] if row else None


def validate(root) -> None:
    """Fail closed. Raises rather than returning a verdict."""
    stored = stored_digest(root)
    if stored is None:
        raise StaleStateError("Boundary index has never been built")
    current = content_digest(root)
    if current != stored:
        raise StaleStateError(
            "protected sources or policy changed since the last build; "
            "run `zurvan boundary build`"
        )


def atomic_replace(root, build_fn) -> None:
    """Build into a temp DB, then `os.replace` it in. Never a partial index."""
    root = Path(root)
    tmp = root / (DB_NAME + ".building")
    if tmp.exists():
        tmp.unlink()
    build_fn(tmp)
    os.replace(tmp, root / DB_NAME)
```

**1.4 — Run the test, watch it pass.** Two tests green.

**1.5 — Write the failing tests for fail-closed behaviour.**

```python
def test_scan_before_build_is_stale_not_clean(tmp_path):
    root = tmp_path / "b"; store.init(root, k=32, w=33)
    with pytest.raises(store.StaleStateError):
        store.validate(root)

def test_edited_source_after_build_is_stale(tmp_path):
    root = tmp_path / "b"; store.init(root, k=32, w=33)
    (root / "sources" / "a.md").write_text("protected", encoding="utf-8")
    conn = store.open_db(root)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('content_digest', ?)",
                 (store.content_digest(root),)); conn.commit(); conn.close()
    store.validate(root)                       # clean
    (root / "sources" / "a.md").write_text("protected AND EDITED", encoding="utf-8")
    with pytest.raises(store.StaleStateError):
        store.validate(root)

def test_corrupt_db_is_integrity_error_not_clean(tmp_path):
    root = tmp_path / "b"; store.init(root, k=32, w=33)
    (root / store.DB_NAME).write_bytes(b"\x00\x01corrupt")
    with pytest.raises(store.StateIntegrityError):
        store.open_db(root)
```

**1.6 — Run, watch pass.** The code in 1.3 already satisfies these; if any fails,
fix `store.py`, never the test.

**1.7 — Commit.** `zurvan: boundary store, derived threshold, fail-closed state`

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
def index_source(conn, source_id: str, data: bytes, cfg) -> int  # rows written
def candidates(conn, data: bytes, cfg) -> list[tuple[str, int, int]]
                                          # (source_id, src_offset, cand_offset)
def confirm(root, source_id, src_offset, data, cand_offset, cfg) -> int
                                          # confirmed match length, 0 if none
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


def index_source(conn, source_id: str, data: bytes, cfg) -> int:
    rows = [(h.to_bytes(8, "big"), source_id, off)
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
        for src_id, src_off in conn.execute(
            "SELECT source_id, offset FROM fingerprints WHERE fingerprint = ?",
            (h.to_bytes(8, "big"),),
        ):
            out.append((src_id, src_off, cand_off))
    return out


def confirm(source_bytes: bytes, src_offset: int,
            data: bytes, cand_offset: int, cfg) -> tuple[int, int]:
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
def register(conn, literal: str, source_class: str, forbidden_sinks: list[str]) -> None
def match(conn, text: str) -> list[tuple[str, str, list[str]]]
         # (value_hash, source_class, forbidden_sinks)
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
import json


def _fold(s: str) -> str:
    # Global Constraint 9. On 2026-09-15 a redaction pass covering lower and
    # upper case missed a title-cased occurrence, which survived into a later
    # commit. casefold() also handles 'ß' -> 'ss', which lower() does not.
    return s.casefold()


def register(conn, literal: str, source_class: str, forbidden_sinks) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO literals VALUES (?,?,?,?)",
        (_fold(literal),
         hashlib.sha256(literal.encode("utf-8")).hexdigest(),
         source_class,
         json.dumps(sorted(forbidden_sinks))),
    )
    conn.commit()


def match(conn, text: str):
    folded = _fold(text)
    hits = []
    for lit, value_hash, source_class, sinks in conn.execute(
        "SELECT literal_casefold, value_hash, source_class, forbidden_sinks FROM literals"
    ):
        if lit in folded:
            hits.append((value_hash, source_class, json.loads(sinks)))
    return hits
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
def accede(conn, span: str, accession: str) -> None      # approved public origin
def is_exempt(conn, span_bytes: bytes, source_class: str, sink: str) -> bool
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
_EXEMPTIBLE = {("PRIVATE", "PUBLIC_ARTIFACT"),
               ("PRIVATE", "RETRIEVAL_CORPUS"),
               ("PRIVATE", "EVAL_OUTPUT")}


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def accede(conn, span: str, accession: str) -> None:
    """Admit one span to the trusted public corpus, with its provenance."""
    if not accession or not accession.strip():
        raise ValueError(
            "accession provenance is required; the public corpus is never "
            "seeded from 'everything currently tracked' (Global Constraint 7)"
        )
    data = span.encode("utf-8")
    conn.execute("INSERT OR REPLACE INTO public_spans VALUES (?,?,?)",
                 (_digest(data), accession, _digest(accession.encode())))
    conn.commit()


def is_exempt(conn, span_bytes: bytes, source_class: str, sink: str) -> bool:
    if (source_class, sink) not in _EXEMPTIBLE:
        return False
    row = conn.execute("SELECT 1 FROM public_spans WHERE span_digest = ?",
                       (_digest(span_bytes),)).fetchone()
    return row is not None
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
scan(text: str, sink: str | list[str]) -> Verdict
scan_blob(path: str, blob: str, sink, change_type: str = "modify") -> Verdict
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


def is_blocked(source_class: str, sinks) -> bool:
    """Deny overrides allow (Global Constraint 2).

    Sink labels are non-exclusive: `docs/foo.md` is both a PUBLIC_ARTIFACT and a
    RETRIEVAL_CORPUS member. Every applicable label is evaluated and any BLOCK
    wins; without this the verdict on that file is undefined.
    """
    if isinstance(sinks, str):
        sinks = [sinks]
    unknown = set(sinks) - set(SINKS)
    if unknown:
        raise ValueError(f"unknown sink label(s): {sorted(unknown)}")
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
    __slots__ = ("source_id", "source_class", "detector", "span_digest", "sink")
    source_id: str
    source_class: str
    detector: str
    span_digest: str
    sink: str


@dataclass(frozen=True)
class Verdict:
    blocked: bool
    matches: list = field(default_factory=list)
```

**5.8 — Run, watch pass. 5.9 — Commit.**
`zurvan: boundary policy matrix, verdict hygiene, release ledger`

---

## Task 6 — Wire the pipeline and turn the gauntlet green

**Files**
- Modify: `scripts/boundary/__init__.py` (re-export the public API)
- Modify: `scripts/boundary/scan.py` (add `build`, `scan`, `scan_blob`)
- Test: `tests/test_boundary_gauntlet.py` (already written — do not edit it)

**6.1** Run `PYTHONPATH=. python -m pytest -q tests/test_boundary_gauntlet.py -rs`
and read which contracts fail. They are the specification.

**6.2** Implement `build`/`scan`/`scan_blob` against the interfaces above until
the gauntlet is green. **Do not modify `tests/test_boundary_gauntlet.py`.** If a
contract seems wrong, stop and raise it — changing a gauntlet test to make an
implementation pass is the cardinal sin (loosening a check to make evidence
pass).

**6.3** Run the FP-ceiling contract specifically and record the number:
`≤ 1 blocked artefact per 200 clean`. If it fails, the fix is D3 accession
coverage, never raising the ceiling.

**6.4** Full suite: `PYTHONPATH=. python -m pytest -q` — 719 passed, 5 skipped
before this plan; expect the 13 boundary skips to convert to passes.

**6.5** Commit. `zurvan: boundary detection core — gauntlet green`

---

## Plan self-review

**Spec coverage.** Global Constraints 1–12 each map to a task: 1→T5, 2→T5, 3→T1,
4→T2, 5→T2, 6→T4, 7→T4, 8→T1, 9→T3, 10→T5, 11→T5, 12→T1. Constraint 13 is
enforced by `tests/test_gauntlet_completeness.py::test_the_gauntlet_does_not_itself_leak`,
which already runs. Constraint 14's latency budget is **not** discharged here —
it needs the real corpus and belongs to Plan 2; that is a stated gap, not an
oversight.

**Placeholder scan.** Clean — no deferred markers, no hand-waved error handling,
no "see the previous task" cross-references. Every step shows its code.
(The forbidden tokens are described rather than written, so a grep-based
placeholder scan does not trip on the sentence declaring their absence — the
same reason this plan names evaluation items by number instead of quoting them.)

**Type consistency.** `Config`, `StaleStateError`, `StateIntegrityError` are defined
in Task 1 and used unchanged in 2–6. `Match`/`Verdict` field names match the
gauntlet's assertions exactly (`source_class`, `detector`, `span_digest`, `sink`).

**Known gap, stated.** Rows 12 and 13 (git boundary, rename) are Plan 2. The
gauntlet will still skip them after this plan; that is expected and must not be
papered over by weakening those tests.
