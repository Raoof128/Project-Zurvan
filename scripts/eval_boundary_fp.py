#!/usr/bin/env python3
"""The v2 false-positive gate: does Boundary block benign artefacts?

The v1 measurement was **invalid as measured, not failed**: 11 blocked artefacts
per 200, every one an ingestion round-trip — a tracked file colliding with its
own ingested copy, because ingestion recorded no source class and everything
under `wiki/sources/` was PRIVATE by location. The detector was right each time;
the corpus was wrong. The spec replaced the criterion prospectively:

> **v2 criterion.** <= 1 blocked artefact per 200, measured on a scan set with
> **no ingestion relationship** to the protected corpus.

That independence is the whole point, so this harness keeps two datasets apart
and refuses to run if they touch:

**A. Provenance seed corpus** — documents fresh-ingested through
`scripts/provenance.py`. Private material becomes the protected corpus; material
verified as already-public accedes to D3. This dataset establishes what Boundary
is protecting.

**B. False-positive scan corpus** — exactly 200 benign artefacts that are
**never ingested**. Scanning these answers "does Boundary overreach?".

Scanning a file that WAS ingested answers a different question — "is D3 doing
its job?" — and a clean result there says nothing about overreach. That
experiment lives in `tests/test_ingest_provenance.py` as a round-trip
regression, deliberately outside this statistic.

**Artefacts are counted, not anchors.** A 400-byte true positive still produces
one match per fingerprint anchor (a known open item), so a single file with five
matches is ONE blocked artefact. Match counts are recorded separately, as
diagnostics.

**The frozen set is never tuned against.** The manifest is written before the
measurement runs and carries the selection seed, every artefact's digest, and
the store revision. Tuning a detector against these 200 and then reporting the
result on the same 200 is not a gate; if tuning is needed, it happens on a
separate development corpus and this one stays untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Documented, fixed: the sample must be reproducible by anyone re-running this.
SELECTION_SEED = 20260917
SCAN_CORPUS_SIZE = 200
FP_CEILING = 1

#: Stratified so one file class cannot dominate the estimate. A corpus that is
#: 90% Markdown measures Markdown, not Boundary.
STRATA: dict[str, tuple[str, ...]] = {
    "markdown": (".md",),
    "python": (".py",),
    "config": (".json", ".yaml", ".yml", ".toml", ".cfg", ".ini"),
    "shell": (".sh", ".bash"),
    "other-text": (".txt", ".csv", ".sql", ".html", ".css", ".js"),
}

#: Excluded because their PURPOSE is to trigger Boundary, or because they are
#: the measuring instrument, or because they are not text. Including an
#: adversarial fixture would measure the fixture, not the false-positive rate.
EXCLUDED_PATH_PARTS = (
    "boundary_redteam",          # attacks the scanner on purpose
    "test_boundary_",            # fixtures built to produce matches
    "test_ingest_provenance",    # the round-trip regression itself
    "eval/",                     # gold sets and manifests: the instrument
    "docs/evaluation/retrieval/",  # reports ABOUT the instrument
    "/traces/",                  # derived artefacts
    ".gitkeep",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_probably_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def stratum_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    for name, suffixes in STRATA.items():
        if suffix in suffixes:
            return name
    return None


def candidate_artefacts(repo_root: Path) -> list[Path]:
    """Tracked, textual, non-adversarial files — the benign population."""
    listing = subprocess.run(["git", "-C", str(repo_root), "ls-files"],
                             capture_output=True, text=True, check=True).stdout
    candidates = []
    for line in listing.splitlines():
        rel = line.strip()
        if not rel or any(part in rel for part in EXCLUDED_PATH_PARTS):
            continue
        path = repo_root / rel
        if not path.is_file() or stratum_of(path) is None:
            continue
        if not _is_probably_text(path):
            continue
        candidates.append(path)
    return sorted(candidates)


def select_scan_corpus(candidates: list[Path], repo_root: Path,
                       size: int = SCAN_CORPUS_SIZE,
                       seed: int = SELECTION_SEED) -> list[dict]:
    """A deterministic, proportionally stratified sample.

    Deterministic because the alternative — picking files after looking at
    results — is how a gate becomes a formality. Same seed and same candidate
    set give the same 200 on any machine.
    """
    if len(candidates) < size:
        raise ValueError(
            f"only {len(candidates)} qualifying benign artefacts, need {size}. "
            "Expand the benign source pool rather than weakening the "
            "independence rule — a smaller sample measured against a ceiling of "
            f"{FP_CEILING}/{size} is a different criterion."
        )

    by_stratum: dict[str, list[Path]] = {}
    for path in candidates:
        by_stratum.setdefault(stratum_of(path), []).append(path)

    rng = random.Random(seed)
    # Largest-remainder allocation, so the quota is exact and reproducible.
    exact = {name: len(paths) * size / len(candidates)
             for name, paths in by_stratum.items()}
    quota = {name: min(int(value), len(by_stratum[name])) for name, value in exact.items()}
    while sum(quota.values()) < size:
        remainder = sorted(
            ((exact[name] - quota[name], name) for name in by_stratum
             if quota[name] < len(by_stratum[name])), reverse=True)
        if not remainder:
            break
        quota[remainder[0][1]] += 1

    selected: list[Path] = []
    for name in sorted(by_stratum):
        selected.extend(rng.sample(sorted(by_stratum[name]), quota[name]))

    return [
        {
            "path": str(path.relative_to(repo_root)),
            "sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
            "stratum": stratum_of(path),
        }
        for path in sorted(selected)
    ]


@dataclass
class SeedResult:
    protected: list[dict] = field(default_factory=list)
    acceded: list[dict] = field(default_factory=list)
    declared: list[dict] = field(default_factory=list)


def build_seed_corpus(eval_root: Path, documents: list[Path]) -> SeedResult:
    """Ingest through the provenance path; route by the class it decides.

    Nothing here inspects the tree to decide a class — `classify_origin` looks
    at the one document in front of it, which is what keeps this from being the
    "seed D3 from everything tracked" hole (Global Constraint 7).
    """
    from scripts.boundary import store
    from scripts.provenance import (ORIGIN_PRIVATE, classify_origin, may_accede)

    result = SeedResult()
    sources_dir = eval_root / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    for index, document in enumerate(documents):
        try:
            data = document.read_bytes()
        except OSError:
            continue
        origin_class, provenance = classify_origin(document)
        record = {"sha256": _sha256_bytes(data), "bytes": len(data),
                  "origin_class": origin_class, "provenance_scheme":
                  provenance.split(":", 1)[0]}

        if may_accede(origin_class):
            from scripts import boundary
            boundary.accede_public(eval_root, data, provenance)
            result.acceded.append(record)
        elif origin_class == ORIGIN_PRIVATE:
            # Protected material: this is what the scan set must stay clear of.
            (sources_dir / f"private-{index:04d}.txt").write_bytes(data)
            result.protected.append(record)
        else:
            # PUBLIC_DECLARED: recorded, never acceded, never protected.
            result.declared.append(record)

    _ = store
    return result


def assert_independent(scan_corpus: list[dict], seed: SeedResult) -> None:
    """The criterion's load-bearing property, checked rather than asserted."""
    ingested = {row["sha256"] for row in
                (seed.protected + seed.acceded + seed.declared)}
    scanned = {row["sha256"] for row in scan_corpus}
    overlap = scanned & ingested
    if overlap:
        raise ValueError(
            f"{len(overlap)} scanned artefact(s) were also ingested into the "
            "evaluation store. The v2 criterion requires a scan set with NO "
            "ingestion relationship to the protected corpus; scanning an "
            "ingested file measures D3, not overreach."
        )


