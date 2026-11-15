"""IOU-Z2-4b: image region locators (closed 2026-11-16).

A claim can cite a rectangle of an image: a W3C `FragmentSelector` conforming to Media
Fragments 1.0 (`xywh=pixel:x,y,w,h`), the file's sha256 and the sha256 of the region's
decoded pixels. Locator kind `media_region`, reader `image_region@1` (Pillow). There is
no quote and no capsule: the claim's text says what the region shows, a human says so.
`verify` keeps a claim whose region pixels are unchanged when the file is re-encoded or
edited elsewhere (`moved`), and reports `changed` when the region itself changed.
Claims schema v2 widens the `locator_kind` CHECK; a v1 store migrates on its first
read-write open with every row and hash unchanged.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image")

from scripts import claim_evidence as ce
from scripts import claim_ledger as cl
from scripts import claim_store as cs
from scripts import knowledge_revision as kr
from scripts.claim_store import ClaimRefused

REPO = Path(__file__).resolve().parents[1]
IMG = "raw/figures/chart.png"
SRC = "wiki/sources/s.md"
W = dict(actor="local:test", method="human")
MEDIA_FRAGS = "http://www.w3.org/TR/media-frags/"


def _png(path: Path, *, mark=None, **save):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (100, 80), (255, 255, 255))
    for x in range(20, 50):
        for y in range(10, 40):
            im.putpixel((x, y), (200, 30, 30))           # the bar the claim cites
    if mark:
        im.putpixel(mark, (0, 0, 255))
    im.save(str(path), "PNG", **save)


@pytest.fixture
def root(tmp_path, monkeypatch):
    for d in ("wiki/claims", "wiki/sources", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / SRC).write_text("Orrin Mill is in Halden.\n", encoding="utf-8")
    _png(tmp_path / IMG)
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, region=(20, 10, 30, 30), text="2025 output bar is the tallest"):
    with kr.operation(writer="test", root=root):
        return cl.add(text, IMG, None, region=region, confidence="high", root=root, **W)


def _sel(root, cid):
    return cl.current(root)[cid].evidence[0]


def test_a_region_claim_records_a_media_fragment_and_a_pixel_hash(root):
    cid = _add(root)
    ev = _sel(root, cid)
    assert ev["reader"] == "image_region@1" and ev["locator_kind"] == "media_region"
    frag = ev["selector"]["FragmentSelector"]
    assert frag == {"conformsTo": MEDIA_FRAGS, "value": "xywh=pixel:20,10,30,30"}
    state = ev["selector"]["state"]
    assert len(state["sha256"]) == 64 and len(state["region_sha256"]) == 64
    assert state["decoder"].startswith("Pillow ")
    assert "TextQuoteSelector" not in ev["selector"] and ev.get("capsule") is None


@pytest.mark.parametrize("region", [(90, 70, 20, 20), (0, 0, 0, 5), (-1, 0, 5, 5)])
def test_a_region_outside_the_image_or_empty_is_refused(root, region):
    before = kr.current(root).revision
    with pytest.raises(ClaimRefused) as e:
        _add(root, region=region)
    assert e.value.code == "EVIDENCE_REJECTED"
    assert kr.current(root).revision == before


def test_a_region_on_a_non_image_is_refused(root):
    with kr.operation(writer="test", root=root), pytest.raises(ClaimRefused) as e:
        cl.add("x", SRC, None, region=(0, 0, 1, 1), confidence="high", root=root, **W)
    assert e.value.code == "EVIDENCE_REJECTED"


def test_a_quote_and_a_region_together_are_refused(root):
    with kr.operation(writer="test", root=root), pytest.raises(ValueError):
        cl.add("x", IMG, "a quote", region=(0, 0, 1, 1), confidence="high", root=root, **W)


def test_verify_intact_reencoded_edited_elsewhere_changed_gone(root):
    cid = _add(root)
    sel = _sel(root, cid)["selector"]
    assert ce.resolve(sel, IMG, "image_region@1", root)[0] == "intact"
    _png(root / IMG, compress_level=0)                                  # same pixels, new bytes
    outcome, detail = ce.resolve(sel, IMG, "image_region@1", root)
    assert outcome == "moved" and "region pixels unchanged" in detail
    _png(root / IMG, mark=(90, 70))                                     # outside the region
    assert ce.resolve(sel, IMG, "image_region@1", root)[0] == "moved"
    _png(root / IMG, mark=(25, 15))                                     # inside the region
    assert ce.resolve(sel, IMG, "image_region@1", root)[0] == "changed"
    (root / IMG).unlink()
    assert ce.resolve(sel, IMG, "image_region@1", root)[0] == "gone"


def test_a_smaller_replacement_image_is_changed_not_a_crash(root):
    cid = _add(root)
    sel = _sel(root, cid)["selector"]
    Image.new("RGB", (10, 10)).save(str(root / IMG), "PNG")
    assert ce.resolve(sel, IMG, "image_region@1", root)[0] == "changed"


def test_claim_verify_orphans_a_region_claim_whose_pixels_changed(root):
    cid = _add(root)
    _png(root / IMG, mark=(25, 15))
    with kr.operation(writer="test", root=root):
        cl.verify(apply=True, root=root)
    assert cl.current(root)[cid].status == "orphaned"


def test_the_claim_page_names_the_region(root):
    cid = _add(root)
    page = next((root / "wiki" / "claims").glob("*.md")).read_text(encoding="utf-8")
    assert "xywh=pixel:20,10,30,30" in page and IMG in page


def test_a_region_claim_can_supersede_and_be_superseded(root):
    cid = _add(root)
    with kr.operation(writer="test", root=root):
        new = cl.supersede(cid, text="the 2025 bar, corrected", source=IMG, evidence=None,
                           region=(20, 10, 30, 20), reason="tighter region", root=root, **W)
    assert _sel(root, new)["selector"]["FragmentSelector"]["value"] == "xywh=pixel:20,10,30,20"
    assert cl.current(root)[cid].status == "superseded"


def test_cli_add_with_region(root):
    env = dict(os.environ, PYTHONPATH=str(REPO), ZURVAN_ROOT=str(root))
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", "add",
                        "--text", "the tallest bar is 2025", "--source", IMG,
                        "--region", "20,10,30,30", "--confidence", "high"],
                       capture_output=True, text=True, env=env, cwd=str(REPO), timeout=120)
    assert r.returncode == 0, r.stderr
    (state,) = cl.current(root).values()
    assert state.evidence[0]["selector"]["FragmentSelector"]["value"] == "xywh=pixel:20,10,30,30"
    bad = subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", "add",
                          "--text", "t", "--source", IMG, "--confidence", "high"],
                         capture_output=True, text=True, env=env, cwd=str(REPO), timeout=120)
    assert bad.returncode != 0 and "--evidence or --region" in bad.stderr


# ---- schema v2 ----

def _downgrade_to_v1(root):
    """Rebuild a store's evidence table exactly as schema v1 declared it."""
    db = root / "data" / "claims.sqlite"
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    conn.execute("ALTER TABLE evidence RENAME TO evidence_new")
    conn.execute(cs._EVIDENCE_TABLE.format(kinds="'text_quote'"))
    conn.execute("INSERT INTO evidence SELECT * FROM evidence_new")
    conn.execute("DROP TABLE evidence_new")
    conn.execute("CREATE INDEX IF NOT EXISTS evidence_claim ON evidence(claim_id, rev)")
    conn.execute("UPDATE head SET schema_version = 1")
    conn.execute("PRAGMA user_version = 1")
    conn.execute("COMMIT")
    conn.close()


