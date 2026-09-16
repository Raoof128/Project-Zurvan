#!/usr/bin/env python3
"""Boundary commit gate.

Scans every staged post-image against the sinks its path belongs to. Reports
opaque identifiers only: the guard's own output is printed into terminals and CI
logs, so echoing a matched span would make the gate an exfiltration path.

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
        blocked = gitscan.scan_staged(root, repo)
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
