#!/usr/bin/env python3
"""Manifests over the authoritative scope. Fails closed.

A manifest is `(relpath, size, mtime_ns, ino)` per file, prefixed by the scope
version. It reads no content, which is what makes it cheap enough to compute at
every mutation commit and twice per capture: measured 31/31/30 ms over 4,405
files end-to-end.

Stated bound: this is not a content hash. An in-place same-size write within one
nanosecond tick is undetectable. The alternative is hashing 135 MB per capture
and no observed failure justifies it. `st_ino` is here because an atomic replace
preserves size and can preserve mtime, and that is how a careful editor writes.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from scripts import knowledge_scope as ks


class ManifestUnavailable(RuntimeError):
    """The manifest could not be computed over the whole scope.

    Raised rather than returning a partial digest. A digest over "whatever
    files happened to be readable" compares equal across different corpora,
    which is worse than no answer: it is a confident wrong one.
    """


def manifest_sha(files, root) -> str:
    """Digest a set of paths. No `resolve()` in the loop.

    The caller supplies absolute paths already under `root` (`os.walk` from a
    resolved base guarantees it), so `os.path.relpath` is string arithmetic
    where `Path.resolve().relative_to()` is a syscall per file. Measured: the
    second resolve alone doubled the hash from 109 ms to 226 ms.
    """
    base = Path(root).resolve()
    h = hashlib.sha256()
    h.update(f"scope={ks.SCOPE_VERSION}\x00".encode())
    for path in sorted(str(p) for p in files):
        p = path if os.path.isabs(path) else os.path.join(str(base), path)
        try:
            st = os.stat(p)
            rel = os.path.relpath(p, str(base))
        except (OSError, ValueError) as exc:
            raise ManifestUnavailable(f"cannot manifest {p}: {exc}") from exc
        if rel.startswith(".."):
            raise ManifestUnavailable(f"{p} is outside {base}")
        rec = f"{rel}\x00{st.st_size}\x00{st.st_mtime_ns}\x00{st.st_ino}".encode()
        h.update(len(rec).to_bytes(8, "big") + rec)
    return h.hexdigest()


def authoritative_manifest(root=None) -> str:
    base = ks.store_root(root)
    try:
        files = ks.authoritative_files(base)
    except OSError as exc:
        raise ManifestUnavailable(f"cannot list {base / 'wiki'}: {exc}") from exc
    return manifest_sha(files, base)


def indexable_manifest(root=None) -> str:
    """The narrower set `measurement_snapshot` has always reported."""
    base = ks.store_root(root)
    try:
        from scripts.chunk import scan_markdown_files
        files = scan_markdown_files(base)
    except Exception as exc:
        raise ManifestUnavailable(f"cannot scan {base}: {exc}") from exc
    return manifest_sha(files, base)