def test_a_v1_store_migrates_on_its_first_write_with_every_row_and_hash_unchanged(root):
    with kr.operation(writer="test", root=root):
        a = cl.add("Orrin Mill is in Halden", SRC, "Orrin Mill is in Halden", confidence="high",
                   root=root, **W)
    _downgrade_to_v1(root)
    db = root / "data" / "claims.sqlite"
    ro = sqlite3.connect(str(db))
    ev_before = ro.execute("SELECT * FROM evidence ORDER BY evidence_id").fetchall()
    rev_before = ro.execute("SELECT row_hash FROM revisions ORDER BY seq").fetchall()
    assert ro.execute("PRAGMA user_version").fetchone()[0] == 1
    ro.close()
    assert cl.current(root)[a].status == "asserted", "a v1 store still reads"
    cid = _add(root)                                      # the first write migrates
    conn = cs.connect(root)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == cs.SCHEMA_VERSION == 2
    assert conn.execute("SELECT schema_version FROM head").fetchone()[0] == 2
    assert conn.execute("SELECT * FROM evidence WHERE evidence_id <= ? ORDER BY evidence_id",
                        (ev_before[-1][0],)).fetchall() == ev_before
    assert conn.execute("SELECT row_hash FROM revisions ORDER BY seq").fetchall()[:len(rev_before)] == rev_before
    assert cs.chain_ok(conn)[0]
    conn.close()
    assert _sel(root, cid)["locator_kind"] == "media_region"


def test_text_quote_claims_keep_their_locator_kind(root):
    with kr.operation(writer="test", root=root):
        a = cl.add("Orrin Mill is in Halden", SRC, "Orrin Mill is in Halden", confidence="high",
                   root=root, **W)
    assert _sel(root, a)["locator_kind"] == "text_quote"


def test_mcp_claim_show_redacts_the_region_hash_of_an_unreadable_source(root, monkeypatch):
    """A pixel hash confirms a guessed image as a file hash confirms a guessed file."""
    from scripts import mcp_tools
    cid = _add(root)
    monkeypatch.setattr(mcp_tools, "is_readable_path", lambda *a, **k: False)
    doc = json.loads(mcp_tools.tool_zurvan_claim_show(cid))
    state = doc["evidence"][0]["selector"]["state"]
    assert state["sha256"] is None and state["region_sha256"] is None
