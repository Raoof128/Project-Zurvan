import os
import pytest
import scripts.wiki_merge as _wiki_merge
import scripts.ingest as _ingest
from scripts.ingest import extract_text, calculate_hash


def _patch_roots(monkeypatch, tmp_path):
    """Redirect PROJECT_ROOT in wiki_merge and ingest to tmp_path."""
    monkeypatch.setattr(_wiki_merge, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_ingest, "PROJECT_ROOT", tmp_path)

def test_calculate_hash(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    # sha256 of "hello" is 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    h = calculate_hash(str(f))
    assert h == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

def test_extract_text_txt(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("some test content")
    assert extract_text(str(f)) == "some test content"

def test_extract_text_unsupported():
    with pytest.raises(ValueError):
        extract_text("file.unknown")

import re

def test_append_log_uses_grep_parseable_format(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    _wiki_merge.append_log_ingest("example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log and "example.pdf" in log


# ── Image detection tests (Task 18c) ──────────────────────────────────────────

import json
import re as _re


def test_image_file_produces_pending_visual_stub(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "diagram.png"
    img.write_bytes(b"fakepng")

    ingest_image_stub(str(img))

    stub = tmp_path / "wiki" / "sources" / "diagram_png.md"
    assert stub.exists()
    content = stub.read_text()
    assert "status: pending-visual" in content
    assert "type: image-stub" in content


def test_image_stub_path_is_relative(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "chart.jpg"
    img.write_bytes(b"fakejpg")

    ingest_image_stub(str(img))

    content = (tmp_path / "wiki" / "sources" / "chart_jpg.md").read_text()
    path_match = _re.search(r"path: (.+)", content)
    assert path_match and not os.path.isabs(path_match.group(1).strip())


def test_image_stub_logs_image_skip(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    (tmp_path / "photo.png").write_bytes(b"data")

    ingest_image_stub(str(tmp_path / "photo.png"))

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log and "photo.png" in log


def test_image_stub_prints_warning(tmp_path, monkeypatch, capsys):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    (tmp_path / "icon.gif").write_bytes(b"data")

    ingest_image_stub(str(tmp_path / "icon.gif"))

    captured = capsys.readouterr()
    assert "Image detected but not processed" in captured.out
    assert "icon.gif" in captured.out


def test_image_stub_collision_frontmatter_uses_actual_filename(tmp_path, monkeypatch):
    """Collision-loop must update the frontmatter path to match the actual file written."""
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "image.png"
    img.write_bytes(b"data")

    ingest_image_stub(str(img))  # creates image_png.md
    ingest_image_stub(str(img))  # creates image_png-2.md

    stubs = list((tmp_path / "wiki" / "sources").glob("image_png*.md"))
    assert len(stubs) == 2
    # The collision stub (image_png-2.md) must have path: sources/image_png-2.md in frontmatter
    collision_stub = next(s for s in stubs if s.name == "image_png-2.md")
    content = collision_stub.read_text()
    assert "path: sources/image_png-2.md" in content


def test_image_stub_writes_manifest_json(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "data").mkdir()

    from scripts.ingest import ingest_image_stub
    (tmp_path / "diagram.png").write_bytes(b"data")

    ingest_image_stub(str(tmp_path / "diagram.png"))

    manifest_path = tmp_path / "data" / "image_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest) == 1
    assert manifest[0]["status"] == "pending"
    assert manifest[0]["type"] == "image"
    assert not os.path.isabs(manifest[0]["path"])


def test_source_page_stores_full_text_not_truncated_preview(tmp_path, monkeypatch):
    # "Ingest fully": the derived source page must carry the complete extracted
    # text (previously truncated to text[:1000]), so the whole document is
    # chunked and searchable rather than only its first 1000 chars.
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page

    big = "Constitution clause. " * 500  # ~10k chars, well over the old cap
    filename = create_source_page("/somewhere/doc.txt", "deadbeef", big)

    page = (tmp_path / "wiki" / "sources" / filename).read_text()
    assert big.strip() in page
    assert "Text truncated for preview" not in page


def test_source_page_name_is_guard_safe_for_pdf(tmp_path, monkeypatch):
    # public_repo_guard blocks any tracked path containing ".pdf"/".docx"; the
    # derived wiki page (and its stub concept/claim) must not carry the source
    # extension or a double ".md.md" suffix.
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page, source_page_stem

    filename = create_source_page("/raw/papers/report_v2.02a.pdf", "hash", "body")
    assert ".pdf" not in filename and ".md.md" not in filename
    assert filename == source_page_stem("/raw/papers/report_v2.02a.pdf") + ".md"


def test_is_image_file_detects_extensions():
    from scripts.ingest import is_image_file
    assert is_image_file("photo.png")
    assert is_image_file("diagram.JPG")
    assert is_image_file("chart.webp")
    assert not is_image_file("notes.txt")
    assert not is_image_file("paper.pdf")


def test_markdown_embedded_image_refs_detected(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import scan_for_embedded_images
    md_content = "# Title\n\n![diagram](images/arch.png)\n\nSome text.\n\n![remote](https://example.com/fig.png)\n"
    refs = scan_for_embedded_images(md_content)

    assert len(refs) == 2
    assert any(r["path"] == "images/arch.png" and not r["is_remote"] for r in refs)
    assert any(r["path"] == "https://example.com/fig.png" and r["is_remote"] for r in refs)


def test_remote_image_url_not_downloaded(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from unittest.mock import patch
    from scripts.ingest import scan_for_embedded_images, log_embedded_image_refs
    content = "![fig](https://example.com/image.png)"
    refs = scan_for_embedded_images(content)

    with patch("urllib.request.urlopen") as mock_open:
        log_embedded_image_refs(refs)
        mock_open.assert_not_called()

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log


def test_image_stub_handles_filename_collision_with_counter(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.ingest import ingest_image_stub
    img = tmp_path / "test.png"
    img.write_bytes(b"data")

    ingest_image_stub(str(img))
    ingest_image_stub(str(img))
    ingest_image_stub(str(img))

    stubs = list((tmp_path / "wiki" / "sources").glob("test_png*.md"))
    assert len(stubs) == 3


# ── Failed-ingest recovery + dependency preflight ─────────────────────────────

import sys
import scripts.db as _db


def test_failed_ingest_releases_registration_so_it_can_be_retried(tmp_path, monkeypatch):
    """A crash after register_source must not poison the path.

    Regression: register_source claims the path before the page/log/index
    writes, so a failure in append_log left the path registered with no log
    entry and every retry was refused as "already been ingested".
    """
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "registry.sqlite"))

    src = tmp_path / "paper.txt"
    src.write_text("some content")

    def _boom(_filename):
        raise ModuleNotFoundError("No module named 'yaml'")

    monkeypatch.setattr(_ingest, "append_log", _boom)
    monkeypatch.setattr(sys, "argv", ["ingest", str(src)])

    with pytest.raises(ModuleNotFoundError):
        _ingest.main()

    # The path is free again, so the retry is allowed to proceed.
    assert _db.register_source(str(src), "deadbeef") is True


def test_ingest_refuses_to_start_without_pyyaml(monkeypatch):
    """Preflight fails loudly before writing anything, not midway through."""
    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(SystemExit) as excinfo:
        _ingest.require_ingest_dependencies()

    message = str(excinfo.value)
    assert "PyYAML" in message
    assert "ZURVAN_PYTHON" in message


def test_identical_content_under_a_new_filename_is_not_ingested_twice(tmp_path, monkeypatch, capsys):
    """Dedup is documented as by content hash, but the table is UNIQUE on path,
    so the same document copied to a second filename used to ingest twice."""
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "registry.sqlite"))

    first = tmp_path / "paper.txt"
    first.write_text("identical content")
    second = tmp_path / "paper-copy.txt"
    second.write_text("identical content")

    monkeypatch.setattr(sys, "argv", ["ingest", str(first)])
    _ingest.main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["ingest", str(second)])
    _ingest.main()

    out = capsys.readouterr().out
    assert "same content as already-ingested" in out
    assert str(first) in out


def test_re_ingesting_the_same_path_still_reports_already_ingested(tmp_path, monkeypatch, capsys):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "registry.sqlite"))

    src = tmp_path / "paper.txt"
    src.write_text("content")

    monkeypatch.setattr(sys, "argv", ["ingest", str(src)])
    _ingest.main()
    capsys.readouterr()
    _ingest.main()

    assert "has already been ingested" in capsys.readouterr().out


def test_different_content_still_ingests(tmp_path, monkeypatch, capsys):
    """The hash guard must not block genuinely new documents."""
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "registry.sqlite"))

    for name, body in (("a.txt", "first document"), ("b.txt", "second document")):
        src = tmp_path / name
        src.write_text(body)
        monkeypatch.setattr(sys, "argv", ["ingest", str(src)])
        _ingest.main()

    assert "Successfully ingested" in capsys.readouterr().out
    assert (tmp_path / "wiki" / "sources" / "a.md").exists()
    assert (tmp_path / "wiki" / "sources" / "b.md").exists()


def test_find_source_by_hash_returns_none_for_unseen_content(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "registry.sqlite"))

    assert _db.find_source_by_hash("never-seen") is None


