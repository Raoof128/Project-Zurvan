"""Z2 Task 13: migration with a frozen legacy copy, orphaning and a provenance
report (spec §7.8, gates G1 and G2)."""

import hashlib
import json
import stat

import pytest

from scripts import claim_ledger as cl
from scripts import claim_migrate as cm
from scripts import claim_store as cs
from scripts import knowledge_revision as kr

NORMAL = """---
type: claim
confidence: "high"
source: "wiki/sources/s.md"
tags:
  - "haji"
---

# Claim
Week 9 totals 52 days

## Evidence
> totals 52 days

**Source**: [[wiki/sources/s.md]]
"""
EXTRACTED = """---
type: claim
claim_id: claim-1
source_id: s2-aaaaaaaaaaaa
source_file: s2.md
source_hash: bbbb
claim_type: fact
confidence: medium
tags:
  - ai
---

# claim-1

## Claim

Zurvan keeps a wiki

## Evidence

> "keeps a persistent wiki"

Location: line 1
Source: [[sources/s2.md]]

"""
MISSING = NORMAL.replace("wiki/sources/s.md", "wiki/sources/gone.md").replace(
    "Week 9 totals 52 days", "Source vanished")
EDITED = NORMAL.replace("Week 9 totals 52 days", "Quote edited away").replace(
    "> totals 52 days", "> totals 99 days")
EMPTY_PLUS_UPDATE = NORMAL.replace("Week 9 totals 52 days", "Both sites run from Sarabi-01").replace(
    "> totals 52 days", "> ") + "\n**Update 2026-09-27:** caveat closed.\n"
UNPARSEABLE = "---\ntype: claim\n---\njust some words, no claim section\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    for d in ("wiki/sources", "wiki/claims", "wiki/decisions"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "wiki" / "sources" / "s.md").write_text("Week 9 totals 52 days and more.\n",
                                                        encoding="utf-8")
    (tmp_path / "wiki" / "sources" / "s2.md").write_text("Zurvan keeps a persistent wiki.\n",
                                                         encoding="utf-8")
    pages = {"claim-00000001.md": NORMAL, "claim-00000002.md": EXTRACTED,
             "claim-00000003.md": MISSING, "claim-00000004.md": EDITED,
             "claim-00000005.md": EMPTY_PLUS_UPDATE, "claim-dummy-001.md": UNPARSEABLE}
    for name, text in pages.items():
        (tmp_path / "wiki" / "claims" / name).write_text(text, encoding="utf-8")
    (tmp_path / "wiki" / "decisions" / "d.md").write_text("---\ntype: decision\n---\nkeep me\n",
                                                          encoding="utf-8")
    (tmp_path / "wiki" / "log.md").write_text(
        "\n- **2026-05-30T17:00:47.279234**: Claim Added - Claim 00000001 from wiki/sources/s.md\n",
        encoding="utf-8")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path)
    return tmp_path


def _migrate(root, tmp_path_factory, **kw):
    legacy = tmp_path_factory.mktemp("z2") / "claims-legacy-20261114"
    report = legacy.parent / "migration-report-20261114.json"
    with kr.operation(writer="cli", root=root):
        out = cm.migrate(root=root, legacy_dir=legacy, report_path=report, **kw)
    return out, legacy, report


def test_parse_both_shapes():
    a = cm.parse_legacy_page("claim-00000001.md", NORMAL)
    assert (a.text, a.source, a.quote, a.confidence, a.tags) == (
        "Week 9 totals 52 days", "wiki/sources/s.md", "totals 52 days", "high", ["haji"])
    b = cm.parse_legacy_page("claim-00000002.md", EXTRACTED)
    assert (b.text, b.quote, b.claim_type, b.location, b.source) == (
        "Zurvan keeps a wiki", "keeps a persistent wiki", "fact", "line 1", "wiki/sources/s2.md")
    assert cm.parse_legacy_page("claim-dummy-001.md", UNPARSEABLE) is None


def test_dry_run_writes_nothing(root, tmp_path_factory):
    before = sorted(p.read_bytes() for p in (root / "wiki" / "claims").glob("*.md"))
    out, legacy, report = _migrate(root, tmp_path_factory, dry_run=True)
    assert out["counts"]["parsed"] == 5 and out["counts"]["unparseable"] == 1
    assert out["counts"]["intact"] == 2 and out["counts"]["gone"] == 1
    assert out["counts"]["changed"] == 1 and out["counts"]["no_evidence"] == 1
    assert not cs.db_path(root).exists() and not legacy.exists() and not report.exists()
    assert sorted(p.read_bytes() for p in (root / "wiki" / "claims").glob("*.md")) == before


