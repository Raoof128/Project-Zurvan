"""Z2 Task 6: deterministic claim pages; a hand edit is a conflict, never overwritten
(spec §7.5)."""

import pytest

from scripts import claim_ledger as cl
from scripts import claim_pages as cp
from scripts import claim_store as cs
from scripts import knowledge_revision as kr
from scripts.graph_build import parse_frontmatter

SRC = "wiki/sources/s.md"
HOSTILE = 'line one\n---\nsays "hi" and links ]] [[x\nend'
BODY = "Week 9 totals 52 days.\nWeek 10 totals 58 days.\n" + HOSTILE + "\n"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir(parents=True)
    (tmp_path / SRC).write_text(BODY, encoding="utf-8")
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, text="Acme week 9", quote="Week 9 totals 52 days", **kw):
    with kr.operation(writer="test", root=root):
        return cl.add(text, SRC, quote, confidence="high", actor="local:test",
                      method="human", root=root, **kw)


def test_add_writes_a_generated_page(root):
    cid = _add(root, tags=["acme"])
    page = root / "wiki" / "claims" / f"{cid}.md"
    fm, body = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert fm["type"] == "claim" and fm["claim_id"] == cid and fm["authority"] == "structured"
    assert fm["status"] == "asserted" and fm["confidence"] == "high"
    assert "# Claim\nAcme week 9" in body and "> Week 9 totals 52 days" in body
    assert f"**Source**: [[{SRC}]]" in body and "## History" in body
    assert cp.has_unadopted_edit(cid, root) is False


def test_render_is_byte_stable(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    first = page.read_bytes()
    with kr.operation(writer="test", root=root):
        cp.regenerate(cid, root=root)
    assert page.read_bytes() == first


def test_render_hostile_text_round_trips(root):
    cid = _add(root, text=HOSTILE, quote=HOSTILE, tags=["a---b"])
    text = cp.page_path(cl.current(root)[cid], root).read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)
    assert fm["claim_id"] == cid and fm["status"] == "asserted"
    for line in HOSTILE.splitlines():
        assert f"> {line}" in body                       # every quote line stays quoted
    assert cp.has_unadopted_edit(cid, root) is False


def test_status_banner_and_supersedes_line(root):
    old = _add(root)
    with kr.operation(writer="test", root=root):
        new = cl.supersede(old, text="Acme week 10", source=SRC,
                           evidence="Week 10 totals 58 days", reason="week closed",
                           actor="local:test", method="human", root=root)
    now = cl.current(root)
    old_page = cp.page_path(now[old], root).read_text(encoding="utf-8")
    new_page = cp.page_path(now[new], root).read_text(encoding="utf-8")
    assert "> **STATUS: SUPERSEDED** — week closed" in old_page
    assert "Supersedes: Acme week 9" in new_page


def test_hand_edit_listed_in_conflicts(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    page.write_text(page.read_text(encoding="utf-8").replace("Acme week 9", "Acme wk9"),
                    encoding="utf-8")
    assert cp.has_unadopted_edit(cid, root) is True
    assert cp.conflicts(root) == [cid]


def test_frontmatter_only_edit_is_detected(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    page.write_text(page.read_text(encoding="utf-8").replace("status: \"asserted\"",
                                                             "status: \"supported\""),
                    encoding="utf-8")
    assert cp.has_unadopted_edit(cid, root) is True


def test_supersede_refuses_when_page_has_unadopted_edit(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    edited = page.read_text(encoding="utf-8") + "\nmy own note\n"
    page.write_text(edited, encoding="utf-8")
    before = kr.current(root).revision
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cl.supersede(cid, text="x", source=SRC, evidence="Week 10 totals 58 days",
                         reason="r", actor="local:test", method="human", root=root)
    assert ei.value.code == "CLAIM_CONFLICT"
    assert page.read_text(encoding="utf-8") == edited           # never overwritten
    assert kr.current(root).revision == before


def test_adopt_turns_edit_into_revise(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    page.write_text(page.read_text(encoding="utf-8").replace("Acme week 9", "Acme wk 9"),
                    encoding="utf-8")
    with kr.operation(writer="test", root=root):
        rev = cp.adopt(cid, actor="local:test", reason="typo fix in Obsidian", root=root)
    assert rev == 2 and cl.current(root)[cid].text == "Acme wk 9"
    assert cp.has_unadopted_edit(cid, root) is False


def test_adopt_refuses_unparseable_and_leaves_page(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    page.write_text("garbage with no claim section\n", encoding="utf-8")
    with kr.operation(writer="test", root=root):
        with pytest.raises(cs.ClaimRefused) as ei:
            cp.adopt(cid, actor="local:test", reason="r", root=root)
    assert ei.value.code == "ADOPT_UNPARSEABLE"
    assert page.read_text(encoding="utf-8") == "garbage with no claim section\n"


def test_regenerate_discards_edit(root):
    cid = _add(root)
    page = cp.page_path(cl.current(root)[cid], root)
    clean = page.read_text(encoding="utf-8")
    page.write_text(clean + "\nscribble\n", encoding="utf-8")
    with kr.operation(writer="test", root=root):
        assert cp.regenerate(cid, root=root) == []               # refuses without force
        assert cp.regenerate(cid, force=True, root=root) == [page]
    assert page.read_text(encoding="utf-8") == clean


def test_legacy_id_keeps_its_filename(root):
    with kr.operation(writer="test", root=root):
        cid = cl.add("legacy fact", SRC, "Week 9 totals 52 days", confidence="high",
                     actor="migration", method="migration", legacy_id="claim-0015d220",
                     root=root, _migrating=True)
    assert cp.page_path(cl.current(root)[cid], root).name == "claim-0015d220.md"
