import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.config import PROJECT_ROOT
from scripts.trace_schema import hash_payload
from scripts.trace_validate import validate_trace_file


DEFAULT_GOLD = str(PROJECT_ROOT / "eval" / "provenance_gold.jsonl")
DEFAULT_EVENT_TYPES = ["retrieval.query", "retrieval.result", "context.assembled"]


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def load_gold_dataset(filepath: str = DEFAULT_GOLD) -> List[Dict[str, Any]]:
    gold_path = _resolve(filepath)
    if not gold_path.exists():
        print(f"Error: Gold dataset '{gold_path}' not found.")
        sys.exit(1)

    dataset: List[Dict[str, Any]] = []
    with gold_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                print(f"Warning: Skipping malformed JSON line: {line}")
                continue
            if "trace_path" not in item or "expected_source_paths" not in item:
                print(f"Warning: Skipping malformed line (missing required fields): {line}")
                continue
            item.setdefault("expected_event_types", list(DEFAULT_EVENT_TYPES))
            item.setdefault("expected_chunk_ids", [])
            item.setdefault("expect_graph_context", False)
            dataset.append(item)
    return dataset


def validate_gold_dataset(filepath: str = DEFAULT_GOLD) -> bool:
    dataset = load_gold_dataset(filepath)
    if not dataset:
        print("Error: No valid provenance cases found in gold dataset to validate.")
        sys.exit(1)

    all_valid = True
    for item in dataset:
        trace_path = _resolve(item["trace_path"])
        if not trace_path.exists():
            print(f"Error: Gold dataset references missing trace: {item['trace_path']}")
            all_valid = False

        for field_name in ("expected_source_paths", "expected_event_types", "expected_chunk_ids"):
            if not isinstance(item.get(field_name, []), list):
                print(f"Error: Gold case {item.get('id', item['trace_path'])} has non-list {field_name}")
                all_valid = False

    if not all_valid:
        sys.exit(1)

    print(f"Gold dataset '{filepath}' validated successfully.")
    return True


