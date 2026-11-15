"""Runner and the validity check (spec §6.5, §8)."""

from scripts.rtbench.render import render_case
from scripts.rtbench.score import score_answers

BASELINE_BAR = 0.60


def run_cases(adapter, track, cases, golds, itemised) -> dict:
    gold_by_case = {g["case_id"]: g["answers"] for g in golds}
    rows, misses, errors, n_q = [], [], 0, 0
    for case in cases:
        types = {q["qid"]: q["type"] for q in case["questions"]}
        if track == "N":
            doc = render_case(case)
            items, questions = doc["turns"], doc["questions"]
        else:
            items, questions = case["events"], case["questions"]
        adapter.reset()
        for item in items:
            try:
                adapter.ingest(item)
            except Exception:  # noqa: BLE001 - a refused change is a counted error, not a crash
                errors += 1
        answers = gold_by_case[case["case_id"]]
        for q in questions:
            n_q += 1
            try:
                got = adapter.answer(q)
            except Exception:  # noqa: BLE001 - a crashing answer is a wrong answer
                errors += 1
                got = None
            gold = answers[q["qid"]][track]
            rows.append({"type": types[q["qid"]], "gold": gold, "answer": got})
            if itemised and _wrong(rows[-1]):
                misses.append({"case_id": case["case_id"], "qid": q["qid"], "type": types[q["qid"]]})
    return {"adapter": adapter.name, "track": track, "n_cases": len(cases), "n_questions": n_q,
            "errors": errors, "score": score_answers(rows), "misses": misses if itemised else []}


def _wrong(row):
    from scripts.rtbench.score import normalise
    return normalise(row["answer"]) != normalise(row["gold"])


def validity(reports) -> dict:
    checks = []
    for (name, track), rep in sorted(reports.items()):
        s = rep["score"]
        if name == "oracle":
            checks.append({"check": f"oracle {track} headline == 1", "value": s["headline"], "ok": s["headline"] == 1.0})
            checks.append({"check": f"oracle {track} control == 1", "value": s["control"], "ok": s["control"] == 1.0})
        elif name in ("overwrite", "append_only"):
            checks.append({"check": f"{name} {track} headline <= {BASELINE_BAR}", "value": s["headline"],
                           "ok": s["headline"] is not None and s["headline"] <= BASELINE_BAR})
    return {"pass": bool(checks) and all(c["ok"] for c in checks), "checks": checks}
