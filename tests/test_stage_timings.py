from scripts import hybrid_search as hs
from test_hybrid_search import tmp_index  # noqa: F401 — pytest fixture; tests/ is on sys.path (prepend mode, no package)


def test_timings_are_filled_and_ranking_unchanged(tmp_index):
    plain = hs.search_hybrid("hybrid search citation source", limit=5, db_path=tmp_index)
    assert len(plain) >= 2, "a vacuous result would make the equality below prove nothing"
    timings = {}
    timed = hs.search_hybrid("hybrid search citation source", limit=5, db_path=tmp_index,
                             timings=timings)
    assert timed == plain
    assert set(timings) == set(hs.STAGES)
    assert all(isinstance(v, float) and v >= 0 for v in timings.values())
    assert timings["total"] >= max(v for k, v in timings.items() if k != "total")


def test_missing_index_leaves_timings_empty(tmp_path):
    timings = {}
    assert hs.search_hybrid("x", db_path=str(tmp_path / "none.sqlite"), timings=timings) == []
    assert timings == {}


def test_search_explain_prints_stage_table_and_traces_timings(tmp_path, monkeypatch, capsys):
    import json

    import scripts.context_export as ce
    monkeypatch.setattr(ce, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("alpha beta")
    ce.search_memory("alpha", hybrid=False, explain=True, trace=True,
                     trace_id="trace-20260929T000000Z-explain1")
    out = capsys.readouterr().out
    assert "Stage timings (ms):" in out and "keyword_scan" in out
    trace = json.loads((tmp_path / "data" / "traces" / "trace-20260929T000000Z-explain1.json").read_text())
    assert set(trace["events"][1]["payload"]["timings_ms"]) == {"keyword_scan", "total"}
