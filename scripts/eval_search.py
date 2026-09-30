import hashlib
import math
import os
import json
import sys
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from typing import List, Dict, Any
from scripts.metrics import get_rank, calculate_top_k_hit, calculate_reciprocal_rank, calculate_mean_reciprocal_rank, calculate_accuracy
# We need a keyword search implementation or we can just reuse hybrid search without the semantic part, 
# but the prompt says to use keyword or hybrid search.
from scripts.hybrid_search import search_hybrid
from scripts.config import PROJECT_ROOT

from scripts.instruments import gold_path as _gold_path


def _default_gold() -> str:
    """Resolved lazily: the cleartext is local-only and may be absent."""
    return str(_gold_path())


DEFAULT_GOLD = None   # sentinel; resolved at call time by _default_gold()

def _resolve(path: str) -> str:
    """Resolve a repo-relative path against PROJECT_ROOT so evaluation is
    CWD-independent. Absolute paths are returned unchanged."""
    return path if os.path.isabs(path) else str(PROJECT_ROOT / path)

def load_gold_dataset(filepath: str) -> List[Dict[str, Any]]:
    filepath = _resolve(filepath)
    if not os.path.exists(filepath):
        print(f"Error: Gold dataset '{filepath}' not found.")
        sys.exit(1)

    dataset = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if 'query' not in data or 'expected_paths' not in data:
                    print(f"Warning: Skipping malformed line {lineno} (missing required fields)")
                    continue
                dataset.append(data)
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed JSON line {lineno}")
    return dataset

def validate_gold_dataset(filepath: str, quiet: bool = False) -> bool:
    dataset = load_gold_dataset(filepath)
    if not dataset:
        print("Error: No valid queries found in gold dataset to validate.")
        sys.exit(1)

    all_valid = True
    for item in dataset:
        for path in item['expected_paths']:
            if not os.path.exists(_resolve(path)):
                print(f"Error: Gold dataset references missing path: {path}")
                all_valid = False

    if not all_valid:
        sys.exit(1)

    if not quiet:
        print(f"Gold dataset '{filepath}' validated successfully.")
    return True

def _keyword_retrieve(query: str) -> List[str]:
    """The non-hybrid baseline: a substring scan over wiki/ + docs/."""
    import glob
    wiki_files = glob.glob(str(PROJECT_ROOT / "wiki" / "**" / "*.md"), recursive=True) + \
        glob.glob(str(PROJECT_ROOT / "docs" / "**" / "*.md"), recursive=True)
    keywords = query.lower().split()
    matches = []
    for filepath in wiki_files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            score = sum(1 for k in keywords if k in content.lower())
            if score > 0:
                matches.append((score, filepath))
        except Exception:
            continue
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m[1] for m in matches[:10]]


def _percentile(values, pct):
    ordered = sorted(values)
    if not ordered:
        return None
    k = max(0, min(len(ordered) - 1, math.ceil(pct / 100 * len(ordered)) - 1))  # nearest rank
    return round(ordered[k], 3)


def result_digest(rows, metrics) -> str:
    """SHA-256 over what the ranking produced: per-item ranked ids and the
    quality metrics. Timings, timestamps and resources are excluded — they
    vary by nature (design §3.5)."""
    body = {"rows": [{"index": r["index"], "retrieved": r["retrieved"]} for r in rows],
            "top1": metrics["top1_accuracy"], "top3": metrics["top3_accuracy"],
            "mrr": metrics["mrr"]}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate(dataset, *, index_path, hybrid=True):
    """Pure core: no printing, no exit. Returns (metrics, rows). Rows name
    items by INDEX; no query text leaves this function."""
    rows, rrs, stage_samples = [], [], {}
    top1 = top3 = 0
    for index, item in enumerate(dataset):
        expected = item["expected_paths"]
        if hybrid:
            timings = {}
            results = search_hybrid(item["query"], limit=10, db_path=index_path, timings=timings)
            for name, value in timings.items():
                stage_samples.setdefault(name, []).append(value)
            retrieved = [r["source_path"] for r in results]
        else:
            retrieved = _keyword_retrieve(item["query"])
        first = get_rank(expected, retrieved) or None  # the same matcher the metrics use
        rows.append({"index": index, "retrieved": retrieved, "first_hit_rank": first})
        top1 += calculate_top_k_hit(expected, retrieved, 1)
        top3 += calculate_top_k_hit(expected, retrieved, 3)
        rrs.append(calculate_reciprocal_rank(expected, retrieved))
    n = len(dataset)
    metrics = {
        "queries": n,
        "top1_accuracy": calculate_accuracy(top1, n),
        "top3_accuracy": calculate_accuracy(top3, n),
        "mrr": calculate_mean_reciprocal_rank(rrs),
        "failing_items": [r["index"] for r in rows
                          if r["first_hit_rank"] is None or r["first_hit_rank"] > 3],
        "stage_latency_ms": {name: {"p50": _percentile(v, 50), "p95": _percentile(v, 95)}
                             for name, v in stage_samples.items()},
    }
    metrics["failures"] = len(metrics["failing_items"])
    metrics["result_digest"] = result_digest(rows, metrics)
    return metrics, rows


