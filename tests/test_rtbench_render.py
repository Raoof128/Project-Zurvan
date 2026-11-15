"""RTBench Task 4: Track N rendering and the shared parser (R4)."""
from scripts.rtbench.generate import DEV_SEEDS, generate_case
from scripts.rtbench.render import render_case, parse_turn, parse_question, ParseState
from scripts.rtbench.oracle import fold, answer

def test_round_trip_reproduces_every_answer():                        # R4
    for s in DEV_SEEDS[:60]:
        case, gold = generate_case(s, "x")
        doc, st = render_case(case), ParseState()
        events = [parse_turn(t, st) for t in doc["turns"]]
        tl = fold(events)
        for q in doc["questions"]:
            assert answer(tl, parse_question(q["text"], q["qid"], st)) == gold["answers"][q["qid"]]["N"], q

def test_one_event_per_session_and_aborts_are_asides():
    case, _ = generate_case(DEV_SEEDS[3], "x")
    doc = render_case(case)
    sessions = [t["session"] for t in doc["turns"] if t["session"] is not None]
    assert sessions == list(range(1, len(sessions) + 1))
    assert all(t["text"].startswith("[Aside") for t in doc["turns"] if t["session"] is None)

def test_questions_never_quote_an_erased_value():
    for s in DEV_SEEDS:
        case, _ = generate_case(s, "x")
        erased = {e["sid"] for e in case["events"] if e["type"] == "erase"}
        vals = {e["value"] for e in case["events"] if e.get("sid") in erased and e.get("value")}
        for q in render_case(case)["questions"]:
            assert not any(v in q["text"] for v in vals)

def test_values_with_separators_round_trip():
    st = ParseState()
    ev = parse_turn({"session": 1, "date": "2026-01-06",
                     "text": "[Session 1, 2026-01-06] Note: Orrin Mill's annual output is 4,210 tonnes."}, st)
    assert ev["value"] == "4210 tonnes"
