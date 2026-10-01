"""Z1b Task 5 tests: corpus identity (contract s3.4, defect D1) and DEV-side export (defect D4)."""
import hashlib
import inspect
import os
import stat
import sys
from pathlib import Path

import pytest

from scripts import build_snapshot
from scripts import z1b_corpus
from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.corpus import corpus_pages, corpus_sha256


def _t5_tree(root: Path, files=None) -> Path:
    files = files or {
        "wiki/decisions/a.md": b"alpha\n",
        "wiki/notes/b.md": b"beta\n",
        "wiki/notes/sub/c.md": b"gamma\n",
        "search.sqlite": b"\x00\x01SQLITE",
        "graph.sqlite": b"graph-bytes",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def _t5_copy(src: Path, dst: Path, *, mtime_shift: int, reverse: bool) -> Path:
    items = sorted(p for p in src.rglob("*") if p.is_file())
    if reverse:
        items.reverse()
    for p in items:
        q = dst / p.relative_to(src)
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(p.read_bytes())
        st = p.stat()
        os.utime(q, (st.st_atime + mtime_shift, st.st_mtime + mtime_shift))
    return dst


# ------------------------------------------------------------------ digest

def test_t5_digest_is_pinned_to_the_documented_construction(tmp_path):
    root = _t5_tree(tmp_path / "c", {"b.txt": b"two", "a.txt": b"one"})
    lines = b"".join(
        rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n"
        for rel, data in (("a.txt", b"one"), ("b.txt", b"two")))
    assert corpus_sha256(root) == hashlib.sha256(lines).hexdigest()


def test_t5_digest_stable_across_byte_identical_copy_with_new_mtimes_and_inodes(tmp_path):
    src = _t5_tree(tmp_path / "src")
    dst = _t5_copy(src, tmp_path / "dst", mtime_shift=86400 * 400, reverse=True)
    assert src.stat().st_ino != dst.stat().st_ino
    a, b = src / "search.sqlite", dst / "search.sqlite"
    assert a.stat().st_ino != b.stat().st_ino and a.stat().st_mtime != b.stat().st_mtime
    assert corpus_sha256(src) == corpus_sha256(dst)


def test_t5_digest_changes_when_one_byte_changes(tmp_path):
    root = _t5_tree(tmp_path / "c")
    before = corpus_sha256(root)
    f = root / "wiki/notes/b.md"
    f.write_bytes(b"betA\n")
    assert corpus_sha256(root) != before


def test_t5_digest_changes_on_rename_add_and_remove(tmp_path):
    root = _t5_tree(tmp_path / "c")
    base = corpus_sha256(root)
    (root / "wiki/notes/b.md").rename(root / "wiki/notes/b2.md")
    renamed = corpus_sha256(root)
    assert renamed != base
    (root / "wiki/notes/b2.md").rename(root / "wiki/notes/b.md")
    assert corpus_sha256(root) == base
    (root / "wiki/extra.md").write_bytes(b"")
    added = corpus_sha256(root)
    assert added != base
    (root / "wiki/extra.md").unlink()
    (root / "wiki/notes/b.md").unlink()
    assert corpus_sha256(root) != base


def test_t5_digest_distinguishes_path_from_content_boundary(tmp_path):
    a = _t5_tree(tmp_path / "a", {"x": b"y"})
    b = _t5_tree(tmp_path / "b", {"xy": b""})
    assert corpus_sha256(a) != corpus_sha256(b)


def test_t5_digest_is_independent_of_directory_listing_order(tmp_path, monkeypatch):
    root = _t5_tree(tmp_path / "c")
    expected = corpus_sha256(root)
    real = os.scandir

    class _Rev:
        def __init__(self, it):
            self._items = list(it)[::-1]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(self._items)

    monkeypatch.setattr(os, "scandir", lambda p: _Rev(real(p)))
    assert corpus_sha256(root) == expected


def test_t5_refuses_symlink_to_file_and_dir_and_dangling(tmp_path):
    for kind in ("file", "dir", "dangling"):
        root = _t5_tree(tmp_path / ("c-" + kind))
        link = root / "wiki/evil"
        target = {"file": root / "wiki/notes/b.md", "dir": root / "wiki/notes",
                  "dangling": root / "nope"}[kind]
        os.symlink(target, link)
        with pytest.raises(SealRefusal) as ei:
            corpus_sha256(root)
        assert ei.value.code == "CORPUS_MISMATCH"


def test_t5_refuses_fifo_inside_tree_without_blocking(tmp_path):
    root = _t5_tree(tmp_path / "c")
    os.mkfifo(root / "wiki/pipe")
    with pytest.raises(SealRefusal) as ei:
        corpus_sha256(root)
    assert ei.value.code == "CORPUS_MISMATCH"


def test_t5_refuses_socket_inside_tree(tmp_path, monkeypatch):
    import socket
    root = _t5_tree(tmp_path / "c")
    s = socket.socket(socket.AF_UNIX)
    monkeypatch.chdir(root)  # relative bind: the absolute tmp path exceeds the AF_UNIX limit
    try:
        s.bind("sock")
        with pytest.raises(SealRefusal) as ei:
            corpus_sha256(root)
        assert ei.value.code == "CORPUS_MISMATCH"
    finally:
        s.close()


def test_t5_refuses_missing_or_non_directory_root_and_symlinked_root(tmp_path):
    for bad in (tmp_path / "nope", _t5_tree(tmp_path / "t") / "search.sqlite"):
        with pytest.raises(SealRefusal) as ei:
            corpus_sha256(bad)
        assert ei.value.code == "CORPUS_MISMATCH"
    real = _t5_tree(tmp_path / "real")
    os.symlink(real, tmp_path / "linkroot")
    with pytest.raises(SealRefusal):
        corpus_sha256(tmp_path / "linkroot")


def test_t5_refuses_path_with_newline_or_nul_ambiguity(tmp_path):
    root = _t5_tree(tmp_path / "c", {"a\nb": b"x"})
    with pytest.raises(SealRefusal) as ei:
        corpus_sha256(root)
    assert ei.value.code == "CORPUS_MISMATCH"


def test_t5_swap_between_listing_and_open_is_not_followed(tmp_path, monkeypatch):
    # TOCTOU: a regular file replaced by a symlink after the lstat must be refused at open time.
    root = _t5_tree(tmp_path / "c", {"a.txt": b"one"})
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    real_open = os.open
    state = {"swapped": False}

    def _open(path, flags, *a, **k):
        if str(path).endswith("a.txt") and not state["swapped"]:
            state["swapped"] = True
            os.unlink(path)
            os.symlink(outside, path)
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", _open)
    with pytest.raises(SealRefusal) as ei:
        corpus_sha256(root)
    assert ei.value.code == "CORPUS_MISMATCH"


def test_t5_corpus_pages_lists_markdown_under_wiki_only(tmp_path):
    root = _t5_tree(tmp_path / "c")
    assert corpus_pages(root) == {"decisions/a.md", "notes/b.md", "notes/sub/c.md"}


def test_t5_corpus_pages_refuses_symlink_and_missing_wiki(tmp_path):
    root = _t5_tree(tmp_path / "c")
    os.symlink(root / "wiki/notes/b.md", root / "wiki/l.md")
    with pytest.raises(SealRefusal):
        corpus_pages(root)
    with pytest.raises(SealRefusal):
        corpus_pages(tmp_path / "empty-nonexistent")


def test_t5_hash_of_large_file_streams_not_slurps(tmp_path, monkeypatch):
    root = tmp_path / "c"
    root.mkdir()
    (root / "big").write_bytes(b"\xab" * (5 * 1024 * 1024))
    sizes = []
    real_read = os.read

    def _read(fd, n):
        sizes.append(n)
        return real_read(fd, n)

    monkeypatch.setattr(os, "read", _read)
    corpus_sha256(root)
    assert sizes and max(sizes) <= 8 * 1024 * 1024


# ------------------------------------------------------------------ export (DEV side)

class _T5Snap:
    def __init__(self, path):
        self.path = path
        self.snapshot_id = "z1b-corpus-test"
        self.released = False

    def release(self):
        self.released = True


def _t5_fake_capture(tmp_path, calls, *, boom=None):
    snapdir = _t5_tree(tmp_path / "snap")
    (snapdir / "wiki/traces").mkdir()
    (snapdir / "wiki/traces/t.json").write_bytes(b"derived")
    snap = _T5Snap(snapdir)

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return build_snapshot.BuildSnapshot(snap, 42, "ATOMIC", "m" * 8, "z1b-corpus",
                                            ("graph", "search", "wiki"))
    return fake, snap, snapdir


def test_t5_export_call_is_bound_to_the_real_capture_coherent_signature(tmp_path):
    calls = []
    fake, snap, _ = _t5_fake_capture(tmp_path, calls)
    z1b_corpus.prepare_corpus_export(tmp_path / "out", root=tmp_path / "live", capture=fake)
    assert len(calls) == 1
    args, kwargs = calls[0]
    bound = inspect.signature(build_snapshot.capture_coherent).bind(*args, **kwargs)
    assert bound.arguments["purpose"] == "z1b-corpus"
    assert tuple(bound.arguments["required"]) == ("wiki", "search", "graph")
    assert bound.arguments["root"] == tmp_path / "live"
    # the attributes the export reads exist on the real return type
    fields = set(build_snapshot.BuildSnapshot.__dataclass_fields__) | {"path", "release"}
    assert {"revision", "path", "release", "snapshot"} <= fields


def test_t5_export_default_capture_is_the_real_function():
    sig = inspect.signature(z1b_corpus.prepare_corpus_export)
    assert sig.parameters["capture"].default is None
    assert "capture_coherent" in inspect.getsource(z1b_corpus)


def test_t5_export_copies_never_hardlinks_and_releases_lease(tmp_path):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    out = tmp_path / "out"
    info = z1b_corpus.prepare_corpus_export(out, root=tmp_path / "live", capture=fake)
    assert snap.released is True
    for rel in ("search.sqlite", "graph.sqlite", "wiki/decisions/a.md", "wiki/notes/sub/c.md"):
        src, dst = snapdir / rel, out / rel
        assert dst.read_bytes() == src.read_bytes()
        assert dst.stat().st_ino != src.stat().st_ino, rel
        assert dst.stat().st_nlink == 1, rel
        assert src.stat().st_nlink == 1, rel
    assert info["revision"] == 42 and info["snapshot_id"] == "z1b-corpus-test"
    assert info["corpus_sha256"] == corpus_sha256(out)
    assert not (out / "wiki/traces").exists()


def test_t5_export_is_not_a_hardlink_even_if_source_is_hardlinked_elsewhere(tmp_path):
    # D4 regression: the live index is hardlinked into snapshots; a hardlinked export would share the inode
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    live = tmp_path / "live-search.sqlite"
    os.link(snapdir / "search.sqlite", live)
    out = tmp_path / "out"
    z1b_corpus.prepare_corpus_export(out, root=tmp_path / "live", capture=fake)
    assert (out / "search.sqlite").stat().st_ino != live.stat().st_ino
    assert (out / "search.sqlite").stat().st_nlink == 1
    live.write_bytes(b"mutated live index")
    assert (out / "search.sqlite").read_bytes() != b"mutated live index"


def test_t5_export_releases_lease_when_copy_fails(tmp_path, monkeypatch):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(z1b_corpus.shutil, "copyfile", _boom)
    with pytest.raises(OSError):
        z1b_corpus.prepare_corpus_export(tmp_path / "out", root=tmp_path / "live", capture=fake)
    assert snap.released is True


def test_t5_export_refuses_symlink_in_snapshot_and_nonempty_dest(tmp_path):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    os.symlink(snapdir / "search.sqlite", snapdir / "wiki/evil.md")
    with pytest.raises(SealRefusal) as ei:
        z1b_corpus.prepare_corpus_export(tmp_path / "out", root=tmp_path / "live", capture=fake)
    assert ei.value.code == "CORPUS_MISMATCH" and snap.released
    calls2 = []
    fake2, snap2, _ = _t5_fake_capture(tmp_path / "second", calls2)
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "x").write_bytes(b"y")
    with pytest.raises(SealRefusal):
        z1b_corpus.prepare_corpus_export(dest, root=tmp_path / "live", capture=fake2)
    assert calls2 == []


def test_t5_export_refuses_snapshot_missing_a_required_store(tmp_path):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    (snapdir / "graph.sqlite").unlink()
    with pytest.raises(SealRefusal) as ei:
        z1b_corpus.prepare_corpus_export(tmp_path / "out", root=tmp_path / "live", capture=fake)
    assert ei.value.code == "CORPUS_MISMATCH" and snap.released


def test_t5_export_files_are_not_world_writable(tmp_path):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    out = tmp_path / "out"
    z1b_corpus.prepare_corpus_export(out, root=tmp_path / "live", capture=fake)
    for p in out.rglob("*"):
        if p.is_file():
            assert not (p.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def test_t5_export_refuses_when_a_copy_turns_out_to_be_a_hardlink(tmp_path, monkeypatch):
    calls = []
    fake, snap, snapdir = _t5_fake_capture(tmp_path, calls)
    monkeypatch.setattr(z1b_corpus.shutil, "copyfile", lambda s, d: os.link(s, d))
    with pytest.raises(SealRefusal) as ei:
        z1b_corpus.prepare_corpus_export(tmp_path / "out", root=tmp_path / "live", capture=fake)
    assert ei.value.code == "CORPUS_MISMATCH"
    assert snap.released is True
