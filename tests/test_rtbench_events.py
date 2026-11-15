"""RTBench Task 1: event generator (spec §5, §7)."""
import random
from scripts.rtbench import GENERATOR_VERSION
from scripts.rtbench.canon import dumps
from scripts.rtbench.vocab import make_slots
from scripts.rtbench.events import generate_events

def _gen(seed):
    rng = random.Random(seed)
    slots = make_slots(rng, rng.randint(2, 8))
    return slots, generate_events(rng, slots, "2026-01-05")

def test_version_is_pinned():
    assert GENERATOR_VERSION == "rtbench-1"

def test_same_seed_same_bytes():
    assert dumps(_gen(7)) == dumps(_gen(7))
    assert dumps(_gen(7)) != dumps(_gen(8))

def test_invariants_hold_over_many_seeds():
    for seed in range(300):
        slots, events = _gen(seed)
        assert 8 <= len(events) <= 60
        ticks = [e["t"] for e in events if e["type"] != "abort"]
        assert ticks == list(range(1, len(ticks) + 1))
        believed, dead, by_slot = set(), set(), {}
        for e in events:
            if e["type"] == "abort":
                assert e["t"] is None and e["inner"]["type"] != "abort"
                continue
            if e["type"] == "assert":
                assert by_slot.get(e["slot"]) is None          # one believed statement per slot
                believed.add(e["sid"]); by_slot[e["slot"]] = e["sid"]
            elif e["type"] == "supersede":
                assert e["old"] in believed and by_slot[e["slot"]] == e["old"]
                believed.discard(e["old"]); dead.add(e["old"])
                believed.add(e["sid"]); by_slot[e["slot"]] = e["sid"]
            elif e["type"] in ("retract", "erase"):
                assert e["sid"] in believed                  # never targets a dead statement
                believed.discard(e["sid"]); dead.add(e["sid"])
                by_slot = {k: v for k, v in by_slot.items() if v != e["sid"]}
            elif e["type"] == "revise":
                assert e["sid"] in believed
        values = [e.get("value") or e.get("inner", {}).get("value") for e in events]
        values = [v for v in values if v]
        assert len(values) == len(set(values))              # values unique within a case

def test_mix_matches_the_spec_over_dev_sized_sample():
    n = 200
    gens = [_gen(1000 + i)[1] for i in range(n)]
    erase = sum(any(e["type"] == "erase" for e in ev) for ev in gens) / n
    abort = sum(any(e["type"] == "abort" for e in ev) for ev in gens) / n
    valid = sum(any(e.get("valid_from") or e.get("valid_until") for e in ev) for ev in gens) / n
    assert 0.12 <= erase <= 0.30 and 0.20 <= abort <= 0.40 and 0.20 <= valid <= 0.40
