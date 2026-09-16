"""Evaluation instruments: cleartext local-only, integrity tracked.

`eval/search_gold.jsonl` was tracked in git while Boundary's policy declares
`INSTRUMENT -> PUBLIC_ARTIFACT` blocked. Boundary could not be enabled against
this repository without blocking it — the scanner's first honest run would have
rejected the tree it lives in.

The split:

  ~/.zurvan/instruments/search_gold.jsonl   cleartext, local, never tracked
  eval/search_gold.manifest.json            count + one digest, tracked

The manifest deliberately carries NO per-item digest. A ~50-byte
natural-language query hashed individually is a confirmation oracle: anyone
holding a candidate could verify it against the tracked file. Count plus a
whole-file digest proves integrity without enabling that, and reports reference
items by INDEX — which is the rule the retrieval specs already follow.
"""
import hashlib
import json
import os
from pathlib import Path

from scripts.config import PROJECT_ROOT

INSTRUMENT_DIR = Path(
    os.environ.get("ZURVAN_INSTRUMENTS") or Path.home() / ".zurvan" / "instruments"
)
LEGACY_DIR = PROJECT_ROOT / "eval"
DEFAULT_NAME = "search_gold.jsonl"


class InstrumentMissing(FileNotFoundError):
    """No cleartext instrument anywhere. Never degrade to an empty gold set."""


class InstrumentMismatch(RuntimeError):
    """The instrument does not match its tracked manifest."""


def gold_path(name: str = DEFAULT_NAME) -> Path:
    """Resolve the instrument's cleartext.

    Fails loudly when absent. A missing gold set that silently became `[]` would
    report 0 queries, 0 failures, and read exactly like a passing evaluation.
    """
    local = INSTRUMENT_DIR / name
    if local.is_file():
        return local
    legacy = LEGACY_DIR / name
    if legacy.is_file():
        return legacy                     # pre-migration checkouts still work
    raise InstrumentMissing(
        f"instrument {name!r} not found in {INSTRUMENT_DIR} or {LEGACY_DIR}. "
        f"Cleartext instruments are local-only; see eval/{name}.manifest.json "
        f"for the expected digest and count."
    )


def manifest_path(name: str = DEFAULT_NAME) -> Path:
    return LEGACY_DIR / f"{Path(name).stem}.manifest.json"


def build_manifest(name: str = DEFAULT_NAME) -> dict:
    data = gold_path(name).read_bytes()
    n_items = sum(1 for line in data.decode("utf-8").splitlines() if line.strip())
    return {
        "instrument": name,
        "n_items": n_items,
        "sha256": hashlib.sha256(data).hexdigest(),
        "note": ("Cleartext is local-only. No per-item digest is recorded: a "
                 "per-item hash of a short natural-language query is a "
                 "confirmation oracle. Reference items by index."),
    }


def verify(manifest: dict = None, name: str = DEFAULT_NAME) -> None:
    """Raise unless the local instrument matches the tracked manifest."""
    if manifest is None:
        p = manifest_path(name)
        if not p.is_file():
            raise InstrumentMismatch(f"no manifest at {p}")
        manifest = json.loads(p.read_text(encoding="utf-8"))
    actual = build_manifest(name)
    if actual["sha256"] != manifest["sha256"] or actual["n_items"] != manifest["n_items"]:
        raise InstrumentMismatch(
            f"instrument {name!r} does not match its manifest: "
            f"{actual['n_items']} items / {actual['sha256'][:12]} present, "
            f"{manifest['n_items']} / {manifest['sha256'][:12]} expected"
        )


def write_manifest(name: str = DEFAULT_NAME) -> Path:
    """Regenerate the tracked manifest. A deliberate act after changing the set."""
    p = manifest_path(name)
    p.write_text(json.dumps(build_manifest(name), indent=2) + "\n", encoding="utf-8")
    return p
