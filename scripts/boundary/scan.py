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


# Shortest instrument item that will be registered as a D2 literal. Anything
# shorter is SILENTLY UNPROTECTED, so this is a real cliff, not a tuning knob.
#
# Measured against the live 50-item instrument on 2026-09-16:
#   min 24 B - p5 29 B - median 49 B - max 77 B
#   0 items below 8 B, and 49 of 50 below T=64.
#
# That last number is the point: D1 cannot reach 98% of the instrument by
# construction, so D2 registration is the ONLY thing protecting it.
# `test_no_instrument_item_falls_below_the_registration_floor` fails if a future
# item drops under this line, rather than letting it vanish.
MIN_LITERAL_LEN = 8


def _instrument_literals(data: bytes):
    """Yield the individual items of an instrument file.

    Instrument items are SHORT by nature — a gold query is tens of bytes, well
    under T — so D1 cannot reach them by construction. They must be registered
    as D2 literals or the INSTRUMENT class is unenforceable. The gauntlet found
    this: its instrument item is 47 bytes against a 64-byte threshold.
    """
    import json
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            if len(line) >= MIN_LITERAL_LEN:
                yield line
            continue
        values = obj.values() if isinstance(obj, dict) else (
            obj if isinstance(obj, list) else [obj])
        for v in values:
            if isinstance(v, str) and len(v) >= MIN_LITERAL_LEN:
                yield v


def _is_initialised(root) -> bool:
    return (Path(root) / store.DB_NAME).is_file()


def build(root) -> BuildResult:
    """Index every protected file from an IMMUTABLE snapshot, then publish.

    Order matters and the previous draft had it wrong: it indexed from mutable
    files, replaced the database, and only then re-read the disk to record a
    digest — so bytes indexed and bytes digested could differ. Here each file is
    copied into a content-addressed blob FIRST, everything downstream reads the
    blob, and the manifest is committed inside the temp database before the swap.
    """
    root = Path(root)
    # The contract (tests/test_boundary_gauntlet.py) builds from a bare
    # directory, so build() initialises a store that does not exist yet. It never
    # re-initialises one that does: init() refuses that, because changing k/w
    # would invalidate every fingerprint.
    if not _is_initialised(root):
        store.init(root, k=store.DEFAULT_K, w=store.DEFAULT_W)
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
                    if cls == "INSTRUMENT":
                        # Short by nature; D1 cannot reach them. See
                        # _instrument_literals.
                        for item in _instrument_literals(data):
                            d2.register(conn, item, cls, origin_id)
                    counts["origins"] += 1
                    counts["fingerprints"] += n
            conn.commit()
            digest = store.manifest_digest(conn, root)
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('manifest_digest', ?)",
                         (digest,))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('stat_digest', ?)",
                         (store.stat_digest(root),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('total_fingerprints', ?)",
                         (str(counts["fingerprints"]),))
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


def scan_bytes(root, data: bytes, sink, baseline=None) -> Verdict:
    """The core scanner. Operates on BYTES, because the guarantee is over bytes.

    One connection is opened for the whole scan and validation runs against that
    same connection: the previous draft opened the database three separate times,
    so an atomic rebuild between opens could validate one store and scan another.

    `baseline` is an optional `sink.SinkBaseline` captured by the caller BEFORE
    the candidate operation. A matched span whose exact bytes were already in
    the accepted sink is not a NEW disclosure and is exempted match by match —
    never file by file, or appending a secret to a public file would bypass the
    gate. It is transient and never accedes anything; see `boundary/sink.py`.
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
            span = data[start:start + length]
            # T counts CONTENT, not bytes. A 270-byte rule of box-drawing
            # characters is three bytes repeated; measured against real material
            # it blocked `scripts/check.sh` for sharing a horizontal rule with a
            # protected page. `d1.informative_length` collapses repeating runs,
            # and rows 18-20 of the gauntlet pin that the rule cannot be used to
            # manufacture a match OR to conceal one.
            # `informative_length(span) <= len(span)` always, so the raw
            # length is a free exact short-circuit: it rejects the great
            # majority of anchors without the collapse scan.
            if (length < cfg.threshold_bytes
                    or d1.informative_length(span) < cfg.threshold_bytes):
                continue
            span_digest = hashlib.sha256(span).hexdigest()
            mid = _match_id(conn, span_digest, src_class, origin_id, "D1")
            for s in sinks:
                if not policy.is_blocked(src_class, [s]):
                    continue
                if d3.is_exempt(root, conn, span, src_class, s):
                    continue
                if _already_in_sink(baseline, span, src_class, s):
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



def _already_in_sink(baseline, span: bytes, source_class: str, sink: str) -> bool:
    """Was this exact span already in the accepted sink before the candidate?

    Restricted to the same (class, sink) pair D3 may exempt — PRIVATE reaching
    PUBLIC_ARTIFACT. INSTRUMENT is never exempted here for the same reason it is
    never exempted there: a gold item whose words also appear in the sink still
    matches its own answer and still contaminates a measurement.
    """
    if baseline is None:
        return False
    if (source_class, sink) not in d3._EXEMPTIBLE:
        return False
    return baseline.contains_span(span)


def scan(root, text: str, sink, baseline=None) -> Verdict:
    """Text convenience wrapper. `scan_bytes` is the contract."""
    return scan_bytes(root, text.encode("utf-8"), sink, baseline=baseline)


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
