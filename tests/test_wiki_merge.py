import os
import re
import pytest
from pathlib import Path


def test_log_event_matches_grep_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("ingest", "example.pdf")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert re.search(r"^## \[", log, re.MULTILINE)
    assert "ingest" in log
    assert "example.pdf" in log


def test_log_event_escapes_pipe_in_parts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_event
    append_log_event("query-save", "my|topic|with|pipes")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "my\\|topic\\|with\\|pipes" in log


def test_log_ingest_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_ingest
    append_log_ingest("notes.txt")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "ingest" in log and "notes.txt" in log


def test_log_merge_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_merge
    append_log_merge("RAG", 3)

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "merge" in log and "RAG" in log and "3 sources" in log


def test_log_save_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_save
    append_log_save("vector-search-reliability")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "query-save" in log and "vector-search-reliability" in log


def test_log_image_skip_wrapper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import append_log_image_skip
    append_log_image_skip("diagram.png")

    log = (tmp_path / "wiki" / "log.md").read_text()
    assert "image-skip" in log
    assert "diagram.png" in log
    assert "pending visual extraction" in log


def _make_extraction(source_id, concepts=None, entities=None):
    return {
        "source_id": source_id,
        "concepts": concepts or [],
        "entities": entities or [],
    }


def test_merge_creates_new_concept_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("source_a", concepts=[{"name": "RAG", "definition": "Retrieval-Augmented Generation"}]),
        wiki_dir=str(tmp_path / "wiki"),
    )

    page = tmp_path / "wiki" / "concepts" / "RAG.md"
    assert page.exists()
    content = page.read_text()
    assert "source_a" in content
    assert "source_count: 1" in content


def test_merge_additive_two_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    for sid in ("source_a", "source_b"):
        merge_extraction(
            _make_extraction(sid, concepts=[{"name": "RAG", "definition": f"Def from {sid}"}]),
            wiki_dir=str(tmp_path / "wiki"),
        )

    content = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()
    assert "Evidence from source_a" in content
    assert "Evidence from source_b" in content
    assert "source_count: 2" in content


def test_merge_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction("source_a", concepts=[{"name": "RAG", "definition": "def"}])
    merge_extraction(data, wiki_dir=str(tmp_path / "wiki"))
    first = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()
    merge_extraction(data, wiki_dir=str(tmp_path / "wiki"))
    second = (tmp_path / "wiki" / "concepts" / "RAG.md").read_text()
    assert first == second


def test_merge_preserves_existing_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")
    (wiki / "concepts").mkdir()
    (wiki / "concepts" / "RAG.md").write_text(
        "---\ntype: concept\nsources: source_a\nsource_count: 1\nlast_updated: 2026-01-01\n---\n\n# RAG\n\n## Definition\nOriginal definition.\n"
    )

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("source_b", concepts=[{"name": "RAG", "definition": "New evidence"}]),
        wiki_dir=str(wiki),
    )

    content = (wiki / "concepts" / "RAG.md").read_text()
    assert "Original definition." in content
    assert "Evidence from source_b" in content


def test_source_count_is_derived_not_incremented(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")
    (wiki / "concepts").mkdir()
    # Intentionally wrong source_count — merge must fix it to len(sources)
    (wiki / "concepts" / "RAG.md").write_text(
        "---\ntype: concept\nsources: source_a\nsource_count: 999\nlast_updated: 2026-01-01\n---\n\n# RAG\n\n## Definition\nOriginal.\n"
    )

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("source_b", concepts=[{"name": "RAG", "definition": "new"}]),
        wiki_dir=str(wiki),
    )

    content = (wiki / "concepts" / "RAG.md").read_text()
    assert "source_count: 2" in content
    assert "source_count: 999" not in content


def test_existing_source_id_skips_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    data = _make_extraction("source_a", concepts=[{"name": "RAG", "definition": "def"}])
    merge_extraction(data, wiki_dir=str(wiki))
    before = (wiki / "concepts" / "RAG.md").read_text()
    merge_extraction(data, wiki_dir=str(wiki))
    after = (wiki / "concepts" / "RAG.md").read_text()
    assert before == after


def test_legacy_source_id_frontmatter_preserved(tmp_path, monkeypatch):
    """Old pages use source_id: not sources: — merge must not lose that history."""
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")
    (wiki / "concepts").mkdir()
    # Legacy format: source_id instead of sources
    (wiki / "concepts" / "RAG.md").write_text(
        "---\ntype: concept\nsource_id: legacy_source\n---\n\n# RAG\n\n## Definition\nLegacy definition.\n"
    )

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction("new_source", concepts=[{"name": "RAG", "definition": "New evidence"}]),
        wiki_dir=str(wiki),
    )

    content = (wiki / "concepts" / "RAG.md").read_text()
    # Both original and new source must appear
    assert "legacy_source" in content
    assert "new_source" in content
    assert "source_count: 2" in content


def test_merge_entity_page(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "log.md").write_text("")

    from scripts.wiki_merge import merge_extraction
    merge_extraction(
        _make_extraction(
            "source_a",
            entities=[{"name": "Karpathy", "description": "AI researcher", "entity_type": "person"}],
        ),
        wiki_dir=str(wiki),
    )

    page = wiki / "entities" / "Karpathy.md"
    assert page.exists()
    content = page.read_text()
    assert "entity_type: person" in content
    assert "source_a" in content
