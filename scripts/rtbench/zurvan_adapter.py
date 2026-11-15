"""Zurvan on RTBench Track S (spec §8): drives the Z2 claim ledger in a
temporary store per case and answers from `as_of_revision` / `valid_at`.

The adapter keeps identifiers only: tick -> knowledge revision, sid -> claim
id, slot -> claim ids. Every value, status and interval it answers with is
read back from Zurvan. The id maps are needed because `erase` blanks a claim's
tags at every revision (plan Gauntlet G1). A slot's claims are the adapter's
own ids plus any claim Zurvan tags with that slot, so a claim Zurvan adopts
after a failed write is seen, never hidden (Gauntlet G2).

abort_mode "before_claim" (default): a failed change lands its source file and
raises before the claim write. "after_claim": the claim write lands, then the
operation raises; reconcile adopts it by design (Z2 spec §7.2).
"""

import os
from pathlib import Path

from scripts import claim_ledger as cl
from scripts import knowledge_revision as kr
from scripts.rtbench.adapters import Adapter
from scripts.safe_write import write_file_safely

ACTOR = dict(actor="local:rtbench", method="human")
_STATUS = {"asserted": "believed", "superseded": "superseded", "retracted": "retracted", "erased": "erased"}


class _Abort(RuntimeError):
    pass


