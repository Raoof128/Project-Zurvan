"""check.sh gate [18/20] — evidence pack export and redaction.

check.sh asserts only that `evidence export` exits 0. What actually matters is
the content: an evidence pack carries excerpts lifted out of private wiki
pages, so the markdown must state its redaction status, and re-redacting a pack
must never un-redact it. Those paths were uncovered.

Every test stubs `inspect_evidence_pack`, so no real pack is read or written.
"""

import json

import pytest

import scripts.evidence_export as evidence_export
from scripts.evidence_export import _format_markdown, export_evidence_pack, redact_existing_pack


def _manifest(**overrides):
    manifest = {
        "pack_id": "pack-1",
        "topic": "smoke test",
        "projects_scanned": ["zurvan"],
        "created_at": "2026-01-01T00:00:00",
        "item_count": 1,
        "redaction_status": "unredacted",
        "warnings": [],
        "generator_version": "1.0",
        "content_hashes": ["h1"],
    }
    manifest.update(overrides)
    return manifest


def _item(kind="claim", **overrides):
    item = {
        "source_kind": kind,
        "title": "An item",
        "project": "zurvan",
        "source_path": "wiki/a.md",
        "excerpt": "a quoted excerpt",
    }
    item.update(overrides)
    return item


def _pack(tmp_path, items=None, manifest=None):
    return {
        "manifest": manifest or _manifest(),
        "items": items if items is not None else [_item()],
        "path": str(tmp_path / "pack-1"),
    }


# ── markdown rendering ────────────────────────────────────────────────────────

def test_markdown_states_the_redaction_status(tmp_path):
    """A reader must be able to tell whether excerpts were redacted."""
    out = _format_markdown(_pack(tmp_path, manifest=_manifest(redaction_status="redacted")))

    assert "Redaction Status: redacted" in out


def test_markdown_carries_topic_projects_and_item_count(tmp_path):
    out = _format_markdown(_pack(tmp_path))

    assert "smoke test" in out
    assert "zurvan" in out
    assert "Total Evidence Items: 1" in out


def test_markdown_says_all_available_when_no_projects_were_named(tmp_path):
    out = _format_markdown(_pack(tmp_path, manifest=_manifest(projects_scanned=[])))

    assert "All available" in out


@pytest.mark.parametrize(
    "kind,heading",
    [
        ("claim", "## Claims"),
        ("decision", "## Decisions"),
        ("source", "## Sources"),
        ("radar_ping", "## Contradictions / Radar Pings"),
        ("contradiction", "## Contradictions / Radar Pings"),
        ("graph_neighbor", "## Graph Context"),
    ],
)
def test_each_item_kind_lands_under_its_own_heading(tmp_path, kind, heading):
    out = _format_markdown(_pack(tmp_path, items=[_item(kind)]))

    section = out.split(heading)[1]
    assert "An item" in section.split("\n## ")[0]


def test_an_unrecognised_kind_falls_into_other_evidence(tmp_path):
    """Nothing may be silently dropped from the pack."""
    out = _format_markdown(_pack(tmp_path, items=[_item("something_new")]))

    assert "## Other Evidence" in out
    assert "An item" in out


def test_item_type_is_used_when_source_kind_is_absent(tmp_path):
    item = {"item_type": "decision", "title": "T", "project": "p",
            "source_path": "wiki/a.md", "excerpt": "e"}
    out = _format_markdown(_pack(tmp_path, items=[item]))

    assert "## Decisions" in out.split("### 1. T")[0]


def test_empty_sections_say_none_found(tmp_path):
    out = _format_markdown(_pack(tmp_path, items=[]))

    assert out.count("None found.") >= 5


def test_optional_item_fields_are_rendered_only_when_present(tmp_path):
    rich = _item(status="accepted", confidence="high", reason="because")
    out = _format_markdown(_pack(tmp_path, items=[rich]))
    assert "**Status**: accepted" in out
    assert "**Confidence**: high" in out
    assert "**Reason**: because" in out

    plain = _format_markdown(_pack(tmp_path, items=[_item()]))
    assert "**Status**" not in plain


