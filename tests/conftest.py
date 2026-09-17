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
from pathlib import Path

import pytest

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
    return counts


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
def live_store_is_not_a_test_fixture():
    """Fail the session if the tests changed the real `wiki/`."""
    # If the whole run is already redirected, there is nothing to protect.
    if os.environ.get("ZURVAN_ROOT"):
        yield
        return

    before = _snapshot()
    before_log_length = before["log.md lines"]
    yield
    after = _snapshot()

    moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if moved:
        detail = "\n".join(
            f"  {k}: {b} -> {a} ({a - b:+d})" for k, (b, a) in sorted(moved.items())
        )
        raise AssertionError(
            "The LIVE knowledge base changed while the suite ran. It is the "
            "corpus every retrieval baseline is measured against, so this fails "
            "whatever wrote it:\n"
            f"{detail}\n"
            f"{_log_lines_appended_during(before_log_length)}"
            "If a TEST wrote it, point that test at a throwaway store with "
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