def detect_containment(eval_root: Path, repo_root: Path,
                       scan_corpus: list[dict]) -> dict[str, dict]:
    """Find scanned artefacts that CONTAIN a protected document verbatim.

    Digest disjointness proves no artefact IS an ingested document. It cannot
    see a derived copy: `raw/notes/e2e_smoke_note.txt` is a fixture whose text
    is committed verbatim inside `scripts/e2e_smoke.sh`, so the protected
    document is 99% contained in a scanned artefact while the two files have
    entirely different digests. A block there tells you the detector found text
    that really is in both places — not that Boundary overreaches on
    independent material — so the pair is reported and excluded from the
    headline statistic rather than quietly counted.
    """
    import sqlite3

    from scripts.boundary import store

    conn = sqlite3.connect(Path(eval_root) / store.DB_NAME)
    try:
        blobs = [store.get_blob(eval_root, digest) for (digest,) in
                 conn.execute("SELECT blob_digest FROM origins")]
    finally:
        conn.close()

    found: dict[str, dict] = {}
    for row in scan_corpus:
        data = (repo_root / row["path"]).read_bytes()
        for blob in blobs:
            if blob and len(blob) <= len(data) and blob in data:
                found[row["path"]] = {
                    "protected_bytes": len(blob),
                    "artefact_bytes": len(data),
                    "relationship": "the protected document is contained "
                                    "verbatim in the scanned artefact",
                }
                break
    return found


