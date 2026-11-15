"""Cases, questions and gold (spec §6.1, §7).

Questions stress transitions: a Q1-Q4 tick is drawn, with probability 0.6,
from the slot's change points - the tick at which a statement ended (once for
a supersede, twice for a retract or erase) and every tick while a
later-erased statement was believed. Final-tick questions stay at or under 20%
of Q1-Q4. Gold comes only from the oracle.
"""

import os
import random
import secrets
from datetime import date, timedelta
from pathlib import Path

from scripts.rtbench import GENERATOR_VERSION
from scripts.rtbench.canon import dumps, sha256_file
from scripts.rtbench.events import generate_events
from scripts.rtbench.oracle import answer, fold
from scripts.rtbench.vocab import fresh_value, make_slots

DEV_SEEDS = list(range(1000, 1200))
BASE_DATE = "2026-01-05"
TYPE_WEIGHTS = (("Q1", 20), ("Q2", 20), ("Q3", 20), ("Q4", 20), ("Q5", 12), ("Q6", 8))
P_CHANGE = 0.6
FINAL_CAP = 0.20
HOLDOUT_FILE = "rtbench_holdout.seeds"
N_HOLDOUT = 200


def _change_points(tl) -> dict:
    points = {}
    for s in tl.statements.values():
        if s.ended is not None:
            points.setdefault(s.slot, []).extend([s.ended] * (2 if s.end_kind in ("retracted", "erased") else 1))
        if s.erased:
            points.setdefault(s.slot, []).extend(range(s.born, s.ended))
    return points


def _questions(rng, case_id, slots, events, tl) -> list:
    T = tl.final_tick
    units = {s["slot"]: s["unit"] for s in slots}
    asked_slots = sorted({s.slot for s in tl.statements.values()})
    if T < 2 or not asked_slots:
        return []
    points = _change_points(tl)
    all_points = sorted(p for v in points.values() for p in v)
    used = {s.value for s in tl.statements.values()}
    aborted = [e["inner"] for e in events if e["type"] == "abort"]
    used |= {a["value"] for a in aborted}
    want_yes = rng.random() < 0.5
    timed = finals = 0
    out = []
    for i in range(rng.randint(10, 30)):
        kind = rng.choices([k for k, _ in TYPE_WEIGHTS], [w for _, w in TYPE_WEIGHTS])[0]
        slot = rng.choice(asked_slots)
        q = {"qid": f"{case_id}-q{i:02d}", "type": kind}
        if kind in ("Q1", "Q2", "Q3", "Q4"):
            pool = points.get(slot, []) if kind in ("Q3", "Q4") else all_points
            t = rng.choice(pool) if pool and rng.random() < P_CHANGE else rng.randint(1, T - 1)
            timed += 1
            if t == T:
                if finals + 1 > FINAL_CAP * timed:
                    t = T - 1
                else:
                    finals += 1
            q["t"] = t
            if kind == "Q1":
                q["answer_as"] = "sid"
            elif kind == "Q2":
                mine = sorted(sid for sid, s in tl.statements.items() if s.slot == slot)
                q["sid"] = rng.choice(mine)
            else:
                q["slot"] = slot
            if kind == "Q4":
                s = next((s for s in tl.statements.values() if s.slot == slot and s.believed_at(t)), None)
                if s is not None and rng.random() < 0.5:
                    q["v"] = s.valid_from or (s.valid_until and _shift(s.valid_until, -1)) or _shift(BASE_DATE, rng.randint(0, 500))
                else:
                    q["v"] = _shift(BASE_DATE, rng.randint(0, 500))
        elif kind == "Q5":
            if want_yes:
                live = [s for s in tl.statements.values() if not s.erased]
                ended = [s for s in live if s.ended is not None]
                pick = rng.choice(ended or live) if live else None
                if pick is None:
                    continue
                q.update(slot=pick.slot, value=pick.value)
            elif aborted and not any(o["type"] == "Q5" and o["value"] in {a["value"] for a in aborted} for o in out):
                a = rng.choice(aborted)
                q.update(slot=a["slot"], value=a["value"])
            else:
                q.update(slot=slot, value=fresh_value(rng, units[slot], used))
            want_yes = not want_yes
        else:
            q["slot"] = slot
        out.append(q)
    return out


def _shift(iso, days):
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def generate_case(seed, case_id) -> tuple:
    rng = random.Random(seed)
    slots = make_slots(rng, rng.randint(2, 8))
    events = generate_events(rng, slots, BASE_DATE)
    tl = fold(events)
    questions = _questions(rng, case_id, slots, events, tl)
    case = {"case_id": case_id, "generator_version": GENERATOR_VERSION, "seed": seed,
            "base_date": BASE_DATE, "slots": slots, "events": events, "questions": questions}
    gold = {"case_id": case_id, "answers": {
        q["qid"]: {"S": answer(tl, q), "N": answer(tl, {**q, "answer_as": "value"})} for q in questions}}
    return case, gold


def write_split(split, seeds, out_dir) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cases_p, gold_p = out / f"{split}.cases.jsonl", out / f"{split}.gold.jsonl"
    with open(cases_p, "w", encoding="utf-8", newline="\n") as fc, \
         open(gold_p, "w", encoding="utf-8", newline="\n") as fg:
        for i, seed in enumerate(seeds):
            case, gold = generate_case(seed, f"{split}-{i:04d}")
            fc.write(dumps(case) + "\n")
            fg.write(dumps(gold) + "\n")
    return {"split": split, "generator_version": GENERATOR_VERSION, "n_items": len(seeds),
            "sha256": sha256_file(cases_p), "gold_sha256": sha256_file(gold_p)}


def holdout_seeds(instruments_dir) -> list:
    d = Path(instruments_dir)
    p = d / HOLDOUT_FILE
    if not p.exists():
        d.mkdir(parents=True, exist_ok=True)
        seeds, dev = [], set(DEV_SEEDS)
        while len(seeds) < N_HOLDOUT:
            s = secrets.randbits(63)
            if s not in dev and s not in seeds:
                seeds.append(s)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("".join(f"{s}\n" for s in seeds))
    return [int(line) for line in p.read_text(encoding="utf-8").split()]
