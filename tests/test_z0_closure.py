"""Z0 closure (design §10): every gate item is demonstrated by a named test,
and the implicit inventory is a gated query, not an assertion (item 3)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


def _callers(root, *flags):
    env = {**os.environ, "ZURVAN_ROOT": str(root), "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"),
                           "knowledge", "callers", *flags],
                          capture_output=True, text=True, env=env, cwd=REPO)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki").mkdir()
    kr.ensure_schema(tmp_path)
    return tmp_path


def _implicit_write(root, name):
    from scripts.safe_write import write_file_safely
    with kr.allow_implicit_for_migration(reason="closure test"):
        write_file_safely(str(root / "wiki" / name), "x\n")


def test_gate_3_strict_callers_fails_while_the_inventory_is_open(store):
    _implicit_write(store, "a.md")
    out = _callers(store, "--strict")
    assert out.returncode == 1, out.stdout + out.stderr
    assert "unresolved implicit caller" in out.stdout


def test_strict_callers_passes_once_every_caller_is_resolved(store):
    _implicit_write(store, "a.md")
    for caller in kr.implicit_callers(store):
        kr.resolve_implicit(caller, actor="test", root=store)
    out = _callers(store, "--strict")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "No unresolved implicit callers." in out.stdout


def test_strict_callers_on_a_store_with_no_ledger_passes_and_says_so(tmp_path):
    (tmp_path / "data").mkdir()
    out = _callers(tmp_path, "--strict")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "nothing recorded" in out.stdout


def test_callers_without_strict_still_reports_and_exits_zero(store):
    _implicit_write(store, "a.md")
    out = _callers(store)
    assert out.returncode == 0
    assert out.stdout.strip()


def test_check_sh_gates_the_inventory():
    text = (REPO / "scripts" / "check.sh").read_text()
    assert "knowledge callers --strict" in text