# ── PDF extraction quality (PyMuPDF + normalisation) ──────────────────────────

from scripts.ingest import LIGATURES, normalize_extracted_text


def test_ligatures_are_expanded():
    """Regression: pypdf emitted 191 ligature glyphs across three real PDFs,
    making those words invisible to BM25 and unquotable in a claim."""
    out = normalize_extracted_text("identiﬁable eﬀort ﬂow oﬃce waﬄe")

    assert out == "identifiable effort flow office waffle"
    assert not any(g in out for g in LIGATURES)


def test_one_word_per_line_wrapping_is_rejoined():
    """Regression: one paper arrived with 98% of lines holding a single word,
    which defeats phrase search."""
    mangled = "This\n \nis\n \nnot\n \na\n \nviolation\n \nof\n \nour\n \nthreat\n \nmodel"

    assert normalize_extracted_text(mangled) == "This is not a violation of our threat model"


def test_paragraph_breaks_survive_unwrapping():
    """Blank lines are real structure and must not be flattened away."""
    out = normalize_extracted_text("first\nparagraph\n\nsecond\nparagraph")

    assert out == "first paragraph\n\nsecond paragraph"


def test_non_breaking_spaces_become_ordinary_spaces():
    """A non-breaking space silently broke a verbatim claim quote once."""
    assert normalize_extracted_text("Limited alignment assessment") == "Limited alignment assessment"


