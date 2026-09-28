"""The publication protocol for a derived SQLite store.

build aside -> validate -> fsync file -> chmod 0444 -> os.replace -> fsync dir

A reader must observe the old valid store or the new valid store, never an
intermediate one, and a published file must refuse an in-place write — the
hazard that makes a hardlink snapshot silently stop being one (design §4.2).
"""

import os
import sqlite3
import stat

import pytest

from scripts import sqlite_publish as sp


def _db(path, rows=(1,), journal="delete"):
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA journal_mode={journal}")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    conn.commit()
    conn.close()
    return str(path)


def _rows(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT x FROM t ORDER BY x")]
    finally:
        conn.close()


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
def live(tmp_path):
    """A canonical store already published once, holding row 1."""
    canonical = str(tmp_path / "store.sqlite")
    build = sp.build_path_for(canonical)
    _db(build, rows=(1,))
    sp.publish(build, canonical)
    return canonical


# ── the happy path, stated as properties ─────────────────────────────────────

def test_publication_replaces_the_canonical_store(live):
    build = sp.build_path_for(live)
    _db(build, rows=(2,))
    sp.publish(build, live)
    assert _rows(live) == [2]
    assert not os.path.exists(build)


def test_a_published_store_is_read_only(live):
    assert stat.S_IMODE(os.stat(live).st_mode) == sp.PUBLISHED_MODE


def test_the_build_path_is_a_sibling_owned_by_this_process(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    assert sp.build_path_for(canonical) == f"{canonical}.building-{os.getpid()}"


def test_a_build_path_in_another_directory_is_refused(tmp_path):
    """`os.replace` is atomic only within one directory; a build elsewhere
    could be a copy, and a copy is not atomic."""
    (tmp_path / "other").mkdir()
    build = _db(tmp_path / "other" / "b.sqlite")
    with pytest.raises(sp.PublicationError):
        sp.publish(build, str(tmp_path / "store.sqlite"))


# ── gate 14: an in-place write to a published store fails loudly ─────────────

def test_an_in_place_sqlite_write_to_a_published_store_fails_loudly(live):
    conn = sqlite3.connect(live)          # default read-write open, as readers do
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO t VALUES (99)")
            conn.commit()
    finally:
        conn.close()
    assert _rows(live) == [1]


def test_an_in_place_byte_write_to_a_published_store_fails_loudly(live):
    with pytest.raises(PermissionError):
        open(live, "r+b")


def test_a_refused_write_leaves_no_sidecar(live):
    conn = sqlite3.connect(live)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (99)")
    finally:
        conn.close()
    assert [s for s in sp.SIDECARS if os.path.exists(live + s)] == []


def test_a_writable_published_store_is_reported(tmp_path):
    canonical = _db(tmp_path / "store.sqlite")          # made the old way
    with pytest.raises(sp.PublicationError, match="writable"):
        sp.assert_published_immutable(canonical)


# ── gate 15: publication refuses a store that is not self-contained ──────────

@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_a_build_with_a_sidecar_is_not_published(live, suffix):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,))
    with open(build + suffix, "wb") as f:
        f.write(b"mid-write")
    with pytest.raises(sp.PublicationError, match=suffix.lstrip("-")):
        sp.publish(build, live)
    assert _bytes(live) == before


def test_a_wal_mode_build_is_not_published(live):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,), journal="wal")
    for s in ("-wal", "-shm"):                # checkpointed and closed: no sidecars
        if os.path.exists(build + s):
            os.remove(build + s)
    with pytest.raises(sp.PublicationError, match="wal"):
        sp.publish(build, live)
    assert _bytes(live) == before


def test_a_corrupt_build_is_not_published(live):
    """Page 2's b-tree type byte. Measured 2026-09-28: 512 bytes of 0xff at
    offset 4196 landed in unused space and integrity_check said "ok" - a
    corruption test that corrupts nothing proves nothing."""
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=range(2000))
    with open(build, "r+b") as f:
        f.seek(4096)
        f.write(b"\x00")
    with pytest.raises(sp.PublicationError, match="not a readable database|integrity"):
        sp.publish(build, live, integrity=True)
    assert _bytes(live) == before


# ── kill points: a failure at any step leaves the old store valid ────────────

