"""Plan 3 — the instrument leaves git.

`eval/search_gold.jsonl` was TRACKED while Boundary's policy says
INSTRUMENT -> PUBLIC_ARTIFACT is blocked, so Boundary could not be enabled
against this repository without blocking it. The cleartext moves to a local-only
directory; git keeps an integrity manifest that cannot reconstruct the items.
"""
import json

import pytest

from scripts import instruments


def test_cleartext_resolves_to_the_local_store_not_the_repo(tmp_path, monkeypatch):
    local = tmp_path / "instruments"
    local.mkdir()
    (local / "search_gold.jsonl").write_text('{"query":"a","expected_paths":["x.md"]}\n',
                                             encoding="utf-8")
    monkeypatch.setattr(instruments, "INSTRUMENT_DIR", local)
    assert instruments.gold_path() == local / "search_gold.jsonl"


def test_a_missing_instrument_fails_loudly_not_silently(tmp_path, monkeypatch):
    """A silently-empty gold set would report 0/0 and look like a passing eval."""
    monkeypatch.setattr(instruments, "INSTRUMENT_DIR", tmp_path / "nothing")
    monkeypatch.setattr(instruments, "LEGACY_DIR", tmp_path / "also-nothing")
    with pytest.raises(instruments.InstrumentMissing):
        instruments.gold_path()


def test_the_manifest_cannot_reconstruct_the_items(tmp_path, monkeypatch):
    """The tracked side must not be a confirmation oracle.

    Per-item digests of a ~50-byte natural-language query would let anyone
    holding a candidate confirm it. The manifest therefore carries a count and
    one whole-file digest, and reports reference items by INDEX.
    """
    local = tmp_path / "instruments"; local.mkdir()
    src = local / "search_gold.jsonl"
    src.write_text('{"query":"secret probe one"}\n{"query":"secret probe two"}\n',
                   encoding="utf-8")
    monkeypatch.setattr(instruments, "INSTRUMENT_DIR", local)
    m = instruments.build_manifest()
    blob = json.dumps(m)
    assert m["n_items"] == 2
    assert len(m["sha256"]) == 64
    assert "secret probe" not in blob
    assert "items" not in m and "item_ids" not in m


def test_verify_detects_a_changed_instrument(tmp_path, monkeypatch):
    local = tmp_path / "instruments"; local.mkdir()
    src = local / "search_gold.jsonl"
    src.write_text('{"query":"one"}\n', encoding="utf-8")
    monkeypatch.setattr(instruments, "INSTRUMENT_DIR", local)
    manifest = instruments.build_manifest()
    instruments.verify(manifest)
    src.write_text('{"query":"one"}\n{"query":"two"}\n', encoding="utf-8")
    with pytest.raises(instruments.InstrumentMismatch):
        instruments.verify(manifest)


def test_the_live_instrument_is_not_tracked_by_git():
    """The whole point of Plan 3. Runs against the real repository."""
    import subprocess
    from scripts.config import PROJECT_ROOT
    # pinned to the repo, not the cwd: from another directory this was vacuous
    tracked = subprocess.run(["git", "-C", str(PROJECT_ROOT), "ls-files", "--error-unmatch",
                              "eval/search_gold.jsonl"],
                             capture_output=True, text=True)
    assert "did not match" in tracked.stderr, tracked.stderr  # git ran, in a repo
    tracked = tracked.stdout.strip()
    assert not tracked, "the instrument cleartext is still tracked in git"


def test_the_manifest_is_tracked_so_integrity_survives_a_clone():
    import subprocess
    tracked = subprocess.run(["git", "ls-files", "eval/search_gold.manifest.json"],
                             capture_output=True, text=True).stdout.strip()
    assert tracked, "the integrity manifest must be tracked"


def test_no_instrument_item_falls_below_the_registration_floor():
    """Items shorter than MIN_LITERAL_LEN are silently unprotected.

    Measured 2026-09-16: the shortest live item is 24 B against a floor of 8, and
    49 of 50 items sit below the 64-byte D1 threshold — so D2 registration is the
    only protection for almost the whole instrument. This fails the moment a
    shorter item is added, instead of letting it disappear.
    """
    import json
    from scripts.boundary.scan import MIN_LITERAL_LEN
    from scripts.instruments import gold_path
    items = [json.loads(l)["query"] for l in open(gold_path(), encoding="utf-8") if l.strip()]
    too_short = [i for i, q in enumerate(items, 1) if len(q.encode()) < MIN_LITERAL_LEN]
    assert not too_short, f"instrument items below the D2 floor: {too_short}"
