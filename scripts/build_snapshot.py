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
import time
from contextlib import contextmanager
from dataclasses import dataclass

from scripts import knowledge_revision as kr
from scripts import knowledge_scope as ks


class BuildSnapshotRefused(RuntimeError):
    """The store cannot give a truthful snapshot: SUSPECT (design §6.1)."""


class NoBaseline(BuildSnapshotRefused):
    """No ledger, or no recorded manifest: nothing for a revision to mean."""


class BuildSnapshotNotReady(RuntimeError):
    """The store kept changing; no snapshot was kept (gate item 11)."""


class BuildSnapshotBusy(BuildSnapshotNotReady):
    """Operations stayed in flight past the wait budget; names them."""


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


def capture_build_inputs(root=None, label="rebuild", attempts=3, wait_s=300) -> BuildSnapshot:
    from scripts import measurement_snapshot as ms
    base = ks.store_root(root)
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
        m_before = _live_manifest(base)
        if m_before != before.manifest_digest:
            continue
        snap = ms.capture(base, label=label, components=ms.BUILD_INPUTS)
        if _unchanged(base, m_before, before):
            bs = BuildSnapshot(snap, before.revision, before.atomicity,
                               before.manifest_digest)
            (snap.path / "build.json").write_text(json.dumps(bs.stamp(), indent=2))
            return bs
        snap.release()
    raise BuildSnapshotNotReady(
        f"the store kept changing across {attempts} attempts; nothing was built")


@contextmanager
def inputs_for_rebuild(root=None):
    """A proven snapshot, or None on a store with no baseline (amendment 4).
    SUSPECT and NotReady propagate: a rebuild must not proceed from them."""
    try:
        bs = capture_build_inputs(root)
    except NoBaseline:
        yield None
        return
    try:
        yield bs
    finally:
        bs.release()
