"""Stability and awareness are different questions.

`M_before == M_after` proves these bytes formed one stable filesystem state.
Comparing the live manifest against the digest a revision recorded proves
Zurvan knew about that state. A finished Obsidian edit passes the first
perfectly and fails the second, which is the only reason the second exists.
"""

import os
from pathlib import Path

import pytest

from scripts import knowledge_manifest as km


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "wiki").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "a.md").write_text("alpha\n")
    (tmp_path / "wiki" / "b.md").write_text("beta\n")
    return tmp_path


def test_an_unchanged_tree_hashes_the_same_twice(root):
    assert km.authoritative_manifest(root) == km.authoritative_manifest(root)


def test_editing_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "a.md").write_text("alpha changed\n")
    assert km.authoritative_manifest(root) != before


def test_adding_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "c.md").write_text("gamma\n")
    assert km.authoritative_manifest(root) != before


def test_deleting_a_file_changes_the_manifest(root):
    before = km.authoritative_manifest(root)
    (root / "wiki" / "b.md").unlink()
    assert km.authoritative_manifest(root) != before


def test_an_atomic_replace_changes_the_manifest_through_the_inode(root):
    """Same size, same mtime, new inode. Without `st_ino` this is invisible,
    and an atomic replace is exactly how a careful external editor writes."""
    before = km.authoritative_manifest(root)
    live = root / "wiki" / "a.md"
    st = os.stat(live)
    tmp = root / "wiki" / "a.md.tmp"
    tmp.write_text("alpha\n")
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, live)
    assert km.authoritative_manifest(root) != before


def test_a_generated_trace_does_not_change_the_manifest(root):
    before = km.authoritative_manifest(root)
    traces = root / "wiki" / "traces"
    traces.mkdir()
    (traces / "trace-1.json").write_text('{"id": 1}')
    assert km.authoritative_manifest(root) == before


def test_an_unreadable_file_fails_closed_rather_than_vanishing(root):
    """The promoted-to-correctness rule. Skipping a file that cannot be
    stat'ed produces a digest over whatever happened to work, and two
    different corpora then compare equal."""
    victim = str((root / "wiki" / "a.md").resolve())
    real_stat = os.stat

    def exploding_stat(path, *a, **k):
        if str(path) == victim:
            raise PermissionError("denied")
        return real_stat(path, *a, **k)

    import unittest.mock as mock
    with mock.patch.object(km.os, "stat", exploding_stat):
        with pytest.raises(km.ManifestUnavailable):
            km.authoritative_manifest(root)


def test_a_missing_wiki_is_an_empty_manifest_not_an_error(root):
    """A store with no wiki yet is a legitimate state, distinct from a store
    whose wiki cannot be read."""
    import shutil
    shutil.rmtree(root / "wiki")
    assert km.authoritative_manifest(root) == km.manifest_sha([], root)


def test_the_digest_depends_on_the_scope_version(root):
    """Every stored revision's digest is a property of the scope. Changing the
    scope without changing the digest would silently reinterpret history."""
    from scripts import knowledge_scope as ks
    before = km.authoritative_manifest(root)
    original = ks.SCOPE_VERSION
    try:
        ks.SCOPE_VERSION = original + 1
        assert km.authoritative_manifest(root) != before
    finally:
        ks.SCOPE_VERSION = original
