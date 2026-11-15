"""RTBench Task 2: reference oracle (R2, R3)."""
import ast, pathlib
from scripts.rtbench.oracle import fold, answer

def test_oracle_imports_nothing_from_zurvan():                       # R2
    tree = ast.parse(pathlib.Path("scripts/rtbench/oracle.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m and (m.startswith("scripts.claim") or m.startswith("scripts.knowledge")
                                         or m == "scripts" or m.startswith("scripts.") and not m.startswith("scripts.rtbench"))}

def A(t, sid, slot, value, vf=None, vu=None):
    return {"t": t, "type": "assert", "sid": sid, "slot": slot, "value": value, "valid_from": vf, "valid_until": vu}
def S(t, old, sid, slot, value, vf=None, vu=None):
    return {"t": t, "type": "supersede", "old": old, "sid": sid, "slot": slot, "value": value,
            "valid_from": vf, "valid_until": vu, "reason": "r"}

H1 = [A(1, "s1", "k1", "100 tonnes"), {"t": 2, "type": "noise", "note": "n"},
      S(3, "s1", "s2", "k1", "200 tonnes"), {"t": 4, "type": "retract", "sid": "s2", "reason": "r"}]
H2 = [A(1, "s1", "k1", "300 staff"), A(2, "s2", "k2", "40 square metres"), {"t": 3, "type": "erase", "sid": "s1"}]
H3 = [A(1, "s1", "k1", "500 tonnes"),
      {"t": None, "type": "abort", "inner": {"type": "supersede", "old": "s1", "sid": "s2", "slot": "k1",
       "value": "600 tonnes", "valid_from": None, "valid_until": None, "reason": "r"}},
      {"t": 2, "type": "noise", "note": "n"}]
H4 = [A(1, "s1", "k1", "700 tonnes", "2026-01-01", "2026-06-01"), S(2, "s1", "s2", "k1", "800 tonnes", "2026-06-01")]
H5 = [A(1, "s1", "k1", "900 tonnes"), {"t": 2, "type": "revise", "sid": "s1", "confidence": "low"}]

def q(tl, **kw): return answer(tl, kw)

def test_hand_case_supersede_chain():                                 # R3: Q1 Q2 Q3 Q5 Q6
    tl = fold(H1)
    assert [q(tl, type="Q1", t=t) for t in (1, 2, 3, 4)] == [["s1"], ["s1"], ["s2"], []]
    assert q(tl, type="Q1", t=3, answer_as="value") == ["200 tonnes"]
    assert [q(tl, type="Q2", t=t, sid="s1") for t in (1, 3)] == ["believed", "superseded"]
    assert [q(tl, type="Q2", t=t, sid="s2") for t in (2, 4)] == ["not_yet", "retracted"]
    assert [q(tl, type="Q3", t=t, slot="k1") for t in (2, 3, 4)] == ["100 tonnes", "200 tonnes", "none"]
    assert q(tl, type="Q5", slot="k1", value="100 tonnes") == "yes"
    assert q(tl, type="Q5", slot="k1", value="999 tonnes") == "no"
    assert q(tl, type="Q6", slot="k1") == "none"

def test_hand_case_erase_after_belief():                              # R3: erasure keeps history, drops content
    tl = fold(H2)
    assert q(tl, type="Q1", t=2) == ["s1", "s2"]
    assert q(tl, type="Q1", t=2, answer_as="value") == ["40 square metres", "[erased]"]
    assert q(tl, type="Q1", t=3) == ["s2"]
    assert [q(tl, type="Q2", t=t, sid="s1") for t in (2, 3)] == ["believed", "erased"]
    assert [q(tl, type="Q3", t=t, slot="k1") for t in (1, 3)] == ["[erased]", "none"]
    assert q(tl, type="Q6", slot="k1") == "none"

def test_hand_case_abort_is_never_believed():                         # R3: Q5 negative from an abort
    tl = fold(H3)
    assert tl.final_tick == 2
    assert q(tl, type="Q5", slot="k1", value="600 tonnes") == "no"
    assert q(tl, type="Q3", t=2, slot="k1") == "500 tonnes"
    assert q(tl, type="Q2", t=2, sid="s1") == "believed"

def test_hand_case_bitemporal():                                      # R3: Q4
    tl = fold(H4)
    assert q(tl, type="Q4", t=1, slot="k1", v="2026-03-01") == "700 tonnes"
    assert q(tl, type="Q4", t=1, slot="k1", v="2026-07-01") == "none"
    assert q(tl, type="Q4", t=2, slot="k1", v="2026-03-01") == "none"
    assert q(tl, type="Q4", t=2, slot="k1", v="2026-07-01") == "800 tonnes"

def test_hand_case_revise_and_open_interval():                        # R3: distractors
    tl = fold(H5)
    assert q(tl, type="Q3", t=2, slot="k1") == "900 tonnes"
    assert q(tl, type="Q4", t=2, slot="k1", v="1999-01-01") == "900 tonnes"
    assert q(tl, type="Q2", t=2, sid="s1") == "believed"

def test_boundary_ticks():
    tl = fold(H1)
    assert q(tl, type="Q3", t=1, slot="k1") == "100 tonnes"
    assert q(tl, type="Q3", t=tl.final_tick, slot="k1") == q(tl, type="Q6", slot="k1")
