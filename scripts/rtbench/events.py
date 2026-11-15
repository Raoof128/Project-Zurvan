"""Event-log generator (spec §5). Invariants: one believed statement per slot;
no event targets a superseded, retracted or erased statement (rtbench-2 exception:
an erase may target a superseded or retracted one, spec §12); an abort mints no
tick and its sid and value are never reused; values are unique within a case."""

from datetime import date, timedelta

from scripts.rtbench.vocab import NOTES, RETRACT_REASONS, SUPERSEDE_REASONS, fresh_value

WEIGHTS = {"assert": 28, "supersede": 22, "retract": 18, "revise": 10, "erase": 12,
           "noise": 15, "abort": 8}
CONFIDENCES = ("low", "medium", "high")
P_ERASE = {False: 0.20, True: 0.35}   # rtbench-1 / rtbench-2 share of cases with an erase
P_DEAD = 0.6                          # rtbench-2: chance an erase picks dead history when any exists


def _iso(base, days):
    return (date.fromisoformat(base) + timedelta(days=days)).isoformat()


def generate_events(rng, slots, base_date, dead_erase=False) -> list:
    """`dead_erase=False` is rtbench-1 and must stay byte-identical: every rtbench-2
    branch below is gated on it and draws no random number when it is off."""
    length = rng.randint(8, 60)
    has_erase = rng.random() < P_ERASE[dead_erase]
    has_abort = rng.random() < 0.30
    has_valid = rng.random() < 0.30
    units = {s["slot"]: s["unit"] for s in slots}
    used, current, events = set(), {}, []
    dead = []   # rtbench-2: superseded or retracted, not yet erased, in order of death
    state = {"t": 0, "n": 0, "erased": False, "aborted": False, "valid": False}

    def new_sid():
        state["n"] += 1
        return f"s{state['n']}"

    def interval(force=False):
        if not has_valid or (not force and rng.random() < 0.5):
            return None, None
        state["valid"] = True
        start = rng.randint(0, 300)
        until = _iso(base_date, start + rng.randint(10, 200)) if rng.random() < 0.5 else None
        return _iso(base_date, start), until

    def change(kind, slot):
        vf, vu = interval(force=has_valid and not state["valid"])
        e = {"type": kind, "sid": new_sid(), "slot": slot, "value": fresh_value(rng, units[slot], used),
             "valid_from": vf, "valid_until": vu}
        if kind == "supersede":
            e["old"] = current[slot]
            e["reason"] = rng.choice(SUPERSEDE_REASONS)
        return e

    while len(events) < length:
        empty = [s["slot"] for s in slots if s["slot"] not in current]
        full = [s["slot"] for s in slots if s["slot"] in current]
        left = length - len(events)
        if has_erase and not state["erased"] and left <= 2:
            kind = "erase" if full or (dead_erase and dead) else "assert"
        elif has_abort and not state["aborted"] and left <= 1:
            kind = "abort"
        else:
            legal = ["noise"] + (["assert"] if empty else [])
            if full:
                legal += ["supersede", "retract", "revise"] + (["erase"] if has_erase else [])
            elif dead_erase and dead and has_erase:
                legal.append("erase")
            if has_abort:
                legal.append("abort")
            kind = rng.choices(legal, [WEIGHTS[k] for k in legal])[0]
        if kind == "abort":
            inner = change("assert", rng.choice(empty)) if empty and (not full or rng.random() < 0.3) \
                else change("supersede", rng.choice(full))
            events.append({"t": None, "type": "abort", "inner": inner})
            state["aborted"] = True
            continue
        state["t"] += 1
        t = state["t"]
        if kind in ("assert", "supersede"):
            slot = rng.choice(empty if kind == "assert" else full)
            e = change(kind, slot)
            if kind == "supersede" and dead_erase:
                dead.append(current[slot])
            current[slot] = e["sid"]
        elif kind == "erase" and dead_erase and dead and (not full or rng.random() < P_DEAD):
            e = {"type": "erase", "sid": dead.pop(rng.randrange(len(dead)))}
            state["erased"] = True
        elif kind in ("retract", "erase"):
            slot = rng.choice(full)
            e = {"type": kind, "sid": current.pop(slot)}
            if kind == "retract":
                e["reason"] = rng.choice(RETRACT_REASONS)
                if dead_erase:
                    dead.append(e["sid"])
            else:
                state["erased"] = True
        elif kind == "revise":
            e = {"type": "revise", "sid": current[rng.choice(full)], "confidence": rng.choice(CONFIDENCES)}
        else:
            e = {"type": "noise", "note": rng.choice(NOTES)}
        events.append({"t": t, **e})
    return events
