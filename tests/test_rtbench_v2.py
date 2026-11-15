"""RTBench generator v2 (`rtbench-2`, Raouf 2026-11-15): erasing already-dead history.

rtbench-1 only ever erased a statement that was still believed. The common GDPR case is
the opposite: erase a value that was superseded or retracted long ago. In rtbench-2 an
erase may target a dead statement; its status history survives (superseded, then erased
from the erase tick on) and its content is `[erased]` at every tick. rtbench-1 is frozen:
its registered dev split must regenerate byte for byte.
"""
import json
import os
import random
import stat
import subprocess
import sys
from pathlib import Path

from scripts.rtbench.adapters import AppendOnlyAdapter, OracleAdapter, OverwriteAdapter, TrackNReader
from scripts.rtbench.events import generate_events
from scripts.rtbench.generate import DEV2_SEEDS, DEV_SEEDS, generate_case, write_split
from scripts.rtbench.oracle import answer, fold
from scripts.rtbench.render import ParseState, parse_question, parse_turn, render_case
from scripts.rtbench.run import run_cases
from scripts.rtbench.vocab import make_slots

REG = json.loads(Path("eval/datasets.json").read_text())["datasets"]

# H7: s1 believed at t1, superseded by s2 at t2, then s1 (dead) erased at t3.
H7 = [{"t": 1, "type": "assert", "sid": "s1", "slot": "k1", "value": "10 kg", "valid_from": None, "valid_until": None},
      {"t": 2, "type": "supersede", "sid": "s2", "old": "s1", "slot": "k1", "value": "12 kg", "reason": "audit",
       "valid_from": None, "valid_until": None},
      {"t": 3, "type": "erase", "sid": "s1"},
      {"t": 4, "type": "noise", "note": "n"}]


def test_v1_is_frozen():
    m = write_split("dev", DEV_SEEDS, Path(os.environ.get("TMPDIR", "/tmp")) / f"rtv1-{os.getpid()}")
    assert (m["sha256"], m["gold_sha256"]) == (REG["rtbench_dev"]["sha256"], REG["rtbench_dev"]["gold_sha256"])
    assert m["generator_version"] == "rtbench-1"


def test_hand_case_erase_of_dead_history():
    tl = fold(H7)
    assert answer(tl, {"type": "Q3", "t": 1, "slot": "k1"}) == "[erased]"      # retroactive for content
    assert answer(tl, {"type": "Q3", "t": 3, "slot": "k1"}) == "12 kg"         # the successor is untouched
    assert answer(tl, {"type": "Q2", "t": 2, "sid": "s1"}) == "superseded"     # history survives
    assert answer(tl, {"type": "Q2", "t": 3, "sid": "s1"}) == "erased"
    assert answer(tl, {"type": "Q1", "t": 1, "answer_as": "value"}) == ["[erased]"]
    assert answer(tl, {"type": "Q1", "t": 1}) == ["s1"]
    assert answer(tl, {"type": "Q5", "slot": "k1", "value": "10 kg"}) == "no"
    assert answer(tl, {"type": "Q6", "slot": "k1"}) == "12 kg"


def _gen2(seed):
    rng = random.Random(seed)
    slots = make_slots(rng, rng.randint(2, 8))
    return generate_events(rng, slots, "2026-01-05", dead_erase=True)


def test_v2_invariants_and_dead_erases_occur():
    dead_erases = 0
    for seed in range(400):
        believed, dead, erased, by_slot = set(), set(), set(), {}
        for e in _gen2(seed):
            if e["type"] == "abort":
                continue
            if e["type"] == "assert":
                assert by_slot.get(e["slot"]) is None
                believed.add(e["sid"]); by_slot[e["slot"]] = e["sid"]
            elif e["type"] == "supersede":
                assert e["old"] in believed and by_slot[e["slot"]] == e["old"]
                believed.discard(e["old"]); dead.add(e["old"])
                believed.add(e["sid"]); by_slot[e["slot"]] = e["sid"]
            elif e["type"] == "retract":
                assert e["sid"] in believed
                believed.discard(e["sid"]); dead.add(e["sid"])
                by_slot = {k: v for k, v in by_slot.items() if v != e["sid"]}
            elif e["type"] == "erase":
                assert e["sid"] not in erased, "erased at most once"
                assert e["sid"] in believed or e["sid"] in dead
                dead_erases += e["sid"] in dead
                erased.add(e["sid"])
                believed.discard(e["sid"]); dead.discard(e["sid"])
                by_slot = {k: v for k, v in by_slot.items() if v != e["sid"]}
            elif e["type"] == "revise":
                assert e["sid"] in believed
    assert dead_erases >= 40