def test_freeze_first_and_read_only(root, tmp_path_factory):
    originals = {p.name: p.read_bytes() for p in (root / "wiki" / "claims").glob("*.md")}
    out, legacy, _ = _migrate(root, tmp_path_factory)
    manifest = json.loads((legacy / "MANIFEST.json").read_text())
    assert manifest["files"] == {n: hashlib.sha256(b).hexdigest() for n, b in originals.items()}
    for name, data in originals.items():
        assert (legacy / name).read_bytes() == data
        assert stat.S_IMODE((legacy / name).stat().st_mode) == 0o444
    assert stat.S_IMODE(legacy.stat().st_mode) == 0o555


def test_provenance_retained_for_every_parsed_page(root, tmp_path_factory):
    out, _, report = _migrate(root, tmp_path_factory)
    assert out["provenance_mismatches"] == []
    now = cl.current(root)
    by_legacy = {s.legacy_id: s for s in now.values()}
    assert set(by_legacy) == {"claim-00000001", "claim-00000002", "claim-00000003",
                              "claim-00000004", "claim-00000005", "claim-dummy-001"}
    s1 = by_legacy["claim-00000001"]
    assert (s1.text, s1.evidence[0]["source"]) == ("Week 9 totals 52 days", "wiki/sources/s.md")
    assert s1.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == "totals 52 days"
    assert s1.basis == ["legacy_unstated"] and s1.tags == ["haji"]
    assert json.loads(report.read_text())["provenance_mismatches"] == []


def test_broken_evidence_orphaned_with_reason(root, tmp_path_factory):
    out, _, _ = _migrate(root, tmp_path_factory)
    by_legacy = {s.legacy_id: s for s in cl.current(root).values()}
    assert by_legacy["claim-00000003"].status == "orphaned"
    assert by_legacy["claim-00000004"].status == "orphaned"
    gone = by_legacy["claim-00000003"]
    assert gone.evidence[0]["source"] == "wiki/sources/gone.md"            # provenance kept
    assert gone.evidence[0]["selector"]["state"]["sha256"] is None         # never fingerprinted
    reasons = [h["reason"] for h in cl.history(gone.claim_id, root)]
    assert any("gone" in (r or "") for r in reasons)
    assert {o["legacy_id"] for o in out["orphaned"]} == {"claim-00000003", "claim-00000004"}


def test_unparseable_imported_as_unknown_not_dropped(root, tmp_path_factory):
    out, _, _ = _migrate(root, tmp_path_factory)
    by_legacy = {s.legacy_id: s for s in cl.current(root).values()}
    assert by_legacy["claim-dummy-001"].status == "unknown"
    assert by_legacy["claim-dummy-001"].text == UNPARSEABLE
    assert by_legacy["claim-00000005"].status == "unknown"                 # empty quote
    assert "caveat closed" in by_legacy["claim-00000005"].text or out["no_evidence"]
    assert out["unparseable"] == ["claim-dummy-001"]


def test_authored_markdown_untouched(root, tmp_path_factory):
    before = (root / "wiki" / "decisions" / "d.md").read_bytes()
    _migrate(root, tmp_path_factory)
    assert (root / "wiki" / "decisions" / "d.md").read_bytes() == before


def test_pages_regenerated_under_legacy_names(root, tmp_path_factory):
    _migrate(root, tmp_path_factory)
    names = sorted(p.name for p in (root / "wiki" / "claims").glob("*.md"))
    assert names == ["claim-00000001.md", "claim-00000002.md", "claim-00000003.md",
                     "claim-00000004.md", "claim-00000005.md", "claim-dummy-001.md"]
    text = (root / "wiki" / "claims" / "claim-00000001.md").read_text()
    assert 'authority: "structured"' in text and 'legacy_id: "claim-00000001"' in text
    from scripts import claim_pages
    assert claim_pages.conflicts(root) == []


def test_observed_at_from_log(root, tmp_path_factory):
    out, _, _ = _migrate(root, tmp_path_factory)
    conn = cs.connect(root, readonly=True)
    rows = dict(conn.execute(
        "SELECT c.legacy_id, r.observed_at_legacy_source FROM revisions r JOIN claims c"
        " ON c.claim_id = r.claim_id WHERE r.rev = 1"))
    assert rows["claim-00000001"] == "log.md"
    assert rows["claim-00000002"] == "mtime"
    assert out["observed_at_sources"] == {"log.md": 1, "mtime": 5}


def test_rerun_is_refused_or_noop(root, tmp_path_factory):
    _migrate(root, tmp_path_factory)
    n = len(cl.current(root))
    out, _, _ = _migrate(root, tmp_path_factory)
    assert out["counts"]["parsed"] == 0 and len(cl.current(root)) == n


def test_pre_ledger_as_of_before_migration(root, tmp_path_factory):
    pre = kr.current(root).revision
    _migrate(root, tmp_path_factory)
    assert cl.as_of_revision(pre, root).pre_ledger is True
    assert cl.as_of_revision(kr.current(root).revision, root).pre_ledger is False


