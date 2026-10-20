"""DEV-side corpus export for Z1b (contract s3.4, defect D4).

Freezes a coherent snapshot (wiki + search + graph at one knowledge revision) with the
real build_snapshot.capture_coherent, then COPIES it into `dest`. Never hardlinks: the
live search index is hardlinked into snapshots, so a hardlinked export would share an
inode with a store that other sessions can touch. The snapshot lease is released in a
finally block whether or not the copy succeeds. `wiki/traces/` is derived, never
indexed, and is not exported.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from scripts.eval_seal.codes import SealRefusal
from scripts.eval_seal.corpus import corpus_sha256

_STORES = ("search.sqlite", "graph.sqlite")
_SKIP_TOP = ("traces",)
# Rev 3 #11: the search index spans every git-tracked indexed root, not just wiki/. The snapshot lays
# docs/ under its root and the root markdown files bare at top level (matching chunks.source_path:
# "docs/x.md", "README.md"). The corpus export must carry all of them, and must NOT carry the snapshot's
# own volatile metadata (lease.json/manifest.json/build.json), which would make the digest irreproducible.
_ROOT_FILES = ("README.md", "AGENTS.md", "CLAUDE.md", "CHANGELOG.md")


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    with os.scandir(src) as it:
        entries = sorted(it, key=lambda e: e.name)
    for e in entries:
        st = e.stat(follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            _copy_tree(Path(e.path), dst / e.name)
        elif stat.S_ISREG(st.st_mode):
            shutil.copyfile(e.path, dst / e.name)
        else:
            raise SealRefusal("CORPUS_MISMATCH")


def prepare_corpus_export(dest: Path, *, root: Path = None, capture=None) -> dict:
    dest = Path(dest)
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise SealRefusal("CORPUS_MISMATCH")
    if capture is None:
        from scripts.build_snapshot import capture_coherent as capture
    bs = capture(root, purpose="z1b-corpus", required=("wiki", "search", "graph"))
    try:
        src = Path(bs.path)
        wiki = src / "wiki"
        if not wiki.is_dir() or not all((src / n).is_file() for n in _STORES):
            raise SealRefusal("CORPUS_MISMATCH")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "wiki").mkdir()
        with os.scandir(wiki) as it:
            tops = sorted(it, key=lambda e: e.name)
        for e in tops:
            if e.name in _SKIP_TOP:
                continue
            st = e.stat(follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                _copy_tree(Path(e.path), dest / "wiki" / e.name)
            elif stat.S_ISREG(st.st_mode):
                shutil.copyfile(e.path, dest / "wiki" / e.name)
            else:
                raise SealRefusal("CORPUS_MISMATCH")
        docs = src / "docs"
        if docs.is_dir():
            _copy_tree(docs, dest / "docs")
        for name in _ROOT_FILES:
            rf = src / name
            if rf.is_file():
                shutil.copyfile(rf, dest / name)
        for name in _STORES:
            shutil.copyfile(src / name, dest / name)
        digest = corpus_sha256(dest)
        for p in dest.rglob("*"):
            if p.is_file():
                os.chmod(p, 0o644)
                if p.stat().st_nlink != 1:
                    raise SealRefusal("CORPUS_MISMATCH")
        return {"corpus_sha256": digest, "revision": bs.revision,
                "snapshot_id": bs.snapshot.snapshot_id}
    finally:
        bs.release()
