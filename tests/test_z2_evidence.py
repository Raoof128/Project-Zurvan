"""Z2 Task 3: evidence anchors (W3C Web Annotation shape), source fingerprints,
capsules, and the six re-resolution outcomes (spec §7.4)."""

import os

import pytest

from scripts import claim_evidence as ce
from scripts.claim_store import ClaimRefused

NOW = "2026-11-14T00:00:00Z"
BODY = ("Intro line.\nThe sealed suite holds 160 holdout items and 80 reserve items.\n"
        "Closing remark.\n")
QUOTE = "160 holdout items and 80 reserve"


@pytest.fixture
def src(tmp_path):
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    p = tmp_path / "wiki" / "sources" / "s.md"
    p.write_text(BODY, encoding="utf-8")
    return tmp_path, "wiki/sources/s.md", p


def _anchor(root, ref):
    return ce.anchor(ref, QUOTE, "utf8", root, NOW)


def test_anchor_shape_and_positions(src):
    root, ref, _ = src
    a = _anchor(root, ref)
    sel = a["selector"]
    assert set(sel) == {"TextQuoteSelector", "TextPositionSelector", "state"}
    assert set(sel["TextQuoteSelector"]) == {"exact", "prefix", "suffix"}
    assert set(sel["state"]) == {"sha256", "bytes", "captured_at"}
    start, end = sel["TextPositionSelector"]["start"], sel["TextPositionSelector"]["end"]
    assert BODY[start:end] == QUOTE
    assert sel["TextQuoteSelector"]["prefix"] == BODY[max(0, start - 32):start]
    assert sel["state"]["bytes"] == len(BODY.encode()) and sel["state"]["captured_at"] == NOW


def test_intact_when_file_unchanged(src):
    root, ref, _ = src
    assert ce.resolve(_anchor(root, ref)["selector"], ref, "utf8", root)[0] == "intact"


def test_moved_when_text_inserted_before(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.write_text("A new first paragraph.\n" + BODY, encoding="utf-8")
    assert ce.resolve(sel, ref, "utf8", root)[0] == "moved"


def test_moved_normalised_when_whitespace_reflowed(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.write_text(BODY.replace("holdout items and", "holdout\n  items and"), encoding="utf-8")
    assert ce.resolve(sel, ref, "utf8", root)[0] == "moved_normalised"


def test_ambiguous_when_quote_repeats_with_identical_context(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.write_text(BODY + BODY, encoding="utf-8")
    assert ce.resolve(sel, ref, "utf8", root)[0] == "ambiguous"


def test_prefix_suffix_disambiguate_duplicate_quote(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.write_text("Other: " + QUOTE + " elsewhere, with different words around it.\n" + BODY,
                 encoding="utf-8")
    outcome, _ = ce.resolve(sel, ref, "utf8", root)
    assert outcome == "moved"


def test_changed_when_quote_deleted(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.write_text(BODY.replace("80 reserve", "90 reserve"), encoding="utf-8")
    assert ce.resolve(sel, ref, "utf8", root)[0] == "changed"


def test_gone_when_file_removed(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.unlink()
    outcome, detail = ce.resolve(sel, ref, "utf8", root)
    assert outcome == "gone" and detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through chmod 000")
def test_resolve_unreadable_source_is_gone_not_raise(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    p.chmod(0)
    try:
        outcome, detail = ce.resolve(sel, ref, "utf8", root)
    finally:
        p.chmod(0o644)
    assert outcome == "gone" and "ermission" in detail


def test_non_utf8_source(src):
    root, ref, p = src
    sel = _anchor(root, ref)["selector"]
    latin = "caf\xe9 " + BODY
    p.write_bytes(latin.encode("latin-1"))
    with pytest.raises(ClaimRefused) as ei:
        ce.anchor(ref, QUOTE, "utf8", root, NOW)
    assert ei.value.code == "EVIDENCE_REJECTED"
    assert ce.resolve(sel, ref, "utf8", root)[0] == "changed"


def test_capsule_bounds(tmp_path):
    (tmp_path / "s.md").write_text("x" * 500 + QUOTE + "y" * 500, encoding="utf-8")
    cap = ce.anchor("s.md", QUOTE, "utf8", tmp_path, NOW)["capsule"]
    assert cap == "x" * 64 + QUOTE + "y" * 64


def test_empty_quote_rejected(src):
    root, ref, _ = src
    for bad in ("", "   ", None):
        with pytest.raises(ClaimRefused) as ei:
            ce.anchor(ref, bad, "utf8", root, NOW)
        assert ei.value.code == "EVIDENCE_REJECTED"


def test_quote_not_in_source_rejected(src):
    root, ref, _ = src
    with pytest.raises(ClaimRefused) as ei:
        ce.anchor(ref, "not in the file", "utf8", root, NOW)
    assert ei.value.code == "EVIDENCE_REJECTED"


def test_absolute_source_ref(tmp_path, src):
    root, _, p = src
    a = ce.anchor(str(p), QUOTE, "utf8", root, NOW)
    assert ce.resolve(a["selector"], str(p), "utf8", root)[0] == "intact"


def test_unknown_reader_refused(src):
    root, ref, _ = src
    with pytest.raises(ValueError):
        ce.read_source(ref, "pdf_magic", root)
