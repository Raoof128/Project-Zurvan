#!/usr/bin/env python3
"""What distinguishes reusable boilerplate from protected knowledge?

The v2 gate failed at 4/200 (3/199 independent). Every surviving false positive
is a short span of *shared construction* rather than shared knowledge:

* 68 B — a Superpowers plan-template header, in two different plans
* 64 B — `<meta name="viewport" content="width=device-width, initial-scale=1`

The tempting fix is to raise `T` until they stop matching. That is training on
the exam paper, and it answers the wrong question. The right question is what
*property* separates these spans from a genuine 64-byte secret, and that
property has to be measurable on data the evaluation set never sees.

This script measures candidate properties on a DEVELOPMENT corpus that is
disjoint from the frozen v2 scan set — by digest and by containment, because
digest disjointness has already been shown insufficient (`e2e_smoke.sh` embeds a
protected fixture verbatim while hashing differently).

It changes no detector. It only reports.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import Counter
from pathlib import Path

from scripts.eval_boundary_fp import (
    _is_probably_text,
    _sha256_file,
    candidate_artefacts,
    stratum_of,
)


def shannon_entropy(data: bytes) -> float:
    """Bits per byte. A template header and a passphrase differ here."""
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def token_entropy(data: bytes) -> float:
    """Bits per whitespace-separated token — closer to how text repeats."""
    tokens = data.decode("utf-8", "replace").split()
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    total = len(tokens)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def development_corpus(repo_root: Path, frozen_hashes: set[str],
                       frozen_paths: set[str]) -> list[Path]:
    """Benign artefacts the frozen evaluation set has never seen.

    Disjoint by digest AND by path. Containment is checked separately, against
    the actual spans, because a file can share text without sharing a digest.
    """
    corpus = []
    for path in candidate_artefacts(repo_root):
        rel = str(path.relative_to(repo_root))
        if rel in frozen_paths or _sha256_file(path) in frozen_hashes:
            continue
        corpus.append(path)
    return corpus


def external_corpus(roots: list[Path]) -> list[Path]:
    """Unrelated documents — ingested sources, other projects' exports.

    "How many unrelated documents contain this span?" needs documents that were
    written by different people for different reasons. The repository alone
    cannot answer it: everything here shares one author and one house style.
    """
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and _is_probably_text(path):
                out.append(path)
    return out


def span_report(span: bytes, label: str, dev: list[Path],
                external: list[Path]) -> dict:
    """Every property a candidate policy might key on, measured."""
    dev_hits = sum(1 for p in dev if span in p.read_bytes())
    ext_hits = sum(1 for p in external if span in p.read_bytes())
    text = span.decode("utf-8", "replace")
    return {
        "label": label,
        "bytes": len(span),
        "preview": text[:70],
        "documents_containing_it": {
            "development_artefacts": dev_hits,
            "development_total": len(dev),
            "external_documents": ext_hits,
            "external_total": len(external),
            "corpus_frequency": round((dev_hits + ext_hits) /
                                      max(1, len(dev) + len(external)), 5),
        },
        "byte_entropy_bits": round(shannon_entropy(span), 3),
        "token_entropy_bits": round(token_entropy(span), 3),
        "distinct_tokens": len(set(text.split())),
        "tokens": len(text.split()),
        "punctuation_ratio": round(
            sum(1 for c in text if not c.isalnum() and not c.isspace()) / max(1, len(text)), 3),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--frozen-manifest", required=True)
    parser.add_argument("--external", nargs="*", default=["raw"])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    manifest = json.loads(Path(args.frozen_manifest).read_text(encoding="utf-8"))
    frozen_hashes = {row["sha256"] for row in manifest["scan_corpus"]}
    frozen_paths = {row["path"] for row in manifest["scan_corpus"]}

    dev = development_corpus(repo_root, frozen_hashes, frozen_paths)
    external = external_corpus([Path(p) for p in args.external])

    # The three independent false positives, taken from the failed run. Their
    # BYTES are needed to measure their properties; nothing here keys on them.
    spans = {
        "superpowers-plan-header":
            b" Plan\n\n> **For agentic workers:** REQUIRED SUB-SKILL: Use superpower",
        "html-viewport-meta":
            b' <meta name="viewport" content="width=device-width, initial-scal',
    }
    # A control: a span of genuinely distinctive protected prose, same order of
    # length. Without it the report says nothing — every property looks damning
    # until you check what a real secret scores.
    spans["control-distinctive-prose"] = (
        b"The quarterly containment review concluded that the residue threshold "
        b"must be recalibrated")

    report = {
        "development_artefacts": len(dev),
        "external_documents": len(external),
        "disjointness": {
            "frozen_scan_artefacts": len(frozen_hashes),
            "development_overlap_by_digest": 0,
            "development_overlap_by_path": 0,
        },
        "spans": [span_report(span, label, dev, external)
                  for label, span in spans.items()],
    }

    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Development corpus: {len(dev)} artefacts "
              f"(disjoint from the frozen {len(frozen_hashes)} by digest and path)")
        print(f"External documents: {len(external)}")
        print()
        for row in report["spans"]:
            freq = row["documents_containing_it"]
            print(f"── {row['label']} ({row['bytes']} B)")
            print(f"   {row['preview']!r}")
            print(f"   appears in {freq['development_artefacts']}/{freq['development_total']} dev "
                  f"artefacts, {freq['external_documents']}/{freq['external_total']} external docs "
                  f"→ corpus frequency {freq['corpus_frequency']}")
            print(f"   byte entropy {row['byte_entropy_bits']} bits/byte, "
                  f"token entropy {row['token_entropy_bits']} bits/token, "
                  f"{row['distinct_tokens']}/{row['tokens']} distinct tokens, "
                  f"punctuation {row['punctuation_ratio']}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
