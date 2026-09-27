import pytest

from scripts import question_routing as qr


@pytest.fixture(autouse=True)
def _fixed_project_lists(monkeypatch):
    # Test the MECHANISM, never the current contents of the private list —
    # otherwise the suite breaks whenever a project is added or removed.
    import scripts.public_repo_guard as guard
    monkeypatch.setattr(guard, "PUBLIC_PROJECTS", frozenset({"zurvan", "simurgh"}))
    monkeypatch.setattr(guard, "PRIVATE_PROJECT_TAGS", frozenset({"acme-private", "other-private"}))


def test_only_a_declared_public_project_is_publishable():
    assert qr.is_publishable(["zurvan"]) is True
    assert qr.is_publishable(["simurgh", "topic"]) is True


def test_undeclared_questions_are_private():
    # wiki/open-questions.md is TRACKED on a public remote. Publishability must
    # be declared, never inferred — the safe direction is to withhold.
    assert qr.is_publishable([]) is False
    assert qr.is_publishable(None) is False
    assert qr.is_publishable(["some-unlisted-project"]) is False


def test_a_private_tag_vetoes_even_alongside_a_public_one():
    # The failure this whole split exists to prevent: one private question
    # made the entire tracked file unpublishable.
    assert qr.is_publishable(["zurvan", "acme-private"]) is False
    assert qr.is_publishable(["acme-private"]) is False


def test_tags_are_matched_case_and_whitespace_insensitively():
    assert qr.is_publishable(["  ZURVAN  "]) is True
    assert qr.is_publishable(["Acme-Private", "zurvan"]) is False


def test_routing_picks_the_matching_file(tmp_path, monkeypatch):
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    assert qr.questions_path(["zurvan"]).endswith("open-questions.md")
    assert qr.questions_path(["acme-private"]).endswith("open-questions.private.md")
    assert qr.questions_path([]).endswith("open-questions.private.md")


def _op(tmp_path, monkeypatch):
    """`add_question` refuses to write outside an operation (2026-09-28), so
    each test declares one over its throwaway store, as the CLI and MCP do."""
    from scripts import knowledge_revision as kr
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(exist_ok=True)
    return kr.operation(writer="test", root=tmp_path)


def test_add_question_writes_to_the_private_file_for_a_private_tag(tmp_path, monkeypatch):
    import scripts.memory as memory

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    # safe_write validates against its OWN project root, so redirect it too or
    # every write is refused as outside the repo.
    import scripts.safe_write as sw
    monkeypatch.setattr(sw, "get_project_root", lambda: tmp_path.resolve())
    (tmp_path / "wiki").mkdir()

    with _op(tmp_path, monkeypatch):
        assert memory.add_question("private q", "because", ["acme-private"]) is True

    private = (tmp_path / "wiki" / "open-questions.private.md")
    assert private.exists() and "private q" in private.read_text(encoding="utf-8")
    assert not (tmp_path / "wiki" / "open-questions.md").exists(), \
        "a private question must never touch the tracked file"


def test_add_question_writes_to_the_tracked_file_for_a_public_tag(tmp_path, monkeypatch):
    import scripts.memory as memory

    monkeypatch.setattr(memory, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    import scripts.safe_write as sw
    monkeypatch.setattr(sw, "get_project_root", lambda: tmp_path.resolve())
    (tmp_path / "wiki").mkdir()

    with _op(tmp_path, monkeypatch):
        assert memory.add_question("public q", "because", ["zurvan"]) is True

    tracked = (tmp_path / "wiki" / "open-questions.md")
    assert tracked.exists() and "public q" in tracked.read_text(encoding="utf-8")
    assert not (tmp_path / "wiki" / "open-questions.private.md").exists()


def test_count_covers_both_halves(tmp_path, monkeypatch):
    monkeypatch.setattr(qr, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "open-questions.md").write_text("## Q: a\n## Q: b\n", encoding="utf-8")
    (tmp_path / "wiki" / "open-questions.private.md").write_text("## Q: c\n", encoding="utf-8")

    assert qr.count_questions() == 3


def test_the_private_file_is_gitignored_and_guard_blocked():
    import subprocess

    from scripts.public_repo_guard import BLOCKED_PATTERNS

    assert any("open-questions.private" in p for p in BLOCKED_PATTERNS)
    r = subprocess.run(["git", "check-ignore", "-q", "wiki/open-questions.private.md"])
    assert r.returncode == 0, "the private questions file must be gitignored"
