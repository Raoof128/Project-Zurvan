#!/usr/bin/env python3
"""Boundary commit gate.

Scans every staged post-image against the sinks its path belongs to. Reports
opaque identifiers only: the guard's own output is printed into terminals and CI
logs, so echoing a matched span would make the gate an exfiltration path.

The gate captures an accepted-sink baseline ONCE per invocation and scans the
whole staged set against it. Until 2026-09-18 it did not: scan-side sink
provenance was implemented, given 13 contracts and measured at 0/200 on a frozen
corpus, and then this file called `scan_staged` without a baseline. The policy
that was measured and the policy that shipped were different, and every contract
below the caller stayed green while they diverged — none of them asserted on the
argument this caller passes.

Exit codes:
  0  clean, or Boundary is not configured on this machine
  1  a protected span reaches a forbidden sink, OR the store cannot prove itself
"""
import os
import sys
from pathlib import Path


def default_root() -> Path:
    return Path(os.environ.get("ZURVAN_BOUNDARY_ROOT")
                or Path.home() / ".zurvan" / "boundary")


def gate_baseline():
    """The accepted-sink snapshot this gate scans against, or None.

    Scoped to the repository Boundary PROTECTS — `protected_repo_root()`, the
    same notion the ingestion provenance layer uses — and never to whatever
    repository the gate happens to be pointed at. If the baseline followed the
    scanned repo, `git init && git commit` in a scratch directory would
    manufacture sink evidence, and anyone who can run git could cross the
    boundary.

    Captured from the tip trees of the ALLOWLISTED refs only, so the working
    tree, the index, the candidate commit, `refs/stash`, `refs/original/*` and
    scratch branches are all excluded by construction. A candidate can therefore
    never prove its own bytes public.

    Fails CLOSED in the useful direction: no protected repository, no accepted
    refs, or an unreadable one all yield an empty baseline, which grants no
    exemptions at all rather than granting every one.

    Transient. It is handed to the scanner and discarded; nothing here is ever
    written into `public_origins`, because an accession is permanent and a leak
    sitting in the tree would then be blessed forever (Global Constraint 7).
    """
    from scripts.boundary.sink import capture_baseline
    from scripts.provenance import protected_repo_root

    root = protected_repo_root()
    if root is None:
        return None
    return capture_baseline(root)


def main(boundary_root=None, repo=None) -> int:
    root = Path(boundary_root or default_root())
    repo = Path(repo or ".")

    if not (root / "boundary.sqlite").is_file():
        # Opt-in. Blocking every commit before setup is not fail-closed, it is
        # broken — and a gate that blocks everything gets bypassed, then removed.
        print(f"Boundary: NOT CONFIGURED (no store at {root}); gate inactive.")
        return 0

    from contextlib import closing

    from scripts.boundary import gitscan, store
    try:
        # Validate the store FIRST, independently of what is staged. A commit
        # that stages nothing would otherwise never open it, and a broken store
        # would sit silently until the next commit that actually carries
        # content — which is precisely when a false clean is most expensive.
        with closing(store.open_db(root)) as conn:
            store.validate(conn, root)
        # Captured BEFORE any candidate is scanned, and frozen for the whole
        # staged batch. Reconstructing it per candidate would let the first
        # staged copy of a leak make the second one look already public.
        blocked = gitscan.scan_staged(root, repo, baseline=gate_baseline())
    except (store.StaleStateError, store.StateIntegrityError) as exc:
        print(f"Boundary: FAILED — the store cannot prove its own integrity.\n  {exc}")
        return 1

    if not blocked:
        print("Boundary: clean.")
        return 0

    print("Boundary: BLOCKED — protected content reaches a forbidden sink.\n")
    for path, change, verdict in blocked:
        print(f"  {path}  ({change})")
        for m in verdict.matches:
            print(f"    {m.detector}  class={m.source_class}  sink={m.sink}  "
                  f"span={m.span_digest[:12]}  origin={m.origin_id[:12]}")
    print("\n  Identifiers only; no protected text is shown by design.")
    print("  Inspect locally with:  zurvan boundary explain <span-digest>")
    return 1


if __name__ == "__main__":
    sys.exit(main())
