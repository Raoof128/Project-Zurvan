"""Capture a rebuild's inputs as a snapshot PROVEN to be knowledge revision R.

Design §6.1: a rebuild takes minutes; one that reads the live corpus while it
advances indexed no single revision. The proof is §5.1's double manifest -
live manifest before == live manifest after == the ledger's digest at R,
revision unchanged, no mutation active. A clone has new inodes, so the
snapshot's own manifest can never equal the live one (amendment 2).

The revision describes `wiki/` (the ledger's scope). `docs/` and the root
files are captured so the build reads no live byte, but they are git-tracked
and outside the ledger - a stated bound (amendment 1).
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks
from scripts.knowledge_manifest import ManifestUnavailable


class BuildSnapshotRefused(RuntimeError):
    """The store cannot give a truthful snapshot: SUSPECT (design §6.1)."""


class NoBaseline(BuildSnapshotRefused):
    """No ledger, or no recorded manifest: nothing for a revision to mean."""


class BuildSnapshotNotReady(RuntimeError):
    """The store kept changing; no snapshot was kept (gate item 11)."""


class BuildSnapshotBusy(BuildSnapshotNotReady):
    """Operations stayed in flight past the wait budget; names them."""


KNOWN_REQUIRED = ("wiki", "search", "graph")
VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"
NOT_REQUIRED = "NOT_REQUIRED"


class SnapshotNotReady(RuntimeError):
    """SNAPSHOT_NOT_READY (design §7.2): a REQUIRED derived store is not at the
    revision the snapshot pins. Never retried: staleness does not fix itself
    inside a capture; a rebuild does (§2.2)."""

    def __init__(self, component, state, built_from, revision, reason):
        self.component = component
        self.state = state
        self.built_from = built_from
        self.revision = revision
        self.reason = reason
        super().__init__(
            f"SNAPSHOT_NOT_READY: {component} is {state} (built from "
            f"{built_from}, knowledge revision {revision}): {reason}")


class _Moved(Exception):
    """The live store moved during the clone; the attempt is discarded."""


def _normalise_required(required) -> tuple:
    names = tuple(sorted(set(required)))
    if "claims" in names:
        raise ValueError("claims cannot be required: UNSUPPORTED_BY_DESIGN "
                         "until the Z2 claim ledger (design §7.3)")
    unknown = [n for n in names if n not in KNOWN_REQUIRED]
    if unknown:
        raise ValueError(f"unknown required component(s) {unknown}; "
                         f"known: {KNOWN_REQUIRED}")
    if "wiki" not in names:
        raise ValueError("a coherent capture pins the authoritative inputs: "
                         "required must include 'wiki' (amendment 9.3)")
    return names


def _frozen_meta(kind, directory) -> dict:
    """Stamps read from the FROZEN copy, never the live store: a rebuild may
    promote a new live file between the check and the clone, and the copy is
    what the measurement will read. A store with no stamp table reads as {}:
    unstamped, therefore UNVERIFIED (design §9.1), never a crash."""
    try:
        if kind == "search":
            from scripts.rebuild_search_index import read_index_meta
            return read_index_meta(Path(directory) / "search.sqlite")
        from scripts.graph_schema import read_graph_meta
        return read_graph_meta(Path(directory) / "graph.sqlite")
    except sqlite3.Error:
        return {}


def _verifier(base, required, before, m_before, purpose):
    """What runs on the building directory before it may be published."""
    from scripts import derived_health as dh
    from scripts import measurement_snapshot as ms

    def verify(working) -> dict:
        try:
            if not _unchanged(base, m_before, before):
                raise _Moved()
        except ManifestUnavailable as exc:           # a file vanished: a race
            raise _Moved() from exc
        components = {
            "wiki": {"status": VERIFIED},
            "docs": {"status": "CAPTURED", "note": "git-tracked, outside the ledger (amendment 8)"},
            "roots": {"status": "CAPTURED", "note": "git-tracked, outside the ledger (amendment 8)"},
        }
        for kind in ("search", "graph"):
            if kind not in required:
                components[kind] = {"status": NOT_REQUIRED}
                continue
            meta = _frozen_meta(kind, working)
            state, reason = dh.stamp_verdict(meta, before.revision,
                                             before.manifest_digest)
            if state != dh.CURRENT:
                built = meta.get("built_from_revision")
                raise SnapshotNotReady(kind, UNVERIFIED if built is None else state,
                                       built, before.revision, reason)
            components[kind] = {"status": VERIFIED,
                                "built_from_revision": meta["built_from_revision"],
                                "built_from_snapshot": meta.get("built_from_snapshot")}
        components["claims"] = dict(ms.CLAIMS_STATUS)
        return {"status": VERIFIED, "purpose": purpose, "required": list(required),
                "revision": before.revision, "atomicity": before.atomicity,
                "manifest_digest": before.manifest_digest, "components": components}
    return verify


def _precheck(base, required, before) -> None:
    """Fail fast on the LIVE stamps. Gauntlet 2026-09-28: refusing a stale
    live store AFTER freezing it took 11.7 s, nearly all `index_identity`
    over a 739 MB index, to say no. This is not the proof: a rebuild may
    promote between here and the clone, so the frozen copies are still
    checked. It is only a cheap early no."""
    from scripts import derived_health as dh
    for kind in ("search", "graph"):
        if kind not in required:
            continue
        state, built, now, reason = dh.store_health(kind, base)
        if state != dh.CURRENT and now in (None, before.revision):
            raise SnapshotNotReady(kind, UNVERIFIED if built is None else state,
                                   built, before.revision, reason)


def _live_manifest(base) -> str:
    from scripts.knowledge_manifest import authoritative_manifest
    return authoritative_manifest(base)


def _refuse_if_suspect(state) -> None:
    if state.state != kr.CLEAN:
        raise BuildSnapshotRefused(
            "store is SUSPECT; reconcile before rebuilding - a derived store "
            "built from torn knowledge looks exactly like a correct one")


def _unchanged(base, m_before, before) -> bool:
    """The second half of §5.1's double manifest: nothing moved during the
    clone. A separate function so falsification can remove exactly this."""
    after = kr.current(base)
    return (_live_manifest(base) == m_before and after.revision == before.revision
            and after.state == kr.CLEAN and not kr.mutations_in(kr.ACTIVE, base))


@dataclass
class BuildSnapshot:
    snapshot: object
    revision: int
    atomicity: str
    manifest_digest: str
    purpose: str = "rebuild"
    required: tuple = ("wiki",)

    @property
    def path(self):
        return self.snapshot.path

    def stamp(self) -> dict:
        """What a derived store records about its source (design §4.4)."""
        return {
            "built_from_revision": str(self.revision),
            "built_from_snapshot": self.snapshot.snapshot_id,
            "built_from_atomicity": self.atomicity,
            "built_from_manifest": self.manifest_digest,
        }

    def release(self) -> None:
        self.snapshot.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False


def _wait_for_quiet(base, wait_s) -> None:
    """Wait for live operations to finish - a different thing from retrying a
    race. Gauntlet 2026-09-28: with only the race retries, a capture started
    while another process held an operation for 2 s gave up after 0.64 s and
    blamed the store for changing; any rebuild during an ingest would fail."""
    deadline = time.monotonic() + wait_s
    while True:
        kr.detect_orphans(base)
        live = kr.mutations_in(kr.ACTIVE, base)
        if not live:
            return
        if time.monotonic() >= deadline:
            raise BuildSnapshotBusy(
                f"operation(s) still in flight after {wait_s}s: "
                + ", ".join(f"{m.writer} pid {m.owner_pid}" for m in live))
        time.sleep(0.25)


def capture_coherent(root=None, purpose="rebuild", required=("wiki",), label=None,
                     attempts=3, wait_s=300, deep=False, lease_ttl_s=None) -> BuildSnapshot:
    """Design §7.1: freeze the authoritative inputs plus every REQUIRED derived
    store, and publish only if all of them describe knowledge revision R.

    Only the declared capabilities are inspected (§7.1 step 6). An
    authoritative capture, `required=("wiki",)`, never reads a derived stamp,
    which is what removes the §2.2 deadlock. Races are retried a bounded
    number of times; SUSPECT and SNAPSHOT_NOT_READY are not retried.
    """
    from scripts import measurement_snapshot as ms
    required = _normalise_required(required)
    base = ks.store_root(root)
    label = label or purpose
    ttl = ms.DEFAULT_LEASE_TTL_S if lease_ttl_s is None else lease_ttl_s
    names = ms.BUILD_INPUTS + tuple(k for k in ("search", "graph") if k in required)
    for attempt in range(attempts):
        try:
            state = kr.current(base)
        except kr.LedgerError as exc:
            raise NoBaseline(f"no ledger: {exc}") from exc
        if not state.manifest_digest:
            raise NoBaseline("no recorded manifest; run `zurvan knowledge bootstrap`")
        _refuse_if_suspect(state)
        _wait_for_quiet(base, wait_s)
        try:
            kr.observe_external_drift(base, trigger="capture")
        except kr.DriftUnobservable:
            time.sleep(0.1 * (attempt + 1))
            continue
        before = kr.current(base)
        if before.state != kr.CLEAN:
            continue
        try:
            m_before = _live_manifest(base)
        except ManifestUnavailable:
            continue                     # a file vanished mid-manifest: a race
        if m_before != before.manifest_digest:
            continue
        _precheck(base, required, before)
        try:
            snap = ms._freeze(base, label, deep, names, purpose, before.revision,
                              ttl, _verifier(base, required, before, m_before, purpose))
        except _Moved:
            continue
        bs = BuildSnapshot(snap, before.revision, before.atomicity,
                           before.manifest_digest, purpose, required)
        try:
            (snap.path / "build.json").write_text(json.dumps(bs.stamp(), indent=2))
        except BaseException:
            # Only GC sweeps data/snapshots, so a leak here would outlive the
            # process (final review, 2026-09-28).
            snap.release()
            raise
        return bs
    raise BuildSnapshotNotReady(
        f"the store kept changing across {attempts} attempts; nothing was built")


def capture_build_inputs(root=None, label="rebuild", attempts=3, wait_s=300) -> BuildSnapshot:
    """The step-5 contract, unchanged: an authoritative capture for a rebuild."""
    return capture_coherent(root, purpose=label, required=("wiki",), label=label,
                            attempts=attempts, wait_s=wait_s)


@contextmanager
def inputs_for_rebuild(root=None, purpose="rebuild"):
    """A proven snapshot, or None on a store with no baseline (amendment 4).
    SUSPECT and NotReady propagate: a rebuild must not proceed from them."""
    try:
        bs = capture_build_inputs(root, label=purpose)
    except NoBaseline:
        yield None
        return
    try:
        yield bs
    finally:
        bs.release()
