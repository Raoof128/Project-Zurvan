#!/usr/bin/env python3
"""The operational false-positive gate: candidate blobs, not committed files.

Diagnostic attempt A measured 4/200 and failed, and then the diagnosis changed:
all 200 of its artefacts were blobs **already reachable from the accepted
sink**, which is not what a commit gate ever sees. A gate scans candidates — new
files, and modified versions of existing ones. Attempt A is preserved as a
regression corpus for sink-publicity behaviour; this is the operational score.

Two halves, declared rather than inferred from imagined production frequencies:

* **100 modified** — deterministic benign edits to files that ARE in the
  baseline sink. Each keeps its untouched public content, so match-level sink
  provenance is exercised naturally rather than theoretically. This is the case
  attempt A could not contain at all.
* **100 new** — independently sourced benign documents that are not reachable
  from the baseline and were never ingested into the protected corpus.

Every candidate is asserted absent from the baseline sink before measurement: a
candidate that is already sink-public would be skipped rather than scanned, and
a corpus of those measures nothing. That is the mistake attempt A made, so it is
now a hard precondition rather than an observation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.eval_boundary_fp import (
    FP_CEILING,
    _is_probably_text,
    _sha256_bytes,
    _sha256_file,
    candidate_artefacts,
    stratum_of,
)

GENERATION_SEED = 20260917
RECIPE_VERSION = "candidates-v1"
HALF = 100

#: One deterministic benign edit per file type. Comment syntax matters: an edit
#: that does not parse is not a benign change, it is a broken file.
EDIT_RECIPES = {
    "markdown": "\n<!-- recall/fp corpus: benign appended line, recipe {v} -->\n",
    "python": "\n# recall/fp corpus: benign appended line, recipe {v}\n",
    "config": "\n",
    "shell": "\n# recall/fp corpus: benign appended line, recipe {v}\n",
    "other-text": "\n",
}


def build_modified(baseline, repo_root: Path, pool: list[Path],
                   count: int = HALF) -> list[dict]:
    """Benign edits to baseline-public files, preserving their public content."""
    out = []
    for path in pool:
        if len(out) >= count:
            break
        original = path.read_bytes()
        if not baseline.contains_blob(original):
            continue                      # must start from genuinely public content
        stratum = stratum_of(path)
        edit = EDIT_RECIPES.get(stratum, "\n").format(v=RECIPE_VERSION).encode()
        candidate = original + edit
        if baseline.contains_blob(candidate):
            continue                      # astronomically unlikely, still checked
        out.append({
            "candidate_id": f"M{len(out):03d}",
            "class": "modified",
            "stratum": stratum,
            "derived_from": str(path.relative_to(repo_root)),
            "sha256": _sha256_bytes(candidate),
            "bytes": len(candidate),
            "_data": candidate,
        })
    return out


def build_new(baseline, repo_root: Path, ingested_hashes: set[str],
              count: int = HALF) -> list[dict]:
    """New-to-the-sink benign blobs from the repository's own HISTORY.

    The first attempt sourced these from `raw/` — the same tree the protected
    corpus comes from. The protected corpus held 254 documents from one export
    family and all 100 "new" candidates were siblings of those documents, so
    they genuinely shared content and blocking them was largely CORRECT. That
    half measured nothing about overreach, and 33/100 was recorded as invalid by
    construction rather than reported as a false-positive rate.

    Superseded historical blobs are independent by construction: `raw/` has
    never been committed, so nothing in git history can have an ingestion
    relationship with the protected corpus. They are real project work, they are
    not in the baseline tip, and a gate genuinely sees them — a revert, a
    cherry-pick, a restored file are all exactly this shape.
    """
    import subprocess

    listing = subprocess.run(
        ["git", "-C", str(repo_root), "rev-list", "--objects", "--all"],
        capture_output=True, text=True, check=True).stdout

    seen: set[str] = set()
    out: list[dict] = []
    for line in listing.splitlines():
        if len(out) >= count:
            break
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        oid, rel = parts
        path = Path(rel)
        stratum = stratum_of(path)
        if stratum is None or oid in seen:
            continue
        seen.add(oid)
        blob = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-t", oid],
            capture_output=True, text=True)
        if blob.stdout.strip() != "blob":
            continue
        data = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "blob", oid],
            capture_output=True).stdout
        if not data or len(data) < 200:
            continue
        digest = _sha256_bytes(data)
        if digest in ingested_hashes or baseline.contains_blob(data):
            continue                       # superseded versions only
        out.append({
            "candidate_id": f"N{len(out):03d}",
            "class": "new",
            "stratum": stratum,
            "derived_from": f"history:{rel}",
            "sha256": digest,
            "bytes": len(data),
            "_data": data,
        })
    return out


def assert_preconditions(candidates: list[dict], baseline,
                         ingested_hashes: set[str]) -> None:
    problems = []
    for row in candidates:
        if baseline.contains_blob(row["_data"]):
            problems.append(f"{row['candidate_id']}: already in the baseline sink")
        if row["sha256"] in ingested_hashes:
            problems.append(f"{row['candidate_id']}: has an ingestion relationship")
    if problems:
        raise ValueError("operational candidate corpus is invalid:\n  "
                         + "\n  ".join(problems))


def measure(eval_root: Path, candidates: list[dict], baseline) -> dict:
    from scripts import boundary

    rows = []
    for row in candidates:
        verdict = boundary.scan_bytes(eval_root, row["_data"],
                                      sink="PUBLIC_ARTIFACT", baseline=baseline)
        without = boundary.scan_bytes(eval_root, row["_data"], sink="PUBLIC_ARTIFACT")
        rows.append({
            "candidate_id": row["candidate_id"],
            "class": row["class"],
            "stratum": row["stratum"],
            "derived_from": row["derived_from"],
            "blocked": bool(verdict.blocked),
            "matches": len(verdict.matches),
            "blocked_without_baseline": bool(without.blocked),
            "suppressed_by_sink_publicity":
                bool(without.blocked) and not bool(verdict.blocked),
        })

    blocked = [r for r in rows if r["blocked"]]
    return {
        "candidates": len(rows),
        "blocked": len(blocked),
        "clean": len(rows) - len(blocked),
        "ceiling": FP_CEILING,
        "passed": len(blocked) <= FP_CEILING,
        "modified_blocked": sum(1 for r in blocked if r["class"] == "modified"),
        "modified_total": sum(1 for r in rows if r["class"] == "modified"),
        "new_blocked": sum(1 for r in blocked if r["class"] == "new"),
        "new_total": sum(1 for r in rows if r["class"] == "new"),
        "suppressed_by_sink_publicity":
            [r["candidate_id"] for r in rows if r["suppressed_by_sink_publicity"]],
        "total_matches": sum(r["matches"] for r in rows),
        "blocked_detail": blocked,
    }


def measure_via_gate(eval_root: Path, candidates: list[dict]) -> dict:
    """Run the SAME frozen candidates through `boundary_guard` — the real gate.

    Measuring `scan_bytes` proves things about the scanner. It proves nothing
    about the gate, and on 2026-09-18 the two had different policies: the gate
    called `scan_staged` without a baseline, so the 0/200 measured a
    configuration that never shipped. This path stages every candidate in a
    throwaway repository and runs the production entry point, baseline and all.

    The corpus is NOT regenerated: same manifest, same bytes, same recipe. Only
    the execution path changes. Candidates are staged under `artefacts/` so
    `sinks_for` yields `PUBLIC_ARTIFACT` alone and the two paths are comparable
    on sink as well as on content.
    """
    import subprocess
    import tempfile

    from scripts import boundary_guard
    from scripts.boundary import gitscan

    with tempfile.TemporaryDirectory(prefix="boundary-gate-corpus-") as tmp:
        repo = Path(tmp) / "staging"
        (repo / "artefacts").mkdir(parents=True)
        for cmd in (["init", "-q", "-b", "main"],
                    ["config", "user.email", "eval@example.invalid"],
                    ["config", "user.name", "eval"]):
            subprocess.run(["git", "-C", str(repo), *cmd], check=True,
                           capture_output=True)
        (repo / "README.md").write_text("staging ground\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True,
                       capture_output=True)

        for row in candidates:
            (repo / "artefacts" / row["candidate_id"]).write_bytes(row["_data"])
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       capture_output=True)

        # Exactly what boundary_guard.main composes.
        baseline = boundary_guard.gate_baseline()
        blocked = gitscan.scan_staged(eval_root, repo, baseline=baseline)
        rc = boundary_guard.main(boundary_root=eval_root, repo=repo)

    blocked_ids = {Path(path).name for path, _c, _v in blocked}
    return {
        "candidates": len(candidates),
        "blocked": len(blocked_ids),
        "blocked_ids": sorted(blocked_ids),
        "ceiling": FP_CEILING,
        "passed": len(blocked_ids) <= FP_CEILING,
        "guard_exit_code": rc,
        "baseline_blobs": (baseline.identity["blobs"] if baseline else 0),
        "baseline_refs": (baseline.identity["refs"] if baseline else []),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-root", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--private-docs", default="raw")
    parser.add_argument("--max-private", type=int, default=300)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--via-gate", action="store_true",
                        help="also run the frozen corpus through boundary_guard, "
                             "the production commit gate, and require agreement")
    args = parser.parse_args(argv)

    from scripts import boundary
    from scripts.boundary import store
    from scripts.boundary.sink import capture_baseline
    from scripts.eval_boundary_fp import build_seed_corpus

    repo_root = Path(args.repo_root).resolve()
    eval_root = Path(args.eval_root).resolve()
    if eval_root == (Path.home() / ".zurvan" / "boundary").resolve():
        raise SystemExit("refusing to use the live Boundary store as an eval root")
    store.init(eval_root, k=store.DEFAULT_K, w=store.DEFAULT_W)

    private_pool = [p for p in sorted(Path(args.private_docs).rglob("*"))
                    if p.is_file() and _is_probably_text(p)]
    seed_docs = private_pool[:args.max_private]
    seed = build_seed_corpus(eval_root, seed_docs)
    boundary.build(eval_root)

    # Captured BEFORE any candidate exists. That ordering is the guarantee.
    baseline = capture_baseline(repo_root)
    ingested = {row["sha256"] for row in
                (seed.protected + seed.acceded + seed.declared)}

    modified = build_modified(baseline, repo_root, candidate_artefacts(repo_root))
    new = build_new(baseline, repo_root, ingested)
    candidates = modified + new
    if len(modified) < HALF or len(new) < HALF:
        raise SystemExit(
            f"corpus incomplete: {len(modified)} modified, {len(new)} new "
            f"(need {HALF} of each). Expand the source pool rather than "
            "shrinking the corpus — a smaller denominator is a different criterion.")

    assert_preconditions(candidates, baseline, ingested)

    manifest_path = Path(args.manifest or (eval_root / "candidates-manifest.json"))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recipe_version": RECIPE_VERSION,
        "generation_seed": GENERATION_SEED,
        "baseline_identity": baseline.identity,
        "protected_corpus": {"documents": len(seed.protected),
                             "acceded_public": len(seed.acceded),
                             "declared": len(seed.declared)},
        "strata": {name: sum(1 for c in candidates if c["stratum"] == name)
                   for name in sorted({c["stratum"] for c in candidates})},
        "classes": {"modified": len(modified), "new": len(new)},
        "candidates": [{k: v for k, v in row.items() if k != "_data"}
                       for row in candidates],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen candidate manifest: {manifest_path}")

    result = measure(eval_root, candidates, baseline)

    gate = measure_via_gate(eval_root, candidates) if args.via_gate else None
    if gate is not None:
        result["via_gate"] = gate
        scanner_blocked = {r["candidate_id"] for r in result["blocked_detail"]}
        if set(gate["blocked_ids"]) != scanner_blocked:
            raise SystemExit(
                "POLICY MISMATCH: the production commit gate and the evaluation "
                f"scanner disagree.\n  gate blocked:    {sorted(gate['blocked_ids'])}"
                f"\n  scanner blocked: {sorted(scanner_blocked)}")

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print("\nOperational false-positive gate (candidate blobs)")
        print("=" * 56)
        print(f"Protected corpus: {len(seed.protected)} private documents, "
              f"{len(seed.acceded)} acceded public")
        print(f"Baseline sink: {baseline.identity['blobs']} blobs from "
              f"{baseline.identity['refs']}")
        print("-" * 56)
        print(f"Blocked overall:  {result['blocked']} / {result['candidates']} "
              f"(ceiling {FP_CEILING})")
        print(f"  modified files: {result['modified_blocked']} / {result['modified_total']}")
        print(f"  new files:      {result['new_blocked']} / {result['new_total']}")
        print(f"VERDICT: {'PASS' if result['passed'] else 'FAIL'}")
        print(f"Matches suppressed by sink publicity: "
              f"{len(result['suppressed_by_sink_publicity'])} candidate(s)")
        if gate is not None:
            print("-" * 56)
            print("Production commit-gate path (boundary_guard):")
            print(f"  Blocked:   {gate['blocked']} / {gate['candidates']} "
                  f"(ceiling {FP_CEILING})")
            print(f"  Exit code: {gate['guard_exit_code']}")
            print(f"  Baseline:  {gate['baseline_blobs']} blobs from "
                  f"{gate['baseline_refs']}")
            print(f"  VERDICT:   {'PASS' if gate['passed'] else 'FAIL'}")
            print("  Policy equivalence with the evaluation scanner: CONFIRMED")
        for row in result["blocked_detail"]:
            print(f"  BLOCKED {row['candidate_id']} [{row['class']}/{row['stratum']}] "
                  f"{row['matches']} matches"
                  + (f" from {row['derived_from']}" if row["derived_from"] else ""))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
