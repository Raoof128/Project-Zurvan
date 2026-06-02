import pytest
import os
from scripts.context_export import export_context, search_memory
import io
import sys

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
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "save_kw_test.md").write_text("save_unique_kw_abc123")

    from scripts.context_export import export_context
    export_context("save_unique_kw_abc123", save=True)

    syntheses = list((tmp_path / "wiki" / "syntheses").glob("*.md"))
    assert len(syntheses) == 1


def test_save_synthesis_has_required_frontmatter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "fm_kw_test.md").write_text("fm_unique_kw_xyz789")

    from scripts.context_export import export_context
    export_context("fm_unique_kw_xyz789", save=True)

    content = list((tmp_path / "wiki" / "syntheses").glob("*.md"))[0].read_text()
    assert "type: synthesis" in content
    assert "query:" in content
    assert "created_at:" in content
    assert "tags: synthesis, query-derived" in content


def test_save_no_overwrite_microsecond_collision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "ow_kw_test.md").write_text("overwrite_kw_unique123")

    from scripts.context_export import export_context
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
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("")
    (tmp_path / "wiki" / "search_save_kw.md").write_text("search_save_unique_kw_xyz")

    from scripts.context_export import search_memory
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
