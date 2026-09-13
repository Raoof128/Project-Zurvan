"""Figure handling: make the chart gap loud, and get the images out.

A PDF's text layer holds nothing of what a chart shows. On one real ingest four
raster charts carried per-model values found nowhere in the prose, and the
caption-count heuristic missed them because that paper used no "Figure N"
captions. Nothing warned. These tests lock in that the gap is now stated on the
page itself, and that the images can be exported for a visual pass.
"""

import pymupdf
import pytest

from scripts.export_figures import MIN_PIXELS, export_figures
from scripts.ingest import pdf_figure_inventory, render_figure_notice


def _pdf_with_images(path, pages_with_images, size=200):
    """A PDF where the listed 1-based pages each carry one real image."""
    doc = pymupdf.open()
    blob = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size))
    blob.set_rect(blob.irect, (255, 0, 0))
    data = blob.tobytes("png")
    for number in range(1, max(pages_with_images, default=0) + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {number} text")
        if number in pages_with_images:
            page.insert_image(pymupdf.Rect(72, 100, 272, 300), stream=data)
    doc.save(str(path))
    doc.close()
    return path


# ── inventory ─────────────────────────────────────────────────────────────────

def test_inventory_reports_the_pages_that_carry_images(tmp_path):
    pdf = _pdf_with_images(tmp_path / "figs.pdf", {2, 4})

    inventory = pdf_figure_inventory(str(pdf))

    assert [f["page"] for f in inventory] == [2, 4]
    assert all(f["images"] == 1 for f in inventory)


def test_inventory_of_a_text_only_pdf_is_empty(tmp_path):
    pdf = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "no pictures here")
    doc.save(str(pdf))
    doc.close()

    assert pdf_figure_inventory(str(pdf)) == []


def test_inventory_of_an_unreadable_file_fails_soft(tmp_path):
    """A broken PDF must not abort an ingest over its figure count."""
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")

    assert pdf_figure_inventory(str(bad)) == []


# ── the notice on the page ────────────────────────────────────────────────────

def test_no_notice_when_there_are_no_figures():
    assert render_figure_notice([]) == ""


def test_notice_states_the_count_the_pages_and_the_limitation():
    notice = render_figure_notice([{"page": 3, "images": 2}, {"page": 9, "images": 1}])

    assert "3 embedded image(s)" in notice
    assert "3, 9" in notice
    assert "absent from the text above" in notice
    assert "export_figures.py" in notice


def test_notice_warns_against_anchoring_a_claim_to_a_figure():
    """Claims are byte-checked against the page; a figure's numbers are not there."""
    notice = render_figure_notice([{"page": 1, "images": 1}])

    assert "do not anchor a claim" in notice.lower()


# ── export ────────────────────────────────────────────────────────────────────

def test_export_writes_one_png_per_figure(tmp_path):
    pdf = _pdf_with_images(tmp_path / "figs.pdf", {1, 3})
    out = tmp_path / "out"

    exported = export_figures(str(pdf), str(out))

    assert len(exported) == 2
    assert {r["page"] for r in exported} == {1, 3}
    for record in exported:
        assert (out / record["file"].split("/")[-1]).exists()


def test_export_filenames_carry_the_page_number(tmp_path):
    """So a transcription can be tied back to where it came from."""
    pdf = _pdf_with_images(tmp_path / "figs.pdf", {7})

    exported = export_figures(str(pdf), str(tmp_path / "out"))

    assert exported[0]["file"].endswith("p007_0.png")


def test_export_skips_rules_and_logos_below_the_size_floor(tmp_path):
    """Tiny images are page furniture, not figures worth a visual pass."""
    tiny = int(MIN_PIXELS ** 0.5) // 2
    pdf = _pdf_with_images(tmp_path / "tiny.pdf", {1}, size=tiny)

    assert export_figures(str(pdf), str(tmp_path / "out")) == []


def test_export_of_a_figureless_pdf_returns_nothing(tmp_path):
    pdf = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "text only")
    doc.save(str(pdf))
    doc.close()

    assert export_figures(str(pdf), str(tmp_path / "out")) == []


def test_export_never_writes_into_the_wiki(tmp_path):
    """Figures are unread images; nothing may enter the knowledge base until a
    human or a vision pass has actually looked at them."""
    pdf = _pdf_with_images(tmp_path / "figs.pdf", {1})
    out = tmp_path / "out"

    export_figures(str(pdf), str(out))

    assert all("wiki" not in p.parts for p in out.rglob("*"))
