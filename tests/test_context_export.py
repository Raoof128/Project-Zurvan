import pytest
import os
import scripts.context_export as _context_export
import scripts.wiki_merge as _wiki_merge
from scripts.context_export import export_context, search_memory
import io
import sys


def _patch_roots(monkeypatch, tmp_path):
    """Redirect PROJECT_ROOT in context_export and wiki_merge to tmp_path."""
    monkeypatch.setattr(_context_export, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_wiki_merge, "PROJECT_ROOT", tmp_path)

def test_search_memory(capsys):
    # Ensure there's a file to find
    test_file = "wiki/test_search.md"
    os.makedirs("wiki", exist_ok=True)
    with open(test_file, "w") as f:
        f.write("unique_search_term_123")
        
    search_memory("unique_search_term_123")
    captured = capsys.readouterr()
    assert "Found" in captured.out
    assert "test_search.md" in captured.out
    
    os.remove(test_file)

def test_export_context(capsys):
    test_file = "wiki/test_context.md"
    os.makedirs("wiki", exist_ok=True)
    with open(test_file, "w") as f:
        f.write("export_target_keyword")
        
    output = export_context("export_target_keyword")
    assert "Zurvan Context Bundle" in output
    assert "test_context.md" in output

    os.remove(test_file)


def test_save_writes_synthesis_file(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "save_kw_test.md").write_text("save_unique_kw_abc123")

    export_context("save_unique_kw_abc123", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1


def test_save_synthesis_has_required_frontmatter(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "fm_kw_test.md").write_text("fm_unique_kw_xyz789")

    export_context("fm_unique_kw_xyz789", save=True)

    content = list((tmp_path / "wiki" / "syntheses").glob("*.md"))[0].read_text()
    assert "type: synthesis" in content
    assert "query:" in content
    assert "created_at:" in content
    assert "tags: synthesis, query-derived" in content


def test_save_no_overwrite_microsecond_collision(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "ow_kw_test.md").write_text("overwrite_kw_unique123")

    export_context("overwrite_kw_unique123", save=True)
    export_context("overwrite_kw_unique123", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 2


def test_save_false_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "nosave_kw.md").write_text("nosave_kw")

    from scripts.context_export import export_context
    export_context("nosave_kw", save=False)

    synth_dir = tmp_path / "wiki" / "syntheses"
    assert not synth_dir.exists() or not list(synth_dir.glob("*.md"))


def test_search_save_writes_synthesis_file(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "search_save_kw.md").write_text("search_save_unique_kw_xyz")

    search_memory("search_save_unique_kw_xyz", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1
    content = syntheses[0].read_text()
    assert "type: synthesis" in content


def test_search_save_false_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "search_nosave.md").write_text("search_nosave_kw")

    from scripts.context_export import search_memory
    search_memory("search_nosave_kw", save=False)

    synth_dir = tmp_path / "wiki" / "syntheses"
    assert not synth_dir.exists() or not list(synth_dir.glob("*.md"))


def test_format_table_produces_markdown_table(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_table.md").write_text("table_kw_unique_abc999")

    output = export_context("table_kw_unique_abc999", fmt="table")

    assert "| Source |" in output
    assert "|---|" in output


def test_format_table_escapes_pipes_in_excerpts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "pipe_test.md").write_text("pipe_kw_unique999 | has | pipes")

    from scripts.context_export import export_context
    output = export_context("pipe_kw_unique999", fmt="table")

    excerpt_lines = [l for l in output.splitlines() if "has" in l]
    if excerpt_lines:
        assert "\\|" in excerpt_lines[0]


def test_format_marp_starts_with_frontmatter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "test_marp.md").write_text("marp_kw_unique_xyz999")

    from scripts.context_export import export_context
    output = export_context("marp_kw_unique_xyz999", fmt="marp")

    assert output.startswith("---\nmarp: true\n---")


def test_format_table_empty_results_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()

    from scripts.context_export import export_context
    output = export_context("absolutely_no_match_xyzxyz999", fmt="table")

    assert output
    assert "No results" in output


def test_format_marp_empty_results_graceful(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()

    from scripts.context_export import export_context
    output = export_context("absolutely_no_match_xyzxyz999", fmt="marp")

    assert output
    assert "marp: true" in output


def test_format_markdown_default_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "default_fmt.md").write_text("default_fmt_kw_abc999")

    from scripts.context_export import export_context
    output = export_context("default_fmt_kw_abc999", fmt="markdown")

    assert "Zurvan Context Bundle" in output


def test_save_with_marp_format_writes_canonical_markdown(tmp_path, monkeypatch):
    _patch_roots(monkeypatch, tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "marp_save.md").write_text("marp_save_kw_unique999")

    export_context("marp_save_kw_unique999", save=True, fmt="marp")

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1
    content = syntheses[0].read_text()
    assert "type: synthesis" in content
    assert not content.startswith("---\nmarp: true")

# ── JSON output (agent-facing) ────────────────────────────────────────────────

def test_export_context_json_format_is_parseable():
    import json as _json
    from scripts.context_export import export_context

    out = export_context("vector search", limit=3, hybrid=True, graph=False, fmt="json")
    payload = _json.loads(out)

    assert payload["topic"] == "vector search"
    assert isinstance(payload["results"], list)
    assert "dropped_count" in payload
    for r in payload["results"]:
        assert set(r) >= {"source_path", "hybrid_score", "snippet"}
        assert len(r["snippet"]) <= 300
        assert not r["source_path"].startswith("/")  # repo-relative, no abs paths


def test_search_memory_json_output(capsys):
    import json as _json
    from scripts.context_export import search_memory

    search_memory("vector search", hybrid=True, as_json=True)
    payload = _json.loads(capsys.readouterr().out)

    assert payload["query"] == "vector search"
    assert isinstance(payload["results"], list)
