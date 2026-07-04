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

def test_keyword_search_includes_docs_and_returns_relative_paths(tmp_path):
    # Regression: keyword mode globbed only wiki/, so `zurvan search <term>`
    # could not find docs/ pages that hybrid mode surfaces; and it returned
    # absolute paths. It must scan docs/ too and return repo-relative paths.
    (tmp_path / "wiki").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "wiki" / "note.md").write_text("some wiki body")
    (tmp_path / "docs" / "API.md").write_text("uniquedocsterm integration guide")

    results = _context_export._search_internal(
        "uniquedocsterm", hybrid=False, root=tmp_path
    )

    assert results, "keyword search must find the docs/ page"
    assert results[0]["source_path"] == os.path.join("docs", "API.md")
    assert all(not os.path.isabs(r["source_path"]) for r in results)


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
    # Pin the search root to the tmp corpus: _search_internal scans PROJECT_ROOT
    # (now wiki/ + docs/), so without this the test searches the real repo.
    _patch_roots(monkeypatch, tmp_path)
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
    # Hermetic: search the empty tmp corpus, not the real repo (whose docs/ now
    # happen to contain the sentinel term).
    _patch_roots(monkeypatch, tmp_path)
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

# ── R4b: source dedupe before budgeting ──────────────────────────────────────

def _fake_match(src, chunk_id, score):
    return {"source_path": src, "chunk_id": chunk_id, "text": f"text {chunk_id}", "hybrid_score": score}


def test_dedupe_sources_caps_per_source_and_records_reason():
    from scripts.context_export import _dedupe_sources

    matches = [
        _fake_match("wiki/a.md", "a1", 0.9),
        _fake_match("wiki/a.md", "a2", 0.8),
        _fake_match("wiki/a.md", "a3", 0.7),
        _fake_match("wiki/b.md", "b1", 0.6),
    ]
    kept, dropped = _dedupe_sources(matches, max_per_source=2)

    assert [m["chunk_id"] for m in kept] == ["a1", "a2", "b1"]
    assert dropped == [{"chunk_id": "a3", "reason": "source_dedupe"}]


def test_dedupe_sources_zero_disables():
    from scripts.context_export import _dedupe_sources

    matches = [_fake_match("wiki/a.md", f"a{i}", 1.0 - i / 10) for i in range(5)]
    kept, dropped = _dedupe_sources(matches, max_per_source=0)
    assert len(kept) == 5 and dropped == []


def test_export_context_dedupe_frees_slots_for_other_sources(monkeypatch):
    # The R1B near-miss: one source's chunks took 3/5 slots, pushing another
    # expected source below the cutoff. With the cap, it makes the bundle.
    import scripts.context_export as ce

    matches = [
        _fake_match("wiki/dominant.md", "d1", 0.9),
        _fake_match("wiki/dominant.md", "d2", 0.8),
        _fake_match("wiki/dominant.md", "d3", 0.7),
        _fake_match("wiki/other1.md", "o1", 0.6),
        _fake_match("wiki/other2.md", "o2", 0.5),
        _fake_match("wiki/nearmiss.md", "n1", 0.4),  # rank 6 — cut off before R4b
    ]
    monkeypatch.setattr(ce, "_search_internal", lambda *a, **k: matches)

    out = ce.export_context("q", limit=5, hybrid=True, max_per_source=2)
    assert "wiki/nearmiss.md" in out          # recovered
    assert out.count("Source: wiki/dominant.md") == 2

    out_off = ce.export_context("q", limit=5, hybrid=True, max_per_source=0)
    assert "wiki/nearmiss.md" not in out_off  # old behaviour preserved via 0
