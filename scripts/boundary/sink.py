#!/usr/bin/env python3
"""What was already in the sink before the candidate operation began.

The v2 diagnostic run blocked four benign artefacts, and none of them was
detector overreach. Each shared a span with a protected document, and every one
of those spans was already committed to the canonical branch. Bytes already
reachable from `refs/heads/main` have already reached the `PUBLIC_ARTIFACT`
sink; emitting them again discloses nothing that was not disclosed when they
were committed.

Three other explanations were measured first and rejected:

* **Byte entropy** — inverted. The boilerplate scored 4.74 and 4.21 bits/byte
  against 4.02 for genuine protected prose, so an entropy filter would suppress
  secrets and keep templates.
* **Protected-origin frequency** — each span appeared in 1 of 300 protected
  origins, so "many origins, no information" never fires.
* **Raising `T`** — fixes neither, and tunes a magic number against the exam.

## The invariant

A matched span is not a new disclosure when those exact bytes were present in
the accepted protected-repository sink **before the candidate operation began**.

Three properties make that safe rather than a bypass:

**Match-level.** Exemption applies span by span. A changed file carries old
public spans and possibly new private ones; exempting the whole candidate
because part of it is public would turn "append a secret to a public file" into
a bypass.

**The candidate cannot prove itself.** The baseline is captured by the caller
BEFORE the operation, from accepted refs only. The working tree, the index, the
candidate commit, `refs/stash`, `refs/original/*`, scratch refs and foreign
repositories are all excluded — the same allowlist the ingestion provenance
layer uses, applied to the other side of the boundary.

**It is transient, never an accession.** This is recomputed per operation and is
never written into `public_origins`. Global Constraint 7 forbids seeding the
trusted public corpus from "everything currently tracked", because a leak
sitting in the tree would then be blessed permanently. A per-operation baseline
has the opposite property: remove the text from the canonical branch and the
exemption disappears with it. That difference is the reason this lives here and
not in `d3.accede`.

**Scope, stated:** the baseline is the TIP TREES of the accepted refs, not all
of history. Content deleted from the canonical branch stops being evidence. That
is conservative in the safe direction — it blocks something that was once
public, recoverable through an explicit recorded accession — and it keeps the
snapshot bounded rather than proportional to the whole history.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_GIT_TIMEOUT = 120


@dataclass
class SinkBaseline:
    """An immutable snapshot of what the accepted sink already held."""

    blobs: dict[str, bytes] = field(default_factory=dict)
    identity: dict = field(default_factory=dict)

    def contains_blob(self, data: bytes) -> bool:
        """True when this EXACT artefact is already in the sink."""
        return any(data == blob for blob in self.blobs.values())

    def contains_span(self, span: bytes) -> bool:
        """True when these exact bytes appear inside something already in the sink.

        Containment, not equality: a public document makes its own substrings
        public, which is what lets a benign edit keep its untouched paragraphs
        while its new ones are still scanned.
        """
        if not span:
            return False
        return any(span in blob for blob in self.blobs.values())


def _git(repo_dir: str, *args: str, binary: bool = False):
    try:
        result = subprocess.run(["git", "-C", repo_dir, *args],
                                capture_output=True, timeout=_GIT_TIMEOUT)
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout if binary else result.stdout.decode("utf-8", "replace").strip()


def capture_baseline(repo_root, refs: list[str] | None = None) -> SinkBaseline:
    """Snapshot the accepted sink. Call this BEFORE the candidate operation.

    The ordering is the guarantee, and it is the caller's to keep: a baseline
    captured after the candidate lands would contain the candidate, and the
    candidate would prove itself public.
    """
    repo_dir = str(Path(repo_root))
    if refs is None:
        from scripts.provenance import protected_refs
        refs = protected_refs(repo_dir)

    blobs: dict[str, bytes] = {}
    commits: dict[str, str] = {}
    for ref in refs:
        head = _git(repo_dir, "rev-parse", ref)
        if not head:
            continue
        commits[ref] = head
        listing = _git(repo_dir, "ls-tree", "-r", "--format=%(objectname) %(objecttype)", ref)
        if not listing:
            continue
        for line in listing.splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[1] != "blob":
                continue
            oid = parts[0]
            if oid in blobs:
                continue
            content = _git(repo_dir, "cat-file", "blob", oid, binary=True)
            if content is not None:
                blobs[oid] = content

    return SinkBaseline(
        blobs=blobs,
        identity={
            "repo": repo_dir,
            "refs": list(refs),
            "commits": commits,
            "blobs": len(blobs),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "scope": "tip trees of the accepted refs; history is not included",
        },
    )
