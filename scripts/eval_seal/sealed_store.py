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


def active_dir(paths) -> Path:
    """The directory holding the live generation's four files. Step A: the historical flat locations do not
    share one directory (manifest.json sits at eval_home, the .age files under sets/), so callers use the
    per-file accessors below rather than active_dir in Step A. Kept for Step B, where it resolves `active`."""
    return _eval_home(paths) / ACTIVE


def holdout_age(paths) -> Path:
    return Path(paths.sets_dir) / HOLDOUT


def reserve_age(paths) -> Path:
    return Path(paths.sets_dir) / RESERVE


def manifest(paths) -> Path:
    return Path(paths.manifest_file)


def manifest_sig(paths) -> Path:
    return Path(str(paths.manifest_file) + SIG[len(MANIFEST):])  # "<manifest>.sig"


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
