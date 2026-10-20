"""Suite-wide guard: running the tests must not change the live knowledge base.

Zurvan measures itself. Retrieval baselines, the failure set, graph size and the
chunk count are all properties of `wiki/`, so a test that writes a page there is
not a harmless side effect — it moves the instrument. On 2026-09-15 the live
store held 48 `note-test-note-*.md` pages from one CLI test plus 28 notes and 4
decisions from the MCP smoke, all indexed and all silently part of every
"before/after" comparison the project had ever run.

The fix is isolation (`ZURVAN_ROOT`, see `scripts/config.py`), not cleanup — a
crash between write and cleanup leaves the contamination behind. This fixture is
the gate that keeps it fixed: it counts the live store before and after the
session and fails if anything moved.

A test that legitimately exercises live-state mutation should redirect the store
with `ZURVAN_ROOT` rather than being exempted here.
"""
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

# Hugging Face tokenizers prints "The current process just got forked, after
# parallelism has already been used" to the CHILD's stderr whenever a process
# that already used it forks. In one pytest session the embedding tests load
# it, and the Z1b broker tests then fork and assert stderr is EMPTY, so they
# failed in suite order only (merge of z1b-rev3, 2026-10-20). The library's own
# documented switch is this variable; production brokers never load it.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

LIVE_ROOT = Path(__file__).resolve().parents[1]

# Files are counted; `wiki/log.md` is measured by LENGTH.
#
# The first version of this guard deliberately ignored the log, reasoning that it
# is append-only and changes legitimately. That reasoning was wrong for a test
# run: nothing should append to the live log while the suite executes. It let a
# real contaminator through — `test_tool_zurvan_remember` wrote a note into the
# live wiki and `os.remove`d it, so the file count came back even while the log
# kept the append permanently, altering a chunk of an indexed 4,000-chunk file on
# every single run.
_WATCHED = {
    "wiki pages": ("wiki", "*.md"),
    "decisions": ("wiki/decisions", "*.md"),
    "claims": ("wiki/claims", "*.md"),
    "notes": ("wiki", "note-*.md"),
    "source pages": ("wiki/sources", "*.md"),
    # Added after check.sh gate 11 was found writing session pages into the live
    # store — a directory this guard had not been watching, so an earlier
    # "live store unchanged" result was true but incomplete.
    "session pages": ("wiki/sessions", "*.md"),
}


def _ledger_fingerprint() -> str:
    """Everything the knowledge ledger asserts, as one digest.

    `data/` was outside this guard's field of view, and the revision ledger
    lives there. A module that resolves its root through the import-bound
    `scripts.config.PROJECT_ROOT` — which `monkeypatch.setenv("ZURVAN_ROOT")`
    does NOT move — writes here while the page under test goes to the redirect,
    and every watched count stays put.

    A revision counter alone is not enough: CLEAN -> SUSPECT, an atomicity
    change, a mutation reaching ORPHANED and a reconciliation record all leave
    it untouched. The guard needs no semantics, only a way to notice movement.

    Defensive: a guard that raises on its own bookkeeping is worse than no
    guard, so any failure reads as "unavailable" and simply never moves.
    """
    try:
        from scripts.knowledge_revision import ledger_fingerprint
        return ledger_fingerprint(LIVE_ROOT)
    except Exception:
        return "unavailable"


def _snapshot() -> dict:
    counts = {}
    for label, (rel, glob) in _WATCHED.items():
        d = LIVE_ROOT / rel
        counts[label] = len(list(d.glob(glob))) if d.is_dir() else 0
    log = LIVE_ROOT / "wiki" / "log.md"
    counts["log.md lines"] = (
        len(log.read_text(encoding="utf-8", errors="replace").splitlines())
        if log.is_file() else 0
    )
    counts["knowledge ledger"] = _ledger_fingerprint()
    return counts


def _report(config, message: str) -> None:
    """Emit a notice that survives a PASSING run.

    pytest captures file descriptor 2 wholesale, so neither `print` nor
    `sys.__stderr__` reaches anyone unless the test fails — and this notice
    exists precisely for runs that pass. The terminal reporter writes to the
    real output stream. `test_conftest_guard_behaviour` caught the first two
    attempts at this; a diagnostic nobody sees is not a diagnostic.
    """
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:                       # -p no:terminal, or an odd harness
        print(message, file=sys.__stderr__, flush=True)
        return
    reporter.write_sep("-", "live store")
    for line in message.splitlines():
        reporter.write_line(line)


@pytest.fixture(scope="session", autouse=True)
def live_search_index_is_not_a_test_fixture():
    """Fail the session if the tests touched the real `data/search.sqlite`.

    The wiki guard above watches the corpus; this watches the index built FROM
    it, which is just as load-bearing and was not covered. A test that rebuilds
    the canonical index under the mock provider replaces every vector with a
    deterministic hash — the index still looks healthy by row count and
    measures 0% semantic recall. Tests build indexes at a tmp_path or under
    `ZURVAN_SEARCH_DB`; nothing in the suite has a reason to write this file.
    """
    index = LIVE_ROOT / "data" / "search.sqlite"

    def _stat():
        try:
            st = index.stat()
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return None

    before = _stat()
    yield
    after = _stat()
    if before != after:
        raise AssertionError(
            "The test suite modified the LIVE search index "
            f"({index}): {before} -> {after}. Point the rebuild at a throwaway "
            "path or set ZURVAN_SEARCH_DB; a mock-provider rebuild of this file "
            "destroys semantic search without changing a single row count."
        )


