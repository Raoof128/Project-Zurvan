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
def live_store_is_not_a_test_fixture():
    """Fail the session if the tests changed the real `wiki/`."""
    # If the whole run is already redirected, there is nothing to protect.
    if os.environ.get("ZURVAN_ROOT"):
        yield
        return

    before = _snapshot()
    yield
    after = _snapshot()

    moved = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    if moved:
        detail = "\n".join(
            f"  {k}: {b} -> {a} ({a - b:+d})" for k, (b, a) in sorted(moved.items())
        )
        raise AssertionError(
            "The test suite wrote into the LIVE knowledge base, which is the "
            "corpus every retrieval baseline is measured against:\n"
            f"{detail}\n"
            "Point the offending test at a throwaway store by setting "
            "ZURVAN_ROOT (see tests/test_cli.py::test_cli_remember), rather "
            "than deleting the files afterwards — a crash mid-test would leave "
            "them behind."
        )