def measure(eval_root: Path, repo_root: Path, scan_corpus: list[dict],
            scan_fn=None) -> dict:
    """Scan every artefact once. ONE file = ONE artefact, whatever its anchors.

    `scan_fn` is injectable so the counting rule can be tested against a stub
    verdict. Reaching into the module to monkeypatch a function-local import
    would test the patch, not the rule.
    """
    if scan_fn is None:
        from scripts import boundary
        scan_fn = boundary.scan_bytes

    blocked, clean, detail = 0, 0, []
    total_matches = 0
    for row in scan_corpus:
        data = (repo_root / row["path"]).read_bytes()
        verdict = scan_fn(eval_root, data, sink="PUBLIC_ARTIFACT")
        matches = len(verdict.matches)
        total_matches += matches
        if verdict.blocked:
            blocked += 1
            detail.append({
                "path": row["path"],
                "stratum": row["stratum"],
                "matches": matches,
                # Opaque origin ids only: a Match never carries a path.
                "origins": sorted({m.origin_id for m in verdict.matches}),
                "detectors": sorted({m.detector for m in verdict.matches}),
            })
        else:
            clean += 1

    return {
        "blocked_artefacts": blocked,
        "clean_artefacts": clean,
        "artefacts": len(scan_corpus),
        "ceiling": FP_CEILING,
        "passed": blocked <= FP_CEILING,
        "total_matches": total_matches,   # diagnostic; NOT the criterion
        "blocked_detail": detail,
    }


