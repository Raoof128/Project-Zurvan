import pytest
import os
from scripts.safe_write import is_safe_path, escape_yaml_string, write_file_safely
from pathlib import Path

def test_is_safe_path():
    root = Path(os.getcwd()).resolve()
    
    # Safe paths
    assert is_safe_path(root / "wiki" / "test.md") == True
    assert is_safe_path(root / "data" / "db.sqlite") == True
    
    # Unsafe paths
    assert is_safe_path(root / "raw" / "test.txt") == False
    assert is_safe_path(root / "raw" / "papers" / "doc.pdf") == False
    assert is_safe_path(Path("/etc/passwd")) == False
    assert is_safe_path(root.parent / "outside.txt") == False

def test_escape_yaml_string():
    assert escape_yaml_string("normal") == '"normal"'
    assert escape_yaml_string('has "quotes"') == '"has \\"quotes\\""'
    assert escape_yaml_string("has\nnewlines") == '"has\\nnewlines"'
    assert escape_yaml_string("") == ""


def test_escape_yaml_string_keeps_long_values_on_one_line():
    # Regression: yaml.dump line-wraps long scalars by default, which the naive
    # line-by-line frontmatter parsers (graph_build, wiki_merge) then truncate.
    import yaml
    long = "Use SQLite FTS5 for hybrid retrieval because it is simple and local " * 4
    out = escape_yaml_string(long)
    assert "\n" not in out
    # The single-line frontmatter round-trips through a real YAML parser intact.
    assert yaml.safe_load(f"title: {out}\ntype: decision\n")["title"] == long

def test_write_file_safely_prevents_raw_writes():
    root = Path(os.getcwd()).resolve()
    target = str(root / "raw" / "hacked.txt")
    assert write_file_safely(target, "malicious") == False
    assert not os.path.exists(target)

def test_add_claim_resolves_source_against_project_root(tmp_path, monkeypatch):
    # Regression: add_claim checked the source path relative to the CWD, so
    # zurvan_claim_add via MCP (launched from any other directory) rejected
    # valid repo-relative sources like "wiki/foo.md".
    import scripts.memory as memory
    import scripts.safe_write as safe_write

    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "src.md").write_text("the exact evidence line")
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    # A CWD from which the relative path does NOT exist:
    monkeypatch.chdir(tmp_path / "wiki")

    ok = memory.add_claim(
        "test claim", "wiki/src.md", "the exact evidence line", "high", ["t"]
    )

    assert ok is True
    claims = list((tmp_path / "wiki" / "claims").glob("claim-*.md"))
    assert len(claims) == 1


def test_add_decision_colliding_slugs_do_not_overwrite(tmp_path, monkeypatch):
    # Regression: two titles that slugify identically ("Use SQLite!" / "Use
    # SQLite?") wrote the same filename, silently overwriting the first.
    import scripts.memory as memory
    import scripts.safe_write as safe_write
    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)

    assert memory.add_decision("Use SQLite!", "reason A", "accepted", ["db"]) is True
    assert memory.add_decision("Use SQLite?", "reason B", "accepted", ["db"]) is True

    files = sorted(p.name for p in (tmp_path / "wiki" / "decisions").glob("*.md"))
    assert len(files) == 2, files
    bodies = [(tmp_path / "wiki" / "decisions" / f).read_text() for f in files]
    assert any("reason A" in b for b in bodies) and any("reason B" in b for b in bodies)


def test_untagged_question_writes_no_trailing_whitespace(tmp_path, monkeypatch):
    """Regression: an empty tag list left "- **Tags**: " with a trailing space,
    which `git diff --check` reports as a whitespace error on every commit."""
    import scripts.memory as memory
    import scripts.safe_write as safe_write

    # safe_write derives the allowed root from its own __file__, so redirect
    # that too or every tmp_path write is (correctly) refused.
    import scripts.question_routing as qr

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path.resolve())
    (tmp_path / "wiki").mkdir()

    memory.add_question("Is this tagged?", "no tags given", [])

    # An untagged question declares no public project, so it routes to the
    # PRIVATE file — publishability is declared, never inferred.
    content = (tmp_path / "wiki" / "open-questions.private.md").read_text()
    assert "- **Tags**:\n" in content
    assert not any(line.rstrip() != line for line in content.splitlines())


