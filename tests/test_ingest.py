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
