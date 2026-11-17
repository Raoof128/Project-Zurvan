"""Z2 Task 4: ledger writes and bitemporal views (spec §7.1-7.3)."""

import pytest

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

SRC = "wiki/sources/s.md"
BODY = "Week 9 totals 52 days and $1,234.50.\nWeek 10 totals 58 days and $1,400.00.\n"
Q9 = "totals 52 days and $1,234.50"
Q10 = "totals 58 days and $1,400.00"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, text="Acme totals 52 days", quote=Q9, **kw):
    with kr.operation(writer="test", root=root) as op:
        cid = cl.add(text, SRC, quote, confidence="high", basis=["primary_source"],
                     actor="local:test", method="human", root=root, **kw)
    return cid, op.minted_revision


def test_add_then_current(root):
    cid, rev = _add(root, tags=["acme"], valid_from="2026-11-09")
    state = cl.current(root)[cid]
    assert (state.rev, state.status, state.text, state.confidence) == (1, "asserted",
                                                                        "Acme totals 52 days", "high")
    assert state.basis == ["primary_source"] and state.tags == ["acme"]
    assert state.valid_from == "2026-11-09" and state.valid_until is None
    assert state.evidence[0]["source"] == SRC
    assert state.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == Q9
    assert rev == kr.current(root).revision


def test_supersede_links_and_ends_old(root):
    old, _ = _add(root, valid_from="2026-11-09")
    with kr.operation(writer="test", root=root):
        new = cl.supersede(old, text="Acme totals 58 days", source=SRC, evidence=Q10,
                           reason="week 10 closed", actor="local:test", method="human",
                           valid_from="2026-11-16", root=root)
    now = cl.current(root)
    assert now[old].status == "superseded" and now[old].valid_until == "2026-11-16"
    assert now[new].status == "asserted" and now[new].valid_from == "2026-11-16"
    conn = cs.connect(root, readonly=True)
    assert conn.execute("SELECT from_claim, to_claim, kind FROM relations").fetchall() == [
        (new, old, "supersedes")]
    events = [h["event"] for h in cl.history(old, root)]
    assert events == ["assert", "supersede"]
    assert cl.history(old, root)[-1]["reason"] == "week 10 closed"


def test_supersede_is_one_operation(root):
    old, r1 = _add(root)
    with kr.operation(writer="test", root=root) as op:
        cl.supersede(old, text="t", source=SRC, evidence=Q10, reason="r",
                     actor="local:test", method="human", root=root)
    assert op.minted_revision == r1 + 1


def test_retract_keeps_history(root):
    cid, _ = _add(root)
    with kr.operation(writer="test", root=root):
        rev = cl.retract(cid, reason="misread the sheet", basis=["direct_measurement"],
                         actor="local:test", method="human", root=root)
    assert rev == 2
    state = cl.current(root)[cid]
    assert state.status == "retracted" and state.text == "Acme totals 52 days"
    assert [h["status"] for h in cl.history(cid, root)] == ["asserted", "retracted"]


def test_revise_carries_unchanged_fields(root):
    cid, _ = _add(root, tags=["acme"])
    with kr.operation(writer="test", root=root):
        cl.revise(cid, reason="note basis", basis=["primary_source", "direct_measurement"],
                  actor="local:test", method="human", root=root)
    s = cl.current(root)[cid]
    assert s.rev == 2 and s.text == "Acme totals 52 days" and s.tags == ["acme"]
    assert s.basis == ["primary_source", "direct_measurement"]
    assert s.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == Q9   # inherited


def test_reason_required(root):
    cid, _ = _add(root)
    with kr.operation(writer="test", root=root):
        for call in (lambda: cl.retract(cid, reason="  ", basis=[], actor="a", method="human",
                                        root=root),
                     lambda: cl.revise(cid, reason=None, actor="a", method="human", root=root),
                     lambda: cl.supersede(cid, text="t", source=SRC, evidence=Q10, reason="",
                                          actor="a", method="human", root=root)):
            with pytest.raises(cs.ClaimRefused) as ei:
                call()
            assert ei.value.code == "REASON_REQUIRED"
    assert cl.current(root)[cid].rev == 1


def test_expected_rev_conflict_writes_nothing(root):
    cid, _ = _add(root)
    before = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.retract(cid, reason="stale view", basis=[], actor="a", method="agent",
                       expected_rev=5, root=root)
    assert ei.value.code == "CLAIM_CONFLICT"
    assert cl.current(root)[cid].rev == 1 and kr.current(root).revision == before
    with kr.operation(writer="test", root=root):
        cl.retract(cid, reason="right view", basis=[], actor="a", method="agent",
                   expected_rev=1, root=root)