def run_search_evaluation(gold_file: str = None, hybrid: bool = False, min_top3: float = 0.0,
                          as_json: bool = False, max_queries: int | None = None,
                          index_path: str | None = None, keep_rows: bool = False):
    """Score retrieval against the gold set.

    ``index_path`` names the index to score. It defaults to the canonical one,
    so ad-hoc use is unchanged, and it exists so a gate can hand this function a
    MEASUREMENT SNAPSHOT — a hard link to the index as it was when the gate
    started. Another project writing to the shared store mid-run then changes
    the live store and the next rebuild, and changes nothing about the number
    being computed here. The index is reported alongside the metrics, because a
    result that cannot name its input is not a measurement.

    ``max_queries`` runs only the first N queries. It exists for LIVENESS
    checks, not for measurement: the MCP e2e smoke called this tool with the
    full 50-query set, which at 199k chunks takes minutes and dominated the
    smoke's runtime — a smoke test should assert the tool responds, not run an
    evaluation. Quality numbers must always come from the whole set, so a
    truncated run labels itself as one in its output.
    """
    gold_file = gold_file or _default_gold()
    if index_path is None:
        from scripts.config import get_search_db_path
        index_path = str(get_search_db_path())
    validate_gold_dataset(gold_file, quiet=as_json)
    dataset = load_gold_dataset(gold_file)
    if not dataset:
        print("Error: No valid queries found in gold dataset.")
        sys.exit(1)

    full_size = len(dataset)
    if max_queries is not None:
        dataset = dataset[:max(1, int(max_queries))]

    if not as_json:
        print(f"Running Evaluation on {len(dataset)} queries (Hybrid: {hybrid})...")

    core, rows = evaluate(dataset, index_path=index_path, hybrid=hybrid)
    total_queries, failures = core["queries"], core["failures"]
    top1_accuracy, top3_accuracy, mrr = core["top1_accuracy"], core["top3_accuracy"], core["mrr"]
    metrics = dict(core)
    metrics.update({
        "min_top3": min_top3,
        "passed": top3_accuracy >= min_top3,
        "index_path": index_path,
        "index_identity": _identity_of(index_path),
        "snapshot_coherence": _coherence_of(index_path),
    })
    if keep_rows:
        metrics["_rows"] = rows

    if as_json:
        print(json.dumps({k: v for k, v in metrics.items() if not k.startswith("_")}, indent=2))
    else:
        print("\nSearch Evaluation Results")
        if total_queries < full_size:
            print(f"(LIVENESS RUN — first {total_queries} of {full_size} queries; not a quality measurement)")
        print("=========================")
        print(f"Queries: {total_queries}")
        print(f"Top-1 accuracy: {top1_accuracy * 100:.0f}%")
        print(f"Top-3 accuracy: {top3_accuracy * 100:.0f}%")
        print(f"Mean reciprocal rank: {mrr:.2f}")
        print(f"Failures: {failures}")
        ident = metrics["index_identity"]
        if ident:
            print(f"Index: {index_path}")
            print(f"  {ident.get('chunks')} chunks / {ident.get('sources')} sources"
                  f"  chunk-set {str(ident.get('chunk_id_set_sha'))[:12]}"
                  f"  space {','.join(ident.get('embedding_space') or [])}")
        c = metrics["snapshot_coherence"]
        detail = (f"revision {c['revision']} ({c['atomicity']})"
                  if c.get("revision") is not None else c.get("reason"))
        print(f"Snapshot coherence: {c['status']} — {detail}")
        print("=========================")

    if top3_accuracy < min_top3:
        if _index_uses_mock_embeddings(index_path):
            # Retrieval quality cannot be judged against deterministic-hash
            # vectors: the semantic half of the score is noise, so the number
            # measures nothing. The smoke test runs under the mock provider,
            # and this gate previously "passed" there only because that run had
            # overwritten the real index with mock embeddings — the very bug
            # this skip exists to stop masking.
            if not as_json:
                print(
                    f"Skipping the retrieval quality gate: the index was built with mock "
                    f"embeddings, so top-3 ({top3_accuracy:.2f}) is not a meaningful "
                    f"measurement. Rebuild with a real provider to enforce it."
                )
            metrics["skipped_reason"] = "mock embeddings"
            return metrics
        if not as_json:
            print(f"Error: Top-3 accuracy ({top3_accuracy}) is below required minimum ({min_top3})")
        sys.exit(1)

    return metrics


