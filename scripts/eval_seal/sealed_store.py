"""Z1b Rev 3 P1 #14: the sealed store as immutable generations behind one atomic pointer.

The four files that must change together under a reseal -- `holdout.age`, `reserve.age`, `manifest.json`,
`manifest.json.sig` -- live inside an immutable generation directory `generations/<seq>/`, and a single
symlink `active` names the live one. A reseal builds a complete new generation, fsyncs it, and promotes it
with ONE atomic rename of `active`; a crash therefore leaves `active` pointing at generation N or N+1 and
never at a half-written mix. The previous healthy generation is kept; `gc` reclaims any generation that
`active` does not name.

`canaries.age` and `dev-ref.jsonl` are NOT part of a generation (a reseal never rewrites them); they stay
beside the store. This module is the ONLY place production code names a sealed dataset file -- a grep guard
(tests/test_z1b_sealed_store.py) enforces that so no reader can bypass the pointer.

Sealed-side: stdlib only, Python 3.9 compatible.

Step A of the migration keeps the historical FLAT layout (generation == the store root), so every reader
can move onto this API with no behaviour change; Step B turns on real generation directories + the pointer.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

HOLDOUT = "holdout.age"
RESERVE = "reserve.age"
MANIFEST = "manifest.json"
SIG = "manifest.json.sig"
CANARIES = "canaries.age"
DEVREF = "dev-ref.jsonl"

GENERATIONS = "generations"
ACTIVE = "active"

# the files a single reseal swaps together (the atomic unit)
GENERATION_FILES = (HOLDOUT, RESERVE, MANIFEST, SIG)


def _eval_home(paths) -> Path:
    return Path(paths.eval_home)


def generations_dir(paths) -> Path:
    return _eval_home(paths) / GENERATIONS


def _gen_name(seq: int) -> str:
    if type(seq) is not int or isinstance(seq, bool) or seq < 1:
        raise ValueError("generation sequence must be a positive int")
    return str(seq).zfill(6)


def generation_dir(paths, seq: int) -> Path:
    """The immutable directory that holds generation <seq>'s four files (before and after it is promoted)."""
    return generations_dir(paths) / _gen_name(seq)


def active_dir(paths) -> Path:
    """The pointer: `<eval_home>/active` is a symlink to the live generation directory. Reads go THROUGH it,
    so one atomic rename of this link is the whole swap. Readers that open with O_NOFOLLOW are unaffected:
    the symlink is an intermediate path component, the final file (e.g. holdout.age) is a real file."""
    return _eval_home(paths) / ACTIVE


def holdout_age(paths) -> Path:
    return active_dir(paths) / HOLDOUT


def reserve_age(paths) -> Path:
    return active_dir(paths) / RESERVE


def manifest(paths) -> Path:
    return active_dir(paths) / MANIFEST


def manifest_sig(paths) -> Path:
    return active_dir(paths) / SIG


def age_path(paths, pool: str) -> Path:
    """The sealed set for pool in ('holdout','reserve')."""
    if pool == "holdout":
        return holdout_age(paths)
    if pool == "reserve":
        return reserve_age(paths)
    raise ValueError("pool must be holdout or reserve")


def canaries_age(paths) -> Path:
    return Path(paths.sets_dir) / CANARIES


def dev_ref(paths) -> Path:
    return Path(paths.sets_dir) / DEVREF


def fsync_file(path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_dir(path) -> None:
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------- generation lifecycle (the atomic swap)

def active_seq(paths):
    """The sequence currently promoted, read from the `active` symlink target, or None if unset."""
    try:
        target = os.readlink(str(active_dir(paths)))
    except OSError:
        return None
    name = os.path.basename(target.rstrip("/"))
    return int(name) if name.isdigit() else None


def new_generation(paths, seq: int) -> Path:
    """Create (fresh, empty) the directory for generation <seq> and return it. A leftover partial directory
    of the same seq -- the orphan a pre-promotion crash leaves -- is removed first, so a rebuild never
    appends to a half-written generation. Never touches `active`."""
    generations_dir(paths).mkdir(parents=True, exist_ok=True)
    os.chmod(str(generations_dir(paths)), 0o700)
    gen = generation_dir(paths, seq)
    if gen.exists():
        shutil.rmtree(str(gen))
    gen.mkdir()
    os.chmod(str(gen), 0o700)
    return gen


def promote(paths, seq: int) -> None:
    """Atomically make generation <seq> the live one: fsync the generation dir, then replace `active` with
    a fresh symlink to it in ONE os.replace, then fsync the parent. A crash leaves `active` on the old
    generation or the new one, never a mix. The generation dir must already hold all four validated files."""
    gen = generation_dir(paths, seq)
    for name in GENERATION_FILES:
        if not (gen / name).is_file():
            raise ValueError("generation %s is incomplete: missing %s" % (seq, name))
    fsync_dir(gen)
    link = active_dir(paths)
    tmp = _eval_home(paths) / (ACTIVE + ".swap-" + _gen_name(seq))
    try:
        os.unlink(str(tmp))
    except OSError:
        pass
    os.symlink(os.path.join(GENERATIONS, _gen_name(seq)), str(tmp))   # relative: the store stays relocatable
    os.replace(str(tmp), str(link))                                   # the single atomic swap
    fsync_dir(_eval_home(paths))


def gc(paths) -> list:
    """Remove every generation directory `active` does not name. Returns the reclaimed sequence names. Only
    an UNREFERENCED generation is removed, so the previous healthy generation survives until the next gc
    after a successful promotion. Never removes the live one."""
    live = active_seq(paths)
    reclaimed = []
    gdir = generations_dir(paths)
    if not gdir.is_dir():
        return reclaimed
    for child in sorted(gdir.iterdir()):
        if child.name.isdigit() and (live is None or int(child.name) != live):
            shutil.rmtree(str(child))
            reclaimed.append(child.name)
    return reclaimed


def seed_generation(paths, seq: int, *, holdout: bytes, reserve: bytes, manifest_bytes: bytes,
                    sig: bytes) -> None:
    """Install an already-validated set of four files as generation <seq> and promote it. Used to migrate a
    legacy flat store into the generation layout exactly once (the files are validated by the caller first;
    this never rewrites a legacy copy in parallel). Writes 0600, fsyncs each, then one atomic promote."""
    gen = new_generation(paths, seq)
    for name, data in ((HOLDOUT, holdout), (RESERVE, reserve), (MANIFEST, manifest_bytes), (SIG, sig)):
        dest = gen / name
        fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
    promote(paths, seq)
