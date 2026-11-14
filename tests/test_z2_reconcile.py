"""Z2 Task 9: reconcile validates claims.sqlite instead of crashing; claim doctor
(spec §7.2, gate G8)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_reconcile as krec
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
Q = "fact number one is recorded here"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(Q + "\n", encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _crash_after_claim(root):
    with pytest.raises(RuntimeError):
        with kr.operation(writer="test", root=root):
            cid = cl.add("crashed claim", SRC, Q, confidence="high", actor="a",
                         method="human", root=root)
            raise RuntimeError("process died after the claim write")
    return cid


def test_reconcile_does_not_crash_on_sqlite(root):
    _crash_after_claim(root)
    assert kr.current(root).state == kr.SUSPECT
    reports = krec.inspect(root)                       # read claims.sqlite as a path
    assert reports and all(r.complete for r in reports)


def test_crash_mid_claim_write_then_reconcile(root):
    cid = _crash_after_claim(root)
    suspect_rev = kr.current(root).revision
    assert cid not in cl.current(root)                 # ORPHANED rows are invisible
    result = krec.reconcile(root, actor="test")
    assert result["status"] == "CLEAN"
    resolved = result["revision"]
    assert cl.current(root)[cid].text == "crashed claim"
    assert cid not in cl.as_of_revision(suspect_rev, root).claims
    assert cl.as_of_revision(resolved, root).claims[cid].status == "asserted"


def test_a_torn_claims_store_needs_review(root):
    _crash_after_claim(root)
    with open(cs.db_path(root), "r+b") as f:          # tear the file
        f.seek(100)
        f.write(b"\x00" * 64)
    reports = krec.inspect(root)
    assert any(str(cs.db_path(root)) in r.parse_failures for r in reports)
    assert krec.reconcile(root)["status"] == "NEEDS_REVIEW"


def test_doctor_reports_invisible_rows(root):
    with kr.operation(writer="test", root=root):
        ok = cl.add("kept", SRC, Q, confidence="high", actor="a", method="human", root=root)
    cid = _crash_after_claim(root)
    report = cs.doctor(root)
    assert report["chain_ok"] is True and report["integrity"] == "ok"
    assert [(r[0], r[2]) for r in report["invisible_rows"]] == [(cid, "ORPHANED")]
    assert report["dangling_cids"] == [] and report["page_mismatches"] == []
    assert ok in cl.current(root)


def test_doctor_reports_page_mismatch(root):
    with kr.operation(writer="test", root=root):
        cid = cl.add("kept", SRC, Q, confidence="high", actor="a", method="human", root=root)
    page = root / "wiki" / "claims" / f"{cid}.md"
    page.write_text(page.read_text(encoding="utf-8") + "edit\n", encoding="utf-8")
    assert cs.doctor(root)["page_mismatches"] == [cid]


def test_doctor_cli(root):
    with kr.operation(writer="test", root=root):
        cl.add("kept", SRC, Q, confidence="high", actor="a", method="human", root=root)
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO))
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", "doctor"],
                         capture_output=True, text=True, env=env, cwd=str(REPO))
    assert res.returncode == 0, res.stderr
    assert '"chain_ok": true' in res.stdout
