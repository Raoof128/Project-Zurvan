"""RTBench Task 3: sampling, gold, splits, holdout seeds (R1, spec §6.1, §7)."""
import os, stat
from scripts.rtbench.generate import DEV_SEEDS, generate_case, write_split, holdout_seeds

def test_write_split_is_byte_identical(tmp_path):                     # R1
    a = write_split("dev", DEV_SEEDS[:20], tmp_path / "a")
    b = write_split("dev", DEV_SEEDS[:20], tmp_path / "b")
    assert a == b and a["n_items"] == 20
    assert (tmp_path / "a" / "dev.cases.jsonl").read_bytes() == (tmp_path / "b" / "dev.cases.jsonl").read_bytes()

def test_gold_is_kept_apart_from_cases(tmp_path):
    write_split("dev", DEV_SEEDS[:5], tmp_path)
    assert '"answers"' not in (tmp_path / "dev.cases.jsonl").read_text()

def test_question_mix_meets_the_spec():
    cases = [generate_case(s, f"dev-{i:04d}")[0] for i, s in enumerate(DEV_SEEDS)]
    qs = [q for c in cases for q in c["questions"]]
    assert all(10 <= len(c["questions"]) <= 30 for c in cases)
    assert {q["type"] for q in qs} == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6"}
    timed = [(c, q) for c in cases for q in c["questions"] if q["type"] in ("Q1", "Q2", "Q3", "Q4")]
    final = sum(q["t"] == max(e["t"] or 0 for e in c["events"]) for c, q in timed)
    assert final / len(timed) <= 0.20
    golds = [generate_case(s, "x")[1] for s in DEV_SEEDS]
    q5 = [a["S"] for g in golds for qid, a in g["answers"].items() if a["S"] in ("yes", "no")]
    assert q5.count("yes") / len(q5) >= 0.40 and q5.count("no") / len(q5) >= 0.40

def test_change_point_rule():
    from scripts.rtbench.oracle import fold
    near = total = 0
    for s in DEV_SEEDS:
        case, _ = generate_case(s, "x")
        points = {}
        for st in fold(case["events"]).statements.values():
            if st.ended is not None:
                points.setdefault(st.slot, set()).add(st.ended)
            if st.erased:
                points[st.slot].update(range(st.born, st.ended))
        for q in case["questions"]:
            if q["type"] in ("Q3", "Q4"):
                total += 1; near += q["t"] in points.get(q["slot"], set())
    assert near / total >= 0.5

def test_holdout_seeds_are_private_and_stable(tmp_path):
    a = holdout_seeds(tmp_path)
    assert len(a) == 200 and len(set(a)) == 200 and not set(a) & set(DEV_SEEDS)
    p = tmp_path / "rtbench_holdout.seeds"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert holdout_seeds(tmp_path) == a
