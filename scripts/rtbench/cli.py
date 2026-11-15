"""`zurvan bench rtbench generate|run|report` (spec §9).

A holdout run is sealed: scores only, never an item, in the report or on stdout.
"""

import contextvars
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".zurvan"
REGISTRY = Path(__file__).resolve().parents[2] / "eval" / "datasets.json"
TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6")
SPLITS = ("dev", "holdout", "dev2", "holdout2")   # *2 = generator rtbench-2 (spec §12)


def _sealed(split) -> bool:
    return split.startswith("holdout")


def register(subparsers) -> None:
    from scripts.rtbench.adapters import ADAPTERS
    bench = subparsers.add_parser("bench", help="Benchmarks (rtbench: record-time, IOU-Z2-5)")
    names = bench.add_subparsers(dest="bench_name")
    rt = names.add_parser("rtbench", help="System-neutral record-time benchmark")
    sub = rt.add_subparsers(dest="action")
    g = sub.add_parser("generate", help="Write a split's cases and gold")
    g.add_argument("--split", choices=SPLITS, required=True)
    g.add_argument("--out", default=None)
    r = sub.add_parser("run", help="Score one adapter on one track of one split")
    r.add_argument("--adapter", choices=sorted(ADAPTERS), required=True)
    r.add_argument("--track", choices=("S", "N"), required=True)
    r.add_argument("--split", choices=SPLITS, required=True)
    r.add_argument("--cases-dir", default=None)
    r.add_argument("--report-dir", default=None)
    r.add_argument("--work-dir", default=None, help="Zurvan adapters: where per-case stores go (default: a temp dir)")
    r.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("report", help="Print a run report")
    p.add_argument("path")
    v = sub.add_parser("validity", help="Spec §6.5: oracle and both baselines on both tracks")
    v.add_argument("--split", choices=SPLITS, default="dev")
    v.add_argument("--cases-dir", default=None)


def _default_dir(split):
    return HOME / "instruments" / f"rtbench_{split}" if _sealed(split) else HOME / "rtbench" / split


def _registration(split, cases_path):
    """REGISTERED only when BOTH the cases and the gold file match their
    registered digests: an edited answer key must never run as registered."""
    from scripts.rtbench.canon import sha256_file
    try:
        entry = json.loads(REGISTRY.read_text(encoding="utf-8"))["datasets"].get(f"rtbench_{split}")
    except (OSError, ValueError):
        return "UNREADABLE"
    if entry is None:
        return "UNREGISTERED"
    gold_path = cases_path.with_name(f"{split}.gold.jsonl")
    if entry["sha256"] != sha256_file(cases_path):
        return "MISMATCH: cases"
    if entry.get("gold_sha256") != sha256_file(gold_path):
        return "MISMATCH: gold"
    return "REGISTERED"


