"""RTBench Task 5: scorer (spec §6.3)."""
from scripts.rtbench.score import normalise, wilson, score_answers

def test_normalise():
    assert normalise(" 4,210  Tonnes. ") == "4210 tonnes"
    assert normalise(["b", "A"]) == ["a", "b"]

def test_headline_is_macro_over_q1_to_q5_and_q6_is_a_control():
    rows = [{"type": t, "gold": "x", "answer": "x" if ok else "y"}
            for t, ok in [("Q1", 1), ("Q2", 0), ("Q3", 1), ("Q4", 1), ("Q5", 1), ("Q6", 0)]]
    r = score_answers(rows)
    assert r["headline"] == 0.8 and r["control"] == 0.0

def test_q1_partial_credit_is_reported_but_not_counted_as_correct():
    r = score_answers([{"type": "Q1", "gold": ["a", "b"], "answer": ["a"]}])
    assert r["by_type"]["Q1"]["accuracy"] == 0.0 and r["q1_prf"]["recall"] == 0.5

def test_wilson_bounds():
    lo, hi = wilson(50, 100)
    assert 0.40 < lo < 0.41 and 0.59 < hi < 0.60
