import pytest
import os
from scripts.metrics import calculate_top_k_hit, calculate_reciprocal_rank, calculate_mean_reciprocal_rank, calculate_accuracy
from scripts.eval_search import load_gold_dataset

def test_metrics():
    expected = ["wiki/target.md"]
    retrieved = ["wiki/other.md", "wiki/target.md", "wiki/another.md"]
    
    # rank is 2
    assert calculate_top_k_hit(expected, retrieved, 1) is False
    assert calculate_top_k_hit(expected, retrieved, 3) is True
    assert calculate_reciprocal_rank(expected, retrieved) == 0.5
    
    mrr = calculate_mean_reciprocal_rank([1.0, 0.5, 0.0])
    assert mrr == 0.5
    
    assert calculate_accuracy(2, 4) == 0.5

def test_load_gold_dataset(tmp_path):
    gold_file = tmp_path / "gold.jsonl"
    content = '{"query": "q1", "expected_paths": ["p1"]}\n{"invalid_json"}\n{"query": "q2"}'
    gold_file.write_text(content)
    
    dataset = load_gold_dataset(str(gold_file))
    # Should only load the valid line
    assert len(dataset) == 1
    assert dataset[0]['query'] == "q1"

def test_validate_gold_dataset_fails_on_missing(tmp_path):
    from scripts.eval_search import validate_gold_dataset
    gold_file = tmp_path / "gold.jsonl"
    gold_file.write_text('{"query": "q1", "expected_paths": ["does_not_exist.md"]}')
    with pytest.raises(SystemExit):
        validate_gold_dataset(str(gold_file))

def test_validate_gold_dataset_passes_on_valid(tmp_path):
    from scripts.eval_search import validate_gold_dataset
    real_file = tmp_path / "real.md"
    real_file.write_text("content")
    gold_file = tmp_path / "gold.jsonl"
    gold_file.write_text(f'{{"query": "q1", "expected_paths": ["{real_file}"]}}')
    assert validate_gold_dataset(str(gold_file)) is True

def test_run_search_evaluation_json_output(capsys):
    import json as _json
    from scripts.eval_search import run_search_evaluation

    metrics = run_search_evaluation(hybrid=True, min_top3=0.0, as_json=True)
    payload = _json.loads(capsys.readouterr().out)

    assert payload["queries"] == metrics["queries"]
    assert payload["passed"] is True
    assert 0.0 <= payload["top3_accuracy"] <= 1.0
