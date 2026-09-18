"""One immutable pre-candidate view of the world, shared by a whole batch.

The commit gate scans every staged post-image. Before this module it did so by
calling `scan_bytes` once per file, and `scan_bytes` opens the store, validates
it, reads the configuration, loads every origin row and rebuilds its blob cache
from scratch. None of that depends on which candidate is being scanned, so the
gate paid for it N times.

Measured on a disposable 150-document fixture, 100 staged files:

    100 store opens - 100 validations - 4,056 blob reads - 4.13 s

The blob reads are the expensive part: `store.get_blob` re-hashes every blob it
returns, on purpose, and a per-scan cache threw that work away between files.

## The session invariant

A session reads protected state, public accessions, the literal registry, the
release table and the accepted sink baseline **once, before the first
candidate**, and never re-reads them. Every candidate in the batch is therefore
answered against exactly the same view, whatever else happens to the filesystem
while the batch runs.

That is a stronger guarantee than the per-file path gave, not a weaker one: with
isolated scans, an edit to the protected tree halfway through a commit made the
gate raise `StaleStateError` partway down the staged list, so the same staged set
got different answers depending on where the edit landed. The next gate
invocation still refuses until a rebuild — staleness is deferred to the next
batch, never swallowed.

## The anti-laundering invariant

The baseline is **frozen** and candidates are never added to it. The obvious
optimisation — accumulate what you have seen so far — would create a laundering
primitive that does not otherwise exist: stage the leak twice, the first copy
enters the "already public" set, the second reads clean, and one `git add` has
declassified protected material. `test_an_earlier_candidate_cannot_make_a_later_one_already_public`
is the regression.

For the same reason match de-duplication stays **per candidate**. Hoisting the
`seen` set into the session would drop a genuine leak from the second file that
happens to share a span with the first.

## Read-only

A session opens the store, validates it, and reads. It writes nothing: not the
index, not `public_origins`, not the baseline, not the wiki. A gate that acceded
what it scanned would declassify the leak it had just found.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.boundary import d1, d2, d3, policy, store
from scripts.boundary.scan import (
    Match, Verdict, _already_in_sink, _derive_match_id,
)


class ScanSession:
    """A frozen view of protected state. Scan many candidates against it.

    Use as a context manager; the connection is closed on the way out whether
    the body returned or raised. There are no temporary files and no write
    locks, so an interrupted process leaves nothing behind but a file
    descriptor the kernel reclaims.
    """

    def __init__(self, root, baseline=None):
        self._root = Path(root)
        self._baseline = baseline
        self._closed = False
        self._blobs: dict[str, bytes] = {}
        conn = store.open_db(self._root)
        try:
            # Validation comes FIRST and is the reason the rest of this
            # constructor may assume a built store — including that the literal
            # registry key already exists, so reading it cannot write one.
            store.validate(conn, self._root)
            self._cfg = store.read_config(conn)
            self._origins = {
                oid: (cls, blob) for oid, cls, blob in
                conn.execute("SELECT origin_id, source_class, blob_digest FROM origins")}
            self._key = d2._registry_key(conn)
            self._literals = d2.load_literals(conn)
            self._public_docs = tuple(d3.public_docs(self._root, conn))
            # One read of the release table instead of one query per (match,
            # sink). Scoped to the normalisation each release was granted under,
            # exactly as `_released` scopes it.
            self._releases = frozenset(
                (match_id, sink) for match_id, sink in conn.execute(
                    "SELECT r.match_id, s.sink FROM releases r "
                    "JOIN release_sinks s USING(release_id) WHERE r.normalisation = ?",
                    (store.D1_VERSION,)))
        except BaseException:
            conn.close()
            self._closed = True
            raise
        self._conn = conn

    # ── lifecycle ────────────────────────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self) -> None:
        """Idempotent: a session closed twice is not an error."""
        if not self._closed:
            self._conn.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    # ── the scan ─────────────────────────────────────────────────────────────
    def _blob(self, digest: str) -> bytes:
        """Content-addressed, so caching across candidates is safe by identity.

        `store.get_blob` re-verifies the digest on read; the cache preserves that
        guarantee because the key IS the proof. Shared across the batch, which is
        where most of the saving lives.
        """
        if digest not in self._blobs:
            self._blobs[digest] = store.get_blob(self._root, digest)
        return self._blobs[digest]

    def scan(self, data: bytes, sink) -> Verdict:
        """Scan one candidate. Identical to `scan_bytes` against this view."""
        if self._closed:
            raise RuntimeError("scan on a closed ScanSession")
        conn = self._conn
        cfg = self._cfg
        sinks = [sink] if isinstance(sink, str) else list(sink)
        # Per CANDIDATE, never per session: see the anti-laundering note above.
        seen, matches = set(), []

        # ── D1 ───────────────────────────────────────────────────────────────
        anchors = set(d1.candidates(conn, data, cfg))      # dedup before any blob read
        for origin_id, src_off, cand_off in sorted(anchors):
            meta = self._origins.get(origin_id)
            if meta is None:
                raise store.StateIntegrityError(
                    f"fingerprint references unknown origin {origin_id[:12]}")
            src_class, blob_digest = meta
            start, length = d1.confirm(self._blob(blob_digest), src_off, data, cand_off)
            span = data[start:start + length]
            # T counts CONTENT. `informative_length(span) <= len(span)` always,
            # so the raw length is a free exact short-circuit.
            if (length < cfg.threshold_bytes
                    or d1.informative_length(span) < cfg.threshold_bytes):
                continue
            span_digest = hashlib.sha256(span).hexdigest()
            mid = _derive_match_id(self._key, span_digest, src_class, origin_id, "D1")
            for s in sinks:
                if not policy.is_blocked(src_class, [s]):
                    continue
                if d3.span_in_public_docs(span, src_class, s, self._public_docs):
                    continue
                if _already_in_sink(self._baseline, span, src_class, s):
                    continue
                if (mid, s) in self._releases:
                    continue
                if (mid, s) in seen:
                    continue
                seen.add((mid, s))
                matches.append(Match(match_id=mid, origin_id=origin_id,
                                     source_class=src_class, detector="D1",
                                     span_digest=span_digest, sink=s))

        # ── D2 ───────────────────────────────────────────────────────────────
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
        for literal_id, src_class, origin_id in d2.match_loaded(self._literals, text):
            mid = _derive_match_id(self._key, literal_id, src_class, origin_id, "D2")
            for s in sinks:
                if not policy.is_blocked(src_class, [s]):
                    continue
                if (mid, s) in self._releases:
                    continue
                if (mid, s) in seen:
                    continue
                seen.add((mid, s))
                matches.append(Match(match_id=mid, origin_id=origin_id,
                                     source_class=src_class, detector="D2",
                                     span_digest=literal_id, sink=s))

        return Verdict(blocked=bool(matches), matches=tuple(matches))


def batch_scan(root, items, baseline=None) -> list:
    """Scan many candidates against ONE frozen view.

    `items` is an iterable of `(key, data, sink)`; the key is opaque to the
    scanner and comes back attached to its verdict. Returns
    `[(key, Verdict)]` for EVERY candidate — there is no fail-fast, because a
    gate that stops at the first block turns one commit into N commits of
    whack-a-mole and never shows the author the shape of what they staged.
    """
    items = list(items)
    if not items:
        return []
    with ScanSession(root, baseline=baseline) as session:
        return [(key, session.scan(data, sink)) for key, data, sink in items]
