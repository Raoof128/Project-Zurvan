"""Z2 final-review fix pass (whole-branch review, 2026-11-14). Each test failed
before its fix."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_evidence as ce
from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_reconcile as krec
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
Q = "fact number one is recorded here"
Q2 = "fact number two is recorded here"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(f"{Q}\n{Q2}\n", encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, text="a claim", quote=Q, **kw):
    with kr.operation(writer="test", root=root):
        return cl.add(text, SRC, quote, confidence="high", actor="a", method="human",
                      root=root, **kw)


def _cli(root, *args):
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", *args],
                          capture_output=True, text=True, env=env, cwd=str(REPO))


# I1 ---------------------------------------------------------------------------

def test_i1_a_reader_exception_is_changed_not_a_crash(root, monkeypatch):
    import scripts.ingest as ingest
    (root / "wiki" / "sources" / "p.pdf").write_bytes(b"%PDF-broken")

    def boom(path):
        raise RuntimeError("pymupdf FileDataError")

    monkeypatch.setattr(ingest, "extract_text", boom)
    sel = {"TextQuoteSelector": {"exact": "x", "prefix": "", "suffix": ""},
           "state": {"sha256": "0" * 64}}
    outcome, detail = ce.resolve(sel, "wiki/sources/p.pdf", "extract_text@1", root)
    assert outcome == "changed" and "RuntimeError" in detail


def test_i1_verify_continues_past_a_failing_reader(root, monkeypatch):
    a = _add(root)
    b = _add(root, text="second", quote=Q2)
    real = ce.read_source

    def flaky(source_ref, reader, base):
        if b:  # every read fails for an unexpected reason
            raise RuntimeError("reader exploded")
        return real(source_ref, reader, base)

    monkeypatch.setattr(ce, "read_source", flaky)
    with kr.operation(writer="test", root=root):
        out = cl.verify(root=root)
    assert set(out["claims"]) == {a, b}


# I2 ---------------------------------------------------------------------------

def test_i2_verify_names_broken_claims_with_reason(root):
    cid = _add(root)
    (root / SRC).unlink()
    res = _cli(root, "verify")
    assert res.returncode == 0, res.stderr
    assert cid in res.stdout and "gone" in res.stdout
    assert "No such file" in res.stdout or "FileNotFoundError" in res.stdout
    with kr.operation(writer="test", root=root):
        cl.verify(apply=True, root=root)
    reason = cl.history(cid, root)[-1]["reason"]
    assert "FileNotFoundError" in reason or "No such file" in reason


# I3 ---------------------------------------------------------------------------

def test_i3_write_commands_warn_the_index_is_stale(root):
    cid = _add(root)
    res = _cli(root, "retract", cid, "--reason", "wrong")
    assert res.returncode == 0, res.stderr
    assert "zurvan index search" in res.stderr


# I4 ---------------------------------------------------------------------------

def test_i4_claim_writes_refused_while_store_is_suspect(root):
    cid = _add(root)
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root):
            cl.retract(cid, reason="crashing retract", basis=[], actor="a", method="human",
                       root=root)
            raise RuntimeError("crash after the claim write")
    assert kr.current(root).state == kr.SUSPECT
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.revise(cid, reason="later edit", confidence="low", actor="a", method="human",
                      root=root)
    assert ei.value.code == "CLAIMS_STORE_SUSPECT"
    assert krec.reconcile(root, actor="test")["status"] == "CLEAN"
    state = cl.current(root)[cid]
    assert state.status == "retracted" and state.rev == 2       # the reconciled write wins
    with kr.operation(writer="test", root=root):
        cl.revise(cid, reason="now allowed", confidence="low", actor="a", method="human",
                  root=root)
    assert cl.current(root)[cid].rev == 3 and cl.current(root)[cid].status == "retracted"


# I5 ---------------------------------------------------------------------------

def test_i5_doctor_reports_a_page_that_disagrees_with_the_ledger(root):
    cid = _add(root)
    other = _add(root, text="other", quote=Q2)
    page = root / "wiki" / "claims" / f"{cid}.md"
    # a page regenerated from an OLD state: valid hash, wrong status
    from scripts import claim_pages as cp
    state = cl.current(root)[cid]
    state.status = "superseded"
    page.write_text(cp.render(state, cl.history(cid, root), [], state.mutation_id),
                    encoding="utf-8")
    (root / "wiki" / "claims" / f"{other}.md").unlink()
    report = cs.doctor(root)
    assert sorted(report["page_mismatches"]) == sorted([cid, other])


# erase is terminal (Minor 1, re-graded Important) ------------------------------

def test_erase_is_terminal(root):
    cid = _add(root)
    with kr.operation(writer="test", root=root):
        cl.erase(cid, reason_class="client_request", actor="a", root=root)
    for call in (
        lambda: cl.revise(cid, reason="r", text="resurrected", actor="a", method="human",
                          root=root),
        lambda: cl.retract(cid, reason="r", basis=[], actor="a", method="agent", root=root),
        lambda: cl.supersede(cid, text="t", source=SRC, evidence=Q2, reason="r", actor="a",
                             method="human", root=root),
    ):
        with kr.operation(writer="test", root=root):
            with pytest.raises(cs.ClaimRefused) as ei:
                call()
        assert ei.value.code == "CLAIM_ERASED"
    assert cl.current(root)[cid].status == "erased" and cl.current(root)[cid].text is None


# validated inputs (Minor 3, re-graded Important) --------------------------------

@pytest.mark.parametrize("kw", [{"valid_from": "next tuesday"}, {"valid_until": "soon"},
                                {"project": "Client X"}])
def test_valid_time_and_project_are_validated(root, kw):
    with kr.operation(writer="test", root=root):
        with pytest.raises(ValueError):
            cl.add("x", SRC, Q, confidence="high", actor="a", method="human", root=root, **kw)
    assert cl.current(root) == {}


def test_valid_dates_and_slug_accepted(root):
    cid = _add(root, valid_from="2026-11-09", valid_until="2026-11-16T00:00:00+11:00",
               project="acme")
    assert cl.current(root)[cid].valid_from == "2026-11-09"


# Deferred minor, fixed 2026-11-15: `zurvan claim add` wrote no `Claim Added`
# line to wiki/log.md, while memory.add_claim (the MCP path) did.
def test_cli_claim_add_logs_like_the_mcp_path(root):
    res = _cli(root, "add", "--text", "logged fact", "--source", SRC, "--evidence", Q,
               "--confidence", "high")
    assert res.returncode == 0, res.stderr
    cid = next(iter(cl.current(root)))
    log = (root / "wiki" / "log.md").read_text(encoding="utf-8")
    assert f"Claim Added - Claim {cid} from {SRC}" in log


# Deferred minor, fixed 2026-11-15: retract wrote its basis (why it was
# withdrawn) over the claim's confidence_basis (why it was believed).
def test_retract_keeps_the_confidence_basis_and_records_its_own(root):
    cid = _add(root, basis=["primary_source"])
    with kr.operation(writer="test", root=root):
        cl.retract(cid, reason="source misread", basis=["agent_inference"], actor="a",
                   method="human", root=root)
    assert cl.current(root)[cid].basis == ["primary_source"]
    last = cl.history(cid, root)[-1]
    assert last["reason"] == "source misread (retraction basis: agent_inference)"


# Deferred minor, fixed 2026-11-15: after A was superseded by B and then
# retracted, B's page still said "Supersedes: <A's text>" with no sign that A
# was withdrawn - B's page was never regenerated.
def test_a_successor_page_shows_that_the_claim_it_replaced_was_retracted(root):
    a = _add(root, text="the old reading")
    with kr.operation(writer="test", root=root):
        b = cl.supersede(a, text="the new reading", source=SRC, evidence=Q2, reason="re-read",
                         actor="a", method="human", root=root)
    with kr.operation(writer="test", root=root):
        cl.retract(a, reason="never true", basis=[], actor="a", method="human", root=root)
    page = (root / "wiki" / "claims" / f"{b}.md").read_text(encoding="utf-8")
    assert "Supersedes: the old reading (since retracted)" in page
    from scripts import claim_pages as cp
    assert not cp.has_unadopted_edit(b, root)


def test_a_retract_never_overwrites_a_hand_edit_on_the_successor_page(root):
    a = _add(root, text="the old reading")
    with kr.operation(writer="test", root=root):
        b = cl.supersede(a, text="the new reading", source=SRC, evidence=Q2, reason="re-read",
                         actor="a", method="human", root=root)
    page = root / "wiki" / "claims" / f"{b}.md"
    page.write_text(page.read_text(encoding="utf-8") + "my note\n", encoding="utf-8")
    with kr.operation(writer="test", root=root):
        cl.retract(a, reason="never true", basis=[], actor="a", method="human", root=root)
    assert page.read_text(encoding="utf-8").endswith("my note\n")
