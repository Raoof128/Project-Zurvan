"""Corpus identity (contract s3.4, defect D1).

corpus_sha256 is a CONTENT digest: sha256 over sorted lines
    <relpath> NUL <sha256 of the file's bytes> LF
for every regular file under the directory. It never reads mtime, inode, mode or
ownership, so a byte-identical copy digests identically (the repo's manifest_digest
hashes mtime/inode and cannot be used across a copy). Symlinks, devices, FIFOs and
sockets anywhere in the tree are refused: they are not content. Empty directories are
not hashed (they carry no content); every other difference changes the digest.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .codes import SealRefusal

_CHUNK = 1024 * 1024


def _refuse():
    raise SealRefusal("CORPUS_MISMATCH")


def _check_root(root):
    root = Path(root)
    try:
        st = os.lstat(root)
    except OSError:
        _refuse()
    if not stat.S_ISDIR(st.st_mode):
        _refuse()
    return root


def _walk(root: Path):
    """Yield (relpath, abspath) of every regular file; refuse anything else."""
    stack = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            _refuse()
        for entry in entries:
            rel = prefix + entry.name
            if "\n" in rel or "\x00" in rel:
                _refuse()
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                _refuse()
            if stat.S_ISDIR(st.st_mode):
                stack.append((Path(entry.path), rel + "/"))
            elif stat.S_ISREG(st.st_mode):
                yield rel, entry.path
            else:  # symlink, fifo, socket, device
                _refuse()


def _hash_file(path: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _refuse()
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _refuse()
        h = hashlib.sha256()
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            h.update(chunk)
        return h.hexdigest()
    except OSError:
        _refuse()
    finally:
        os.close(fd)


def corpus_sha256(corpus_dir: Path) -> str:
    root = _check_root(corpus_dir)
    lines = []
    for rel, abspath in _walk(root):
        lines.append((rel.encode("utf-8", "surrogateescape"), _hash_file(abspath)))
    lines.sort()
    outer = hashlib.sha256()
    for rel_b, digest in lines:
        outer.update(rel_b + b"\x00" + digest.encode("ascii") + b"\n")
    return outer.hexdigest()


def corpus_pages(corpus_dir: Path) -> set:
    """Store-relative wiki page paths: every *.md under <corpus_dir>/wiki, relative to wiki/."""
    root = _check_root(corpus_dir)
    wiki = _check_root(root / "wiki")
    pages = set()
    for rel, _abs in _walk(wiki):
        if rel.endswith(".md"):
            pages.add(rel)
    return pages
