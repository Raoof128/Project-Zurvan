"""Publish a derived SQLite store: built aside, validated, made immutable, swapped in.

    build   <canonical>.building-<pid>
    validate            self-contained (no sidecar, not WAL), integrity
    fsync the file      so the bytes are durable before anyone can see them
    chmod 0444          so the published inode refuses an in-place write
    os.replace()        the only mutation of the canonical path
    fsync the directory so the rename itself is durable

A reader observes the old valid store or the new one, never an intermediate.
A hardlink snapshot is legal only because the published inode is never
modified in place (design §4.2); `0444` is what turns that from a convention
into an error. Bounded: the owner can chmod it back and root ignores it, so
this stops an accidental in-place write and is not a security boundary.

Knows nothing about which stores exist. `rebuild_search_index` and
`graph_build` are its callers.
"""

import fcntl
import glob
import os
import sqlite3
import stat

BUILD_SUFFIX = ".building-"
SIDECARS = ("-journal", "-wal", "-shm")
PUBLISHED_MODE = 0o444

# Indirections, so a test can inject a failure at each step without patching
# the `os` module for the whole process.
_chmod = os.chmod
_replace = os.replace


class PublicationError(RuntimeError):
    """A store could not be published. The canonical file is untouched."""


def build_path_for(canonical) -> str:
    return f"{canonical}{BUILD_SUFFIX}{os.getpid()}"


def sweep_abandoned_builds(canonical) -> set:
    """Delete build files left by builds that were killed outright.

    A SIGKILL cannot clean up after itself, so these accumulate. Files owned by
    a LIVE process are left alone and their pids returned: that is a concurrent
    build, and the caller refuses to start beside it. Moved verbatim from
    `rebuild_search_index` on 2026-09-28 so the graph shares it.
    """
    live_builders: set = set()
    for path in glob.glob(f"{canonical}{BUILD_SUFFIX}*"):
        # SQLite's own sidecars are named `<db>-journal` / `-wal` / `-shm`, so
        # the pid is the LEADING integer of the suffix, not the whole of it.
        # Parsing the whole suffix made a live rebuild's journal look ownerless
        # and deletable, which would corrupt that build.
        suffix = path.rsplit(BUILD_SUFFIX, 1)[-1]
        digits = ""
        for ch in suffix:
            if not ch.isdigit():
                break
            digits += ch
        pid = int(digits) if digits else None
        if pid is not None and pid != os.getpid():
            try:
                os.kill(pid, 0)
                live_builders.add(pid)
                continue  # a live build owns this file
            except (OSError, ProcessLookupError):
                pass
        if pid != os.getpid():
            try:
                os.remove(path)
                print(f"  discarded abandoned build file {os.path.basename(path)}")
            except OSError:
                pass
    return live_builders


def discard_build(build_path) -> None:
    """Remove a build file and its SQLite sidecars. Absence is fine; a
    read-only build is removable because unlink needs the directory, not the
    file."""
    for leftover in (build_path, *(f"{build_path}{s}" for s in SIDECARS)):
        try:
            os.remove(leftover)
        except FileNotFoundError:
            pass


def assert_publishable(build_path, integrity=True) -> None:
    """The file must BE the database (design §4.3), and a whole one."""
    for suffix in SIDECARS:
        if os.path.exists(f"{build_path}{suffix}"):
            raise PublicationError(
                f"{os.path.basename(build_path)} has a {suffix.lstrip('-')} "
                "sidecar: its committed state is not all in the file")
    # Measured: real corruption makes the PRAGMA itself raise
    # `DatabaseError: database disk image is malformed` rather than return
    # rows, so every sqlite3 error here is a refusal to publish.
    try:
        conn = sqlite3.connect(f"file:{build_path}?mode=ro", uri=True)
        try:
            mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            result = (conn.execute("PRAGMA integrity_check").fetchall()
                      if integrity else [("ok",)])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise PublicationError(
            f"{os.path.basename(build_path)} is not a readable database: {exc}"
        ) from exc
    if mode != "delete":
        raise PublicationError(
            f"{os.path.basename(build_path)} is in {mode} mode; a published "
            "store must be self-contained (journal_mode=delete)")
    if result != [("ok",)]:
        raise PublicationError(
            f"{os.path.basename(build_path)} failed integrity_check: "
            f"{result[:3]}")


def _fsync_file(path) -> None:
    """`F_FULLFSYNC` where it exists: on macOS plain `fsync` does not flush the
    drive's cache."""
    fd = os.open(path, os.O_RDONLY)
    try:
        if hasattr(fcntl, "F_FULLFSYNC"):
            try:
                fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                return
            except OSError:
                pass
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(build_path, canonical, integrity=True) -> None:
    """Validate, make durable, make read-only, swap in, make the swap durable.

    Leaves `build_path` for the caller to discard on failure; the caller owns
    the build and its `finally`.
    """
    build_dir = os.path.dirname(os.path.abspath(build_path))
    canonical_dir = os.path.dirname(os.path.abspath(canonical))
    if build_dir != canonical_dir:
        raise PublicationError(
            "a build must be a sibling of the canonical file: os.replace is "
            "atomic only within one directory")
    assert_publishable(build_path, integrity=integrity)
    _fsync_file(build_path)
    _chmod(build_path, PUBLISHED_MODE)
    _replace(build_path, canonical)
    _fsync_dir(canonical_dir)


def assert_published_immutable(canonical) -> None:
    """A published store's inode must refuse an in-place write.

    Called where a hardlink is about to be taken: a writable published file
    means either it predates this protocol or someone chmodded it back, and in
    both cases a hardlink to it could stop being a snapshot.
    """
    mode = stat.S_IMODE(os.stat(canonical).st_mode)
    if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise PublicationError(
            f"{os.path.basename(canonical)} is writable (mode {oct(mode)}); a "
            "published store must be read-only before it can be hardlinked. "
            "Republish it (`zurvan index search` / `zurvan graph rebuild`).")
