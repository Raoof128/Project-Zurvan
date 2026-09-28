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
    """A substring match passed a commented-out line, `|| true`, or a line
    after the final exit (closure review). The gate must be a live command
    under `set -e` that can fail the run."""
    lines = (REPO / "scripts" / "check.sh").read_text().splitlines()
    assert "set -e" in [l.strip() for l in lines]
    live = [i for i, l in enumerate(lines)
            if l.strip() == "PYTHONPATH=. python scripts/cli.py knowledge callers --strict"]
    assert len(live) == 1, "exactly one live, unguarded `callers --strict` line"
    final = max(i for i, l in enumerate(lines) if "All Zurvan checks passed" in l)
    exits = [i for i, l in enumerate(lines) if l.strip().startswith("exit 0")]
    assert live[0] < final and not any(i < live[0] for i in exits)


SPEC = REPO / "docs" / "superpowers" / "specs" / "2026-09-18-z0-coherent-snapshots-design.md"


def _matrix():
    text = SPEC.read_text(encoding="utf-8")
    section = text.split("### 10.5 Closure matrix", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|[^|]*\|(.*)\|\s*$", line)
        if m:
            rows[int(m.group(1))] = re.findall(r"`(test_[A-Za-z0-9_]+)`", m.group(2))
    return rows


def _defined_tests():
    names = set()
    for path in (REPO / "tests").glob("test_*.py"):
        names.update(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.M))
    return names


def test_every_gate_item_names_a_test_that_exists():
    """A renamed test must not silently unprove a gate item."""
    rows = _matrix()
    assert sorted(rows) == list(range(1, 21)), f"matrix covers {sorted(rows)}"
    defined = _defined_tests()
    missing = {item: [t for t in tests if t not in defined]
               for item, tests in rows.items()}
    missing = {k: v for k, v in missing.items() if v}
    empty = [item for item, tests in rows.items() if not tests and item != 20]
    assert not missing, f"matrix names tests that do not exist: {missing}"
    assert not empty, f"items with no named test: {empty}"