def test_unknown_claim(root):
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.retract("clm_0000000000000000", reason="x", basis=[], actor="a",
                       method="human", root=root)
    assert ei.value.code == "UNKNOWN_CLAIM"


def test_evidence_rejected_writes_nothing(root):
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.add("x", SRC, "not in the source", confidence="high", actor="a",
                   method="human", root=root)
    assert ei.value.code == "EVIDENCE_REJECTED"
    assert cl.current(root) == {}


def test_aborted_operation_rows_invisible(root):
    keep, _ = _add(root)
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root):
            gone = cl.add("never committed", SRC, Q10, confidence="low", actor="a",
                          method="human", root=root)
            assert gone in cl.current(root)            # visible to its own operation
            raise RuntimeError("abort after the claim write")
    assert set(cl.current(root)) == {keep}
    assert cl.history(gone, root) == []
    conn = cs.connect(root, readonly=True)            # the bytes are there, invisible
    assert conn.execute("SELECT COUNT(*) FROM revisions WHERE claim_id = ?",
                        (gone,)).fetchone()[0] == 1


def test_as_of_revision_and_pre_ledger(root):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / "note.md"), "before the ledger\n")
    pre = kr.current(root).revision
    cid, r1 = _add(root)
    with kr.operation(writer="test", root=root) as op:
        cl.retract(cid, reason="r", basis=[], actor="a", method="human", root=root)
    b_pre = cl.as_of_revision(pre, root)
    assert b_pre.pre_ledger is True and b_pre.claims == {}
    assert cl.as_of_revision(r1, root).claims[cid].status == "asserted"
    assert cl.as_of_revision(op.minted_revision, root).claims[cid].status == "retracted"
    assert cl.as_of_revision(op.minted_revision, root).pre_ledger is False


def test_as_of_date(root):
    cid, r1 = _add(root)
    b = cl.as_of_date("2999-01-01T00:00:00+00:00", root)
    assert b.revision == kr.current(root).revision and cid in b.claims
    assert cl.as_of_date("2000-01-01T00:00:00+00:00", root).pre_ledger is True


def test_valid_at_filters_by_valid_time(root):
    a, _ = _add(root, valid_from="2026-11-09", valid_until="2026-11-16")
    b, _ = _add(root, text="timeless", quote=Q10)
    now = cl.current(root)
    assert set(cl.valid_at(now, "2026-11-10")) == {a, b}
    assert set(cl.valid_at(now, "2026-11-20")) == {b}       # unknown valid time is kept
    assert set(cl.valid_at(now, "2026-11-01")) == {b}


def test_write_needs_an_operation(root):
    with pytest.raises(kr.KnowledgeOperationRequired):
        cl.add("x", SRC, Q9, confidence="high", actor="a", method="human", root=root)


def test_write_refused_before_migration(root):
    (root / "wiki" / "claims" / "claim-0001.md").write_text("---\ntype: claim\n---\nold\n",
                                                            encoding="utf-8")
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.add("x", SRC, Q9, confidence="high", actor="a", method="human", root=root)
    assert ei.value.code == "MIGRATION_REQUIRED"


def test_vocabularies_enforced(root):
    with kr.operation(writer="test", root=root):
        with pytest.raises(ValueError):
            cl.add("x", SRC, Q9, confidence="0.93", actor="a", method="human", root=root)
        with pytest.raises(ValueError):
            cl.add("x", SRC, Q9, confidence="high", basis=["vibes"], actor="a",
                   method="human", root=root)


def test_refused_write_on_a_fresh_store_creates_nothing(root):
    """Found by tests/test_mcp_bridge.py: a refused first claim created
    data/claims.sqlite outside any planned write, and the next operation minted
    an EXTERNAL_OBSERVED revision for it."""
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / "d.md"), "x\n")
    before = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused):
            cl.add("x", SRC, "not in the source", confidence="high", actor="a",
                   method="human", root=root)
        with pytest.raises(cs.ClaimRefused):
            cl.retract("clm_0000000000000000", reason="r", basis=[], actor="a",
                       method="human", root=root)
    assert not cs.db_path(root).exists()
    with kr.operation(writer="test", root=root) as op:
        write_file_safely(str(root / "wiki" / "e.md"), "y\n")
    assert op.minted_revision == before + 1
