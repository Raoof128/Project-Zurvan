"""IOU-Z2-3 (design 2026-11-15-z2-iou-1-4 §1): what is built FROM a claim is flagged, never rewritten.

Syntheses, evidence packs and reports are point-in-time snapshots. When a claim they
cite stops being current, `claim impact` / `claim stale-derived` say so, and the
writes that end a claim print how many artifacts cite it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import claim_impact as ci
from scripts import claim_ledger as cl
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
SRC = "wiki/sources/s.md"
BODY = "Week 9 totals 52 days.\nWeek 10 totals 58 days.\n"
W = dict(actor="local:test", method="human")


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "store"
    for d in ("wiki/sources", "wiki/claims", "wiki/syntheses", "data"):
        (r / d).mkdir(parents=True, exist_ok=True)
    (r / SRC).write_text(BODY, encoding="utf-8")
    (r / "wiki" / "log.md").write_text("", encoding="utf-8")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("ZURVAN_ROOT", str(r))
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", str(cfg))
    import scripts.context_export as ce
    import scripts.wiki_merge as wm
    monkeypatch.setattr(ce, "PROJECT_ROOT", r)
    monkeypatch.setattr(wm, "PROJECT_ROOT", r)
    kr.ensure_schema(r)
    return r


def _add(root, text, quote):
    with kr.operation(writer="test", root=root):
        return cl.add(text, SRC, quote, confidence="high", basis=[], root=root, **W)


def _page_rel(root, cid):
    from scripts import claim_pages as cp
    return cp.page_path(cl.current(root)[cid], root).relative_to(root).as_posix()


def _synthesis(root, cids, others=()):
    from scripts.context_export import _save_synthesis
    paths = [root / _page_rel(root, c) for c in cids] + [root / o for o in others]
    with kr.operation(writer="test", root=root) as op:
        _save_synthesis("week totals", "body\n", paths)
    return op


def _cfg():
    return Path(os.environ["ZURVAN_CONFIG_DIR"])


def test_a_new_synthesis_records_the_claim_ids_it_cites(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    _synthesis(root, [a], others=[SRC])
    page = next((root / "wiki" / "syntheses").glob("*.md")).read_text(encoding="utf-8")
    assert f"claims: {a}\n" in page


def test_a_synthesis_is_written_through_the_write_barrier(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    op = _synthesis(root, [a])
    assert op.minted_revision is not None, "the synthesis write must be planned, not external"


def test_impact_finds_syntheses_packs_and_reports_by_id_or_page_path(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    rel = _page_rel(root, a)
    _synthesis(root, [a])
    # An older synthesis: provenance only as a `sources:` path.
    (root / "wiki" / "syntheses" / "old.md").write_text(
        f"---\ntype: synthesis\nquery: \"x\"\nsources: {rel}, {SRC}\ntags: synthesis, query-derived\n---\n\nb\n",
        encoding="utf-8")
    pack = _cfg() / "evidence-packs" / "pack-1"
    pack.mkdir(parents=True)
    (pack / "items.json").write_text(json.dumps([{"evidence_id": "ev-1", "project": "p", "source_path": rel,
                                                  "item_type": "claim"}]), encoding="utf-8")
    rep = _cfg() / "reports" / "rep-1"
    rep.mkdir(parents=True)
    (rep / "report.json").write_text(json.dumps({"claims": [{"source_path": rel}], "citations": []}),
                                     encoding="utf-8")
    (_cfg() / "reports" / "rtbench-x.json").write_text("{}", encoding="utf-8")   # a file, not a report dir
    hits = ci.impact(a, root=root)
    assert sorted(h["kind"] for h in hits) == ["evidence_pack", "report", "synthesis", "synthesis"]
    assert all(h["path"] for h in hits)


def test_stale_derived_lists_only_artifacts_citing_a_non_current_claim(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    b = _add(root, "week 10 is 58", "Week 10 totals 58 days")
    _synthesis(root, [a, b])
    assert ci.stale_derived(root=root) == []
    with kr.operation(writer="test", root=root):
        cl.retract(a, reason="misread", basis=[], root=root, **W)
    stale = ci.stale_derived(root=root)
    assert [(s["claim_id"], s["status"], s["kind"]) for s in stale] == [(a, "retracted", "synthesis")]


def test_impact_never_writes(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    _synthesis(root, [a])
    before = kr.current(root).revision
    snapshot = {p: p.read_bytes() for p in root.rglob("*.md")}
    ci.impact(a, root=root)
    ci.stale_derived(root=root)
    assert kr.current(root).revision == before
    assert {p: p.read_bytes() for p in root.rglob("*.md")} == snapshot


def _zurvan(root, *args):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", *args],
                          capture_output=True, text=True, env=env, cwd=str(REPO))


def test_cli_retract_reports_dependents_and_stale_derived_lists_them(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    _synthesis(root, [a])
    r = _zurvan(root, "retract", a, "--reason", "misread")
    assert r.returncode == 0, r.stderr
    assert f"1 derived artifact cites {a}" in r.stderr and f"zurvan claim impact {a}" in r.stderr
    s = _zurvan(root, "stale-derived")
    assert s.returncode == 0, s.stderr
    assert a in s.stdout and "retracted" in s.stdout and "synthesis" in s.stdout
    i = _zurvan(root, "impact", a, "--json")
    assert i.returncode == 0, i.stderr
    assert [h["kind"] for h in json.loads(i.stdout)] == ["synthesis"]


def test_erase_lists_derived_artifacts_as_residue(root):
    a = _add(root, "week 9 is 52", "Week 9 totals 52 days")
    _synthesis(root, [a])
    with kr.operation(writer="test", root=root):
        out = cl.erase(a, reason_class="owner_request", actor="local:test", root=root)
    assert any(r["kind"] == "derived_artifact" and "syntheses" in r["where"] for r in out["residue"])
