"""check.sh gate [1/7] — Public Repo Guard.

The guard is the last thing standing between a private wiki and the public
remote, but it was invoked only as a shell step, so none of its pattern logic
was covered. These tests drive `main()` directly with a stubbed `git ls-files`.
"""

import subprocess

import pytest

import scripts.public_repo_guard as guard


def _fake_ls_files(monkeypatch, paths):
    """Make `git ls-files` return exactly `paths`."""
    def _run(cmd, capture_output, text, check):
        assert cmd == ["git", "ls-files"]
        return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(paths), stderr="")
    monkeypatch.setattr(guard.subprocess, "run", _run)


def test_guard_passes_when_no_private_paths_are_tracked(monkeypatch, capsys):
    _fake_ls_files(monkeypatch, ["README.md", "scripts/cli.py", "wiki/index.md"])

    guard.main()

    assert "Public repo guard passed." in capsys.readouterr().out


@pytest.mark.parametrize(
    "tracked",
    [
        "raw/papers/private.txt",
        "dist/snapshots/snap.json",
        "dist/backups/backup.tar",
        "evidence-packs/pack.json",
        "reports/report.md",
        "data/search.sqlite",
        "data/graph.sqlite",
        "data/graph_export.md",
        "data/graph_export.dot",
        "data/image_manifest.json",
        ".zurvan/state.json",
        "snapshots/old.json",
        "publications/paper.md",
        "dist/publications/paper.md",
        "wiki/sources/leaked.pdf",
        "wiki/sources/leaked.docx",
        "wiki/sources/",
    ],
)
def test_guard_fails_on_each_blocked_pattern(monkeypatch, capsys, tracked):
    """Every entry in BLOCKED_PATTERNS must actually block something."""
    _fake_ls_files(monkeypatch, ["README.md", tracked])

    with pytest.raises(SystemExit) as excinfo:
        guard.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Public repo guard failed." in out
    assert tracked in out


def test_guard_allows_gitkeep_inside_a_blocked_directory(monkeypatch, capsys):
    """`raw/.gitkeep` keeps the empty dir tracked and must not trip the guard."""
    _fake_ls_files(monkeypatch, ["raw/.gitkeep", "evidence-packs/.gitkeep"])

    guard.main()

    assert "passed" in capsys.readouterr().out


@pytest.mark.parametrize("allowed", ["docs/design.pdf", "tests/fixtures/sample.pdf"])
def test_guard_allows_pdfs_under_docs_and_tests(monkeypatch, capsys, allowed):
    """Extension patterns are exempt under docs/ and tests/ by design."""
    _fake_ls_files(monkeypatch, [allowed])

    guard.main()

    assert "passed" in capsys.readouterr().out


def test_guard_does_not_block_a_path_that_merely_contains_a_pattern(monkeypatch, capsys):
    """Bare-name patterns match exactly, so a lookalike path stays allowed."""
    _fake_ls_files(monkeypatch, ["docs/data/search.sqlite.md", "scripts/raw_utils.py"])

    guard.main()

    assert "passed" in capsys.readouterr().out


def test_guard_reports_every_offending_path_not_just_the_first(monkeypatch, capsys):
    _fake_ls_files(monkeypatch, ["raw/a.txt", "reports/b.md", "README.md"])

    with pytest.raises(SystemExit):
        guard.main()

    out = capsys.readouterr().out
    assert "raw/a.txt" in out and "reports/b.md" in out


def test_guard_exits_nonzero_when_git_is_unavailable(monkeypatch, capsys):
    """No git means the guard cannot prove the tree is clean — it must not pass."""
    def _boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "ls-files"])
    monkeypatch.setattr(guard.subprocess, "run", _boom)

    with pytest.raises(SystemExit) as excinfo:
        guard.main()

    assert excinfo.value.code == 1
    assert "Failed to run git ls-files" in capsys.readouterr().out


# ── ingested source pages (regression) ────────────────────────────────────────

@pytest.mark.parametrize(
    "tracked",
    [
        "wiki/sources/private-report.md",
        "wiki/sources/private-report.md.pre-repair.bak",
        "wiki/sources/private-report.md.orig",
        "wiki/sources/notes.txt",
        "wiki/sources/nested/deep-page.md",
    ],
)
def test_guard_blocks_anything_under_wiki_sources(monkeypatch, capsys, tracked):
    """Regression: a source page's full private text must never be trackable.

    .gitignore matched only wiki/sources/*.md and the guard's extension
    patterns only .pdf/.docx, so a sibling artefact such as
    <page>.md.pre-repair.bak was committable on a public remote.
    """
    _fake_ls_files(monkeypatch, [tracked])

    with pytest.raises(SystemExit) as excinfo:
        guard.main()

    assert excinfo.value.code == 1
    assert tracked in capsys.readouterr().out


def test_guard_still_allows_the_sources_gitkeep(monkeypatch, capsys):
    _fake_ls_files(monkeypatch, ["wiki/sources/.gitkeep"])

    guard.main()

    assert "passed" in capsys.readouterr().out


def test_guard_does_not_block_other_wiki_directories(monkeypatch, capsys):
    """Decisions and digests are tracked by design."""
    _fake_ls_files(monkeypatch, ["wiki/decisions/a.md", "wiki/digests/b.md"])

    guard.main()

    assert "passed" in capsys.readouterr().out
