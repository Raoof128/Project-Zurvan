"""Gate 16: snapshot bytes survive every way a live file can change.

Design §4.5 requires five patterns, because proving inode separation proves too
little: copy-on-write survives an in-place edit obviously, and the interesting
cases are the ones that replace or remove the directory entry rather than the
contents. `os.replace` is how Obsidian and every careful editor writes.

**Measured, and it inverts part of the design's reasoning.** Running this file
against a deliberately WRONG mechanism — a hardlink, which shares the inode —
fails only three of the seven tests:

    modify in place        FAILS under a hardlink   <- discriminating
    truncate               FAILS under a hardlink   <- discriminating
    write through snapshot FAILS under a hardlink   <- discriminating
    rename                 passes under a hardlink
    delete                 passes under a hardlink
    atomic replace         passes under a hardlink
    delete the whole tree  passes under a hardlink

§4.5 argues that rename/delete/atomic-replace are the patterns a reviewer
cannot predict, and they are — but they pass under BOTH mechanisms, because
they change the directory entry and leave the inode alone. Their value is
proving a snapshot survives directory churn, which is real and different. The
power to distinguish a correct mechanism from an incorrect one lives entirely
in the three that touch the inode. Anyone trimming this file should trim from
the other four, never from those.
"""

import os
import shutil
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib

BODY = "---\ntitle: A\n---\n\nthe original bytes\n"


@pytest.fixture
def frozen(tmp_path):
    """A live wiki and a frozen clone of it. Returns (live, snapshot)."""
    live = tmp_path / "wiki"
    (live / "decisions").mkdir(parents=True)
    (live / "decisions" / "a.md").write_text(BODY, encoding="utf-8")
    snapshot = tmp_path / "frozen"
    ib.clone_tree(live, snapshot)
    return live, snapshot


def _snapshot_page(snapshot):
    return snapshot / "decisions" / "a.md"


def test_modifying_the_live_file_in_place_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    (live / "decisions" / "a.md").write_text("rewritten\n", encoding="utf-8")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_renaming_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    os.rename(live / "decisions" / "a.md", live / "decisions" / "renamed.md")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_deleting_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    (live / "decisions" / "a.md").unlink()
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_atomically_replacing_the_live_file_does_not_reach_the_snapshot(frozen):
    """The pattern that matters most: `safe_write` and every careful external
    editor write a temp file and `os.replace` it, which swaps the directory
    entry for a NEW inode rather than touching the old one."""
    live, snapshot = frozen
    temp = live / "decisions" / "a.md.tmp"
    temp.write_text("replaced\n", encoding="utf-8")
    os.replace(temp, live / "decisions" / "a.md")
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_truncating_the_live_file_does_not_reach_the_snapshot(frozen):
    live, snapshot = frozen
    with open(live / "decisions" / "a.md", "w", encoding="utf-8"):
        pass
    assert (live / "decisions" / "a.md").stat().st_size == 0
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_deleting_the_whole_live_tree_does_not_reach_the_snapshot(frozen):
    """The sixth pattern, beyond the design's five. A snapshot whose source
    directory is gone must still be readable, or a rebuild consuming it would
    fail for a reason unrelated to its own correctness."""
    live, snapshot = frozen
    shutil.rmtree(live)
    assert _snapshot_page(snapshot).read_text(encoding="utf-8") == BODY


def test_the_snapshot_is_not_writable_through_the_live_path(frozen):
    """Symmetry check: the matrix proves live -> snapshot isolation. This
    proves the inodes really are distinct rather than the reads being cached."""
    live, snapshot = frozen
    _snapshot_page(snapshot).write_text("tampered\n", encoding="utf-8")
    assert (live / "decisions" / "a.md").read_text(encoding="utf-8") == BODY


def test_the_matrix_would_catch_a_hardlink_mechanism(tmp_path):
    """A test that cannot fail proves nothing.

    A hardlink SHARES the inode, so an in-place edit reaches the "snapshot".
    This runs the first matrix pattern against that wrong mechanism and asserts
    it leaks — which is what gives the seven tests above their meaning.
    """
    live = tmp_path / "live"
    live.mkdir()
    page = live / "a.md"
    page.write_text("original", encoding="utf-8")
    hardlinked = tmp_path / "hardlinked.md"
    os.link(page, hardlinked)

    page.write_text("rewritten", encoding="utf-8")

    assert hardlinked.read_text(encoding="utf-8") == "rewritten", (
        "a hardlink must leak the edit; if it did not, this file's other "
        "seven tests are passing for a reason that has nothing to do with "
        "the clone mechanism")
