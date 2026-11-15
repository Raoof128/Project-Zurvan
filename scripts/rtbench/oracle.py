"""Reference oracle (spec §6.2): a pure fold over the event log.

It shares no code with Zurvan, so Zurvan is never graded by itself
(tests/test_rtbench_oracle.py::test_oracle_imports_nothing_from_zurvan).
Query time is the end of the log: an erase hides content at every tick.
"""

from dataclasses import dataclass, field


@dataclass
class Stmt:
    slot: str
    value: str
    valid_from: str | None
    valid_until: str | None
    born: int
    ended: int | None = None
    end_kind: str | None = None
    erased: bool = False

    def believed_at(self, t: int) -> bool:
        return self.born <= t and (self.ended is None or self.ended > t)


@dataclass
class Timeline:
    final_tick: int = 0
    statements: dict = field(default_factory=dict)


def fold(events) -> "Timeline":
    tl = Timeline()
    for e in events:
        if e["type"] == "abort":
            continue
        t = e["t"]
        tl.final_tick = t
        kind = e["type"]
        if kind in ("assert", "supersede"):
            tl.statements[e["sid"]] = Stmt(e["slot"], e["value"], e.get("valid_from"),
                                           e.get("valid_until"), born=t)
            if kind == "supersede":
                old = tl.statements[e["old"]]
                old.ended, old.end_kind = t, "superseded"
        elif kind in ("retract", "erase"):
            s = tl.statements[e["sid"]]
            s.ended = t
            s.end_kind = "retracted" if kind == "retract" else "erased"
            s.erased = kind == "erase"
    return tl


def _believed(tl, slot, t):
    for s in tl.statements.values():
        if s.slot == slot and s.believed_at(t):
            return s
    return None


def _content(s):
    return "[erased]" if s.erased else s.value


def answer(tl, question) -> object:
    kind = question["type"]
    if kind == "Q1":
        alive = [(sid, s) for sid, s in tl.statements.items() if s.believed_at(question["t"])]
        if question.get("answer_as", "sid") == "value":
            return sorted(_content(s) for _, s in alive)
        return sorted(sid for sid, _ in alive)
    if kind == "Q2":
        s = tl.statements.get(question["sid"])
        t = question["t"]
        if s is None or s.born > t:
            return "not_yet"
        if s.ended is not None and s.ended <= t:
            return s.end_kind
        return "believed"
    if kind in ("Q3", "Q4", "Q6"):
        t = tl.final_tick if kind == "Q6" else question["t"]
        s = _believed(tl, question["slot"], t)
        if s is None:
            return "none"
        if kind == "Q4":
            v = question["v"]
            if (s.valid_from is not None and v < s.valid_from) or \
               (s.valid_until is not None and v >= s.valid_until):
                return "none"
        return _content(s)
    if kind == "Q5":
        hit = any(s.slot == question["slot"] and s.value == question["value"] and not s.erased
                  for s in tl.statements.values())
        return "yes" if hit else "no"
    raise ValueError(f"unknown question type {kind!r}")
