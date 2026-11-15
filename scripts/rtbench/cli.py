"""`zurvan bench rtbench generate|run|report` (spec §9).

A holdout run is sealed: scores only, never an item, in the report or on stdout.
"""

import contextvars
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HOME = Path.home() / ".zurvan"
REGISTRY = Path(__file__).resolve().parents[2] / "eval" / "datasets.json"
TYPES = ("Q1", "Q2", "Q3", "Q4", "Q5", "Q6")


def register(subparsers) -> None:
    from scripts.rtbench.adapters import ADAPTERS
    bench = subparsers.add_parser("bench", help="Benchmarks (rtbench: record-time, IOU-Z2-5)")
    names = bench.add_subparsers(dest="bench_name")
    rt = names.add_parser("rtbench", help="System-neutral record-time benchmark")
    sub = rt.add_subparsers(dest="action")
    g = sub.add_parser("generate", help="Write a split's cases and gold")
    g.add_argument("--split", choices=("dev", "holdout"), required=True)
    g.add_argument("--out", default=None)
    r = sub.add_parser("run", help="Score one adapter on one track of one split")
    r.add_argument("--adapter", choices=sorted(ADAPTERS), required=True)
    r.add_argument("--track", choices=("S", "N"), required=True)
    r.add_argument("--split", choices=("dev", "holdout"), required=True)
    r.add_argument("--cases-dir", default=None)
    r.add_argument("--report-dir", default=None)
    r.add_argument("--work-dir", default=None, help="Zurvan adapters: where per-case stores go (default: a temp dir)")
    r.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("report", help="Print a run report")
    p.add_argument("path")


def _default_dir(split):
    return HOME / "rtbench" / "dev" if split == "dev" else HOME / "instruments" / "rtbench_holdout"


def _registration(split, cases_path):
    from scripts.rtbench.canon import sha256_file
    try:
        entry = json.loads(REGISTRY.read_text(encoding="utf-8"))["datasets"].get(f"rtbench_{split}")
    except (OSError, ValueError):
        entry = None
    if entry is None:
        return "UNREGISTERED"
    return "REGISTERED" if entry["sha256"] == sha256_file(cases_path) else "MISMATCH"


def _load(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_report(report, report_dir) -> tuple:
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = out / f"rtbench-{report['adapter']}-{report['track']}-{report.get('split', 'dev')}-{stamp}"
    sealed = report.get("split") == "holdout"
    if sealed:
        report = {k: v for k, v in report.items() if k != "misses"}
    j, m = base.with_suffix(".json"), base.with_suffix(".md")
    j.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    m.write_text(_markdown(report, sealed), encoding="utf-8")
    return j, m


def _markdown(rep, sealed):
    s = rep["score"]
    lines = [f"# RTBench run: {rep['adapter']}, Track {rep['track']}, {rep.get('split', 'dev')}", "",
             f"- cases {rep['n_cases']}, questions {rep['n_questions']}, errors {rep['errors']}",
             f"- headline (Q1-Q5 macro) **{s['headline']:.3f}**; control (Q6) {s['control']:.3f}",
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


def _run(args) -> int:
    from scripts.rtbench import GENERATOR_VERSION
    from scripts.rtbench.adapters import ADAPTERS, ZURVAN_ADAPTERS, TrackNReader
    from scripts.rtbench.run import run_cases
    if args.adapter in ZURVAN_ADAPTERS and args.track == "N":
        print("refused: the Zurvan adapters are Track S only (spec §8); Track N is for systems that read prose",
              file=sys.stderr)
        return 2
    d = Path(args.cases_dir) if args.cases_dir else _default_dir(args.split)
    cases_p = d / f"{args.split}.cases.jsonl"
    if not cases_p.exists():
        print(f"no cases at {cases_p}; run `zurvan bench rtbench generate --split {args.split}` first", file=sys.stderr)
        return 1
    registration = _registration(args.split, cases_p)
    if registration == "MISMATCH":
        print(f"refused: {cases_p} does not match the registered rtbench_{args.split} digest", file=sys.stderr)
        return 1
    cases, golds = _load(cases_p), _load(d / f"{args.split}.gold.jsonl")
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
                                           itemised=args.split != "holdout")
    finally:
        adapter.close()
        if tmp is not None:
            tmp.cleanup()
    report.update(split=args.split, generator_version=GENERATOR_VERSION, registration=registration,
                  date=datetime.now().date().isoformat())
    j, _ = write_report(report, args.report_dir or HOME / "reports")
    s = report["score"]
    print(f"{report['adapter']} Track {args.track} {args.split} ({registration}): headline {s['headline']:.3f}, "
          f"control {s['control']:.3f}, errors {report['errors']}")
    print("  " + "  ".join(f"{t} {s['by_type'][t]['accuracy']:.3f}" for t in TYPES if t in s["by_type"]))
    print(f"  report: {j}")
    return 0


def dispatch(args) -> int:
    if getattr(args, "bench_name", None) != "rtbench" or not getattr(args, "action", None):
        print("usage: zurvan bench rtbench {generate,run,report} ...", file=sys.stderr)
        return 2
    if args.action == "generate":
        from scripts.rtbench.generate import DEV_SEEDS, holdout_seeds, write_split
        seeds = DEV_SEEDS if args.split == "dev" else holdout_seeds(HOME / "instruments")
        print(json.dumps(write_split(args.split, seeds, Path(args.out) if args.out else _default_dir(args.split)),
                         indent=2, sort_keys=True))
        return 0
    if args.action == "run":
        return _run(args)
    rep = json.loads(Path(args.path).read_text(encoding="utf-8"))
    print(_markdown(rep, rep.get("split") == "holdout"), end="")
    return 0