def test_blank_optional_fields_are_omitted(tmp_path):
    out = _format_markdown(_pack(tmp_path, items=[_item(status="", confidence="")]))

    assert "**Status**" not in out
    assert "**Confidence**" not in out


def test_manifest_warnings_are_surfaced(tmp_path):
    manifest = _manifest(warnings=["excerpt may contain PII"])
    out = _format_markdown(_pack(tmp_path, manifest=manifest))

    assert "- excerpt may contain PII" in out


def test_manifest_summary_reports_the_hash_count(tmp_path):
    """Hash count is the integrity handle for the pack's items."""
    manifest = _manifest(content_hashes=["h1", "h2", "h3"])
    out = _format_markdown(_pack(tmp_path, manifest=manifest))

    assert "Item Hashes: 3" in out
    assert "Pack ID: `pack-1`" in out


# ── export ────────────────────────────────────────────────────────────────────

def test_export_rejects_an_unknown_pack(monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack", lambda _id: None)

    with pytest.raises(ValueError, match="not found"):
        export_evidence_pack("ghost")


def test_export_rejects_an_unsupported_format(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))

    with pytest.raises(ValueError, match="Unsupported format"):
        export_evidence_pack("pack-1", fmt="pdf")


def test_markdown_export_writes_into_the_pack_directory_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))

    out = export_evidence_pack("pack-1", fmt="markdown")

    assert out.endswith("pack-1.md")
    assert "# Zurvan Evidence Pack" in (tmp_path / "pack-1" / "pack-1.md").read_text()


def test_json_export_round_trips_the_pack_data(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))

    out = export_evidence_pack("pack-1", fmt="json")

    written = json.loads((tmp_path / "pack-1" / "pack-1.json").read_text())
    assert written["manifest"]["pack_id"] == "pack-1"
    assert out.endswith("pack-1.json")


def test_export_honours_an_explicit_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))
    target = tmp_path / "elsewhere"

    out = export_evidence_pack("pack-1", fmt="markdown", output_dir=str(target))

    assert (target / "pack-1.md").exists()
    assert out.startswith(target.as_posix())


def test_export_warns_when_writing_into_a_repository(tmp_path, monkeypatch, capsys):
    """Evidence packs are private output; landing one in a repo risks committing it."""
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))
    target = tmp_path / "my-zurvan"

    export_evidence_pack("pack-1", fmt="markdown", output_dir=str(target))

    assert "WARNING" in capsys.readouterr().out


# ── redaction ─────────────────────────────────────────────────────────────────

def test_redacting_an_unknown_pack_reports_failure(monkeypatch):
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack", lambda _id: None)

    assert redact_existing_pack("ghost") is False


def test_redaction_rewrites_items_and_flips_the_manifest_status(tmp_path, monkeypatch):
    pack_dir = tmp_path / "pack-1"
    pack_dir.mkdir()
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack",
                        lambda _id: _pack(tmp_path))
    monkeypatch.setattr(evidence_export, "redact_evidence_pack_items",
                        lambda items: [{**i, "excerpt": "[REDACTED]"} for i in items])

    assert redact_existing_pack("pack-1") is True

    items = json.loads((pack_dir / "items.json").read_text())
    manifest = json.loads((pack_dir / "manifest.json").read_text())
    assert items[0]["excerpt"] == "[REDACTED]"
    assert manifest["redaction_status"] == "redacted"


def test_an_already_redacted_pack_is_left_untouched(tmp_path, monkeypatch):
    """Re-running redaction must be a no-op, never a second pass over the text."""
    pack_dir = tmp_path / "pack-1"
    pack_dir.mkdir()
    pack = _pack(tmp_path, manifest=_manifest(redaction_status="redacted"))
    monkeypatch.setattr(evidence_export, "inspect_evidence_pack", lambda _id: pack)

    def _must_not_run(_items):
        raise AssertionError("redaction re-ran on an already redacted pack")

    monkeypatch.setattr(evidence_export, "redact_evidence_pack_items", _must_not_run)

    assert redact_existing_pack("pack-1") is True
    assert not (pack_dir / "items.json").exists()
