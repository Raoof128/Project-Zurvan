"""An edit made outside Zurvan becomes its own revision, on the record.

2026-09-28: `commit_mutation` records the manifest of the whole store, so a
hand edit made between two managed writes was absorbed into the second one's
MANAGED revision, and `observe_external_drift` - which detects exactly this -
had no production caller. Reproduced in a throwaway store before any change.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr
from scripts.knowledge_manifest import authoritative_manifest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "a.md").write_text("# a\n")
    kr.ensure_schema(tmp_path)
    kr.bootstrap(tmp_path, actor="test")
    return tmp_path


def _managed_write(root, name="b.md"):
    from scripts.safe_write import write_file_safely
    with kr.operation(writer="test", root=root):
        write_file_safely(str(root / "wiki" / name), f"# {name}\n")


# ── Task 1: the observation is recorded ─────────────────────────────────────

def test_a_manual_observation_is_recorded(root):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    before = kr.current(root)
    minted = kr.observe_external_drift(root)
    [row] = kr.external_observations(root)
    assert row[0] == minted == before.revision + 1
    assert row[1] == "manual"
    assert row[2] == before.manifest_digest
    assert row[3] == authoritative_manifest(root)


def test_no_drift_records_nothing(root):
    assert kr.observe_external_drift(root) is None
    assert kr.external_observations(root) == []


def test_an_unstable_store_raises_the_named_error(root, monkeypatch):
    (root / "wiki" / "a.md").write_text("# a\nhand edit\n")
    digests = iter(["one", "two"])
    monkeypatch.setattr("scripts.knowledge_manifest.authoritative_manifest",
                        lambda base=None: next(digests))
    with pytest.raises(kr.DriftUnobservable):
        kr.observe_external_drift(root)
