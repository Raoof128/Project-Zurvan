import json

from scripts import eval_search as es

DATA = [{"query": "secret-query-text-one", "expected_paths": ["wiki/a.md"]},
        {"query": "secret-query-text-two", "expected_paths": ["wiki/z.md"]}]


def _fake(query, limit=10, db_path=None, timings=None):
    if timings is not None:
        timings.update({s: 1.0 for s in ("fts", "embed_query", "vector_scan", "hydrate", "fuse")}
                       | {"total": 5.0})
    return [{"source_path": "wiki/b.md"}, {"source_path": "wiki/a.md"}]


def test_rows_ranks_and_failing_items(monkeypatch):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    metrics, rows = es.evaluate(DATA, index_path="x")
    assert rows == [{"index": 0, "retrieved": ["wiki/b.md", "wiki/a.md"], "first_hit_rank": 2},
                    {"index": 1, "retrieved": ["wiki/b.md", "wiki/a.md"], "first_hit_rank": None}]
    assert metrics["failing_items"] == [1]
    assert "failing_queries" not in metrics
    assert metrics["stage_latency_ms"]["total"] == {"p50": 5.0, "p95": 5.0}


def test_result_digest_ignores_timings_and_tracks_ranking(monkeypatch):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    m1, r1 = es.evaluate(DATA, index_path="x")

    def slower(*a, timings=None, **k):
        out = _fake(*a, timings=timings, **k)
        if timings is not None:
            timings["total"] = 999.0
        return out
    monkeypatch.setattr(es, "search_hybrid", slower)
    m2, _ = es.evaluate(DATA, index_path="x")
    assert m1["result_digest"] == m2["result_digest"]

    monkeypatch.setattr(es, "search_hybrid",
                        lambda *a, **k: [{"source_path": "wiki/a.md"}, {"source_path": "wiki/b.md"}])
    m3, _ = es.evaluate(DATA, index_path="x")
    assert m3["result_digest"] != m1["result_digest"]


def test_json_output_never_contains_query_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(es, "search_hybrid", _fake)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("x")
    (tmp_path / "wiki" / "z.md").write_text("x")
    gold = tmp_path / "g.jsonl"
    gold.write_text("".join(json.dumps({"query": d["query"],
                                        "expected_paths": [str(tmp_path / p) for p in d["expected_paths"]]})
                            + "\n" for d in DATA))
    es.run_search_evaluation(str(gold), hybrid=True, as_json=True, index_path="x")
    out = capsys.readouterr().out
    assert "secret-query-text" not in out
    assert json.loads(out)["failing_items"] == [0, 1]


def test_a_malformed_gold_line_is_reported_by_number_not_content(tmp_path, capsys):
    gold = tmp_path / "g.jsonl"
    gold.write_text('{"query": "secret-query-text-three"}\n{not json secret-query-text-four\n')
    assert es.load_gold_dataset(str(gold)) == []
    out = capsys.readouterr().out
    assert "secret-query-text" not in out
    assert "line 1" in out and "line 2" in out


def test_result_digest_sees_a_ranking_change_the_metrics_cannot(monkeypatch):
    # Same hits at the same ranks, different results below them: every quality
    # metric is equal, so only the per-item ranked ids can tell the runs apart.
    monkeypatch.setattr(es, "search_hybrid", lambda *a, **k: [
        {"source_path": "wiki/a.md"}, {"source_path": "wiki/c.md"}])
    m1, _ = es.evaluate(DATA, index_path="x")
    monkeypatch.setattr(es, "search_hybrid", lambda *a, **k: [
        {"source_path": "wiki/a.md"}, {"source_path": "wiki/d.md"}])
    m2, _ = es.evaluate(DATA, index_path="x")
    assert (m1["top1_accuracy"], m1["top3_accuracy"], m1["mrr"]) == \
           (m2["top1_accuracy"], m2["top3_accuracy"], m2["mrr"])
    assert m1["result_digest"] != m2["result_digest"]


def test_percentiles_are_nearest_rank():
    samples = [float(v) for v in range(1, 21)]
    assert es._percentile(samples, 50) == 10.0
    assert es._percentile(samples, 95) == 19.0
    assert es._percentile([5.0], 95) == 5.0
    assert es._percentile([], 50) is None


def test_evaluate_reports_p50_and_p95_of_each_stage(monkeypatch):
    totals = iter([1.0, 9.0])

    def varying(query, limit=10, db_path=None, timings=None):
        timings.update({"total": next(totals)})
        return []
    monkeypatch.setattr(es, "search_hybrid", varying)
    metrics, _ = es.evaluate(DATA, index_path="x")
    assert metrics["stage_latency_ms"]["total"] == {"p50": 1.0, "p95": 9.0}
