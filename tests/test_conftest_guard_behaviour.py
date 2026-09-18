"""What the session guard actually does — proven, not assumed.

`tests/conftest.py` decides whether a run is valid. Changing it by reasoning
alone is how a safety net quietly becomes decorative, and the change made on
2026-09-18 moved it from "fail on any change" to "fail on OUR changes". That is
exactly the kind of relaxation that has to be demonstrated rather than argued.

It cannot be tested in-process: the guard is a session fixture of the suite
doing the testing, and exercising it for real means contaminating the live
store. So a nested pytest is run in a temporary directory with a COPY of
`conftest.py`, whose `LIVE_ROOT` is `Path(__file__).parents[1]` and therefore
resolves to that temporary directory. The guard then runs against a fake store
and the real production wiki is never in scope.

Three behaviours, all load-bearing:

* a write from the run itself still FAILS the session — the 48-stray-pages
  defect, unchanged;
* a write from another session does NOT fail it — the case that aborted two
  twenty-minute gates for no reason;
* a change nobody stamped FAILS it — fail closed, because an un-stamped writer
  is the case attribution cannot reason about.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_OURS = '''
def test_writes_a_page_as_this_very_process():
    from pathlib import Path
    from scripts import writelog
    root = Path(__file__).resolve().parents[1]
    page = root / "wiki" / "note-contaminated.md"
    page.write_text("# written by the suite\\n", encoding="utf-8")
    writelog.record(page, "write", root=root)
'''

_EXTERNAL = '''
def test_another_session_writes_while_we_run():
    from pathlib import Path
    from scripts import writelog
    root = Path(__file__).resolve().parents[1]
    page = root / "wiki" / "decisions" / "theirs.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# written by another project\\n", encoding="utf-8")
    writelog._append(root, {
        "at": writelog.now(), "pid": 999999, "ppid": 999998,
        "session": "another-repositorys-session",
        "path": str(page), "action": "write"})
'''

_UNSTAMPED = '''
def test_writes_a_page_and_stamps_nothing():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    (root / "wiki" / "note-mystery.md").write_text("# no idea who\\n", encoding="utf-8")
'''


def _nested(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    """Run pytest in a fake store whose conftest is a copy of the real one."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    shutil.copy2(REPO / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    (tmp_path / "tests" / "test_case.py").write_text(body, encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(REPO)}
    # The guard short-circuits when the whole run is redirected, which would
    # skip the very thing under test.
    env.pop("ZURVAN_ROOT", None)
    env.pop("ZURVAN_WRITE_SESSION", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_case.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    # The external-write notice is written to real stderr so pytest's capture
    # cannot swallow it; failures land on stdout. Both are evidence.
    result.output = result.stdout + result.stderr
    return result


def test_a_write_from_the_run_itself_still_fails_the_session(tmp_path):
    """The defect the guard was built for. Unchanged by attribution."""
    result = _nested(tmp_path, _OURS)
    assert result.returncode != 0, (
        "the suite contaminated its own store and the guard passed it:\n"
        + result.output[-3000:])
    assert "LIVE knowledge base changed" in result.output
    assert "Attributed to THIS run" in result.output


def test_a_write_from_another_session_does_not_fail_the_run(tmp_path):
    """The case that aborted two twenty-minute gates for no reason."""
    result = _nested(tmp_path, _EXTERNAL)
    assert result.returncode == 0, (
        "an unrelated session's write still failed the suite:\n"
        + result.output[-3000:])
    assert "every write was EXTERNAL" in result.output, (
        "the external write passed silently; it must be reported, not ignored")
    assert "frozen snapshot" in result.output


def test_an_unattributed_change_fails_the_session(tmp_path):
    """Fail closed. A new write path that forgets to stamp makes this stricter."""
    result = _nested(tmp_path, _UNSTAMPED)
    assert result.returncode != 0, (
        "a change nobody claimed was treated as innocent:\n" + result.output[-3000:])
    assert "No write was stamped at all" in result.output


def test_the_guard_still_short_circuits_for_a_fully_redirected_run(tmp_path):
    """`ZURVAN_ROOT` means there is no live store to protect."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "wiki").mkdir()
    shutil.copy2(REPO / "tests" / "conftest.py", tmp_path / "tests" / "conftest.py")
    (tmp_path / "tests" / "test_case.py").write_text(_UNSTAMPED, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(REPO), "ZURVAN_ROOT": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_case.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.output[-2000:]


def test_the_nested_store_is_never_the_real_repository(tmp_path):
    """Precondition for the test below. The copied conftest's LIVE_ROOT is
    `Path(__file__).resolve().parents[1]`, which resolves to the temp
    directory — so the nested run's "production" IS the fixture. Asserted
    rather than inferred, because the next test deliberately mutates what that
    run considers production."""
    assert tmp_path.resolve() != REPO.resolve()


def test_a_test_that_mutates_the_nested_ledger_fails_the_session(tmp_path):
    """The guard watched `wiki/` and nothing under `data/`.

    A module resolving its root through the import-bound `config.PROJECT_ROOT`
    writes the ledger into the real store while the page under test lands in
    the redirect — silently, because no watched count moved. This is that wire.
    """
    result = _nested(tmp_path, """
import sqlite3
from pathlib import Path

def test_bumps_the_nested_ledger():
    root = Path(__file__).resolve().parents[1]
    db = root / "data" / "registry.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS knowledge_state ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " current_revision INTEGER NOT NULL, state TEXT NOT NULL,"
        " atomicity TEXT NOT NULL, manifest_digest TEXT NOT NULL);"
        "INSERT OR REPLACE INTO knowledge_state"
        " VALUES (1, 99, 'CLEAN', 'MANAGED', '');")
    conn.commit()
    conn.close()
""")
    assert result.returncode != 0
    assert "knowledge ledger" in result.output


def test_the_fingerprint_is_part_of_the_snapshot(tmp_path):
    """`CLEAN -> SUSPECT` moves neither a revision counter nor a row count, so
    the guard must watch a fingerprint rather than either. Asserted directly on
    the helper, because a nested run compares a store against itself and a
    change made before the run starts is not a change DURING it."""
    # Loaded BY PATH, never as `import tests.conftest`: measured, that resolves
    # to /opt/anaconda3/.../site-packages/tests/conftest.py — an unrelated
    # package that happens to own the name. A test asserting against a
    # stranger's file passes or fails for reasons that have nothing to do with
    # this repository.
    import importlib.util
    path = REPO / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_conftest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__file__ == str(path)
    assert "knowledge ledger" in module._snapshot()
