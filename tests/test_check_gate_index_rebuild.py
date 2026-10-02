"""Navigation-index rebuild.

`rebuild_index` decides which page *filenames* appear in the tracked
`wiki/index.md`. That makes it a privacy boundary, not just formatting: leaking
the filenames of gitignored pages (wiki/sources, AutoConcept-*, Claim-*, note-*)
is how private document titles previously reached the public remote. These
tests cover the categorisation, the gitignore exclusion, and the fail-open path.
"""

import subprocess

import pytest

import scripts.rebuild_index as rebuild_index_mod
from scripts.rebuild_index import _gitignored, rebuild_index


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    """`index.md` is knowledge, so rebuild_index writes it through safe_write
    inside an operation (2026-10-01); each test gets a throwaway store and one."""
    from scripts import knowledge_revision as kr
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir(exist_ok=True)
    kr.ensure_schema(tmp_path)
    with kr.operation(writer="test", root=tmp_path):
        yield


def _page(wiki, relpath, content="---\ntitle: t\n---\n"):
    path = wiki / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _stub_gitignore(monkeypatch, ignored_suffixes):
    """Make `git check-ignore --stdin` report the paths ending in a given suffix.

    rebuild_index feeds git absolute paths, and git echoes back the same
    spelling it was given, so the stub matches on suffix and returns the input
    line verbatim rather than a relative path of its own.
    """
    real_run = subprocess.run

    def _run(cmd, *args, **kwargs):
        # Only `git check-ignore` is stubbed. `rebuild_index_mod.subprocess` IS the global module, and since
        # the rebuild writes through the barrier other callers (with their own timeout=) run during the test.
        if list(cmd[:2]) != ["git", "check-ignore"]:
            return real_run(cmd, *args, **kwargs)
        hits = [
            line for line in kwargs["input"].splitlines()
            if any(line.endswith(suffix) for suffix in ignored_suffixes)
        ]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(hits), stderr="")
    monkeypatch.setattr(rebuild_index_mod.subprocess, "run", _run)


def test_index_lists_pages_under_their_category_heading(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    _page(wiki, "decisions/use-sqlite.md")
    _page(wiki, "concepts/memory.md")
    _stub_gitignore(monkeypatch, set())

    rebuild_index(str(wiki))

    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert "## Decisions" in index
    assert "- [use-sqlite.md](decisions/use-sqlite.md)" in index
    assert "## Concepts" in index
    assert "- [memory.md](concepts/memory.md)" in index


def test_index_always_carries_the_navigation_header(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _stub_gitignore(monkeypatch, set())

    rebuild_index(str(wiki))

    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert "# Wiki Index" in index
    for link in ("overview.md", "open-questions.md", "log.md"):
        assert link in index


def test_gitignored_pages_are_excluded_from_the_index(tmp_path, monkeypatch):
    """Regression: private page filenames must never reach the tracked index."""
    wiki = tmp_path / "wiki"
    _page(wiki, "sources/confidential-report.md")
    _page(wiki, "decisions/public-choice.md")
    _stub_gitignore(monkeypatch, {"sources/confidential-report.md"})

    rebuild_index(str(wiki))

    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert "confidential-report.md" not in index
    assert "public-choice.md" in index


def test_a_category_with_no_visible_pages_gets_no_heading(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    _page(wiki, "sources/only-private.md")
    _stub_gitignore(monkeypatch, {"sources/only-private.md"})

    rebuild_index(str(wiki))

    assert "## Sources" not in (wiki / "index.md").read_text(encoding="utf-8")


def test_pages_are_listed_in_sorted_order(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    for name in ("zebra.md", "alpha.md", "middle.md"):
        _page(wiki, f"decisions/{name}")
    _stub_gitignore(monkeypatch, set())

    rebuild_index(str(wiki))

    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert index.index("alpha.md") < index.index("middle.md") < index.index("zebra.md")


def test_non_markdown_files_are_not_indexed(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    _page(wiki, "decisions/notes.txt", "not markdown")
    _stub_gitignore(monkeypatch, set())

    rebuild_index(str(wiki))

    assert "notes.txt" not in (wiki / "index.md").read_text(encoding="utf-8")


def test_missing_category_directories_are_skipped_without_error(tmp_path, monkeypatch):
    """A fresh wiki has none of the category dirs yet."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _stub_gitignore(monkeypatch, set())

    rebuild_index(str(wiki))

    assert (wiki / "index.md").exists()


# ── _gitignored helper ────────────────────────────────────────────────────────

def test_gitignored_returns_empty_for_no_candidates():
    """Short-circuits before spawning git."""
    assert _gitignored([], ".") == set()


def test_gitignored_fails_open_when_git_is_missing(monkeypatch):
    """Fail-open lists nothing extra rather than crashing the rebuild."""
    def _boom(*_args, **_kwargs):
        raise FileNotFoundError("git not found")
    monkeypatch.setattr(rebuild_index_mod.subprocess, "run", _boom)

    assert _gitignored(["wiki/sources/a.md"], ".") == set()


def test_index_rebuild_survives_git_being_unavailable(tmp_path, monkeypatch):
    """Fail-open means every page is listed — the index still rebuilds."""
    wiki = tmp_path / "wiki"
    _page(wiki, "decisions/a.md")

    def _boom(*_args, **_kwargs):
        raise FileNotFoundError("git not found")
    monkeypatch.setattr(rebuild_index_mod.subprocess, "run", _boom)

    rebuild_index(str(wiki))

    assert "a.md" in (wiki / "index.md").read_text(encoding="utf-8")
