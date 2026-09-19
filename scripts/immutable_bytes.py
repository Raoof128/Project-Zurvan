#!/usr/bin/env python3
"""Freeze bytes by the cheapest mechanism that is legal, and say which one.

Three mechanisms, in descending order of preference and ascending order of
availability:

    clonefile(2)   APFS copy-on-write. Measured 43-72 ms for wiki/ (4,439
                   files, 135 MB) and 0.33 ms for a 13.2 MB SQLite file. Zero
                   DATA blocks; about 4 MB of directory metadata for the tree.
    byte copy      Universal, and honestly slower: 1,779 ms and 147 MB really
                   allocated for the same tree, 25-37x the clone.
    hardlink       O(1), and legal ONLY for a store whose published inode is
                   never modified in place (design §4.2). This module offers
                   the constant; deciding whether a given store qualifies is
                   the caller's judgement, not this module's.

This module knows nothing about Zurvan's stores. A module that both chose
mechanisms and knew which stores exist would need re-testing every time a store
is added.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
import tempfile
from pathlib import Path

CLONEFILE = "clonefile"
COPY = "copy"
HARDLINK = "hardlink"

#: The ONLY failures that justify falling back to a byte copy. `except OSError`
#: would turn every failure into permission to copy — ENOSPC, EIO, EACCES and
#: anything corruption-shaped included — so a disk filling up or a read error
#: would quietly become a slow success instead of a loud failure. Portability is
#: a reason to copy; a broken disk is not.
_PORTABILITY_ERRNOS = frozenset(
    e for e in (
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "EXDEV", None),
        getattr(errno, "ENOSYS", None),
    ) if e is not None
)


class ImmutableBytesError(RuntimeError):
    """Bytes could not be frozen. Never swallowed: a capture that silently
    skipped a component would describe a corpus that never existed."""


def _load_clonefile():
    """`clonefile(2)` via ctypes, or None where it does not exist.

    A subprocess (`cp -c`) would pay a fork per call. The design's 0.55 s
    figure for this tree is consistent with that shape; one syscall on the
    directory measures 43-72 ms.
    """
    try:
        lib = ctypes.CDLL(ctypes.util.find_library("System"), use_errno=True)
        fn = lib.clonefile
    except (OSError, AttributeError, TypeError):
        return None
    fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    fn.restype = ctypes.c_int
    return fn


_CLONEFILE = _load_clonefile()


def _clonefile(source, destination) -> None:
    """Raise OSError with a real errno, or return. Patched in tests to force
    the fallback, which is why it is a module-level function and not inline."""
    if _CLONEFILE is None:
        raise OSError(errno.ENOTSUP, "clonefile unavailable on this platform")
    if _CLONEFILE(os.fsencode(str(source)), os.fsencode(str(destination)), 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(source))


def _not_portability(exc: OSError, what: str) -> ImmutableBytesError:
    name = errno.errorcode.get(exc.errno, exc.errno)
    return ImmutableBytesError(
        f"clone of {what} failed with {name} ({exc.strerror}); this is not a "
        "portability failure, so it is not grounds to copy")


def clone_supported(destination_directory) -> bool:
    """Probe the DESTINATION filesystem, never guess.

    Support belongs to where the bytes land, and one machine can host several
    filesystems. Never probe inside the authoritative corpus: a probe file
    appearing under `wiki/` would register as a corpus change in the manifest.

    `tempfile.mkstemp` creates exclusively, so this can never unlink a
    pre-existing file left by an earlier crash — a predictable pid-based name
    could, and the cleanup would then destroy someone else's data.
    """
    if _CLONEFILE is None:
        return False
    directory = Path(destination_directory)
    probe = clone = None
    try:
        handle, name = tempfile.mkstemp(dir=directory, prefix=".clone-probe-")
        os.close(handle)
        probe = Path(name)
        clone = probe.with_name(probe.name + ".clone")
        _clonefile(probe, clone)
        return True
    except OSError:
        return False
    finally:
        for path in (probe, clone):
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass


def clone_file(source, destination) -> str:
    """Freeze one file. Returns the mechanism used.

    Refuses an existing destination: `clonefile(2)` returns EEXIST and the
    fallback must agree, or the two paths would differ in the one case that
    turns two captures into one.
    """
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise ImmutableBytesError(f"no such source: {source}")
    if destination.exists():
        raise ImmutableBytesError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _clonefile(source, destination)
        return CLONEFILE
    except OSError as exc:
        if exc.errno not in _PORTABILITY_ERRNOS:
            raise _not_portability(exc, str(source)) from exc
        try:
            shutil.copy2(source, destination)
        except OSError as copy_exc:
            raise ImmutableBytesError(
                f"could not freeze {source} -> {destination}: {copy_exc}"
            ) from copy_exc
        return COPY


def assert_regular_tree(source) -> None:
    """Regular files and directories only. Nothing else is replayable.

    A symlink inside a snapshot points at a path OUTSIDE it, which stays
    mutable — so the snapshot would contain an entry that is neither immutable
    nor self-contained, and the immutability matrix would pass while the thing
    it guards leaked. Sockets, fifos and devices are not content at all.

    Measured 2026-09-19: the live `wiki/` contains 4,439 entries and zero
    symlinks and zero non-regular files, so this refuses nothing that exists
    today. It locks in a property that currently holds rather than fixing a
    live defect — and Z0 deliberately chooses rejection over dereference
    semantics, because dereferencing would silently pull bytes from outside the
    corpus into a snapshot that claims to be the corpus.
    """
    source = Path(source)
    offenders = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            offenders.append(f"{path.relative_to(source)} (symlink)")
        elif not (path.is_file() or path.is_dir()):
            offenders.append(f"{path.relative_to(source)} (not a regular file)")
    if offenders:
        raise ImmutableBytesError(
            f"{source} contains {len(offenders)} entr(ies) that cannot be "
            f"frozen immutably: {offenders[:5]}")


def clone_tree(source, destination) -> str:
    """Freeze a whole directory. Returns the mechanism used.

    `clonefile(2)` on a DIRECTORY clones it recursively in one syscall, which
    is why this is not a loop over `clone_file`: measured 43-72 ms for 4,439
    files against 1,779 ms for `shutil.copytree` of the same tree.

    The clone is deliberately a superset of the authoritative manifest.
    `wiki/traces/` is generated output that bypasses `safe_write`, so it is
    excluded from the manifest — but it is captured, because a snapshot that
    excluded it could not answer a forensic question later and the clone costs
    nothing extra (design §4.1).
    """
    source = Path(source)
    destination = Path(destination)
    if not source.is_dir():
        raise ImmutableBytesError(f"no such directory: {source}")
    if destination.exists():
        raise ImmutableBytesError(f"destination already exists: {destination}")
    assert_regular_tree(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _clonefile(source, destination)
        return CLONEFILE
    except OSError as exc:
        if exc.errno not in _PORTABILITY_ERRNOS:
            raise _not_portability(exc, f"tree {source}") from exc
        try:
            # `symlinks=False` is deliberate and `assert_regular_tree` above has
            # already refused any, so this never dereferences anything. Both
            # statements are needed: the assertion is the contract, this is the
            # belt that makes a future assertion bug fail safe rather than
            # silently capture a mutable path.
            shutil.copytree(source, destination, symlinks=False)
        except OSError as copy_exc:
            raise ImmutableBytesError(
                f"could not freeze tree {source} -> {destination}: {copy_exc}"
            ) from copy_exc
        return COPY
