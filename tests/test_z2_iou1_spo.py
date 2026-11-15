"""IOU-Z2-1 (design 2026-11-15-z2-iou-1-4 §3): subject / predicate / object on claims.

A closed predicate vocabulary; subjects are entity ids (`ent:<entity page slug>`);
objects are exactly one of entity / value(+unit) / date. Writable through add, supersede
and revise; extraction may emit it; a reviewed, opt-in backfill proposes and applies it.
No model is called on the live store by any test.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_ledger as cl
from scripts import claim_spo as spo
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
BODY = "Orrin Mill produced 4,210 tonnes in 2025.\nOrrin Mill is in Halden.\n"
Q1 = "Orrin Mill produced 4,210 tonnes in 2025"
Q2 = "Orrin Mill is in Halden"
W = dict(actor="local:test", method="human")
TRIPLE = ("Orrin Mill", "has_value", {"value": "4210", "unit": "tonnes"})


@pytest.fixture
def root(tmp_path, monkeypatch):
    for d in ("wiki/sources", "wiki/claims", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    kr.ensure_schema(tmp_path)
    return tmp_path


def _op(root, fn):
    with kr.operation(writer="test", root=root):
        return fn()


def test_vocabulary_is_closed_and_has_an_explicit_fallback():
    preds = spo.predicates()
    assert "related_to" in preds and "has_value" in preds and len(preds) <= 24
    assert all(isinstance(v, str) and v for v in preds.values())


def test_normalise_builds_canonical_ids_and_rejects_bad_triples():
    s, p, o = spo.normalise(*TRIPLE)
    assert (s, p) == ("ent:Orrin_Mill", "has_value")
    assert json.loads(o) == {"unit": "tonnes", "value": "4210"} and o == json.dumps(json.loads(o), sort_keys=True, separators=(",", ":"))
    assert spo.normalise("ent:Halden", "located_in", '{"entity": "Halden"}')[2] == '{"entity":"ent:Halden"}'
    assert json.loads(spo.normalise("Orrin Mill", "occurred_on", {"date": "2025-12-31"})[2]) == {"date": "2025-12-31"}
    for bad in [("Orrin Mill", "made_up", {"value": "x"}), ("", "has_value", {"value": "x"}),
                ("Orrin Mill", "has_value", {"value": "x", "date": "2025-01-01"}),
                ("Orrin Mill", "has_value", {"unit": "t"}), ("Orrin Mill", "occurred_on", {"date": "31/12/2025"}),
                ("Orrin Mill", "has_value", "not json"), ("ent:", "has_value", {"value": "x"})]:
        with pytest.raises(ValueError):
            spo.normalise(*bad)


def test_add_stores_spo_and_the_chain_still_verifies(root):
    cid = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", spo=TRIPLE, root=root, **W))
    st = cl.current(root)[cid]
    assert (st.subject, st.predicate, st.object) == ("ent:Orrin_Mill", "has_value", {"unit": "tonnes", "value": "4210"})
    conn = cs.connect(root)
    try:
        assert cs.chain_ok(conn)[0]
    finally:
        conn.close()


def test_a_claim_without_spo_is_unchanged(root):
    cid = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", root=root, **W))
    st = cl.current(root)[cid]
    assert (st.subject, st.predicate, st.object) == (None, None, None)
    page = next((root / "wiki" / "claims").glob("*.md")).read_text(encoding="utf-8")
    assert "predicate:" not in page and "subject:" not in page


def test_revise_with_only_spo_is_a_revision_and_later_revisions_carry_it(root):
    cid = _op(root, lambda: cl.add("Orrin is in Halden", SRC, Q2, confidence="high", root=root, **W))
    rev = _op(root, lambda: cl.revise(cid, reason="S-P-O", spo=("Orrin Mill", "located_in", {"entity": "Halden"}),
                                      root=root, **W))
    assert rev == 2
    _op(root, lambda: cl.revise(cid, reason="more sure", confidence="low", root=root, **W))
    st = cl.current(root)[cid]
    assert (st.rev, st.predicate, st.object) == (3, "located_in", {"entity": "ent:Halden"})
    page = next((root / "wiki" / "claims").glob("*.md")).read_text(encoding="utf-8")
    assert "predicate: " in page and "located_in" in page


def test_supersede_takes_a_new_triple(root):
    a = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", spo=TRIPLE, root=root, **W))
    b = _op(root, lambda: cl.supersede(a, text="Orrin produced 4210 tonnes", source=SRC, evidence=Q1, reason="unit",
                                       spo=("Orrin Mill", "has_value", {"value": "4.21", "unit": "kilotonnes"}),
                                       root=root, **W))
    assert cl.current(root)[b].object == {"unit": "kilotonnes", "value": "4.21"}


def test_an_invalid_triple_writes_nothing(root):
    before = kr.current(root).revision
    with pytest.raises(ValueError):
        _op(root, lambda: cl.add("x", SRC, Q1, confidence="high", spo=("Orrin", "nope", {"value": "1"}), root=root, **W))
    assert kr.current(root).revision == before
    assert not list((root / "wiki" / "claims").glob("*.md"))


def _zurvan(root, *args, **env):
    e = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO), **env)
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", *args],
                          capture_output=True, text=True, env=e, cwd=str(REPO), timeout=180)


def test_cli_add_show_and_partial_triples(root):
    r = _zurvan(root, "add", "--text", "Orrin is in Halden", "--source", SRC, "--evidence", Q2, "--confidence",
                "high", "--subject", "Orrin Mill", "--predicate", "located_in", "--object", '{"entity": "Halden"}')
    assert r.returncode == 0, r.stderr
    cid = json.loads(r.stdout.strip().splitlines()[-1])["claim_id"]
    s = _zurvan(root, "show", cid)
    assert "ent:Orrin_Mill located_in" in s.stdout
    j = json.loads(_zurvan(root, "show", cid, "--json").stdout)
    assert j["predicate"] == "located_in"
    before = kr.current(root).revision
    bad = _zurvan(root, "add", "--text", "t", "--source", SRC, "--evidence", Q1, "--confidence", "high",
                  "--subject", "Orrin Mill", "--predicate", "has_value")
    assert bad.returncode != 0 and "--subject, --predicate and --object" in bad.stderr
    assert kr.current(root).revision == before


def test_extraction_keeps_a_valid_triple_and_drops_an_invalid_one(capsys):
    ok = spo.from_extraction({"spo": {"subject": "Orrin Mill", "predicate": "has_value",
                                      "object": {"value": "4210", "unit": "tonnes"}}})
    assert ok == ("Orrin Mill", "has_value", {"value": "4210", "unit": "tonnes"})
    assert spo.from_extraction({}) is None
    assert spo.from_extraction({"claim_id": "c1", "spo": {"subject": "x", "predicate": "nope", "object": {}}}) is None
    assert "c1" in capsys.readouterr().err
    from scripts.llm import _call_mock
    mock = json.loads(_call_mock("", "m", 0))
    assert all(spo.from_extraction(c) is not None for c in mock["claims"])
    prompt = (REPO / "scripts" / "prompts" / "extract_source.md").read_text(encoding="utf-8")
    assert '"spo"' in prompt and "related_to" in prompt


def test_backfill_proposes_without_writing_and_applies_only_approved(root, tmp_path):
    from scripts import claim_structure as cst
    a = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", root=root, **W))
    b = _op(root, lambda: cl.add("Orrin is in Halden", SRC, Q2, confidence="high", root=root, **W))
    done = _op(root, lambda: cl.add("Orrin, again", SRC, Q2, confidence="high", spo=TRIPLE, root=root, **W))

    def fake(prompt):
        return json.dumps({"subject": "Orrin Mill", "predicate": "related_to", "object": {"value": "x"}})

    before = kr.current(root).revision
    out = tmp_path / "proposals.jsonl"
    n = cst.propose(out, call=fake, root=root)
    assert n == 2 and kr.current(root).revision == before
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert {r["claim_id"] for r in rows} == {a, b} and done not in {r["claim_id"] for r in rows}
    assert all(r["approved"] is False and r["text"] for r in rows)
    rows[0]["approved"] = True
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    with kr.operation(writer="test", root=root) as op:
        applied = cst.apply(out, actor="local:test", root=root)
    assert applied == [rows[0]["claim_id"]] and op.minted_revision == before + 1
    hist = cl.history(rows[0]["claim_id"], root)
    assert hist[-1]["event"] == "revise" and hist[-1]["method"] == "model_extraction"
    assert "IOU-Z2-1" in hist[-1]["reason"]
    assert cl.current(root)[rows[1]["claim_id"]].predicate is None


def test_backfill_apply_checks_every_entry_before_writing(root, tmp_path):
    from scripts import claim_structure as cst
    a = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", root=root, **W))
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps({"claim_id": a, "approved": True, "subject": "Orrin Mill", "predicate": "has_value",
                             "object": {"value": "4210"}}) + "\n" +
                 json.dumps({"claim_id": a, "approved": True, "subject": "Orrin Mill", "predicate": "nope",
                             "object": {"value": "1"}}) + "\n")
    before = kr.current(root).revision
    with pytest.raises(ValueError):
        with kr.operation(writer="test", root=root):
            cst.apply(p, actor="local:test", root=root)
    assert kr.current(root).revision == before and cl.current(root)[a].predicate is None


def test_cli_propose_refuses_the_mock_provider(root, tmp_path):
    r = _zurvan(root, "structure", "--propose", "--out", str(tmp_path / "p.jsonl"), ZURVAN_LLM_PROVIDER="mock")
    assert r.returncode == 1 and "PROVIDER_REQUIRED" in r.stderr
    assert not (tmp_path / "p.jsonl").exists()


# ---- final review fixes (2026-11-15) ----

def test_erase_blanks_the_subject_and_object_everywhere(root):            # C1
    cid = _op(root, lambda: cl.add("Jane's salary", SRC, Q1, confidence="high",
                                   spo=("Jane Roe", "has_value", {"value": "98000", "unit": "USD"}), root=root, **W))
    _op(root, lambda: cl.revise(cid, reason="unit", spo=("Jane Roe", "has_value", {"value": "98001", "unit": "USD"}),
                                root=root, **W))
    _op(root, lambda: cl.erase(cid, reason_class="personal_data", actor="local:test", root=root))
    st = cl.current(root)[cid]
    assert (st.subject, st.predicate, st.object) == (None, None, None)
    page = next((root / "wiki" / "claims").glob("*.md")).read_text(encoding="utf-8")
    assert "Jane" not in page and "98000" not in page and "98001" not in page
    import sqlite3
    db = (root / "data" / "claims.sqlite").read_bytes()
    assert b"Jane_Roe" not in db and b"98000" not in db and b"98001" not in db
    for r in range(1, 4):
        old = cl.as_of_revision(r, root).claims.get(cid)
        assert old is None or (old.subject, old.object) == (None, None)
    conn = cs.connect(root)
    try:
        assert cs.chain_ok(conn)[0]
    finally:
        conn.close()


def test_non_ascii_entity_names_are_entities():                            # I3
    assert spo.entity_id("Müller AG") == "ent:Müller_AG"
    assert spo.entity_id("حاجی") == "ent:حاجی"
    with pytest.raises(ValueError):
        spo.entity_id("!!!")


def test_history_shows_the_triple(root):                                    # spec §3.2
    cid = _op(root, lambda: cl.add("Orrin is in Halden", SRC, Q2, confidence="high", root=root, **W))
    _op(root, lambda: cl.revise(cid, reason="S-P-O", spo=("Orrin Mill", "located_in", {"entity": "Halden"}),
                                root=root, **W))
    h = cl.history(cid, root)
    assert h[0]["predicate"] is None and h[1]["predicate"] == "located_in"
    assert h[1]["subject"] == "ent:Orrin_Mill" and h[1]["object"] == {"entity": "ent:Halden"}


@pytest.mark.parametrize("problem", ["stale_rev", "no_rev", "orphaned", "duplicate", "hand_edit"])
def test_apply_refuses_before_any_write(root, tmp_path, problem):           # I1
    from scripts import claim_structure as cst
    from scripts import claim_pages as cp
    a = _op(root, lambda: cl.add("Orrin produced 4210 t", SRC, Q1, confidence="high", root=root, **W))
    b = _op(root, lambda: cl.add("Orrin is in Halden", SRC, Q2, confidence="high", root=root, **W))
    good = {"claim_id": a, "rev": 1, "approved": True, "subject": "Orrin Mill", "predicate": "has_value",
            "object": {"value": "4210"}}
    bad = {"claim_id": b, "rev": 1, "approved": True, "subject": "Orrin Mill", "predicate": "located_in",
           "object": {"entity": "Halden"}}
    if problem == "stale_rev":
        _op(root, lambda: cl.revise(b, reason="r", confidence="low", root=root, **W))
    elif problem == "no_rev":
        del bad["rev"]
    elif problem == "orphaned":
        _op(root, lambda: cl._orphan(b, reason="gone", root=root, **W))
        bad["rev"] = 2
    elif problem == "duplicate":
        bad = dict(good)
    elif problem == "hand_edit":
        page = cp.page_path(cl.current(root)[b], root)
        page.write_text(page.read_text(encoding="utf-8") + "my note\n", encoding="utf-8")
    p = tmp_path / "p.jsonl"
    p.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    before = kr.current(root).revision
    pages = {q: q.read_bytes() for q in (root / "wiki" / "claims").glob("*.md")}
    with pytest.raises((ValueError, cs.ClaimRefused)):
        with kr.operation(writer="test", root=root):
            cst.apply(p, actor="local:test", root=root)
    assert kr.current(root).revision == before and kr.current(root).state != kr.SUSPECT
    assert {q: q.read_bytes() for q in (root / "wiki" / "claims").glob("*.md")} == pages
    assert cl.current(root)[a].predicate is None