def test_v2_cases_are_versioned_and_round_trip_through_track_n():
    for s in DEV2_SEEDS[:60]:
        case, gold = generate_case(s, "x", version=2)
        assert case["generator_version"] == "rtbench-2"
        doc, st = render_case(case), ParseState()
        tl = fold([parse_turn(t, st) for t in doc["turns"]])
        for q in doc["questions"]:
            assert answer(tl, parse_question(q["text"], q["qid"], st)) == gold["answers"][q["qid"]]["N"], q


def test_v2_questions_never_quote_an_erased_value():
    for s in DEV2_SEEDS:
        case, _ = generate_case(s, "x", version=2)
        erased = {e["sid"] for e in case["events"] if e["type"] == "erase"}
        vals = {e["value"] for e in case["events"] if e.get("sid") in erased and e.get("value")}
        for q in render_case(case)["questions"]:
            assert not any(v in q["text"] for v in vals)
        assert not any(q.get("value") in vals for q in case["questions"] if q["type"] == "Q5")


def test_v2_dev_is_registered_and_regenerates(tmp_path):
    m = write_split("dev2", DEV2_SEEDS, tmp_path)
    e = REG["rtbench_dev2"]
    assert (e["state"], e["sha256"], e["gold_sha256"], e["n_items"], e["generator_version"]) == \
        ("DEV", m["sha256"], m["gold_sha256"], 200, "rtbench-2")
    h = REG["rtbench_holdout2"]
    assert h["state"] == "HOLDOUT" and set(h) <= {"state", "sha256", "gold_sha256", "n_items", "generator_version"}


def test_v2_oracle_full_marks_and_append_only_loses_on_erased_history():
    pairs = [generate_case(s, f"d-{i}", version=2) for i, s in enumerate(DEV2_SEEDS[:80])]
    cases, golds = [c for c, _ in pairs], [g for _, g in pairs]
    for track in ("S", "N"):
        o = OracleAdapter()
        assert run_cases(TrackNReader(o) if track == "N" else o, track, cases, golds, itemised=False)["score"]["headline"] == 1.0
    for ad in (OverwriteAdapter, AppendOnlyAdapter):
        assert run_cases(ad(), "S", cases, golds, itemised=False)["score"]["headline"] <= 0.60


def test_zurvan_matches_the_oracle_on_dead_history_erasure(tmp_path):
    from scripts.rtbench.zurvan_adapter import ZurvanAdapter
    qs = [{"type": "Q3", "t": 1, "slot": "k1"}, {"type": "Q2", "t": 2, "sid": "s1"},
          {"type": "Q2", "t": 3, "sid": "s1"}, {"type": "Q1", "t": 1, "answer_as": "value"},
          {"type": "Q5", "slot": "k1", "value": "10 kg"}, {"type": "Q6", "slot": "k1"}]
    z = ZurvanAdapter(tmp_path / "w")
    try:
        z.reset()
        for e in H7:
            z.ingest(e)
        got = [z.answer(q) for q in qs]
    finally:
        z.close()
    o = OracleAdapter(); o.reset()
    for e in H7:
        o.ingest(e)
    assert got == [o.answer(q) for q in qs]


def _cli(*args, home):
    env = {"PYTHONPATH": ".", "PATH": "/usr/bin:/bin", "HOME": str(home)}
    return subprocess.run([sys.executable, "scripts/cli.py", "bench", "rtbench", *args],
                          capture_output=True, text=True, env=env)


def test_cli_generates_v2_splits_and_seals_holdout2(tmp_path):
    r = _cli("generate", "--split", "dev2", home=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["generator_version"] == "rtbench-2"
    r = _cli("generate", "--split", "holdout2", home=tmp_path)
    assert r.returncode == 0, r.stderr
    inst = tmp_path / ".zurvan" / "instruments"
    mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
    assert mode(inst / "rtbench_holdout2") == 0o700
    assert {mode(p) for p in (inst / "rtbench_holdout2").iterdir()} == {0o600}
    assert mode(inst / "rtbench_holdout2.seeds") == 0o600
    v1 = (inst / "rtbench_holdout.seeds")
    assert not v1.exists() or v1.read_text() != (inst / "rtbench_holdout2.seeds").read_text()
    r = _cli("run", "--adapter", "oracle", "--track", "S", "--split", "holdout2", "--limit", "3", home=tmp_path)
    assert r.returncode == 2 and "--limit" in r.stderr