def test_hyphenated_line_breaks_are_left_alone():
    """Joining them naively corrupts legitimate compounds, so we don't."""
    out = normalize_extracted_text("state-\nof-the-art")

    assert out == "state- of-the-art"
    assert "stateof" not in out


def test_runs_of_spaces_are_collapsed():
    assert normalize_extracted_text("double  spaced   words") == "double spaced words"


def test_normalisation_is_idempotent():
    once = normalize_extracted_text("a\n \nb\n\nc  d")
    assert normalize_extracted_text(once) == once


def test_pdf_extraction_uses_pymupdf_when_available(tmp_path, monkeypatch):
    """PyMuPDF is ~4x faster than pypdf on this corpus and emits no ligatures."""
    import pymupdf

    pdf = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello ingested world")
    doc.save(str(pdf))
    doc.close()

    assert "hello ingested world" in extract_text(str(pdf))


def test_pdf_extraction_falls_back_to_pypdf_without_pymupdf(tmp_path, monkeypatch):
    """An environment without PyMuPDF must still ingest, not fail."""
    import builtins
    import pymupdf

    pdf = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "fallback path works")
    doc.save(str(pdf))
    doc.close()

    real_import = builtins.__import__

    def _no_pymupdf(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("simulated: PyMuPDF not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_pymupdf)

    assert "fallback path works" in extract_text(str(pdf))


def test_colliding_slugs_do_not_overwrite_each_other(tmp_path, monkeypatch):
    # Two DIFFERENT documents that sanitise to the same slug (two README.md
    # files from two repos; a paper's PDF and its web-article twin) each need
    # their own page. A bare open(..., 'w') silently overwrote the first, so
    # the registry kept claiming a document whose text on disk was another's —
    # and re-ingesting the loser was refused as "already ingested".
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page

    first = create_source_page("/repo-a/README.md", "hash-a", "alpha body")
    second = create_source_page("/repo-b/README.md", "hash-b", "beta body")
    third = create_source_page("/repo-c/README.md", "hash-c", "gamma body")

    assert len({first, second, third}) == 3
    assert second.endswith("-2.md") and third.endswith("-3.md")

    sources = tmp_path / "wiki" / "sources"
    assert "alpha body" in (sources / first).read_text()
    assert "beta body" in (sources / second).read_text()
    assert "gamma body" in (sources / third).read_text()


def test_reingesting_same_content_reuses_its_own_page(tmp_path, monkeypatch):
    # Idempotence: matching on the frontmatter hash means a re-ingest rewrites
    # the document's own page instead of growing -2, -3, -4 on every run.
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page

    create_source_page("/repo-a/README.md", "hash-a", "alpha")
    collided = create_source_page("/repo-b/README.md", "hash-b", "beta")
    again = create_source_page("/repo-b/README.md", "hash-b", "beta revised")

    assert again == collided
    assert len(list((tmp_path / "wiki" / "sources").glob("*.md"))) == 2
    assert "beta revised" in (tmp_path / "wiki" / "sources" / again).read_text()


def test_ingest_no_longer_has_a_stub_generator():
    # Replaces test_stub_concept_and_claim_follow_the_resolved_page_name, which
    # guarded the linkage of pages that must no longer be created at all.
    import scripts.ingest as ing

    assert not hasattr(ing, "create_stub_concept_and_claim"), \
        "the stub generator was removed; nothing may pre-create a claim page"


def test_ingest_creates_no_fake_claim_or_concept_objects(tmp_path, monkeypatch):
    # An ingest used to write two placeholder pages per source: a claim stub
    # reading "Evidence missing? No, cited from <source>" and an AutoConcept
    # stub. The claim stub ASSERTS EVIDENCE IT DOES NOT HAVE, which is the exact
    # fabrication this system exists to prevent; 3,811 of them accumulated,
    # outnumbering real claims 15:1 and taking 7.65% of the search index.
    #
    # wiki/claims/ means "actual claims". Nothing may pre-create one.
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page

    create_source_page("/raw/papers/paper.pdf", "hash-x", "body text")

    for sub in ("claims", "concepts"):
        d = tmp_path / "wiki" / sub
        assert not d.exists() or list(d.glob("*.md")) == [], \
            f"ingest must not pre-create anything in wiki/{sub}/"


def test_source_page_declares_that_nothing_was_extracted(tmp_path, monkeypatch):
    # The honest replacement for the stubs: the SOURCE page states its own
    # extraction status, so absence is recorded rather than faked elsewhere.
    _patch_roots(monkeypatch, tmp_path)
    from scripts.ingest import create_source_page

    filename = create_source_page("/raw/papers/paper.pdf", "hash-x", "body text")
    page = (tmp_path / "wiki" / "sources" / filename).read_text()

    assert "extraction_status: unextracted" in page