def freeze_manifest(path: Path, scan_corpus: list[dict], seed: SeedResult,
                    store_revision: str, selection_seed: int) -> dict:
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "criterion": f"<= {FP_CEILING} blocked artefact per {SCAN_CORPUS_SIZE}",
        "selection": {
            "seed": selection_seed,
            "method": "proportional stratified sample, largest-remainder quota, "
                      "sorted candidates, random.Random(seed).sample",
            "strata": {name: sum(1 for r in scan_corpus if r["stratum"] == name)
                       for name in sorted(STRATA)},
            "excluded_path_parts": list(EXCLUDED_PATH_PARTS),
        },
        "store_revision": store_revision,
        "seed_corpus": {
            "protected": len(seed.protected),
            "acceded_public": len(seed.acceded),
            "declared_not_acceded": len(seed.declared),
        },
        "scan_corpus": scan_corpus,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-root", required=True,
                        help="throwaway Boundary store; never the live one")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--private-docs", default="raw",
                        help="directory of PRIVATE documents for the protected corpus")
    parser.add_argument("--max-private", type=int, default=40)
    parser.add_argument("--max-public", type=int, default=10,
                        help="tracked repo documents to ingest so PUBLIC_VERIFIED "
                             "-> D3 accession is genuinely exercised")
    parser.add_argument("--size", type=int, default=SCAN_CORPUS_SIZE)
    parser.add_argument("--selection-seed", type=int, default=SELECTION_SEED)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    from scripts import boundary
    from scripts.boundary import store

    repo_root = Path(args.repo_root).resolve()
    eval_root = Path(args.eval_root).resolve()
    if eval_root == (Path.home() / ".zurvan" / "boundary").resolve():
        raise SystemExit("refusing to use the live Boundary store as an eval root")

    store.init(eval_root, k=store.DEFAULT_K, w=store.DEFAULT_W)

    # 1. The scan corpus is chosen FIRST, from a seeded stratified sample, so
    #    independence holds by construction rather than by luck. Selecting it
    #    after the store exists would let the store's contents influence it.
    candidates = candidate_artefacts(repo_root)
    scan_corpus = select_scan_corpus(candidates, repo_root, args.size,
                                     args.selection_seed)
    scanned_hashes = {row["sha256"] for row in scan_corpus}

    # 2. PRIVATE material: untracked documents, which become the protected corpus.
    private_docs = [p for p in sorted(Path(args.private_docs).rglob("*"))
                    if p.is_file() and _is_probably_text(p)][:args.max_private]

    # 3. PUBLIC material: tracked repo documents NOT in the scan set, so the
    #    PUBLIC_VERIFIED -> D3 path is exercised without ever scanning something
    #    that was ingested.
    public_docs = [p for p in candidates
                   if _sha256_file(p) not in scanned_hashes][:args.max_public]

    seed = build_seed_corpus(eval_root, private_docs + public_docs)
    build_result = boundary.build(eval_root)

    # 4. Belt and braces: prove the two datasets do not touch, by digest.
    assert_independent(scan_corpus, seed)

    manifest_path = Path(args.manifest or (eval_root / "fp-manifest.json"))
    freeze_manifest(manifest_path, scan_corpus, seed,
                    getattr(build_result, "digest", "unknown"), args.selection_seed)
    print(f"Frozen manifest: {manifest_path}")

    contained = detect_containment(eval_root, repo_root, scan_corpus)
    result = measure(eval_root, repo_root, scan_corpus)
    result["manifest"] = str(manifest_path)
    result["non_independent"] = contained
    independent_blocked = [row for row in result["blocked_detail"]
                           if row["path"] not in contained]
    result["blocked_independent"] = len(independent_blocked)
    result["passed_independent"] = len(independent_blocked) <= FP_CEILING

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print("\nv2 false-positive gate")
        print("=" * 46)
        print(f"Benign candidate population: {len(candidates)}")
        print(f"Seed corpus: {len(seed.protected)} protected PRIVATE, "
              f"{len(seed.acceded)} acceded PUBLIC_VERIFIED, "
              f"{len(seed.declared)} PUBLIC_DECLARED (not acceded)")
        print(f"Scan corpus: {result['artefacts']} benign artefacts, none ingested")
        print("-" * 46)
        print(f"Blocked artefacts: {result['blocked_artefacts']} / {result['artefacts']}"
              f"   (ceiling {FP_CEILING})")
        print(f"Clean artefacts:   {result['clean_artefacts']} / {result['artefacts']}")
        print(f"VERDICT (as measured): {'PASS' if result['passed'] else 'FAIL'}")
        if contained:
            print(f"Excluding {len(contained)} artefact(s) that CONTAIN a protected "
                  f"document verbatim (derived-copy, not independent):")
            for path, info in sorted(contained.items()):
                print(f"    {path}  (protected doc {info['protected_bytes']}B "
                      f"inside {info['artefact_bytes']}B artefact)")
            print(f"VERDICT (independent only): "
                  f"{result['blocked_independent']} / "
                  f"{result['artefacts'] - len(contained)}  "
                  f"{'PASS' if result['passed_independent'] else 'FAIL'}")
        print(f"(total anchor matches {result['total_matches']} — diagnostic, "
              f"NOT the criterion)")
        for row in result["blocked_detail"]:
            print(f"  BLOCKED {row['path']}  [{row['stratum']}] "
                  f"{row['matches']} matches, detectors={row['detectors']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
