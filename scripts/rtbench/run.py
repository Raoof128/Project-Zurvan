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


REQUIRED = tuple((name, track) for name in ("oracle", "overwrite", "append_only") for track in ("S", "N"))


def validity(reports) -> dict:
    """Spec §6.5: the oracle scores 1 on both tracks, both baselines stay at or
    under the bar on both tracks, and no run had an error. Every one of the six
    reports must be present; a missing one fails, never passes by omission."""
    checks = []
    for name, track in REQUIRED:
        rep = reports.get((name, track))
        if rep is None:
            checks.append({"check": f"{name} {track} report present", "value": None, "ok": False})
            continue
        s = rep["score"]
        checks.append({"check": f"{name} {track} errors == 0", "value": rep["errors"], "ok": rep["errors"] == 0})
        if name == "oracle":
            checks.append({"check": f"oracle {track} headline and control == 1",
                           "value": [s["headline"], s["control"]],
                           "ok": s["headline"] == 1.0 and s["control"] == 1.0})
        else:
            checks.append({"check": f"{name} {track} headline <= {BASELINE_BAR}", "value": s["headline"],
                           "ok": s["headline"] is not None and s["headline"] <= BASELINE_BAR})
    return {"pass": all(c["ok"] for c in checks), "checks": checks}