def test_report_requires_rebuild(root, tmp_path_factory):
    out, _, report = _migrate(root, tmp_path_factory)
    assert out["derived_rebuild_required"] is True
    assert json.loads(report.read_text())["derived_rebuild_required"] is True


def test_structured_pages_not_reimported(root, tmp_path_factory):
    _migrate(root, tmp_path_factory)
    with kr.operation(writer="test", root=root):
        cl.add("new", "wiki/sources/s.md", "totals 52 days", confidence="low", actor="a",
               method="human", root=root)
    assert cm.parse_legacy_page("x.md", next((root / "wiki" / "claims").glob("clm_*.md")).read_text()) is None


def test_migrate_cli_dry_run(root):
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(repo))
    res = subprocess.run([sys.executable, str(repo / "scripts" / "cli.py"), "claim", "migrate",
                          "--dry-run"], capture_output=True, text=True, env=env, cwd=str(repo))
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["dry_run"] is True and out["counts"]["parsed"] == 5
    assert not cs.db_path(root).exists()


def test_whitespace_only_quote_is_moved_normalised_not_orphaned(root, tmp_path_factory):
    page = NORMAL.replace("Week 9 totals 52 days", "Spacing differs").replace(
        "> totals 52 days", "> totals  52\ndays")
    (root / "wiki" / "claims" / "claim-00000006.md").write_text(page, encoding="utf-8")
    out, _, _ = _migrate(root, tmp_path_factory)
    assert out["counts"]["moved_normalised"] == 1
    by_legacy = {s.legacy_id: s for s in cl.current(root).values()}
    s6 = by_legacy["claim-00000006"]
    assert s6.status == "asserted"
    assert s6.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == "totals  52\ndays"
    assert out["provenance_mismatches"] == []


# Live migration, 2026-11-15: one legacy extraction page had a quote but no
# `source_file`; the ledger refused a None source and the run crashed mid-way.
NO_SOURCE = EXTRACTED.replace("source_file: s2.md\n", "").replace(
    "Zurvan keeps a wiki", "A claim whose page names no source file")


def test_quote_without_a_source_is_imported_and_orphaned_not_a_crash(root, tmp_path_factory):
    (root / "wiki" / "claims" / "claim-00000009.md").write_text(NO_SOURCE, encoding="utf-8")
    out, _, _ = _migrate(root, tmp_path_factory)
    state = {s.legacy_id: s for s in cl.current(root).values()}["claim-00000009"]
    assert state.status == "orphaned"
    assert state.text == "A claim whose page names no source file"
    assert state.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == "keeps a persistent wiki"
    assert not state.evidence[0]["source"]                     # nothing invented
    assert out["provenance_mismatches"] == []
    assert {"legacy_id": "claim-00000009", "outcome": "gone", "source": None} in out["orphaned"]


# IOU-Z2-7, found by the live migration of 2026-11-15: a legacy quote that matched
# its source TWICE after whitespace normalisation was classed `changed` and
# orphaned, while `claim verify` calls the same evidence `ambiguous` - and
# ambiguous never orphans (spec §7.4). The two must agree.
def test_a_quote_matching_twice_after_normalising_is_ambiguous_not_orphaned(root,
                                                                            tmp_path_factory):
    (root / "wiki" / "sources" / "twice.md").write_text(
        "first: alpha beta  gamma here.\nsecond: alpha\nbeta gamma there.\n", encoding="utf-8")
    page = NORMAL.replace("wiki/sources/s.md", "wiki/sources/twice.md").replace(
        "Week 9 totals 52 days", "Twice-matching quote").replace(
        "> totals 52 days", ">   alpha   beta gamma")
    (root / "wiki" / "claims" / "claim-00000007.md").write_text(page, encoding="utf-8")
    dry = cm.migrate(root=root, dry_run=True)
    assert dry["counts"]["ambiguous"] == 1
    assert "claim-00000007" not in {o["legacy_id"] for o in dry["orphaned"]}
    out, _, _ = _migrate(root, tmp_path_factory)
    s7 = {s.legacy_id: s for s in cl.current(root).values()}["claim-00000007"]
    assert s7.status == "asserted"
    assert s7.evidence[0]["selector"]["TextQuoteSelector"]["exact"] == "alpha   beta gamma"
    assert s7.evidence[0]["selector"]["state"]["sha256"] is None     # no position guessed
    assert out["provenance_mismatches"] == []
    with kr.operation(writer="test", root=root):
        v = cl.verify(apply=True, root=root)
    assert v["claims"][s7.claim_id] == ["ambiguous"]
    assert cl.current(root)[s7.claim_id].status == "asserted"      # verify agrees: never orphan
