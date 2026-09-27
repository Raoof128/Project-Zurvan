"""Who wrote to the live store: this test run, or another project's session?

Snapshots fix the RETRIEVAL measurement — it reads a frozen index and no longer
cares what the live store does. They do not fix the failure that actually bit
twice. Both aborts (2026-09-17 14:57, 2026-09-18 15:21) were in `check.sh`
gate 2, **pytest**, where the session guard fails because the live `wiki/`
changed. Pytest is not scoring retrieval; it is asserting that the suite did not
contaminate the corpus.

That guard is still worth having. It was written after the live store was found
holding 48 `note-test-note-*.md` pages from one CLI test plus 28 notes and 4
decisions from the MCP smoke — all indexed, all silently inside every
before/after comparison the project had ever run. Nothing here weakens that.

What changes is the question it asks. "Did the live store move?" cannot
distinguish a test writing into production from a colleague recording a
decision in another repository, and only the first is a defect. So writes are
stamped at the choke point with the identity of the process that made them, and
the guard fails for writes from ITS OWN process tree.

**Attribution is evidence, not an excuse.** It never declares a mutated
measurement valid — snapshots already removed that dependency. And it fails
CLOSED: a change with no attribution at all is treated as ours, because an
un-stamped writer is exactly the case we cannot reason about.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import writelog


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway store. BOTH redirects are required and they are not aliases.

    `safe_write.is_safe_path` gates on the `ZURVAN_ROOT` environment variable —
    it is the boundary that decides whether a write is inside the store at all —
    while everything else resolves `config.PROJECT_ROOT`. Setting only the
    second makes every write here silently return False, which is how the first
    draft of these tests recorded nothing at all and looked like a stamping bug.
    """
    from scripts import config
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "wiki").mkdir()
    (tmp_path / "data").mkdir()
    monkeypatch.setenv("ZURVAN_WRITE_SESSION", "test-session-token")
    # An authoritative write needs an operation above it (2026-09-28). These
    # tests are about stamping, not about operation boundaries, so each runs
    # inside one — the shape every production writer now has.
    from scripts import knowledge_revision as kr
    with kr.operation(writer="test", root=tmp_path):
        yield tmp_path


# ── stamping ─────────────────────────────────────────────────────────────────

def test_a_wiki_write_is_stamped_with_who_made_it(store):
    from scripts.safe_write import write_file_safely

    write_file_safely(str(store / "wiki" / "page.md"), "# Page\n")

    entries = writelog.entries(root=store)
    assert len(entries) == 1
    e = entries[0]
    assert e["pid"] == os.getpid()
    assert e["session"] == "test-session-token"
    assert e["path"].endswith("wiki/page.md")
    assert "at" in e


def test_writes_outside_the_wiki_are_not_stamped(store):
    """The guard watches the corpus. Stamping every file write is noise."""
    from scripts.safe_write import write_file_safely

    (store / "docs").mkdir()
    write_file_safely(str(store / "docs" / "note.md"), "# Not the corpus\n")
    assert writelog.entries(root=store) == []


def test_an_append_to_the_live_log_is_stamped(store):
    from scripts.safe_write import append_file_safely

    append_file_safely(str(store / "wiki" / "log.md"), "\n- entry\n")
    assert len(writelog.entries(root=store)) == 1


def test_stamping_never_breaks_the_write_it_is_recording(store, monkeypatch):
    """Bookkeeping must not be able to fail a real write.

    A guard that can lose someone's decision page in order to record that a
    decision page was written is worse than no guard.
    """
    from scripts.safe_write import write_file_safely

    def explode(*a, **kw):
        raise OSError("disk full, allegedly")

    monkeypatch.setattr(writelog, "record", explode)
    assert write_file_safely(str(store / "wiki" / "survives.md"), "# Still here\n")
    assert (store / "wiki" / "survives.md").is_file()


# ── classification ───────────────────────────────────────────────────────────

def test_a_write_from_this_process_is_ours(store):
    from scripts.safe_write import write_file_safely

    write_file_safely(str(store / "wiki" / "mine.md"), "# Mine\n")
    ours, external = writelog.classify(writelog.entries(root=store))
    assert len(ours) == 1 and external == []


