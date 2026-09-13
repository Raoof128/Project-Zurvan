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


# ── the mock-embedding gate skip ──────────────────────────────────────────────

def test_gate_is_skipped_when_the_index_holds_mock_embeddings(tmp_path, monkeypatch, capsys):
    """Retrieval quality cannot be judged against deterministic-hash vectors.

    This gate used to "pass" during the smoke test only because that run had
    overwritten the real index with mock embeddings — it was measuring the
    poisoning, not the retrieval.
    """
    import sqlite3
    from scripts import eval_search as es

    db = tmp_path / "search.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT, provider TEXT, model TEXT)")
    conn.execute("INSERT INTO embeddings VALUES ('c', 'mock', 'deterministic_hash')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(db))

    assert es._index_uses_mock_embeddings() is True


def test_gate_is_enforced_when_the_index_holds_real_embeddings(tmp_path, monkeypatch):
    import sqlite3
    from scripts import eval_search as es

    db = tmp_path / "search.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE embeddings (chunk_id TEXT, provider TEXT, model TEXT)")
    conn.execute("INSERT INTO embeddings VALUES ('c', 'sentence_transformers', 'all-MiniLM-L6-v2')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(db))

    assert es._index_uses_mock_embeddings() is False


def test_a_missing_index_does_not_count_as_mock(tmp_path, monkeypatch):
    """Absent is not the same as mock — a missing index must still fail the gate."""
    from scripts import eval_search as es

    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "absent.sqlite"))

    assert es._index_uses_mock_embeddings() is False
