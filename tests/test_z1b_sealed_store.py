"""Z1b Rev 3 P1 #14: the sealed store is reached ONLY through scripts/eval_seal/sealed_store.py.

The atomicity guarantee (one pointer names the live immutable generation) holds only if no reader bypasses
the pointer by constructing a sealed-dataset path itself. This guard fails if any production module other
than sealed_store.py names holdout.age / reserve.age, so a future bypass cannot slip in unnoticed.
"""
import re
from pathlib import Path

SEAL = Path(__file__).resolve().parent.parent / "scripts" / "eval_seal"
_DATASET_LITERALS = (re.compile(r'["\']holdout\.age["\']'), re.compile(r'["\']reserve\.age["\']'))


def test_t14_only_sealed_store_names_the_sealed_dataset_files():
    offenders = []
    for py in sorted(SEAL.glob("*.py")):
        if py.name == "sealed_store.py":
            continue
        text = py.read_text(encoding="utf-8")
        for rx in _DATASET_LITERALS:
            if rx.search(text):
                offenders.append("%s: %s" % (py.name, rx.pattern))
    assert offenders == [], ("sealed-dataset paths must be built only in sealed_store.py; offenders: %r"
                             % offenders)


def test_t14_sealed_store_is_the_single_authority_and_exposes_the_accessors():
    import sys
    sys.path.insert(0, str(SEAL.parent.parent))
    from scripts.eval_seal import sealed_store as ss
    for name in ("holdout_age", "reserve_age", "manifest", "manifest_sig", "age_path", "canaries_age",
                 "dev_ref", "fsync_file", "fsync_dir"):
        assert hasattr(ss, name), name
    text = (SEAL / "sealed_store.py").read_text()
    assert '"holdout.age"' in text and '"reserve.age"' in text   # the names live here, and only here
