#!/usr/bin/env python3
"""Remove machine-generated stub pages that assert evidence they do not have.

`create_stub_concept_and_claim` wrote two placeholder pages on every ingest:

    wiki/claims/Claim-<stem>.md     "Evidence missing? No, cited from <source>"
    wiki/concepts/AutoConcept-<stem>.md  "Generated from <source>"

The claim stub's sentence is the problem. Zurvan's distinguishing property is
that a recorded claim cannot be fabricated — `claim add` rejects evidence that is
not byte-exact in an ingested source. A page asserting "Evidence missing? No"
with no evidence section is exactly the fabrication the system exists to
prevent, and they outnumbered real claims 15:1.

They are not inert. Measured on the live index they contributed **15,244 chunks,
7.65% of it**, and a search for "evidence cited from source" returned ten stubs
in the top ten — they displace real evidence on the query class Zurvan is for.

**Selection is by STRUCTURE, never by filename.** Hand-authored pages live in the
same directories (`wiki/concepts/corrigibility.md` is real prose), and a filename
rule would delete them. A page is a stub only if its frontmatter declares no
`type:` AND its body is exactly the generated one-liner. Anything a human may
have touched — an extra sentence, a `type:` field — is AUTHORED and out of scope.
Fail closed: when unsure, keep it.
"""
import argparse
import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.config import PROJECT_ROOT

CLAIM_STUB = "claim_stub"
CONCEPT_STUB = "concept_stub"
AUTHORED = "authored"

# The exact generated bodies. A trailing newline is allowed; anything else is not.
_CLAIM_BODY = re.compile(
    r"^Evidence missing\? No, cited from \[[^\]]*\]\((?P<link>[^)]+)\)\s*$")
_CONCEPT_BODY = re.compile(
    r"^Generated from \[[^\]]*\]\((?P<link>[^)]+)\)\s*$")


def _split(text):
    """Return (frontmatter, body_lines_without_the_h1)."""
    fm = ""
    rest = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            rest = text[end + 4:]
    lines = [ln for ln in rest.splitlines() if ln.strip()]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return fm, lines


def classify_page(path):
    """CLAIM_STUB / CONCEPT_STUB / AUTHORED for one page. Fails closed."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return AUTHORED

    fm, body = _split(text)

    # A declared type means a real knowledge object. Never a stub.
    if re.search(r"^\s*type\s*:", fm, re.M):
        return AUTHORED
    # An evidence section means a real claim regardless of anything else.
    if re.search(r"^##\s+Evidence", text, re.M):
        return AUTHORED
    # The generated pages are exactly one body line. More means a human edited it.
    if len(body) != 1:
        return AUTHORED

    if _CLAIM_BODY.match(body[0]):
        return CLAIM_STUB
    if _CONCEPT_BODY.match(body[0]):
        return CONCEPT_STUB
    return AUTHORED


def _link(path, kind):
    text = Path(path).read_text(encoding="utf-8")
    pattern = _CLAIM_BODY if kind == CLAIM_STUB else _CONCEPT_BODY
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if m:
            return m.group("link")
    return None


def build_manifest(claims_dir=None, concepts_dir=None):
    """One entry per stub: path, kind, source linkage, content hash."""
    claims_dir = Path(claims_dir or (PROJECT_ROOT / "wiki" / "claims"))
    concepts_dir = Path(concepts_dir or (PROJECT_ROOT / "wiki" / "concepts"))

    manifest = []
    for directory in (claims_dir, concepts_dir):
        if not directory.is_dir():
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".md"):
                continue
            p = directory / name
            kind = classify_page(p)
            if kind == AUTHORED:
                continue
            data = p.read_bytes()
            manifest.append({
                "path": str(p),
                "kind": kind,
                "source_link": _link(p, kind),
                "content_hash": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            })
    return manifest


def apply(manifest, snapshot_dir=None, dry_run=True):
    """Snapshot then delete. Returns the number of pages removed."""
    if dry_run:
        return 0
    snapshot_dir = Path(snapshot_dir or (PROJECT_ROOT / "dist" / "backups" / "stub-pages"))
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for entry in manifest:
        src = Path(entry["path"])
        if not src.exists():
            continue
        # Snapshot FIRST — these pages are gitignored, so git cannot restore them.
        shutil.copy2(src, snapshot_dir / src.name)
        src.unlink()
        removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Delete (default: report only)")
    ap.add_argument("--manifest", help="Write the full manifest as JSON to this path")
    args = ap.parse_args()

    m = build_manifest()
    claims = [e for e in m if e["kind"] == CLAIM_STUB]
    concepts = [e for e in m if e["kind"] == CONCEPT_STUB]
    print(f"Machine-generated stub pages (selected by structure, not filename):")
    print(f"  claim stubs   : {len(claims):,}")
    print(f"  concept stubs : {len(concepts):,}")
    print(f"  total         : {len(m):,} files, {sum(e['bytes'] for e in m):,} bytes")
    orphan = [e for e in m if not e["source_link"]]
    if orphan:
        print(f"  WARNING: {len(orphan)} stub(s) with no source linkage")

    if args.manifest:
        import json
        with open(args.manifest, "w", encoding="utf-8") as fh:
            json.dump(m, fh, indent=2)
        print(f"  manifest -> {args.manifest}")

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to snapshot and remove {len(m):,} page(s).")
        return 0

    removed = apply(m, dry_run=False)
    print(f"\nSnapshotted and removed {removed:,} stub page(s).")
    print("Rebuild the index next: `zurvan index search && zurvan graph rebuild`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