def _load_trace(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Error: cannot read trace '{path}': {exc}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid trace JSON '{path}': {exc}")
        sys.exit(1)


def _iter_payload_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_payload_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_payload_values(item)
    else:
        yield value


def _is_raw_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = Path(value).parts
    return "raw" in parts or value.startswith("raw/")


def _raw_leak_detected(trace: Dict[str, Any]) -> bool:
    for event in trace.get("events", []):
        for value in _iter_payload_values(event.get("payload", {})):
            if _is_raw_path(value):
                return True
    return False


def _hash_integrity(trace: Dict[str, Any]) -> tuple[int, int]:
    total = 0
    valid = 0
    for event in trace.get("events", []):
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            total += 1
            continue
        total += 1
        if event.get("payload_hash") == hash_payload(payload):
            valid += 1
    return valid, total


def _event_types(trace: Dict[str, Any]) -> set[str]:
    return {str(event.get("event_type", "")) for event in trace.get("events", [])}


def _result_payloads(trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for event in trace.get("events", []):
        if event.get("event_type") in {"retrieval.result", "retrieval"}:
            payload = event.get("payload", {})
            if isinstance(payload, dict):
                for result in payload.get("results", []):
                    if isinstance(result, dict):
                        results.append(result)
    return results


def _source_paths(trace: Dict[str, Any]) -> set[str]:
    return {str(result.get("source_path", "")) for result in _result_payloads(trace) if result.get("source_path")}


def _chunk_ids(trace: Dict[str, Any]) -> set[str]:
    found = {str(result.get("chunk_id")) for result in _result_payloads(trace) if result.get("chunk_id")}
    for event in trace.get("events", []):
        if event.get("event_type") == "context.assembled":
            payload = event.get("payload", {})
            if isinstance(payload, dict):
                found.update(str(chunk_id) for chunk_id in payload.get("included_chunk_ids", []) if chunk_id)
    return found


def _recall(expected: List[str], observed: set[str]) -> float:
    if not expected:
        return 1.0
    hits = sum(1 for item in expected if item in observed)
    return hits / len(expected)


def _completeness(item: Dict[str, Any], trace: Dict[str, Any]) -> float:
    required_units = list(item.get("expected_event_types", DEFAULT_EVENT_TYPES))
    required_units.extend(f"chunk:{chunk_id}" for chunk_id in item.get("expected_chunk_ids", []))
    if not required_units:
        return 1.0

    event_types = _event_types(trace)
    chunk_ids = _chunk_ids(trace)
    hits = 0
    for unit in required_units:
        if unit.startswith("chunk:"):
            hits += unit.removeprefix("chunk:") in chunk_ids
        else:
            hits += unit in event_types
    return hits / len(required_units)


def _graph_presence(item: Dict[str, Any], trace: Dict[str, Any]) -> float | None:
    if not item.get("expect_graph_context", False):
        return None
    return 1.0 if "graph_context" in _event_types(trace) else 0.0


def _format_percent(value: float) -> str:
    return f"{value * 100:.0f}%"


def run_provenance_evaluation(
    gold_file: str = DEFAULT_GOLD,
    min_source_recall: float = 0.0,
    min_provenance_completeness: float = 0.0,
    min_graph_context_presence: float = 0.0,
) -> Dict[str, float]:
    validate_gold_dataset(gold_file)
    dataset = load_gold_dataset(gold_file)
    traces = [(_resolve(item["trace_path"]), _load_trace(_resolve(item["trace_path"]))) for item in dataset]

    raw_leak_count = sum(1 for _, trace in traces if _raw_leak_detected(trace))
    valid_hashes = 0
    total_hashes = 0
    validation_issues: List[str] = []
    for trace_path, trace in traces:
        validation = validate_trace_file(trace_path)
        if not validation.valid:
            validation_issues.extend(f"{trace_path}: {issue}" for issue in validation.issues)
        valid, total = _hash_integrity(trace)
        valid_hashes += valid
        total_hashes += total

    raw_leak_rate = raw_leak_count / len(traces) if traces else 0.0
    hash_integrity_rate = valid_hashes / total_hashes if total_hashes else 1.0

    if raw_leak_rate != 0.0 or hash_integrity_rate != 1.0 or validation_issues:
        print("Invariant Gate Failed")
        print(f"raw_leak_rate: {_format_percent(raw_leak_rate)}")
        print(f"hash_integrity_rate: {_format_percent(hash_integrity_rate)}")
        for issue in validation_issues:
            print(f" - {issue}")
        sys.exit(1)

    source_scores = []
    completeness_scores = []
    graph_scores = []
    for item, (_, trace) in zip(dataset, traces):
        source_scores.append(_recall(item.get("expected_source_paths", []), _source_paths(trace)))
        completeness_scores.append(_completeness(item, trace))
        graph_score = _graph_presence(item, trace)
        if graph_score is not None:
            graph_scores.append(graph_score)

    expected_source_recall = sum(source_scores) / len(source_scores) if source_scores else 0.0
    provenance_completeness = sum(completeness_scores) / len(completeness_scores) if completeness_scores else 0.0
    graph_context_presence = sum(graph_scores) / len(graph_scores) if graph_scores else 1.0

    print("\nProvenance Evaluation Results")
    print("=============================")
    print(f"Cases: {len(dataset)}")
    print(f"raw_leak_rate: {_format_percent(raw_leak_rate)}")
    print(f"hash_integrity_rate: {_format_percent(hash_integrity_rate)}")
    print(f"expected_source_recall: {_format_percent(expected_source_recall)}")
    print(f"provenance_completeness: {_format_percent(provenance_completeness)}")
    print(f"graph_context_presence: {_format_percent(graph_context_presence)}")
    print("=============================")

    failures = []
    if expected_source_recall < min_source_recall:
        failures.append(
            f"expected_source_recall ({expected_source_recall}) is below required minimum ({min_source_recall})"
        )
    if provenance_completeness < min_provenance_completeness:
        failures.append(
            "provenance_completeness "
            f"({provenance_completeness}) is below required minimum ({min_provenance_completeness})"
        )
    if graph_context_presence < min_graph_context_presence:
        failures.append(
            f"graph_context_presence ({graph_context_presence}) is below required minimum "
            f"({min_graph_context_presence})"
        )
    if failures:
        for failure in failures:
            print(f"Error: {failure}")
        sys.exit(1)

    return {
        "cases": float(len(dataset)),
        "raw_leak_rate": raw_leak_rate,
        "hash_integrity_rate": hash_integrity_rate,
        "expected_source_recall": expected_source_recall,
        "provenance_completeness": provenance_completeness,
        "graph_context_presence": graph_context_presence,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default=DEFAULT_GOLD)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--min-source-recall", type=float, default=0.0)
    parser.add_argument("--min-provenance-completeness", type=float, default=0.0)
    parser.add_argument("--min-graph-context-presence", type=float, default=0.0)
    args = parser.parse_args()

    if args.validate:
        validate_gold_dataset(args.gold)
    else:
        run_provenance_evaluation(
            args.gold,
            min_source_recall=args.min_source_recall,
            min_provenance_completeness=args.min_provenance_completeness,
            min_graph_context_presence=args.min_graph_context_presence,
        )
