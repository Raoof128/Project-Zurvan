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


# Deferred minor from the final review, fixed 2026-11-15: a writer that dies
# mid-transaction leaves `claims.sqlite-journal` (a hot journal). A read-only
# connection cannot roll it back, so every reader failed with
# SQLITE_READONLY_ROLLBACK - `_connect_ro` callers saw it at the first query,
# and `reconcile_ok` returned False, so the store could never be reconciled.
_HOT = r'''
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute("PRAGMA cache_size=1")
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT INTO content(cid, kind, body) SELECT 'cnt_hot' || i, 'text', "
          "hex(randomblob(2000)) FROM (WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL "
          "SELECT i + 1 FROM r WHERE i < 300) SELECT i FROM r)")
os._exit(0)
'''


def _leave_hot_journal(root):
    subprocess.run([sys.executable, "-c", _HOT, str(cs.db_path(root))], check=True)
    journal = Path(str(cs.db_path(root)) + "-journal")
    assert journal.exists() and journal.stat().st_size > 0
    return journal


def test_a_reader_names_a_hot_journal_instead_of_crashing_or_going_empty(root):
    with kr.operation(writer="test", root=root):
        cid = cl.add("kept", SRC, Q, confidence="high", actor="a", method="human", root=root)
    _leave_hot_journal(root)
    with pytest.raises(cs.ClaimRefused) as ei:
        cl.current(root)
    assert ei.value.code == "CLAIMS_STORE_RECOVERY_NEEDED"
    assert "knowledge reconcile" in ei.value.detail
    assert cid  # the claim is not silently reported missing


def test_reconcile_rolls_back_a_hot_journal_left_by_a_crashed_claim_write(root):
    with kr.operation(writer="test", root=root):
        kept = cl.add("kept", SRC, Q, confidence="high", actor="a", method="human", root=root)
    _crash_after_claim(root)
    journal = _leave_hot_journal(root)
    assert krec.reconcile(root, actor="test")["status"] == "CLEAN"
    assert not journal.exists()
    assert kept in cl.current(root)
    assert cs.doctor(root)["integrity"] == "ok"
    assert kr.current(root).state == kr.CLEAN
    with kr.operation(writer="test", root=root):              # no drift recorded later
        cl.add("after", SRC, Q, confidence="high", actor="a", method="human", root=root)
    assert kr.external_observations(root) == []
