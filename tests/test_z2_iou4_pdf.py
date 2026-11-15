"""IOU-Z2-4 (design 2026-11-15-z2-iou-1-4 §4): PDF page locators.

A claim citing a PDF is anchored in that PDF's per-page text (reader `pdf_pages@1`,
pypdf, never normalised) and its selector gains an RFC 3778 `page=N` FragmentSelector.
`verify` reports a page move. Image and media regions stay open (IOU-Z2-4b).
Tiny PDFs are generated offline with PyMuPDF.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("pypdf")

from scripts import claim_evidence as ce
from scripts import claim_ledger as cl
from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
PDF = "sources-pdf/report.pdf"
COVER, OUTPUT, PLACE = ("Cover page of the Orrin report.", "Orrin Mill produced 4,210 tonnes in 2025.",
                        "Appendix: Orrin Mill is in Halden.")
W = dict(actor="local:test", method="human")
RFC3778 = "http://tools.ietf.org/rfc/rfc3778"


def _pdf(path: Path, pages):
    path.parent.mkdir(parents=True, exist_ok=True)
    d = fitz.open()
    for t in pages:
        d.new_page().insert_text((72, 72), t)
    d.save(str(path))
    d.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    for d in ("wiki/claims", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    _pdf(tmp_path / PDF, [COVER, OUTPUT, PLACE])
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    kr.ensure_schema(tmp_path)
    return tmp_path


def _add(root, quote, text="Orrin output"):
    with kr.operation(writer="test", root=root):
        return cl.add(text, PDF, quote, confidence="high", root=root, **W)


def test_a_pdf_claim_records_its_page(root):
    cid = _add(root, "produced 4,210 tonnes")
    ev = cl.current(root)[cid].evidence[0]
    assert ev["reader"] == "pdf_pages@1"
    frag = ev["selector"]["FragmentSelector"]
    assert frag == {"conformsTo": RFC3778, "value": "page=2"}
    assert ev["selector"]["TextQuoteSelector"]["exact"] == "produced 4,210 tonnes"
    assert ev["selector"]["state"]["extractor"].startswith("pypdf ")


def test_page_numbers_are_one_based_and_per_page(root):
    first, third = _add(root, "Cover page"), _add(root, "is in Halden")
    page = lambda c: cl.current(root)[c].evidence[0]["selector"]["FragmentSelector"]["value"]
    assert (page(first), page(third)) == ("page=1", "page=3")


def test_a_quote_that_spans_a_page_break_is_refused(root):
    from scripts.claim_store import ClaimRefused
    with pytest.raises(ClaimRefused):
        _add(root, "report.Orrin Mill")


def test_text_sources_keep_the_utf8_reader_and_no_page(root):
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "wiki" / "sources" / "s.md").write_text("Orrin Mill is in Halden.\n", encoding="utf-8")
    with kr.operation(writer="test", root=root):
        cid = cl.add("place", "wiki/sources/s.md", "is in Halden", confidence="high", root=root, **W)
    ev = cl.current(root)[cid].evidence[0]
    assert ev["reader"] == "utf8" and "FragmentSelector" not in ev["selector"]


def test_verify_intact_then_moved_to_another_page_then_changed(root):
    cid = _add(root, "produced 4,210 tonnes")
    sel = cl.current(root)[cid].evidence[0]["selector"]
    assert ce.resolve(sel, PDF, "pdf_pages@1", root)[0] == "intact"
    _pdf(root / PDF, [COVER, "A new page two.", OUTPUT, PLACE])            # the quote is now on page 3
    outcome, detail = ce.resolve(sel, PDF, "pdf_pages@1", root)
    assert outcome == "moved" and "page 2" in detail and "page 3" in detail
    _pdf(root / PDF, [COVER, "Revised: Orrin Mill produced 4,300 tonnes.", PLACE])
    assert ce.resolve(sel, PDF, "pdf_pages@1", root)[0] == "changed"


def test_verify_moved_within_the_same_page(root):
    cid = _add(root, "produced 4,210 tonnes")
    sel = cl.current(root)[cid].evidence[0]["selector"]
    _pdf(root / PDF, [COVER + " (second edition)", OUTPUT, PLACE])
    outcome, detail = ce.resolve(sel, PDF, "pdf_pages@1", root)
    assert outcome == "moved" and "page 2" in detail and "page 3" not in detail


def test_a_quote_on_two_pages_is_ambiguous_on_verify(root):
    cid = _add(root, "is in Halden")
    sel = cl.current(root)[cid].evidence[0]["selector"]
    _pdf(root / PDF, ["Orrin Mill is in Halden.", OUTPUT, "Orrin Mill is in Halden?"])
    assert ce.resolve(sel, PDF, "pdf_pages@1", root)[0] in ("moved", "ambiguous")


def test_cli_add_and_verify_a_pdf_claim(root):
    env = dict(os.environ, ZURVAN_ROOT=str(root), PYTHONPATH=str(REPO))
    run = lambda *a: subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "claim", *a],
                                    capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180)
    r = run("add", "--text", "Orrin output", "--source", PDF, "--evidence", "produced 4,210 tonnes",
            "--confidence", "high")
    assert r.returncode == 0, r.stderr
    cid = json.loads(r.stdout.strip().splitlines()[-1])["claim_id"]
    v = run("verify", cid)
    assert v.returncode == 0, v.stderr
    assert json.loads(v.stdout.splitlines()[0])["counts"].get("intact") == 1


def test_a_quote_on_two_pages_is_refused_at_anchor(root):                  # I2 (design §4.2)
    from scripts.claim_store import ClaimRefused
    _pdf(root / PDF, ["Orrin Mill is in Halden.", OUTPUT, "Again: Orrin Mill is in Halden."])
    with pytest.raises(ClaimRefused) as exc:
        _add(root, "is in Halden")
    assert exc.value.code == "EVIDENCE_AMBIGUOUS"
    assert cl.current(root) == {}


def test_normalised_verify_never_matches_across_a_page_break(root):
    """Deferred minor (2026-11-15f): whitespace normalisation turned the form feed
    between pages into a space, so a quote split over two pages after an edit was
    `moved_normalised` instead of `changed`."""
    cid = _add(root, "Orrin Mill produced 4,210")
    sel = cl.current(root)[cid].evidence[0]["selector"]
    _pdf(root / PDF, [COVER, "Output of Orrin Mill", "produced 4,210 tonnes in 2025.", PLACE])
    assert ce.resolve(sel, PDF, "pdf_pages@1", root)[0] == "changed"
    _pdf(root / PDF, [COVER, "Orrin Mill  produced 4,210 tonnes.", PLACE])     # same page, new spacing
    outcome, detail = ce.resolve(sel, PDF, "pdf_pages@1", root)
    assert outcome == "moved_normalised" and "page 2" in detail


def test_extraction_from_a_pdf_uses_the_page_reader_when_every_quote_fits(root):
    """Deferred minor (2026-11-15f): extraction always recorded the page-less
    `extract_text@1`. A claim whose quotes each occur once in the per-page text now
    gets `pdf_pages@1` (and so a page); otherwise it keeps `extract_text@1`, which is
    what the model read (PyMuPDF, normalised)."""
    from scripts.extract import evidence_reader
    path = str(root / PDF)
    assert evidence_reader(path, ["produced 4,210 tonnes"]) == "pdf_pages@1"
    assert evidence_reader(path, ["produced 4,210 tonnes", "is in Halden"]) == "pdf_pages@1"
    assert evidence_reader(path, ["report.\nOrrin Mill"]) == "extract_text@1"          # spans a page
    assert evidence_reader(path, ["Orrin Mill"]) == "extract_text@1"                    # occurs twice
    assert evidence_reader(str(root / "notes.md"), ["x"]) == "extract_text@1"           # not a PDF


def test_extraction_passes_the_chosen_reader_to_the_ledger():
    src = (REPO / "scripts" / "extract.py").read_text(encoding="utf-8")
    assert 'reader="extract_text@1", claim_type' not in src
    assert "evidence_reader(" in src