@pytest.mark.parametrize("point", ["_fsync_file", "_chmod", "_replace"])
def test_a_failure_before_the_swap_leaves_the_old_store(live, monkeypatch, point):
    before = _bytes(live)
    build = _db(sp.build_path_for(live), rows=(2,))

    def boom(*a, **k):
        raise OSError(28, f"injected at {point}")
    monkeypatch.setattr(sp, point, boom)

    with pytest.raises(OSError):
        sp.publish(build, live)
    assert _bytes(live) == before and _rows(live) == [1]


def test_a_failure_after_the_swap_leaves_the_new_store(live, monkeypatch):
    """The directory fsync runs after the rename. If it fails, the rename has
    already happened: the new store is live and complete, and the error still
    propagates, because durability was not confirmed."""
    build = _db(sp.build_path_for(live), rows=(2,))

    def dir_fsync_fails(path):
        raise OSError(5, "injected at _fsync_dir")
    monkeypatch.setattr(sp, "_fsync_dir", dir_fsync_fails)
    with pytest.raises(OSError):
        sp.publish(build, live)
    assert _rows(live) == [2]


def test_the_protocol_runs_in_order(live, monkeypatch):
    calls = []
    for name in ("assert_publishable", "_fsync_file", "_chmod", "_replace", "_fsync_dir"):
        real = getattr(sp, name)
        def spy(*a, _real=real, _name=name, **k):
            calls.append(_name)
            return _real(*a, **k)
        monkeypatch.setattr(sp, name, spy)
    sp.publish(_db(sp.build_path_for(live), rows=(2,)), live)
    assert calls == ["assert_publishable", "_fsync_file", "_chmod", "_replace", "_fsync_dir"]


# ── readers ──────────────────────────────────────────────────────────────────

def test_a_reader_holding_the_old_store_keeps_reading_it(live):
    held = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        assert [r[0] for r in held.execute("SELECT x FROM t")] == [1]
        sp.publish(_db(sp.build_path_for(live), rows=(2,)), live)
        assert [r[0] for r in held.execute("SELECT x FROM t")] == [1]
    finally:
        held.close()
    assert _rows(live) == [2]


# ── abandoned builds ─────────────────────────────────────────────────────────

def test_a_dead_builders_file_and_journal_are_swept(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    dead = f"{canonical}{sp.BUILD_SUFFIX}999999"
    _db(dead)
    open(dead + "-journal", "wb").close()
    assert sp.sweep_abandoned_builds(canonical) == set()
    assert not os.path.exists(dead) and not os.path.exists(dead + "-journal")


def test_a_live_builders_file_is_left_and_named(tmp_path):
    canonical = str(tmp_path / "store.sqlite")
    parent = os.getppid()
    mine = f"{canonical}{sp.BUILD_SUFFIX}{parent}"
    _db(mine)
    assert sp.sweep_abandoned_builds(canonical) == {parent}
    assert os.path.exists(mine)


def test_discarding_a_build_removes_its_sidecars_and_tolerates_absence(tmp_path):
    build = _db(tmp_path / f"s.sqlite{sp.BUILD_SUFFIX}{os.getpid()}")
    open(build + "-journal", "wb").close()
    os.chmod(build, 0o444)
    sp.discard_build(build)
    sp.discard_build(build)
    assert not os.path.exists(build) and not os.path.exists(build + "-journal")


def test_fsync_falls_back_where_full_fsync_does_not_exist(tmp_path, monkeypatch):
    """Linux has no F_FULLFSYNC. The fallback is a peer path, so it runs."""
    import types
    calls = []
    monkeypatch.setattr(sp, "fcntl", types.SimpleNamespace())
    monkeypatch.setattr(sp.os, "fsync", lambda fd: calls.append(fd))
    sp._fsync_file(_db(tmp_path / "f.sqlite"))
    assert len(calls) == 1


def test_full_fsync_is_used_where_it_exists(tmp_path, monkeypatch):
    """The effect, not the call order: removing the flush must turn a test red,
    and `test_the_protocol_runs_in_order` cannot see it (its spies record the
    name even when the body is gone - found by the gauntlet's dry run)."""
    import types
    seen = []
    fake = types.SimpleNamespace(F_FULLFSYNC=51,
                                 fcntl=lambda fd, op: seen.append(op))
    monkeypatch.setattr(sp, "fcntl", fake)
    sp._fsync_file(_db(tmp_path / "f.sqlite"))
    assert seen == [51]


def test_the_directory_is_fsynced(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sp.os, "fsync", lambda fd: calls.append(fd))
    sp._fsync_dir(str(tmp_path))
    assert len(calls) == 1
