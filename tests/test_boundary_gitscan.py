"""Plan 2 — staged post-image scanning, renames, and the commit gate.

Rows 12 and 13 of the gauntlet prove `scan_bytes` handles cross-hunk assembly and
renamed content. This module proves the GIT plumbing actually hands it the right
bytes: a diff-hunk scanner would see neither case.
"""
import subprocess

import pytest

from scripts.boundary import gitscan


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    return tmp_path


def _commit(repo, name, text):
    (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=repo, check=True)


def test_staged_blobs_returns_the_whole_post_image_not_the_hunk(tmp_path):
    """The load-bearing property. A hunk-local scanner misses cross-hunk assembly."""
    repo = _repo(tmp_path)
    _commit(repo, "a.md", "HEAD\n" + ("pad\n" * 50) + "TAIL\n")
    (repo / "a.md").write_text("HEAD-CHANGED\n" + ("pad\n" * 50) + "TAIL-CHANGED\n",
                               encoding="utf-8")
    subprocess.run(["git", "add", "a.md"], cwd=repo, check=True)
    blobs = gitscan.staged_blobs(repo)
    assert len(blobs) == 1
    path, change, data = blobs[0]
    assert path == "a.md"
    assert b"HEAD-CHANGED" in data and b"TAIL-CHANGED" in data
    assert data.count(b"pad") == 50, "the unchanged middle must be present"


def test_a_pure_rename_is_reported_with_its_content(tmp_path):
    """A rename produces no textual diff, so a diff-driven scanner sees nothing
    while protected bytes move into a tracked path."""
    repo = _repo(tmp_path)
    _commit(repo, "old.md", "PROTECTED CONTENT MOVED BY RENAME\n")
    subprocess.run(["git", "mv", "old.md", "new.md"], cwd=repo, check=True)
    blobs = gitscan.staged_blobs(repo)
    paths = {p for p, _c, _d in blobs}
    assert "new.md" in paths
    data = next(d for p, _c, d in blobs if p == "new.md")
    assert b"PROTECTED CONTENT MOVED BY RENAME" in data


def test_deletions_carry_no_post_image_and_are_skipped(tmp_path):
    """A deletion cannot leak anything INTO a sink; scanning it would be noise."""
    repo = _repo(tmp_path)
    _commit(repo, "gone.md", "text\n")
    subprocess.run(["git", "rm", "-q", "gone.md"], cwd=repo, check=True)
    assert all(p != "gone.md" for p, _c, _d in gitscan.staged_blobs(repo))


def test_binary_blobs_are_returned_as_bytes_not_decoded(tmp_path):
    repo = _repo(tmp_path)
    (repo / "b.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
    subprocess.run(["git", "add", "b.bin"], cwd=repo, check=True)
    data = next(d for p, _c, d in gitscan.staged_blobs(repo) if p == "b.bin")
    assert isinstance(data, bytes) and data == b"\x00\x01\x02\xff\xfe"


def test_sink_labels_are_non_exclusive_and_include_retrieval_for_indexed_paths():
    """`docs/x.md` is both published AND indexed; `scripts/x.py` only published.

    Deny-overrides-allow only helps if the labels are assigned correctly.
    """
    assert set(gitscan.sinks_for("docs/x.md")) == {"PUBLIC_ARTIFACT", "RETRIEVAL_CORPUS"}
    assert set(gitscan.sinks_for("wiki/x.md")) == {"PUBLIC_ARTIFACT", "RETRIEVAL_CORPUS"}
    assert set(gitscan.sinks_for("scripts/x.py")) == {"PUBLIC_ARTIFACT"}
    assert set(gitscan.sinks_for("docs/evaluation/retrieval/r.md")) == {"PUBLIC_ARTIFACT"}
