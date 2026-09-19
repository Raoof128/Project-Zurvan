"""Freezing bytes: the mechanism is chosen, never assumed, and always named.

A snapshot whose mechanism is unknown cannot be reasoned about. `clonefile` is
free and APFS-only; a byte copy is universal and, measured on this corpus,
25-37x slower and 147 MB heavier. Both are correct. Which one ran is a fact the
snapshot must carry, because it decides whether the bytes cost anything.
"""

import os
from pathlib import Path

import pytest

from scripts import immutable_bytes as ib


def _raises_enotsup(source, destination):
    raise OSError(45, "Operation not supported")


def test_a_cloned_file_has_its_own_inode(tmp_path):
    """The whole point. Same bytes, different inode, so editing the live file
    cannot reach the frozen one."""
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    destination = tmp_path / "a-frozen.md"

    ib.clone_file(source, destination)

    assert destination.read_text(encoding="utf-8") == "alpha"
    assert os.stat(destination).st_ino != os.stat(source).st_ino


def test_the_mechanism_is_reported(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")

    mechanism = ib.clone_file(source, tmp_path / "b.md")

    assert mechanism in (ib.CLONEFILE, ib.COPY)


def test_the_fallback_produces_identical_bytes(tmp_path, monkeypatch):
    """Fault injection, not hardware. This machine has one APFS volume, so
    EXDEV cannot be produced on demand — and a fallback that is never executed
    is a comment. Forcing the syscall to fail is the only deterministic way to
    run the path that portability depends on."""
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    source = tmp_path / "a.md"
    source.write_text("alpha" * 1000, encoding="utf-8")
    destination = tmp_path / "b.md"

    mechanism = ib.clone_file(source, destination)

    assert mechanism == ib.COPY
    assert destination.read_bytes() == source.read_bytes()


def test_the_fallback_preserves_mtime_and_mode(tmp_path, monkeypatch):
    """`copy2`, not `copy`. The authoritative manifest is (relpath, size,
    mtime_ns, ino) — a fallback that reset mtime would make every snapshot
    disagree with the manifest that described it."""
    monkeypatch.setattr(ib, "_clonefile", _raises_enotsup)
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    os.chmod(source, 0o640)
    before = os.stat(source)
    destination = tmp_path / "b.md"

    ib.clone_file(source, destination)

    after = os.stat(destination)
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_mode == before.st_mode


def test_a_failure_that_is_not_about_portability_is_not_grounds_to_copy(
        tmp_path, monkeypatch):
    """`except OSError` would turn a full disk, an I/O error or a permission
    failure into permission to copy — so a broken disk becomes a slow success
    instead of a loud failure. Only ENOTSUP / EOPNOTSUPP / EXDEV / ENOSYS mean
    "this filesystem cannot clone"."""
    import errno as _errno

    def _raises_enospc(source, destination):
        raise OSError(_errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ib, "_clonefile", _raises_enospc)
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")

    with pytest.raises(ib.ImmutableBytesError) as excinfo:
        ib.clone_file(source, tmp_path / "b.md")

    assert "not a portability failure" in str(excinfo.value)
    assert not (tmp_path / "b.md").exists()


def test_an_existing_destination_is_refused(tmp_path):
    """`clonefile(2)` returns EEXIST and the fallback must agree. Overwriting a
    snapshot component silently is how two captures become one."""
    source = tmp_path / "a.md"
    source.write_text("alpha", encoding="utf-8")
    destination = tmp_path / "b.md"
    destination.write_text("occupied", encoding="utf-8")

    with pytest.raises(ib.ImmutableBytesError):
        ib.clone_file(source, destination)

    assert destination.read_text(encoding="utf-8") == "occupied"


def test_a_missing_source_fails_closed(tmp_path):
    with pytest.raises(ib.ImmutableBytesError):
        ib.clone_file(tmp_path / "nope.md", tmp_path / "out.md")


def test_clone_support_is_probed_not_guessed(tmp_path):
    """Reported per directory, because support is a property of the filesystem
    the destination lands on, not of the operating system."""
    assert isinstance(ib.clone_supported(tmp_path), bool)


def test_the_support_probe_leaves_nothing_behind(tmp_path):
    """A probe file left in a destination directory would be captured by the
    next snapshot taken there."""
    before = set(tmp_path.iterdir())

    ib.clone_supported(tmp_path)

    assert set(tmp_path.iterdir()) == before


def test_the_support_probe_cannot_delete_a_file_it_did_not_create(tmp_path):
    """A predictable pid-based probe name could collide with a stale file from
    an earlier crash, and the cleanup would then destroy someone else's data.
    Exclusive creation makes the collision impossible."""
    bystander = tmp_path / ".clone-probe-bystander"
    bystander.write_text("not mine", encoding="utf-8")

    ib.clone_supported(tmp_path)

    assert bystander.read_text(encoding="utf-8") == "not mine"
