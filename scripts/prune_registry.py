#!/usr/bin/env python3
"""Report — and optionally prune — registry rows that no longer describe anything.

The registry is what makes Zurvan say "already ingested". A row whose document
has no source page is therefore a lie with consequences: the store overstates
what it holds, AND a re-ingest of that document is refused, so the content can
never come back on its own.

Three states, and only one of them is a defect:

  healthy    source file on disk, page present         — nothing to do
  held       source file GONE, page present            — KEEP. The content is
             still in Zurvan; only the original path died. Pruning the row
             would let the same bytes ingest again as a duplicate page.
  orphan     page MISSING (whatever the file's state)  — PRUNE. Nothing exists
             anywhere for this row. Releasing it lets the document be
             re-ingested if the file is (or becomes) available again.

Dry-run by default: removing registrations is not something to do by accident.
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.config import PROJECT_ROOT
from scripts.db import unregister_source
from scripts.ingest import read_page_hash

HEALTHY, HELD, ORPHAN = "healthy", "held", "orphan"


def _page_hashes(sources_dir=None):
    sources = sources_dir or (PROJECT_ROOT / "wiki" / "sources")
    if not os.path.isdir(sources):
        return set()
    out = set()
    for name in os.listdir(sources):
        if name.endswith(".md"):
            h = read_page_hash(os.path.join(sources, name))
            if h:
                out.add(h)
    return out


def classify(registry_path=None, sources_dir=None):
    """Return {state: [(path, hash), ...]} for every registry row."""
    db = str(registry_path or (PROJECT_ROOT / "data" / "registry.sqlite"))
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT path, hash FROM sources").fetchall()
    finally:
        conn.close()

    pages = _page_hashes(sources_dir)
    buckets = {HEALTHY: [], HELD: [], ORPHAN: []}
    for path, digest in rows:
        if digest not in pages:
            buckets[ORPHAN].append((path, digest))
        elif not os.path.exists(path):
            buckets[HELD].append((path, digest))
        else:
            buckets[HEALTHY].append((path, digest))
    return buckets


def prune(buckets):
    """Release every orphan registration. Returns the number removed."""
    removed = 0
    for path, _ in buckets[ORPHAN]:
        unregister_source(path)
        removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="Actually remove orphan rows (default: report only)")
    ap.add_argument("--list", action="store_true", help="List every affected path")
    args = ap.parse_args()

    b = classify()
    total = sum(len(v) for v in b.values())
    print(f"Registry rows: {total}")
    print(f"  healthy (file + page)        : {len(b[HEALTHY])}")
    print(f"  held    (file gone, page kept): {len(b[HELD])}  — content still in Zurvan, rows KEPT")
    print(f"  orphan  (no page at all)      : {len(b[ORPHAN])}  — the store overstates these")

    if args.list:
        for state in (HELD, ORPHAN):
            for path, _ in b[state]:
                print(f"    {state:7s} {path}")

    if not b[ORPHAN]:
        print("\nNothing to prune.")
        return 0
    if not args.apply:
        print(f"\nDry run. Re-run with --apply to release {len(b[ORPHAN])} orphan row(s).")
        return 0

    removed = prune(b)
    print(f"\nReleased {removed} orphan registration(s). Those documents can be ingested again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
