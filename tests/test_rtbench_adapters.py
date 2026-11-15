"""RTBench Task 6: adapters, runner, validity (R5, R6 on a sample)."""
from scripts.rtbench.generate import DEV_SEEDS, generate_case
from scripts.rtbench.adapters import OracleAdapter, OverwriteAdapter, AppendOnlyAdapter, TrackNReader
from scripts.rtbench.run import run_cases, validity

CASES = [generate_case(s, f"dev-{i:04d}") for i, s in enumerate(DEV_SEEDS[:60])]
cases, golds = [c for c, _ in CASES], [g for _, g in CASES]

def test_oracle_scores_full_marks_on_both_tracks():                    # R5 (sample)
    for track, a in (("S", OracleAdapter()), ("N", TrackNReader(OracleAdapter()))):
        r = run_cases(a, track, cases, golds, itemised=True)
        assert r["score"]["headline"] == 1.0 and r["score"]["control"] == 1.0 and not r["misses"]

def test_baselines_stay_under_the_bar_on_both_tracks():                # R6 (sample)
    reps = {}
    for name, mk in (("oracle", OracleAdapter), ("overwrite", OverwriteAdapter), ("append_only", AppendOnlyAdapter)):
        reps[(name, "S")] = run_cases(mk(), "S", cases, golds, itemised=False)
        reps[(name, "N")] = run_cases(TrackNReader(mk()), "N", cases, golds, itemised=False)
    v = validity(reps)
    assert v["pass"], v["checks"]

def test_baselines_drop_aborts_and_track_n_reader_sees_only_prose():
    a = TrackNReader(OverwriteAdapter())
    a.reset()
    a.ingest({"session": None, "date": "2026-01-06",
              "text": "[Aside, 2026-01-06] Note: Orrin Mill's annual output is 512 tonnes. Scratch that, do not record it."})
    assert a.answer({"qid": "q", "text": "Was 512 tonnes ever recorded as Orrin Mill's annual output? Answer yes or no."}) == "no"