def test_a_write_from_a_child_process_is_ours(store):
    """A test that shells out to the CLI is still a test writing to production.

    This is the exact shape of the original contamination: `test_cli_remember`
    spawned the real CLI, which wrote a note into the live wiki on every run.
    """
    script = (
        "import sys; sys.path.insert(0, %r)\n"
        "from scripts import config\n"
        "config.PROJECT_ROOT = __import__('pathlib').Path(%r)\n"
        "from scripts.safe_write import write_file_safely\n"
        "from scripts import knowledge_revision as kr\n"
        "with kr.operation(writer='test-child'):\n"
        "    write_file_safely(%r, '# From a child\\n')\n"
        % (str(Path(__file__).resolve().parents[1]), str(store),
           str(store / "wiki" / "child.md"))
    )
    subprocess.run([sys.executable, "-c", script], check=True,
                   env={**os.environ, "PYTHONPATH": "."})

    entries = writelog.entries(root=store)
    assert len(entries) == 1
    assert entries[0]["pid"] != os.getpid(), "the fixture did not actually fork"
    ours, external = writelog.classify(entries)
    assert len(ours) == 1, "a child process's write was not attributed to us"
    assert external == []


def test_a_write_from_an_unrelated_session_is_external(store):
    """Another repository's MCP session: different token, unrelated pid."""
    writelog._append(store, {
        "at": "2026-09-18T05:00:00+00:00",
        "pid": 999999, "ppid": 999998,
        "session": "some-other-sessions-token",
        "path": str(store / "wiki" / "decisions" / "theirs.md"),
        "action": "write",
    })
    ours, external = writelog.classify(writelog.entries(root=store))
    assert ours == [] and len(external) == 1


def test_a_change_with_no_attribution_at_all_is_treated_as_ours(store):
    """Fail closed. An un-stamped writer is the case we cannot reason about."""
    ours, external = writelog.classify([])
    assert ours == [] and external == []
    assert not writelog.all_external([]), (
        "no evidence must never be read as evidence of innocence")


def test_a_mixed_window_is_ours(store):
    """One of our writes among ten of theirs is still our defect."""
    from scripts.safe_write import write_file_safely

    writelog._append(store, {
        "at": "2026-09-18T05:00:00+00:00", "pid": 999999, "ppid": 999998,
        "session": "elsewhere", "path": str(store / "wiki" / "theirs.md"),
        "action": "write"})
    write_file_safely(str(store / "wiki" / "ours.md"), "# Ours\n")

    entries = writelog.entries(root=store)
    assert not writelog.all_external(entries)


def test_all_external_is_true_only_when_every_write_is_someone_elses(store):
    for i in range(3):
        writelog._append(store, {
            "at": "2026-09-18T05:00:00+00:00", "pid": 900000 + i, "ppid": 1,
            "session": f"other-{i}", "path": str(store / "wiki" / f"t{i}.md"),
            "action": "write"})
    assert writelog.all_external(writelog.entries(root=store))


# ── the window ───────────────────────────────────────────────────────────────

def test_only_writes_inside_the_measurement_window_are_considered(store):
    from scripts.safe_write import write_file_safely

    write_file_safely(str(store / "wiki" / "before.md"), "# Before\n")
    mark = writelog.now()
    write_file_safely(str(store / "wiki" / "during.md"), "# During\n")

    window = writelog.entries(root=store, since=mark)
    assert [Path(e["path"]).name for e in window] == ["during.md"]


def test_the_write_log_is_not_part_of_the_corpus(store):
    """It lives under data/, never wiki/ — or recording a write would be one."""
    from scripts.safe_write import write_file_safely

    write_file_safely(str(store / "wiki" / "page.md"), "# Page\n")
    log = writelog.log_path(store)
    assert "wiki" not in log.parts
    assert log.parent.name == "data"
    assert len(list((store / "wiki").glob("*.md"))) == 1


def test_a_corrupt_write_log_line_does_not_break_the_guard(store):
    """Half a line from a killed process must not take the suite down with it."""
    log = writelog.log_path(store)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"at": "2026-09-18T05:00:00+00:00", "pid": 1, "sessi\n',
                   encoding="utf-8")
    from scripts.safe_write import write_file_safely
    write_file_safely(str(store / "wiki" / "after.md"), "# After\n")

    entries = writelog.entries(root=store)
    assert len(entries) == 1, "the valid entry after a corrupt line was lost"
    assert json.dumps(entries[0])
