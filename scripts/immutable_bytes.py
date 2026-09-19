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
