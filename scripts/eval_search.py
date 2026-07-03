import os
import json
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from typing import List, Dict, Any
from scripts.metrics import calculate_top_k_hit, calculate_reciprocal_rank, calculate_mean_reciprocal_rank, calculate_accuracy
# We need a keyword search implementation or we can just reuse hybrid search without the semantic part, 
# but the prompt says to use keyword or hybrid search.
from scripts.hybrid_search import search_hybrid
from scripts.config import PROJECT_ROOT

DEFAULT_GOLD = str(PROJECT_ROOT / "eval" / "search_gold.jsonl")

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
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if 'query' not in data or 'expected_paths' not in data:
                    print(f"Warning: Skipping malformed line (missing required fields): {line}")
                    continue
                dataset.append(data)
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed JSON line: {line}")
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

def run_search_evaluation(gold_file: str = DEFAULT_GOLD, hybrid: bool = False, min_top3: float = 0.0, as_json: bool = False):
    validate_gold_dataset(gold_file, quiet=as_json)
    dataset = load_gold_dataset(gold_file)
    if not dataset:
        print("Error: No valid queries found in gold dataset.")
        sys.exit(1)
        
    total_queries = len(dataset)
    top1_hits = 0
    top3_hits = 0
    reciprocal_ranks = []
    failures = 0

    if not as_json:
        print(f"Running Evaluation on {total_queries} queries (Hybrid: {hybrid})...")
    
    for item in dataset:
        query = item['query']
        expected_paths = item['expected_paths']
        
        # If hybrid=False, we ideally want pure keyword search. 
        # But Phase 3.6 search_memory only printed results. 
        # For evaluation, we need structured results. 
        # Since Phase 4 added `search_hybrid`, we will use it and rely on its FTS scoring if semantic is disabled, 
        # but `search_hybrid` hardcodes 0.6 keyword + 0.4 semantic.
        # So we'll just run `search_hybrid` to get paths. If hybrid is false, we can still use it, 
        # or we could parse standard search_memory output, but `search_hybrid` returns structured dicts.
        # For simplicity, we just use search_hybrid for both, or implement a basic keyword search wrapper.
        # Let's just use search_hybrid for now, as it serves the eval purpose and is robust.
        
        if hybrid:
            results = search_hybrid(query, limit=10)
            retrieved_paths = [r['source_path'] for r in results]
        else:
            # Basic keyword search using FTS5 (since search_hybrid relies on FTS5 anyway)
            # We'll just call search_hybrid and rely on its BM25 component implicitly or just use search_hybrid directly.
            # To be strict, if hybrid is False, we just use standard search_memory logic which scans files.
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
            retrieved_paths = [m[1] for m in matches[:10]]

        # Calculate metrics for this query
        if calculate_top_k_hit(expected_paths, retrieved_paths, 1):
            top1_hits += 1
        if calculate_top_k_hit(expected_paths, retrieved_paths, 3):
            top3_hits += 1
        else:
            failures += 1
            
        rr = calculate_reciprocal_rank(expected_paths, retrieved_paths)
        reciprocal_ranks.append(rr)
        
    top1_accuracy = calculate_accuracy(top1_hits, total_queries)
    top3_accuracy = calculate_accuracy(top3_hits, total_queries)
    mrr = calculate_mean_reciprocal_rank(reciprocal_ranks)

    metrics = {
        "queries": total_queries,
        "top1_accuracy": top1_accuracy,
        "top3_accuracy": top3_accuracy,
        "mrr": mrr,
        "failures": failures,
        "min_top3": min_top3,
        "passed": top3_accuracy >= min_top3,
    }

    if as_json:
        print(json.dumps(metrics, indent=2))
    else:
        print("\nSearch Evaluation Results")
        print("=========================")
        print(f"Queries: {total_queries}")
        print(f"Top-1 accuracy: {top1_accuracy * 100:.0f}%")
        print(f"Top-3 accuracy: {top3_accuracy * 100:.0f}%")
        print(f"Mean reciprocal rank: {mrr:.2f}")
        print(f"Failures: {failures}")
        print("=========================")

    if top3_accuracy < min_top3:
        if not as_json:
            print(f"Error: Top-3 accuracy ({top3_accuracy}) is below required minimum ({min_top3})")
        sys.exit(1)

    return metrics

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--min-top3", type=float, default=0.0)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit a single machine-parseable JSON object instead of the text report")
    args = parser.parse_args()

    if args.validate:
        validate_gold_dataset(args.gold)
    else:
        run_search_evaluation(args.gold, args.hybrid, args.min_top3, as_json=args.as_json)
