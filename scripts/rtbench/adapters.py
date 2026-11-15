"""The adapter interface and the shipped stdlib adapters (spec §6.6, §8).

Questions are asked only after the whole log is ingested, so an adapter must
remember history; it cannot answer from the state it happened to be in.
"""

from scripts.rtbench.oracle import answer as oracle_answer
from scripts.rtbench.oracle import fold
from scripts.rtbench.render import ParseState, parse_question, parse_turn


class Adapter:
    name = "adapter"
    tracks = {"S"}

    def reset(self) -> None:
        raise NotImplementedError

    def ingest(self, item) -> None:
        raise NotImplementedError

    def answer(self, question) -> object:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OracleAdapter(Adapter):
    """The reference fold wrapped as an adapter: a scorer self-check, not a baseline."""
    name = "oracle"

    def reset(self):
        self.events = []

    def ingest(self, item):
        self.events.append(item)

    def answer(self, question):
        return oracle_answer(fold(self.events), question)


class OverwriteAdapter(Adapter):
    """Latest value wins: one value per slot, replaced on assert/supersede, deleted
    on retract/erase, aborts dropped; ignores record time and valid time."""
    name = "overwrite"

    def reset(self):
        self.cur = {}

    def ingest(self, e):
        if e["type"] in ("assert", "supersede"):
            self.cur[e["slot"]] = (e["sid"], e["value"])
        elif e["type"] in ("retract", "erase"):
            self.cur = {k: v for k, v in self.cur.items() if v[0] != e["sid"]}

    def answer(self, q):
        kind = q["type"]
        if kind == "Q1":
            i = 1 if q.get("answer_as") == "value" else 0
            return sorted(v[i] for v in self.cur.values())
        if kind == "Q2":
            return "believed" if any(v[0] == q["sid"] for v in self.cur.values()) else "not_yet"
        if kind in ("Q3", "Q4", "Q6"):
            return self.cur.get(q["slot"], (None, "none"))[1]
        if kind == "Q5":
            return "yes" if self.cur.get(q["slot"], (None, None))[1] == q["value"] else "no"
        raise ValueError(kind)


class AppendOnlyAdapter(Adapter):
    """Keep everything: every asserted value kept forever with its tick; retract,
    supersede-ending, erase and revise ignored; aborts dropped."""
    name = "append_only"

    def reset(self):
        self.log = []

    def ingest(self, e):
        if e["type"] in ("assert", "supersede"):
            self.log.append(e)

    def answer(self, q):
        kind = q["type"]
        if kind == "Q1":
            key = "value" if q.get("answer_as") == "value" else "sid"
            return sorted(e[key] for e in self.log if e["t"] <= q["t"])
        if kind == "Q2":
            return "believed" if any(e["sid"] == q["sid"] and e["t"] <= q["t"] for e in self.log) else "not_yet"
        if kind in ("Q3", "Q4", "Q6"):
            t = float("inf") if kind == "Q6" else q["t"]
            hits = [e for e in self.log if e["slot"] == q["slot"] and e["t"] <= t]
            if kind == "Q4":
                v = q["v"]
                hits = [e for e in hits if (e.get("valid_from") is None or e["valid_from"] <= v)
                        and (e.get("valid_until") is None or v < e["valid_until"])]
            return hits[-1]["value"] if hits else "none"
        if kind == "Q5":
            return "yes" if any(e["slot"] == q["slot"] and e["value"] == q["value"] for e in self.log) else "no"
        raise ValueError(kind)


class TrackNReader(Adapter):
    """Feeds Track N prose to a structured adapter through the shared template
    parser. It sees prose only: no hidden structured fields."""
    tracks = {"N"}

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name

    def reset(self):
        self.state = ParseState()
        self.inner.reset()

    def ingest(self, turn):
        self.inner.ingest(parse_turn(turn, self.state))

    def answer(self, question):
        return self.inner.answer(parse_question(question["text"], question["qid"], self.state))

    def close(self):
        self.inner.close()


def _zurvan(**kw):
    from scripts.rtbench.zurvan_adapter import ZurvanAdapter
    return ZurvanAdapter(**kw)


def _zurvan_after(**kw):
    from scripts.rtbench.zurvan_adapter import ZurvanAdapter
    return ZurvanAdapter(abort_mode="after_claim", **kw)


ADAPTERS = {
    "oracle": OracleAdapter,
    "overwrite": OverwriteAdapter,
    "append_only": AppendOnlyAdapter,
    "zurvan": _zurvan,
    "zurvan_abort_after_claim": _zurvan_after,
}
ZURVAN_ADAPTERS = ("zurvan", "zurvan_abort_after_claim")
