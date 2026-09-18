#!/usr/bin/env python3
"""Benchmark the Boundary commit gate on a disposable fixture.

Never points at the live Zurvan working tree or `~/.zurvan/boundary`: a
benchmark that reads production state measures whatever else is touching
production state that minute, and one that writes to it is not a benchmark at
all. The fixture is generated deterministically from a fixed seed, so the same
bytes are scanned before and after a change.

Two modes over one fixture, so before/after is a property of the code path and
not of two different corpora:

* ``isolated`` — one independent scan per staged blob. This is the cost model
  the gate had before batching: N store opens, N validations, N origin loads,
  N blob re-reads.
* ``batch``    — one scan session per gate invocation, N candidates scanned
  against it.

Counters matter more than the clock here. Wall time varies with the machine;
"how many times did we load the protected corpus to scan 100 files" does not.

    python scripts/bench_boundary_gate.py --sizes 1,10,50,100 --reps 5
    python scripts/bench_boundary_gate.py --mode isolated --json before.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

PROTECTED_SEED = 424242
CANDIDATE_SEED = 848484

#: Deliberately mundane: the corpus has to look like prose so D1 fingerprints
#: behave the way they do on real pages, and it has to be generated rather than
#: copied so no real protected material is ever written into a benchmark root.
_WORDS = (
    "provenance boundary retrieval corpus instrument fingerprint winnowing "
    "threshold candidate accession disclosure protected artefact evaluation "
    "measurement integrity manifest digest snapshot session baseline sink "
    "commit gate scanner detector literal registry origin span verdict"
).split()


def _document(rng: random.Random, paragraphs: int = 12) -> bytes:
    out = []
    for _ in range(paragraphs):
        words = [rng.choice(_WORDS) for _ in range(rng.randint(40, 90))]
        out.append(" ".join(words) + ".")
    return ("\n\n".join(out) + "\n").encode("utf-8")


# ── fixture ───────────────────────────────────────────────────────────────────
def build_fixture(workdir: Path, n_protected: int, n_candidates: int,
                  n_leaks: int = 2) -> tuple[Path, Path]:
    """Return (boundary_root, repo_root). Both live under `workdir`."""
    from scripts import boundary
    from scripts.boundary import store

    root = workdir / "boundary"
    repo = workdir / "repo"
    if root.exists() or repo.exists():
        return root, repo                      # already built; reuse it

    store.init(root, k=store.DEFAULT_K, w=store.DEFAULT_W)
    rng = random.Random(PROTECTED_SEED)
    protected = []
    for i in range(n_protected):
        doc = _document(rng)
        (root / "sources" / f"private-{i:04d}.md").write_bytes(doc)
        protected.append(doc)
    # One instrument file, so D2 is exercised rather than measured as absent.
    (root / "instruments" / "gold.jsonl").write_bytes(
        b'{"query": "benchmark instrument item, unique enough to register"}\n')
    boundary.build(root)

    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.invalid"],
                   cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=repo, check=True)

    rng = random.Random(CANDIDATE_SEED)
    docs = (repo / "docs")
    docs.mkdir()
    for i in range(n_candidates):
        data = _document(rng)
        if i < n_leaks:
            # A real block, so the blocked path is timed too rather than only
            # the clean one. Taken from the middle of a protected document so it
            # is comfortably over T.
            data += b"\n\n" + protected[i][200:900]
        (docs / f"page-{i:04d}.md").write_bytes(data)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    return root, repo


#: Appended to each staged file. Re-staging byte-identical content produces an
#: EMPTY `diff --cached` and the gate correctly scans nothing — the first draft
#: of this benchmark measured that, and reported 8 ms for 100 files.
_EDIT = b"\n<!-- benchmark: staged edit -->\n"


def stage(repo: Path, n: int) -> None:
    """Stage exactly `n` modified candidate files. Idempotent across reps."""
    subprocess.run(["git", "reset", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "--", "docs"], cwd=repo, check=True)
    rel = [f"docs/page-{i:04d}.md" for i in range(n)]
    for r in rel:
        p = repo / r
        p.write_bytes(p.read_bytes() + _EDIT)
    subprocess.run(["git", "add", "--", *rel], cwd=repo, check=True)
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"],
                            cwd=repo, capture_output=True, text=True,
                            check=True).stdout.split()
    if len(staged) != n:
        raise RuntimeError(f"staged {len(staged)} paths, expected {n}")


# ── instrumentation ───────────────────────────────────────────────────────────
class Counters(dict):
    def bump(self, name: str) -> None:
        self[name] = self.get(name, 0) + 1


@contextmanager
def counted():
    """Count the expensive repeated operations, not just the clock."""
    import importlib

    from scripts.boundary import d2, store

    # `scripts.boundary.scan` the SUBMODULE, not the `scan()` function the
    # package re-exports under the same name.
    scan_mod = importlib.import_module("scripts.boundary.scan")

    c = Counters()
    originals = {}

    def wrap(module, name, label):
        fn = getattr(module, name)
        originals[(module, name)] = fn

        def counting(*a, **kw):
            c.bump(label)
            return fn(*a, **kw)

        setattr(module, name, counting)

    wrap(store, "open_db", "db_opens")
    wrap(store, "validate", "validations")
    wrap(store, "stat_digest", "stat_digests")
    wrap(store, "get_blob", "blob_reads")
    wrap(d2, "_registry_key", "registry_key_reads")
    wrap(scan_mod, "scan_bytes", "scans")
    try:
        from scripts.boundary import session as session_mod
        wrap(session_mod, "ScanSession", "sessions")
    except ImportError:
        pass                                    # pre-batching build
    try:
        yield c
    finally:
        for (module, name), fn in originals.items():
            setattr(module, name, fn)


# ── the two code paths ────────────────────────────────────────────────────────
def run_isolated(root: Path, repo: Path):
    """One independent scan per staged blob — the pre-batching cost model."""
    from scripts import boundary
    from scripts.boundary import gitscan

    blocked = []
    for path, change, data in gitscan.staged_blobs(repo):
        verdict = boundary.scan_blob(root, path, data, sink=gitscan.sinks_for(path),
                                     change_type=change)
        if verdict.blocked:
            blocked.append((path, change, verdict))
    return blocked


def run_batch(root: Path, repo: Path):
    from scripts.boundary import gitscan
    return gitscan.scan_staged(root, repo)


MODES = {"isolated": run_isolated, "batch": run_batch}


def measure(root: Path, repo: Path, mode: str, sizes, reps: int) -> list[dict]:
    fn = MODES[mode]
    rows = []
    for n in sizes:
        stage(repo, n)
        fn(root, repo)                          # warm: page cache, imports
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            blocked = fn(root, repo)
            samples.append(time.perf_counter() - t0)
        with counted() as counters:
            fn(root, repo)
        tracemalloc.start()
        fn(root, repo)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        samples.sort()
        rows.append({
            "candidates": n,
            "mode": mode,
            "reps": reps,
            "p50_s": round(statistics.median(samples), 6),
            "p95_s": round(samples[min(len(samples) - 1,
                                       int(round(0.95 * (len(samples) - 1))))], 6),
            "total_s": round(sum(samples), 6),
            "per_file_s": round(statistics.median(samples) / n, 6),
            "blocked": len(blocked),
            "peak_mem_mb": round(peak / 1e6, 2),
            "counters": dict(counters),
        })
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=[*MODES, "both"], default="both")
    parser.add_argument("--sizes", default="1,10,50,100")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--protected", type=int, default=150)
    parser.add_argument("--workdir", default=None,
                        help="reuse a fixture directory instead of a fresh temp one")
    parser.add_argument("--json", dest="as_json", default=None)
    args = parser.parse_args(argv)

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    tmp = None
    if args.workdir:
        workdir = Path(args.workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        tmp = tempfile.TemporaryDirectory(prefix="boundary-bench-")
        workdir = Path(tmp.name)

    # The benchmark writes into its fixture. Pointing it at the live store or the
    # working tree would make it a mutation test against production.
    live = (Path.home() / ".zurvan" / "boundary").resolve()
    if workdir == live or live in workdir.parents or workdir == Path.cwd():
        raise SystemExit("refusing to benchmark against the live Boundary root")

    root, repo = build_fixture(workdir, args.protected, max(sizes))
    modes = [*MODES] if args.mode == "both" else [args.mode]
    rows = []
    for mode in modes:
        rows.extend(measure(root, repo, mode, sizes, args.reps))

    if args.as_json:
        Path(args.as_json).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Fixture: {args.protected} protected documents, {workdir}")
    print(f"{'mode':<9} {'n':>4} {'p50 s':>9} {'p95 s':>9} {'per-file':>9} "
          f"{'opens':>6} {'valids':>7} {'blobs':>6} {'mem MB':>7}")
    for r in rows:
        c = r["counters"]
        print(f"{r['mode']:<9} {r['candidates']:>4} {r['p50_s']:>9.4f} {r['p95_s']:>9.4f} "
              f"{r['per_file_s']:>9.4f} {c.get('db_opens', 0):>6} "
              f"{c.get('validations', 0):>7} {c.get('blob_reads', 0):>6} "
              f"{r['peak_mem_mb']:>7.1f}")
    if tmp:
        tmp.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
