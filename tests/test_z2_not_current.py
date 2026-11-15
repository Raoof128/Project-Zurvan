"""Z2 follow-up (RTBench finding, Raouf 2026-11-15): a correction targets a CURRENT claim.

Before this, `supersede` refused only an erased claim. Without `expected_rev` it
accepted superseding a claim that was already superseded, which left two current
successors for one fact (a fork), and it accepted superseding a retracted claim,
which overwrote the retraction with "superseded". `retract` accepted a claim that
was already retracted and minted an empty revision.

Kept on purpose: retracting a SUPERSEDED claim ("it was replaced, and later found
never to have been true"; the successor page says "since retracted"), and
superseding or retracting an ORPHANED claim (its evidence is gone; replacing or
withdrawing it is how it is corrected).
"""

import pytest

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

SRC = "wiki/sources/s.md"
BODY = "Week 9 totals 52 days and $9,140.70.\nWeek 10 totals 58 days and $10,215.60.\n"
Q9 = "totals 52 days and $9,140.70"
Q10 = "totals 58 days and $10,215.60"
W = dict(actor="local:test", method="human")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _op(root, fn):
    with kr.operation(writer="test", root=root):
        return fn()


def _add(root, text="Sarabi totals 52 days"):
    return _op(root, lambda: cl.add(text, SRC, Q9, confidence="high", basis=["primary_source"],
                                    root=root, **W))


def _sup(root, cid, text="Sarabi totals 58 days"):
    return _op(root, lambda: cl.supersede(cid, text=text, source=SRC, evidence=Q10,
                                          reason="newer sheet", root=root, **W))


def _ret(root, cid):
    return _op(root, lambda: cl.retract(cid, reason="misread", basis=[], root=root, **W))


def _believed(root):
    return sorted(c for c, st in cl.current(root).items() if st.status in cl.CURRENT_STATUSES)


def _refused(root, fn):
    before = kr.current(root).revision
    latest_before = {c: s.rev for c, s in cl.current(root).items()}
    with pytest.raises(cs.ClaimRefused) as exc:
        _op(root, fn)
    assert kr.current(root).revision == before, "a refusal must mint no revision"
    assert {c: s.rev for c, s in cl.current(root).items()} == latest_before
    return exc.value


def test_superseding_a_superseded_claim_is_refused_and_names_the_successor(root):
    a = _add(root)
    b = _sup(root, a)
    err = _refused(root, lambda: cl.supersede(a, text="Sarabi totals 60 days", source=SRC,
                                              evidence=Q10, reason="again", root=root, **W))
    assert err.code == "CLAIM_NOT_CURRENT"
    assert b in str(err)
    assert _believed(root) == [b], "exactly one current claim for the fact; no fork"


def test_superseding_a_retracted_claim_is_refused(root):
    a = _add(root)
    _ret(root, a)
    err = _refused(root, lambda: cl.supersede(a, text="Sarabi totals 60 days", source=SRC,
                                              evidence=Q10, reason="back", root=root, **W))
    assert err.code == "CLAIM_NOT_CURRENT"
    assert "retracted" in str(err)
    assert cl.history(a, root)[-1]["status"] == "retracted"


def test_retracting_a_retracted_claim_is_refused(root):
    a = _add(root)
    _ret(root, a)
    err = _refused(root, lambda: cl.retract(a, reason="again", basis=[], root=root, **W))
    assert err.code == "CLAIM_NOT_CURRENT"


def test_retracting_a_superseded_claim_is_still_allowed(root):
    a = _add(root)
    b = _sup(root, a)
    _ret(root, a)
    assert cl.history(a, root)[-1]["status"] == "retracted"
    assert _believed(root) == [b]


def test_an_orphaned_claim_can_still_be_superseded_or_retracted(root):
    a = _add(root, text="orphan one")
    _op(root, lambda: cl._orphan(a, reason="evidence gone", root=root, **W))
    b = _sup(root, a)
    assert cl.history(a, root)[-1]["status"] == "superseded" and b in _believed(root)
    c = _add(root, text="orphan two")
    _op(root, lambda: cl._orphan(c, reason="evidence gone", root=root, **W))
    _ret(root, c)
    assert cl.history(c, root)[-1]["status"] == "retracted"
