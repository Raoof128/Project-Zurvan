"""Event-log generator (spec §5). Invariants: one believed statement per slot;
no event targets a superseded, retracted or erased statement; an abort mints no
tick and its sid and value are never reused; values are unique within a case."""

from datetime import date, timedelta

from scripts.rtbench.vocab import NOTES, RETRACT_REASONS, SUPERSEDE_REASONS, fresh_value

WEIGHTS = {"assert": 28, "supersede": 22, "retract": 18, "revise": 10, "erase": 12,
           "noise": 15, "abort": 8}
CONFIDENCES = ("low", "medium", "high")


def _iso(base, days):
    return (date.fromisoformat(base) + timedelta(days=days)).isoformat()


def generate_events(rng, slots, base_date) -> list:
    length = rng.randint(8, 60)
    has_erase = rng.random() < 0.20
    has_abort = rng.random() < 0.30
    has_valid = rng.random() < 0.30
    units = {s["slot"]: s["unit"] for s in slots}
    used, current, events = set(), {}, []
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
            kind = "erase" if full else "assert"
        elif has_abort and not state["aborted"] and left <= 1:
            kind = "abort"
        else:
            legal = ["noise"] + (["assert"] if empty else [])
            if full:
                legal += ["supersede", "retract", "revise"] + (["erase"] if has_erase else [])
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
            current[slot] = e["sid"]
        elif kind in ("retract", "erase"):
            slot = rng.choice(full)
            e = {"type": kind, "sid": current.pop(slot)}
            if kind == "retract":
                e["reason"] = rng.choice(RETRACT_REASONS)
            else:
                state["erased"] = True
        elif kind == "revise":
            e = {"type": "revise", "sid": current[rng.choice(full)], "confidence": rng.choice(CONFIDENCES)}
        else:
            e = {"type": "noise", "note": rng.choice(NOTES)}
        events.append({"t": t, **e})
    return events