@pytest.fixture(scope="session", autouse=True)
def live_store_is_not_a_test_fixture(pytestconfig):
    """Fail the session if THE TESTS changed the real `wiki/`.

    The emphasis moved on 2026-09-18. This guard used to fail on any change,
    which cannot distinguish the defect it exists to catch — a test writing into
    production — from the product working, because Zurvan is shared
    cross-project memory and another repository's session may record a decision
    at any moment. Twice (2026-09-17 14:57, 2026-09-18 15:21) the second case
    aborted a twenty-minute gate with nothing wrong in any code under test.

    Writes are now stamped at the choke point with the pid and an inherited
    session token (`scripts/writelog.py`), so the guard can ask whose write it
    was. It still fails for anything from its OWN process tree, including
    subprocesses — that is the 48-stray-pages failure, unchanged.

    It fails CLOSED: a change with no attribution at all is treated as ours,
    because an un-stamped writer is exactly the case we cannot reason about.
    Adding a write path without stamping it makes this stricter, never weaker.

    Attribution is evidence, not an excuse. It never declares a measurement
    valid — `scripts/measurement_snapshot.py` already removed that dependency by freezing
    the index a measurement reads.
    """
    # If the whole run is already redirected, there is nothing to protect.
    if os.environ.get("ZURVAN_ROOT"):
        yield
        return

    from scripts import writelog

    # Stamped onto every write this process or its children make, so they can be
    # told apart from everyone else's without inspecting the text.
    token = f"pytest-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    previous = os.environ.get(writelog.SESSION_ENV)
    os.environ[writelog.SESSION_ENV] = token
    window_start = writelog.now()

    before = _snapshot()
    before_log_length = before["log.md lines"]
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(writelog.SESSION_ENV, None)
        else:
            os.environ[writelog.SESSION_ENV] = previous
    after = _snapshot()

    moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if moved:
        records = writelog.entries(root=LIVE_ROOT, since=window_start)
        if writelog.all_external(records, session=token):
            # The store moved and none of it was us. Reported, never silent —
            # and no measurement in this run depended on it holding still.
            _report(
                pytestconfig,
                "[live store] changed during the suite, and every write was "
                "EXTERNAL — another Zurvan client, not this run:\n"
                + "\n".join(
                    f"  {k}: {b} -> {a}"
                    + (f" ({a - b:+d})" if isinstance(a, int) and isinstance(b, int)
                       else "")
                    for k, (b, a) in sorted(moved.items()))
                + "\n" + writelog.describe(records)
                + "\n  Not a failure: retrieval is measured against a frozen "
                  "snapshot (scripts/measurement_snapshot.py), so this changed nothing.")
            return
        detail = "\n".join(
            f"  {k}: {b} -> {a}"
            + (f" ({a - b:+d})" if isinstance(a, int) and isinstance(b, int) else "")
            for k, (b, a) in sorted(moved.items())
        )
        raise AssertionError(
            "The LIVE knowledge base changed while the suite ran. It is the "
            "corpus every retrieval baseline is measured against, so this fails "
            "whatever wrote it:\n"
            f"{detail}\n"
            f"{_log_lines_appended_during(before_log_length)}"
            + ("  Attributed to THIS run (pid, or a session token this process "
               "set and its children inherited):\n"
               + writelog.describe(writelog.classify(records, session=token)[0])
               + "\n" if records else
               "  No write was stamped at all, so this is treated as ours: an "
               "un-stamped writer is the case attribution cannot reason about.\n")
            + "If a TEST wrote it, point that test at a throwaway store with "
            "ZURVAN_ROOT (see tests/test_cli.py::test_cli_remember) rather than "
            "deleting the files afterwards — a crash mid-test leaves them behind.\n"
            "If ANOTHER Zurvan client wrote it (the MCP server runs with "
            "ZURVAN_MCP_READONLY=0, so a session in any other repository can "
            "write here — observed on 2026-09-17, mid-gate), nothing is wrong "
            "with the code: the store is shared and was not quiescent. Re-run "
            "the gate when no other session is writing."
        )


def _log_lines_appended_during(before_length: int) -> str:
    """Show what landed in `wiki/log.md` during the run.

    Every Zurvan write appends one dated line describing itself, so these lines
    are usually enough to tell a test artefact ("Phase 7 smoke test") from
    another project's session ("Acme: ...") without any further digging — which
    is the first question anyone asks when this guard fires.
    """
    log = LIVE_ROOT / "wiki" / "log.md"
    if not log.is_file():
        return ""
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    appended = lines[before_length:]
    if not appended:
        return ""
    shown = "\n".join(f"    {line}" for line in appended[:10])
    return f"  Appended to wiki/log.md during this run:\n{shown}\n"