class ZurvanAdapter(Adapter):
    tracks = {"S"}

    def __init__(self, work_dir, abort_mode="before_claim"):
        if abort_mode not in ("before_claim", "after_claim"):
            raise ValueError(abort_mode)
        self.work_dir = Path(work_dir)
        if self.work_dir.exists() and any(self.work_dir.iterdir()):
            # Case stores are numbered from 1 on every run: a leftover claim in a
            # reused directory would be read back as this run's belief.
            raise ValueError(f"work dir {self.work_dir} is not empty; give an empty or new one")
        self.abort_mode = abort_mode
        self.name = "zurvan" if abort_mode == "before_claim" else "zurvan_abort_after_claim"
        self._n = 0
        self._saved_env = os.environ.get("ZURVAN_ROOT")
        self.root = None

    # ------------------------------------------------------------------ lifecycle
    def reset(self):
        self._n += 1
        self.root = self.work_dir / f"case-{self._n}"
        for d in ("data", "wiki/sources", "wiki/claims"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        os.environ["ZURVAN_ROOT"] = str(self.root)
        kr.ensure_schema(self.root)
        self.tick_rev, self.sid_cid, self.slot_cids, self._states = {}, {}, {}, {}

    def close(self):
        if self._saved_env is None:
            os.environ.pop("ZURVAN_ROOT", None)
        else:
            os.environ["ZURVAN_ROOT"] = self._saved_env

    # ------------------------------------------------------------------ writes
    def _source(self, sid, text):
        rel = f"wiki/sources/{sid}.md"
        write_file_safely(str(self.root / rel), text + "\n")
        return rel

    def _change(self, e, *, claim=True):
        text = f"{e['slot']}: {e['value']}"
        src = self._source(e["sid"], text)
        if not claim:
            raise _Abort("rtbench abort before the claim write")
        tags = [f"rtslot:{e['slot']}", f"rtsid:{e['sid']}"]
        if e["type"] == "assert":
            cid = cl.add(text, src, text, confidence="high", tags=tags, valid_from=e.get("valid_from"),
                         valid_until=e.get("valid_until"), root=self.root, **ACTOR)
        else:
            cid = cl.supersede(self.sid_cid[e["old"]], text=text, source=src, evidence=text,
                               reason=e.get("reason") or "rtbench", tags=tags,
                               valid_from=e.get("valid_from"), root=self.root, **ACTOR)
            if e.get("valid_until"):
                cl.revise(cid, reason="valid_until", valid_until=e["valid_until"], root=self.root, **ACTOR)
        return cid

    def _apply(self, e):
        kind = e["type"]
        if kind in ("assert", "supersede"):
            cid = self._change(e)
            self.sid_cid[e["sid"]] = cid
            self.slot_cids.setdefault(e["slot"], []).append(cid)
        elif kind == "retract":
            cl.retract(self.sid_cid[e["sid"]], reason=e.get("reason") or "rtbench",
                       basis=["agent_inference"], root=self.root, **ACTOR)
        elif kind == "revise":
            cl.revise(self.sid_cid[e["sid"]], reason="confidence", confidence=e["confidence"],
                      root=self.root, **ACTOR)
        elif kind == "erase":
            cl.erase(self.sid_cid[e["sid"]], reason_class="owner_request", actor=ACTOR["actor"], root=self.root)
        else:
            write_file_safely(str(self.root / "wiki" / f"n{e['t']}.md"), e.get("note", "") + "\n")

    def ingest(self, e):
        self._states.clear()
        if e["type"] == "abort":
            return self._abort(e["inner"])
        with kr.operation(writer="rtbench", root=self.root) as op:
            self._apply(e)
        rev = op.minted_revision if op.minted_revision is not None else kr.current(self.root).revision
        self.tick_rev[e["t"]] = rev

    def _abort(self, inner):
        try:
            with kr.operation(writer="rtbench", root=self.root):
                if self.abort_mode == "before_claim":
                    self._change(inner, claim=False)
                self._change(inner)
                raise _Abort("rtbench abort after the claim write")
        except _Abort:
            pass
        if kr.current(self.root).state == kr.SUSPECT:
            orphans = [m.mutation_id for m in kr.mutations_in(kr.ORPHANED, self.root)]
            kr.reconcile_revision(orphans, actor="rtbench", root=self.root)

    # ------------------------------------------------------------------ reads
    def _rev_at(self, t):
        revs = [r for k, r in self.tick_rev.items() if k <= t]
        return max(revs) if revs else None

    def _state(self, rev):
        if rev is None:
            return {}
        if rev not in self._states:
            self._states[rev] = cl.as_of_revision(rev, self.root).claims
        return self._states[rev]

    def _slot_claims(self, state, slot):
        own = list(self.slot_cids.get(slot, []))
        extra = sorted(cid for cid, s in state.items() if f"rtslot:{slot}" in (s.tags or []) and cid not in own)
        return [cid for cid in own + extra if cid in state]

    @staticmethod
    def _value(s):
        return "[erased]" if s.text is None else s.text.split(": ", 1)[1]

    def _sid_of(self, cid, s):
        for sid, c in self.sid_cid.items():
            if c == cid:
                return sid
        return next((t.split(":", 1)[1] for t in (s.tags or []) if t.startswith("rtsid:")), cid)

    def _slot_value(self, rev, slot, v=None):
        state = self._state(rev)
        live = {cid: state[cid] for cid in self._slot_claims(state, slot) if state[cid].status == "asserted"}
        if v is not None:
            live = cl.valid_at(live, v)
        if not live:
            return "none"
        return self._value(live[list(live)[-1]])

    def answer(self, q):
        kind = q["type"]
        if kind == "Q1":
            state = self._state(self._rev_at(q["t"]))
            live = [(cid, s) for cid, s in state.items() if s.status == "asserted"]
            if q.get("answer_as") == "value":
                return sorted(self._value(s) for _, s in live)
            return sorted(self._sid_of(cid, s) for cid, s in live)
        if kind == "Q2":
            state = self._state(self._rev_at(q["t"]))
            cid = self.sid_cid.get(q["sid"])
            if cid is None or cid not in state:
                return "not_yet"
            return _STATUS.get(state[cid].status, state[cid].status)
        if kind == "Q3":
            return self._slot_value(self._rev_at(q["t"]), q["slot"])
        if kind == "Q4":
            return self._slot_value(self._rev_at(q["t"]), q["slot"], v=q["v"])
        if kind == "Q6":
            return self._slot_value(self._rev_at(max(self.tick_rev, default=0)), q["slot"])
        if kind == "Q5":
            for rev in sorted(set(self.tick_rev.values())):
                state = self._state(rev)
                for cid in self._slot_claims(state, q["slot"]):
                    s = state[cid]
                    if s.status == "asserted" and s.text is not None and self._value(s) == q["value"]:
                        return "yes"
            return "no"
        raise ValueError(kind)
