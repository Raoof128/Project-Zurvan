#!/usr/bin/env python3
"""Does Boundary still catch a real disclosure? — the frozen recall corpus.

The false-positive gate asks whether Boundary blocks benign work. This asks the
question that actually matters, and the one that has been **NOT MEASURED** since
the subsystem shipped: does it stop a private span from leaving?

It exists now because it has to. Scan-side sink provenance exempts a matched
span whose exact bytes were already in the accepted sink, and an exemption rule
that nobody measures against real leaks is a hole waiting to be found. The
sharpest case is the one this corpus was built around:

> **A leak whose protected span sits immediately beside already-public bytes.**

If match-level subtraction is sloppy at the boundary, the public neighbour can
swallow part of the private span and the leak walks out looking clean. Four
fixture shapes exist purely to make that failure visible — public bytes before
the span, after it, on both sides, and a private span appended to an otherwise
entirely public file.

**Every expectation is written down before anything is scanned.** The manifest
carries the expected verdict and the reason for each fixture, and the harness
refuses to run if a fixture's intended leak is not actually present in the
protected corpus — a recall corpus whose leaks are not real measures nothing,
and would read as a perfect score.

**The manifest never contains protected cleartext.** Spans are recorded by
digest and length. A frozen evaluation artefact that quotes the private material
it tests with is a leak of its own, which is the mistake this project has
already made once with the retrieval instrument.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Deterministic: the same corpus must be reconstructible from the manifest.
GENERATION_SEED = 20260917
RECIPE_VERSION = "recall-v1"

BLOCK, CLEAN = "BLOCK", "CLEAN"


@dataclass
class Fixture:
    fixture_id: str
    shape: str
    expected: str
    reason: str
    candidate: bytes = b""
    leak_span: bytes = b""
    origin_path: str = ""
    notes: dict = field(default_factory=dict)

    def digest(self) -> str:
        return hashlib.sha256(self.candidate).hexdigest()

    def span_digest(self) -> str:
        return hashlib.sha256(self.leak_span).hexdigest()

    def frozen(self) -> dict:
        """What is safe to write down: digests, lengths, expectations. No text."""
        return {
            "fixture_id": self.fixture_id,
            "shape": self.shape,
            "expected": self.expected,
            "reason": self.reason,
            "candidate_sha256": self.digest(),
            "candidate_bytes": len(self.candidate),
            "leak_span_sha256": self.span_digest(),
            "leak_span_bytes": len(self.leak_span),
            "protected_origin": self.origin_path,
            **self.notes,
        }


BENIGN_FILLER = (
    "This paragraph is ordinary project documentation written for the recall "
    "corpus. It describes nothing confidential and exists only to surround the "
    "material under test with plausible neighbouring content.\n\n"
)


def build_fixtures(protected: list[tuple[str, bytes]], public: bytes,
                   threshold: int) -> list[Fixture]:
    """Eleven leak shapes, each expected to BLOCK, plus one CLEAN control.

    `protected` is (identifier, bytes) for documents in the protected corpus;
    `public` is bytes already committed to the accepted sink, used to build the
    boundary-overlap cases.
    """
    prose_id, prose = protected[0]
    code_id, code = protected[1]

    def span(data: bytes, start: int, length: int) -> bytes:
        return data[start:start + length]

    long_prose = span(prose, 200, 400)
    exact_t = span(prose, 900, threshold)
    just_under = span(prose, 1400, threshold - 1)
    second_span = span(prose, 2000, 200)
    code_span = span(code, 100, 300)

    # Public neighbour bytes, taken from material already in the sink.
    public_head = public[:300]
    public_tail = public[300:600]

    fixtures = [
        Fixture("R1", "exact-long-prose", BLOCK,
                "a long verbatim protected span, the simplest disclosure",
                long_prose, long_prose, prose_id),
        Fixture("R2", "near-threshold-exact-T", BLOCK,
                f"exactly T={threshold} bytes: the guarantee's boundary",
                exact_t, exact_t, prose_id),
        Fixture("R3", "near-threshold-below-T", CLEAN,
                f"T-1 bytes — the control. If this blocks, the threshold is not "
                f"what it claims and every other result is suspect",
                just_under, just_under, prose_id),
        Fixture("R4", "embedded-in-benign", BLOCK,
                "a protected span buried inside ordinary prose",
                BENIGN_FILLER.encode() + long_prose + BENIGN_FILLER.encode(),
                long_prose, prose_id),
        Fixture("R5", "multiple-separated-spans", BLOCK,
                "two protected spans separated by benign text; fragmenting a "
                "disclosure across a document must not hide it",
                long_prose + BENIGN_FILLER.encode() * 3 + second_span,
                long_prose, prose_id,
                {"second_span_sha256": hashlib.sha256(second_span).hexdigest(),
                 "second_span_bytes": len(second_span)}),
        Fixture("R6", "protected-code-or-config", BLOCK,
                "protected source/config material, not prose",
                code_span, code_span, code_id),
        Fixture("R7", "protected-prose", BLOCK,
                "protected prose material",
                span(prose, 3000, 300), span(prose, 3000, 300), prose_id),

        # ── the four that exist because of scan-side sink provenance ─────────
        Fixture("R8", "public-prefix-overlap", BLOCK,
                "already-public bytes immediately BEFORE the private span: the "
                "exemption must not extend past what is genuinely public",
                public_head + long_prose, long_prose, prose_id),
        Fixture("R9", "public-suffix-overlap", BLOCK,
                "already-public bytes immediately AFTER the private span",
                long_prose + public_head, long_prose, prose_id),
        Fixture("R10", "public-both-sides", BLOCK,
                "a private span sandwiched between public bytes — the shape most "
                "likely to be swallowed by sloppy match-level subtraction",
                public_head + long_prose + public_tail, long_prose, prose_id),
        Fixture("R11", "appended-to-entirely-public-file", BLOCK,
                "a private span appended to a file that is otherwise wholly "
                "public; whole-file exemption would let this through",
                public + b"\n\n" + long_prose, long_prose, prose_id),
    ]
    return fixtures


def assert_fixtures_are_real(fixtures: list[Fixture],
                             protected: list[tuple[str, bytes]]) -> None:
    """A recall corpus whose leaks are not leaks would score 100% and mean nothing."""
    corpus = b"\x00".join(data for _, data in protected)
    problems = []
    for fixture in fixtures:
        if fixture.leak_span not in corpus:
            problems.append(f"{fixture.fixture_id}: span is not in the protected corpus")
        if fixture.leak_span not in fixture.candidate:
            problems.append(f"{fixture.fixture_id}: candidate does not contain its span")
        if not fixture.leak_span:
            problems.append(f"{fixture.fixture_id}: empty span")
    if problems:
        raise ValueError("recall fixtures are not testing what they claim:\n  "
                         + "\n  ".join(problems))


def assert_candidates_are_not_wholly_public(fixtures: list[Fixture], baseline) -> None:
    """No fixture may be exempt as a whole artefact, or it tests nothing."""
    wholly_public = [f.fixture_id for f in fixtures
                     if baseline is not None and baseline.contains_blob(f.candidate)]
    if wholly_public:
        raise ValueError(
            f"these fixtures are baseline-public as entire blobs and would be "
            f"skipped rather than scanned: {wholly_public}")


def run(eval_root: Path, fixtures: list[Fixture], baseline=None) -> dict:
    from scripts import boundary

    detected = missed = 0
    rows = []
    for fixture in fixtures:
        verdict = boundary.scan_bytes(eval_root, fixture.candidate,
                                      sink="PUBLIC_ARTIFACT", baseline=baseline)
        # Did the sink baseline change this decision? Re-scan without it.
        without = boundary.scan_bytes(eval_root, fixture.candidate,
                                      sink="PUBLIC_ARTIFACT")
        actual = BLOCK if verdict.blocked else CLEAN
        correct = actual == fixture.expected
        if fixture.expected == BLOCK:
            detected += int(correct)
            missed += int(not correct)
        rows.append({
            "fixture_id": fixture.fixture_id,
            "shape": fixture.shape,
            "expected": fixture.expected,
            "actual": actual,
            "correct": correct,
            "matches": len(verdict.matches),
            "sink_baseline_changed_the_decision":
                bool(verdict.blocked) != bool(without.blocked),
            "would_block_without_baseline": bool(without.blocked),
            "false_negative": fixture.expected == BLOCK and actual == CLEAN,
        })

    leaks = [f for f in fixtures if f.expected == BLOCK]
    return {
        "leaks": len(leaks),
        "detected": detected,
        "missed": missed,
        "recall": round(detected / max(1, len(leaks)), 4),
        "controls_correct": all(r["correct"] for r in rows
                                if r["expected"] == CLEAN),
        "false_negatives": [r["fixture_id"] for r in rows if r["false_negative"]],
        "suppressed_by_baseline": [r["fixture_id"] for r in rows
                                   if r["sink_baseline_changed_the_decision"]],
        "per_case": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--eval-root", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--private-docs", default="raw")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    from scripts import boundary
    from scripts.boundary import store
    from scripts.boundary.sink import capture_baseline
    from scripts.eval_boundary_fp import _is_probably_text

    repo_root = Path(args.repo_root).resolve()
    eval_root = Path(args.eval_root).resolve()
    if eval_root == (Path.home() / ".zurvan" / "boundary").resolve():
        raise SystemExit("refusing to use the live Boundary store as an eval root")

    store.init(eval_root, k=store.DEFAULT_K, w=store.DEFAULT_W)

    # Protected corpus: private documents, big enough to cut real spans from.
    candidates = [p for p in sorted(Path(args.private_docs).rglob("*"))
                  if p.is_file() and _is_probably_text(p) and p.stat().st_size > 4000]
    prose_path = candidates[0]
    code_path = next((p for p in candidates if p.suffix in (".py", ".sh", ".json")),
                     candidates[1])
    protected = [(f"protected-{i}", p.read_bytes())
                 for i, p in enumerate([prose_path, code_path])]

    sources = eval_root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    for name, data in protected:
        (sources / f"{name}.txt").write_bytes(data)
    boundary.build(eval_root)

    baseline = capture_baseline(repo_root)
    public = (repo_root / "README.md").read_bytes()
    threshold = boundary.config(eval_root).threshold_bytes

    fixtures = build_fixtures(protected, public, threshold)
    assert_fixtures_are_real(fixtures, protected)
    assert_candidates_are_not_wholly_public(fixtures, baseline)

    manifest_path = Path(args.manifest or (eval_root / "recall-manifest.json"))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "recipe_version": RECIPE_VERSION,
        "generation_seed": GENERATION_SEED,
        "threshold_bytes": threshold,
        "baseline_identity": baseline.identity,
        "note": "Spans are recorded by digest and length only. A frozen "
                "evaluation artefact must not quote the protected material it "
                "tests with.",
        "fixtures": [f.frozen() for f in fixtures],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen recall manifest: {manifest_path}")

    result = run(eval_root, fixtures, baseline)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print("\nBoundary recall gate")
        print("=" * 54)
        print(f"Threshold T = {threshold} bytes")
        print(f"Detected {result['detected']} / {result['leaks']} leaks "
              f"(recall {result['recall']:.0%})")
        print(f"Controls correct: {result['controls_correct']}")
        print("-" * 54)
        for row in result["per_case"]:
            flag = "ok " if row["correct"] else "FAIL"
            note = ""
            if row["sink_baseline_changed_the_decision"]:
                note = "  <- sink baseline CHANGED this decision"
            print(f"  [{flag}] {row['fixture_id']:4s} {row['shape']:34s} "
                  f"expected {row['expected']:5s} got {row['actual']:5s}"
                  f" ({row['matches']} matches){note}")
        if result["false_negatives"]:
            print(f"\nFALSE NEGATIVES: {result['false_negatives']}")
    return 0 if (result["missed"] == 0 and result["controls_correct"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