def _load(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_report(report, report_dir) -> tuple:
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out / f"rtbench-{report['adapter']}-{report['track']}-{report.get('split', 'dev')}-{stamp}"
    sealed = _sealed(report.get("split", "dev"))
    if sealed:
        report = {k: v for k, v in report.items() if k != "misses"}
    j, m = base.with_suffix(".json"), base.with_suffix(".md")
    j.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    m.write_text(_markdown(report, sealed), encoding="utf-8")
    return j, m


def _f(x):
    return "n/a" if x is None else f"{x:.3f}"


def _markdown(rep, sealed):
    s = rep["score"]
    lines = [f"# RTBench run: {rep['adapter']}, Track {rep['track']}, {rep.get('split', 'dev')}", "",
             f"- cases {rep['n_cases']}, questions {rep['n_questions']}, errors {rep['errors']}",
             f"- headline (Q1-Q5 macro) **{_f(s['headline'])}**; control (Q6) {_f(s['control'])}",
             f"- generator {rep.get('generator_version', '?')}; split {rep.get('registration', '?')}", "",
             "| type | n | accuracy | 95% CI |", "|---|---|---|---|"]
    for t in TYPES:
        b = s["by_type"].get(t)
        if b:
            lines.append(f"| {t} | {b['n']} | {b['accuracy']:.3f} | {b['ci95'][0]:.3f}-{b['ci95'][1]:.3f} |")
    if not sealed and rep.get("misses"):
        lines += ["", f"{len(rep['misses'])} misses (case, type):", ""]
        lines += [f"- {x['case_id']} {x['type']}" for x in rep["misses"]]
    return "\n".join(lines) + "\n"


def _load_split(split, cases_dir):
    """(cases, golds, registration) or None after printing why. A registered
    split must match both digests; the holdout must be registered."""
    d = Path(cases_dir) if cases_dir else _default_dir(split)
    cases_p = d / f"{split}.cases.jsonl"
    if not cases_p.exists():
        print(f"no cases at {cases_p}; run `zurvan bench rtbench generate --split {split}` first", file=sys.stderr)
        return None
    registration = _registration(split, cases_p)
    if registration.startswith("MISMATCH"):
        print(f"refused: the {registration.split(': ')[1]} file under {d} does not match the registered "
              f"rtbench_{split} digest", file=sys.stderr)
        return None
    if _sealed(split) and registration != "REGISTERED":
        print(f"refused: the holdout runs only as a registered split ({registration})", file=sys.stderr)
        return None
    return _load(cases_p), _load(d / f"{split}.gold.jsonl"), registration


def validity_command(args) -> dict:
    from scripts.rtbench.adapters import ADAPTERS, TrackNReader
    from scripts.rtbench.run import REQUIRED, run_cases, validity
    loaded = _load_split(args.split, args.cases_dir)
    if loaded is None:
        return {"pass": False, "checks": [], "refused": True}
    cases, golds, _ = loaded
    if getattr(args, "limit", None):
        cases = cases[:args.limit]
    reports = {}
    for name, track in REQUIRED:
        adapter = ADAPTERS[name]()
        reports[(name, track)] = run_cases(TrackNReader(adapter) if track == "N" else adapter, track, cases, golds,
                                           itemised=False)
    return validity(reports)


def _run(args) -> int:
    from scripts.rtbench.adapters import ADAPTERS, ZURVAN_ADAPTERS, TrackNReader
    from scripts.rtbench.run import run_cases
    if args.adapter in ZURVAN_ADAPTERS and args.track == "N":
        print("refused: the Zurvan adapters are Track S only (spec §8); Track N is for systems that read prose",
              file=sys.stderr)
        return 2
    if _sealed(args.split) and args.limit:
        print("refused: --limit on the holdout would expose per-case results by differencing runs", file=sys.stderr)
        return 2
    loaded = _load_split(args.split, args.cases_dir)
    if loaded is None:
        return 1
    cases, golds, registration = loaded
    if args.limit:
        cases = cases[:args.limit]
    tmp = None
    if args.adapter in ZURVAN_ADAPTERS:
        work = args.work_dir or (tmp := tempfile.TemporaryDirectory(prefix="rtbench-")).name
        adapter = ADAPTERS[args.adapter](work_dir=work)
    else:
        adapter = ADAPTERS[args.adapter]()
    if args.track == "N":
        adapter = TrackNReader(adapter)
    try:
        # A fresh context: the CLI's live-store operation must not enclose the
        # adapter's temporary-store operations (NestedMutationError otherwise).
        report = contextvars.Context().run(run_cases, adapter, args.track, cases, golds,
                                           itemised=not _sealed(args.split))
    finally:
        adapter.close()
        if tmp is not None:
            tmp.cleanup()
    versions = sorted({c.get("generator_version", "?") for c in cases})
    report.update(split=args.split, generator_version=",".join(versions), registration=registration,
                  date=datetime.now().date().isoformat())
    j, _ = write_report(report, args.report_dir or HOME / "reports")
    s = report["score"]
    print(f"{report['adapter']} Track {args.track} {args.split} ({registration}): headline {_f(s['headline'])}, "
          f"control {_f(s['control'])}, errors {report['errors']}")
    print("  " + "  ".join(f"{t} {s['by_type'][t]['accuracy']:.3f}" for t in TYPES if t in s["by_type"]))
    print(f"  report: {j}")
    return 0


def dispatch(args) -> int:
    if getattr(args, "bench_name", None) != "rtbench" or not getattr(args, "action", None):
        print("usage: zurvan bench rtbench {generate,run,report} ...", file=sys.stderr)
        return 2
    if args.action == "generate":
        from scripts.rtbench.generate import DEV2_SEEDS, DEV_SEEDS, holdout_seeds, write_split
        seeds = {"dev": DEV_SEEDS, "dev2": DEV2_SEEDS}.get(args.split) \
            or holdout_seeds(HOME / "instruments", args.split)
        out = Path(args.out) if args.out else _default_dir(args.split)
        if _sealed(args.split):
            # Sealed like the retrieval instrument: owner-only before any byte lands.
            out.mkdir(parents=True, exist_ok=True)
            os.chmod(out, 0o700)
        manifest = write_split(args.split, seeds, out)
        if _sealed(args.split):
            for f in out.iterdir():
                os.chmod(f, 0o600)
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    if args.action == "run":
        return _run(args)
    if args.action == "validity":
        out = validity_command(args)
        for c in out["checks"]:
            print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']}: {c['value']}")
        print("validity:", "PASS" if out["pass"] else "FAIL")
        return 0 if out["pass"] else 1
    rep = json.loads(Path(args.path).read_text(encoding="utf-8"))
    print(_markdown(rep, _sealed(rep.get("split", "dev"))), end="")
    return 0