def _identity_of(index_path: str) -> dict:
    """What produced this number. Never fatal: identity is reporting, not a gate."""
    try:
        from scripts.measurement_snapshot import index_identity
        return index_identity(index_path)
    except Exception:
        return {}


def _coherence_of(index_path) -> dict:
    """The coherence record of the snapshot this index belongs to (design §9.1).

    A live index, or a legacy snapshot, is UNVERIFIED: never read as agreement.
    An EXTERNAL_OBSERVED revision is reported as such, never as MANAGED (gate
    item 13). Reporting, not a gate: this never raises.
    """
    if not index_path:
        return {"status": "UNVERIFIED", "reason": "live index, not a snapshot"}
    manifest = Path(index_path).parent / "manifest.json"
    try:
        coherence = json.loads(manifest.read_text(encoding="utf-8")).get("coherence")
    except (OSError, ValueError):
        coherence = None
    if not coherence:
        return {"status": "UNVERIFIED", "reason": "no coherence record"}
    return {key: coherence.get(key)
            for key in ("status", "reason", "purpose", "revision", "atomicity")}


def _index_uses_mock_embeddings(index_path: str | None = None) -> bool:
    """True when the search index holds deterministic-hash vectors."""
    import sqlite3
    from scripts.config import get_search_db_path

    try:
        target = index_path or get_search_db_path()
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        row = conn.execute("SELECT provider FROM embeddings LIMIT 1").fetchone()
        conn.close()
        return bool(row) and row[0] == "mock"
    except sqlite3.Error:
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=None)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--max-queries", type=int, default=None,
                        help="Run only the first N queries (liveness check, not a measurement)")
    parser.add_argument("--min-top3", type=float, default=0.0)
    parser.add_argument("--index", default=None, dest="index_path",
                        help="Score this index instead of the canonical one — "
                             "point it at a measurement snapshot so an external "
                             "write mid-run cannot change the number")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit a single machine-parseable JSON object instead of the text report")
    parser.add_argument("--save-run", action="store_true",
                        help="Save per-item rows (by index) to ~/.zurvan/eval-runs for `zurvan eval movers`")
    args = parser.parse_args()

    if args.validate:
        validate_gold_dataset(args.gold or _default_gold())
    else:
        gold = args.gold or _default_gold()
        metrics = run_search_evaluation(gold, args.hybrid, args.min_top3, as_json=args.as_json,
                                        max_queries=args.max_queries, index_path=args.index_path,
                                        keep_rows=args.save_run)
        if args.save_run:
            from scripts.eval_datasets import dataset_for_file
            from scripts.eval_runs import save_run
            path = save_run(metrics, metrics.pop("_rows"), dataset_for_file(_resolve(gold)))
            print(f"Run saved: {path}", file=sys.stderr if args.as_json else sys.stdout)
