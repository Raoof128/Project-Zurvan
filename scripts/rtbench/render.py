"""Track N: the same cases as dated conversation sessions (spec §6.4).

One event per session, so every tick is a session boundary and Track N asks
exactly the Track S questions in English. A statement is named by the session
that recorded it, never by its value, so no question leaks erased content.
`parse_turn` / `parse_question` are the one shared template parser: the
shipped adapters read Track N through it, and R4 proves it round-trips.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

_DATE = r"(\d{4}-\d{2}-\d{2})"
_PREFIX = r"^\[Session (\d+), " + _DATE + r"\] "
_VALID = r"( \(in effect [^)]*\))?"
_SA = r"(.+?)'s (.+?)"
_NUM = r"(\d[\d,]*) (.+?)"


def _day(base, t):
    return (date.fromisoformat(base) + timedelta(days=t)).isoformat()


def _fmt_value(value):
    n, unit = value.split(" ", 1)
    return f"{int(n):,} {unit}"


def _parse_value(num, unit):
    return f"{int(num.replace(',', ''))} {unit}"


def _valid_text(vf, vu):
    if vf and vu:
        return f" (in effect from {vf} until before {vu})"
    if vf:
        return f" (in effect from {vf})"
    if vu:
        return f" (in effect until before {vu})"
    return ""


def _parse_valid(text):
    if not text:
        return None, None
    m = re.fullmatch(r" \(in effect(?: from " + _DATE + r")?(?: until before " + _DATE + r")?\)", text)
    if not m:
        raise ValueError(f"unparseable validity: {text!r}")
    return m.group(1), m.group(2)


def _sentence(e, slots, born):
    s = slots[e["slot"]] if "slot" in e else None
    if e["type"] == "assert":
        return (f"Note: {s['entity']}'s {s['attribute']} is {_fmt_value(e['value'])}"
                f"{_valid_text(e.get('valid_from'), e.get('valid_until'))}.")
    if e["type"] == "supersede":
        return (f"Correction: {s['entity']}'s {s['attribute']} is now {_fmt_value(e['value'])}"
                f"{_valid_text(e.get('valid_from'), e.get('valid_until'))}, replacing the record made in "
                f"session {born[e['old']]}. Reason: {e['reason']}.")
    raise ValueError(e["type"])


def render_case(case) -> dict:
    slots = {s["slot"]: s for s in case["slots"]}
    base = case["base_date"]
    born, slot_of, turns, last = {}, {}, [], 0
    for e in case["events"]:
        if e["type"] == "abort":
            inner = e["inner"]
            turns.append({"session": None, "date": _day(base, last),
                          "text": f"[Aside, {_day(base, last)}] {_sentence(inner, slots, born)} "
                                  f"Scratch that, do not record it."})
            continue
        t = last = e["t"]
        head = f"[Session {t}, {_day(base, t)}] "
        kind = e["type"]
        if kind in ("assert", "supersede"):
            text = _sentence(e, slots, born)
            born[e["sid"]], slot_of[e["sid"]] = t, e["slot"]
        elif kind == "retract":
            s = slots[slot_of[e["sid"]]]
            text = (f"Retraction: withdraw the record made in session {born[e['sid']]} about "
                    f"{s['entity']}'s {s['attribute']}. Reason: {e['reason']}.")
        elif kind == "revise":
            text = f"Confidence in the record made in session {born[e['sid']]} is now {e['confidence']}."
        elif kind == "erase":
            text = f"Erase: forget everything in the record made in session {born[e['sid']]}, permanently."
        else:
            text = e["note"]
        turns.append({"session": t, "date": _day(base, t), "text": head + text})
    return {"case_id": case["case_id"], "turns": turns,
            "questions": [{"qid": q["qid"], "text": _question_text(q, slots, born)} for q in case["questions"]]}


def _question_text(q, slots, born):
    kind = q["type"]
    s = slots.get(q.get("slot"))
    sa = f"{s['entity']}'s {s['attribute']}" if s else ""
    if kind == "Q1":
        return (f"As of the end of session {q['t']}, list every value you held as current, comma-separated, "
                f"writing [erased] for erased content, or none.")
    if kind == "Q2":
        return (f"As of the end of session {q['t']}, what was the status of the record made in session "
                f"{born[q['sid']]}? Answer believed, superseded, retracted, erased or not_yet.")
    if kind == "Q3":
        return (f"As of the end of session {q['t']}, what had you recorded as {sa}? "
                f"Answer with the value only, [erased], or none.")
    if kind == "Q4":
        return (f"As of the end of session {q['t']}, what had you recorded as {sa} in effect on {q['v']}? "
                f"Answer with the value only, [erased], or none.")
    if kind == "Q5":
        return f"Was {_fmt_value(q['value'])} ever recorded as {sa}? Answer yes or no."
    if kind == "Q6":
        return f"What is {sa} now? Answer with the value only, or none."
    raise ValueError(kind)


@dataclass
class ParseState:
    slots: dict = field(default_factory=dict)       # (entity, attribute) -> slot id
    sid_by_session: dict = field(default_factory=dict)
    aborts: int = 0

    def slot(self, entity, attribute):
        key = (entity, attribute)
        if key not in self.slots:
            self.slots[key] = f"k{len(self.slots) + 1}"
        return self.slots[key]

    def known_slot(self, entity, attribute):
        """A question may only name a slot some turn already named: a lenient
        lookup here once hid a Q4 question parsed as Q3."""
        if (entity, attribute) not in self.slots:
            raise ValueError(f"question names an unknown slot: {entity!r}, {attribute!r}")
        return self.slots[(entity, attribute)]


_ASSERT = re.compile(r"^Note: " + _SA + r" is " + _NUM + _VALID + r"\.$")
_SUPERSEDE = re.compile(r"^Correction: " + _SA + r" is now " + _NUM + _VALID +
                        r", replacing the record made in session (\d+)\. Reason: (.+)\.$")
_RETRACT = re.compile(r"^Retraction: withdraw the record made in session (\d+) about " + _SA +
                      r"\. Reason: (.+)\.$")
_REVISE = re.compile(r"^Confidence in the record made in session (\d+) is now (\w+)\.$")
_ERASE = re.compile(r"^Erase: forget everything in the record made in session (\d+), permanently\.$")
_ABORT = re.compile(r"^\[Aside, " + _DATE + r"\] (.+) Scratch that, do not record it\.$")


def _parse_change(text, state, sid):
    m = _ASSERT.match(text)
    if m:
        vf, vu = _parse_valid(m.group(5))
        return {"type": "assert", "sid": sid, "slot": state.slot(m.group(1), m.group(2)),
                "value": _parse_value(m.group(3), m.group(4)), "valid_from": vf, "valid_until": vu}
    m = _SUPERSEDE.match(text)
    if m:
        vf, vu = _parse_valid(m.group(5))
        return {"type": "supersede", "sid": sid, "old": state.sid_by_session[int(m.group(6))],
                "slot": state.slot(m.group(1), m.group(2)), "value": _parse_value(m.group(3), m.group(4)),
                "valid_from": vf, "valid_until": vu, "reason": m.group(7)}
    return None


def parse_turn(turn, state) -> dict:
    text = turn["text"]
    m = _ABORT.match(text)
    if m:
        state.aborts += 1
        inner = _parse_change(m.group(2), state, f"a{state.aborts}")
        if inner is None:
            raise ValueError(f"unparseable aside: {text!r}")
        return {"t": None, "type": "abort", "inner": inner}
    m = re.match(_PREFIX + r"(.*)$", text)
    if not m:
        raise ValueError(f"unparseable turn: {text!r}")
    t, body = int(m.group(1)), m.group(3)
    sid = f"n{t}"
    change = _parse_change(body, state, sid)
    if change:
        state.sid_by_session[t] = sid
        return {"t": t, **change}
    m = _RETRACT.match(body)
    if m:
        return {"t": t, "type": "retract", "sid": state.sid_by_session[int(m.group(1))], "reason": m.group(4)}
    m = _REVISE.match(body)
    if m:
        return {"t": t, "type": "revise", "sid": state.sid_by_session[int(m.group(1))], "confidence": m.group(2)}
    m = _ERASE.match(body)
    if m:
        return {"t": t, "type": "erase", "sid": state.sid_by_session[int(m.group(1))]}
    return {"t": t, "type": "noise", "note": body}


_QUESTIONS = (
    ("Q1", re.compile(r"^As of the end of session (\d+), list every value you held as current, comma-separated, "
                      r"writing \[erased\] for erased content, or none\.$")),
    ("Q2", re.compile(r"^As of the end of session (\d+), what was the status of the record made in session "
                      r"(\d+)\? Answer believed, superseded, retracted, erased or not_yet\.$")),
    # Q4 before Q3: Q3's pattern also matches a Q4 sentence.
    ("Q4", re.compile(r"^As of the end of session (\d+), what had you recorded as " + _SA + r" in effect on " +
                      _DATE + r"\? Answer with the value only, \[erased\], or none\.$")),
    ("Q3", re.compile(r"^As of the end of session (\d+), what had you recorded as " + _SA +
                      r"\? Answer with the value only, \[erased\], or none\.$")),
    ("Q5", re.compile(r"^Was " + _NUM + r" ever recorded as " + _SA + r"\? Answer yes or no\.$")),
    ("Q6", re.compile(r"^What is " + _SA + r" now\? Answer with the value only, or none\.$")),
)


def parse_question(qtext, qid, state) -> dict:
    for kind, rx in _QUESTIONS:
        m = rx.match(qtext)
        if not m:
            continue
        q = {"qid": qid, "type": kind}
        if kind == "Q1":
            q.update(t=int(m.group(1)), answer_as="value")
        elif kind == "Q2":
            q.update(t=int(m.group(1)), sid=state.sid_by_session.get(int(m.group(2)), f"n{m.group(2)}"))
        elif kind in ("Q3", "Q4"):
            q.update(t=int(m.group(1)), slot=state.known_slot(m.group(2), m.group(3)))
            if kind == "Q4":
                q["v"] = m.group(4)
        elif kind == "Q5":
            q.update(value=_parse_value(m.group(1), m.group(2)), slot=state.known_slot(m.group(3), m.group(4)))
        else:
            q["slot"] = state.known_slot(m.group(1), m.group(2))
        return q
    raise ValueError(f"unparseable question: {qtext!r}")
