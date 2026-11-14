"""Z2 Task 11: verify reports evidence decay; --apply orphans and restores only on
evidence (spec §7.3/§7.4, gate G5; anti-goal: no restore by assertion)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
Q = "the suite holds 160 holdout items"
BODY = f"Intro.\n{Q}.\nEnd.\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root):
    with kr.operation(writer="test", root=root):
        return cl.add("suite size", SRC, Q, confidence="high", actor="a", method="human",
                      root=root)


def _verify(root, apply=False):
    with kr.operation(writer="test", root=root):
        return cl.verify(apply=apply, root=root)


def test_verify_read_only_by_default(root):
    cid = _add(root)
    (root / SRC).unlink()
    before = kr.current(root).revision
    out = _verify(root)
    assert out["claims"][cid] == ["gone"] and out["counts"]["gone"] == 1
    assert out["integrity_rate"] == 0.0 and out["written"] == []
    assert kr.current(root).revision == before
    assert cl.current(root)[cid].status == "asserted"


def test_integrity_rate_counts_moved_as_anchored(root):
    _add(root)
    (root / SRC).write_text("New first line.\n" + BODY, encoding="utf-8")
    out = _verify(root)
    assert out["counts"]["moved"] == 1 and out["integrity_rate"] == 1.0


def test_apply_orphans_then_restores(root):
    cid = _add(root)
    (root / SRC).write_text("the quote is gone\n", encoding="utf-8")
    out = _verify(root, apply=True)
    assert out["written"] == [(cid, 2, "orphan")]
    assert cl.current(root)[cid].status == "orphaned"
    (root / SRC).write_text(BODY, encoding="utf-8")
    out = _verify(root, apply=True)
    assert out["written"] == [(cid, 3, "restore")]
    assert cl.current(root)[cid].status == "asserted"
    assert [h["method"] for h in cl.history(cid, root)][1:] == ["verifier", "verifier"]


def test_apply_is_idempotent(root):
    cid = _add(root)
    (root / SRC).unlink()
    _verify(root, apply=True)
    before = kr.current(root).revision
    assert _verify(root, apply=True)["written"] == []
    assert kr.current(root).revision == before
    assert cl.current(root)[cid].rev == 2


def test_ambiguous_never_orphans(root):
    cid = _add(root)
    (root / SRC).write_text(BODY + BODY, encoding="utf-8")
    out = _verify(root, apply=True)
    assert out["claims"][cid] == ["ambiguous"] and out["written"] == []


def test_restore_by_assertion_refused(root):
    cid = _add(root)
    (root / SRC).unlink()
    _verify(root, apply=True)
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.revise(cid, reason="keep it alive", confidence="high", actor="agent:x",
                      method="agent", root=root)
    assert ei.value.code == "RESTORE_REFUSED"
    assert cl.current(root)[cid].status == "orphaned"
    (root / "wiki" / "sources" / "new.md").write_text(f"{Q}\n", encoding="utf-8")
    with kr.operation(writer="test", root=root):
        cl.revise(cid, reason="re-anchored", source="wiki/sources/new.md", evidence=Q,
                  actor="a", method="human", root=root)
    assert cl.current(root)[cid].status == "asserted"


def test_orphaned_claim_hidden_from_views(root):
    from scripts import chunk
    cid = _add(root)
    (root / SRC).unlink()
    _verify(root, apply=True)
    files = {f.replace("\\", "/") for f in chunk.scan_markdown_files(root)}
    assert f"wiki/claims/{cid}.md" not in files


def test_verify_cli_prints_disclaimer(root):
    _add(root)
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO))
    res = subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", "verify"],
                         capture_output=True, text=True, env=env, cwd=str(REPO))
    assert res.returncode == 0, res.stderr
    assert "anchoring, not truth" in res.stdout
    assert json.loads(res.stdout.strip().splitlines()[0])["counts"]["intact"] == 1


def test_apply_skips_a_claim_whose_page_has_an_unadopted_edit(root):
    cid = _add(root)
    page = root / "wiki" / "claims" / f"{cid}.md"
    edited = page.read_text(encoding="utf-8") + "\nmy note\n"
    page.write_text(edited, encoding="utf-8")
    (root / SRC).unlink()
    out = _verify(root, apply=True)
    assert out["written"] == [] and out["skipped_edited"] == [cid]
    assert page.read_text(encoding="utf-8") == edited