def test_tagged_question_still_lists_its_tags(tmp_path, monkeypatch):
    import scripts.memory as memory
    import scripts.safe_write as safe_write

    # safe_write derives the allowed root from its own __file__, so redirect
    # that too or every tmp_path write is (correctly) refused.
    import scripts.question_routing as qr

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path.resolve())
    (tmp_path / "wiki").mkdir()

    # "zurvan" is a public project, so this one DOES reach the tracked file.
    memory.add_question("Tagged?", "reason", ["zurvan", "tests"])

    content = (tmp_path / "wiki" / "open-questions.md").read_text()
    assert "- **Tags**: zurvan, tests\n" in content


# --- project attribution (publication control, not a label) -----------------
# On 2026-09-15 four decisions written from inside the Zurvan repo were stamped
# with an UNRELATED project, because another session had left that workspace
# selected in the global registry. public_repo_guard gates publication on that
# field, so the stamp decides whether a page may be published. These tests pin
# the invariant: a decision must never inherit a project merely because that
# project is "current" somewhere else.

def _registry(tmp_path, monkeypatch, projects, current):
    import json
    from scripts import project_registry
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps({"current": current, "projects": {
        n: {"path": str(p)} for n, p in projects.items()}}), encoding="utf-8")
    monkeypatch.setattr(project_registry, "get_registry_path", lambda: reg)
    return reg


def test_project_comes_from_the_working_root_not_the_selected_workspace(tmp_path, monkeypatch):
    from scripts.memory import resolve_project

    zurvan = tmp_path / "Zurvan"; zurvan.mkdir()
    otherproj = tmp_path / "OTHERPROJ"; otherproj.mkdir()
    _registry(tmp_path, monkeypatch, {"zurvan": zurvan, "otherproj": otherproj}, current="otherproj")

    # THE bug: registry current is otherproj, the work is happening in Zurvan.
    assert resolve_project(root=str(zurvan)) == "zurvan"
    # ...and a subdirectory of it resolves the same way.
    (zurvan / "scripts").mkdir()
    assert resolve_project(root=str(zurvan / "scripts")) == "zurvan"
    # Working in OTHERPROJ still resolves to otherproj — cross-repo write-back works.
    assert resolve_project(root=str(otherproj)) == "otherproj"


def test_unrecognised_root_fails_closed_to_unknown(tmp_path, monkeypatch):
    from scripts.memory import resolve_project
    from scripts.public_repo_guard import PUBLIC_PROJECTS

    known = tmp_path / "Zurvan"; known.mkdir()
    outside = tmp_path / "somewhere-else"; outside.mkdir()
    _registry(tmp_path, monkeypatch, {"zurvan": known}, current="zurvan")

    # Not inside any registered project: guess nothing, and block publication.
    assert resolve_project(root=str(outside)) == "unknown"
    assert "unknown" not in PUBLIC_PROJECTS


def test_explicit_project_wins_and_is_validated(tmp_path, monkeypatch):
    from scripts.memory import resolve_project

    zurvan = tmp_path / "Zurvan"; zurvan.mkdir()
    _registry(tmp_path, monkeypatch, {"zurvan": zurvan}, current="zurvan")

    assert resolve_project("simurgh", root=str(zurvan)) == "simurgh"
    # An unsafe name must not reach a path or a frontmatter field.
    assert resolve_project("../../etc", root=str(zurvan)) == "unknown"


def test_nested_projects_resolve_to_the_innermost(tmp_path, monkeypatch):
    from scripts.memory import resolve_project

    outer = tmp_path / "outer"; outer.mkdir()
    inner = outer / "inner"; inner.mkdir()
    _registry(tmp_path, monkeypatch, {"outer": outer, "inner": inner}, current="outer")

    # `current` may not override a project that actually contains the root.
    assert resolve_project(root=str(inner)) == "inner"
    assert resolve_project(root=str(outer)) == "outer"


def test_decision_frontmatter_carries_the_resolved_project(tmp_path, monkeypatch):
    from scripts import memory
    import scripts.safe_write as safe_write

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(safe_write, "get_project_root", lambda: tmp_path)
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    monkeypatch.setattr(memory, "append_to_log", lambda *a, **k: None)

    assert memory.add_decision("T", "r", "accepted", ["x"], project="zurvan") is True
    written = (tmp_path / "wiki" / "decisions" / "t.md").read_text(encoding="utf-8")
    assert 'project: "zurvan"' in written
