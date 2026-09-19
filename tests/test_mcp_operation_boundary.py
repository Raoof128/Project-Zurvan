"""An MCP tool call is one logical operation, exactly like a CLI invocation.

Found in the LIVE ledger on 2026-09-19, at revision 8:

    m99e68f36fdf3  COMMITTED  implicit  scripts.memory:add_decision
    m625b81a185fa  COMMITTED  implicit  scripts.memory:append_to_log
    me499f76dea52  COMMITTED  implicit  scripts.memory:add_decision
    mb9fa67565fd7  COMMITTED  implicit  scripts.memory:append_to_log

Four rows, two decisions. `scripts/mcp_tools.py` called `add_decision`
directly, so the page and the `wiki/log.md` append each activated their own
implicit mutation — and a revision was minted BETWEEN them, naming a state in
which the decision page exists and nothing references it. That is the exact
half-operation the mutation barrier exists to forbid, and it was running in
production, not in a hypothetical.

The boundary belongs at the tool, never inside `memory.add_decision`: making a
library function its own operation would give every caller an independent
boundary and put the split back one level down.

Subprocesses throughout — `scripts/memory.py:7` binds `PROJECT_ROOT` at import,
so an in-process MCP write targets the REAL wiki.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr


@pytest.fixture
def store(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "wiki" / "claims").mkdir()
    (tmp_path / "wiki" / "notes").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path


def _mcp(store, body: str):
    """Run one MCP tool call in a fresh interpreter rooted at this store."""
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(repo),
           "ZURVAN_MCP_READONLY": "0"}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        cwd=str(repo), env=env, capture_output=True, text=True)


def test_one_mcp_decision_is_one_mutation_not_two(store):
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_decision_add
        print(tool_zurvan_decision_add("T", "R", "accepted", ["t"]))
    """)
    assert result.returncode == 0, result.stderr

    committed = kr.mutations_in(kr.COMMITTED, store)
    assert len(committed) == 1, [
        (m.mutation_id, m.caller) for m in committed]


def test_one_mcp_decision_advances_the_revision_exactly_once(store):
    """Two mutations meant two revisions, and the one between them named a
    store holding a decision page that `wiki/log.md` did not mention."""
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_decision_add
        tool_zurvan_decision_add("T", "R", "accepted", ["t"])
    """)
    assert result.returncode == 0, result.stderr

    assert kr.current(store).revision == 1


def test_the_page_and_the_log_are_both_inside_that_one_mutation(store):
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_decision_add
        tool_zurvan_decision_add("T", "R", "accepted", ["t"])
    """)
    assert result.returncode == 0, result.stderr

    m = kr.mutations_in(kr.COMMITTED, store)[0]
    assert len(m.completed_paths) == 2, m.completed_paths
    assert any(p.endswith("log.md") for p in m.completed_paths)
    assert any("decisions" in p for p in m.completed_paths)


def test_the_mutation_is_attributed_to_mcp_and_is_not_implicit(store):
    """`writer=implicit` told an operator nothing about which surface wrote.
    Both `cli` and `mcp` are real boundaries and each says so."""
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_decision_add
        tool_zurvan_decision_add("T", "R", "accepted", ["t"])
    """)
    assert result.returncode == 0, result.stderr

    m = kr.mutations_in(kr.COMMITTED, store)[0]
    assert m.writer == "mcp"
    assert m.implicit is False


def test_the_mcp_mutation_records_the_subject_it_wrote(store):
    """The same resolution the page's `project:` field uses — an explicit
    subject reaches the ledger rather than being resolved a second, different
    way."""
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_decision_add
        tool_zurvan_decision_add("T", "R", "accepted", ["t"], project="otherproj")
    """)
    assert result.returncode == 0, result.stderr

    m = kr.mutations_in(kr.COMMITTED, store)[0]
    assert m.store_project == "otherproj"
    page = next((store / "wiki" / "decisions").glob("*.md"))
    assert 'project: "otherproj"' in page.read_text()


@pytest.mark.parametrize("call", [
    'tool_zurvan_claim_add("t", "wiki/decisions/s.md", "ev", "high", [])',
    'tool_zurvan_question_add("q?", "because", [])',
    'tool_zurvan_remember("note", "T", "body", [])',
])
def test_every_mcp_write_tool_is_a_boundary(store, call):
    """`decision add` is not the only compound write; each of these appends to
    `wiki/log.md` after writing its page."""
    (store / "wiki" / "decisions" / "s.md").write_text("ev\n")
    result = _mcp(store, f"""
        from scripts.mcp_tools import (tool_zurvan_claim_add,
                                       tool_zurvan_question_add,
                                       tool_zurvan_remember)
        print({call})
    """)
    assert result.returncode == 0, result.stderr

    committed = kr.mutations_in(kr.COMMITTED, store)
    assert len(committed) == 1, [(m.writer, m.caller) for m in committed]
    assert committed[0].writer == "mcp"
    assert kr.current(store).revision == 1


def test_a_read_only_mcp_call_leaves_no_row(store):
    """Blocked writes must not activate a mutation: the row is created on the
    first PLANNED write, and a refusal never plans one."""
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(repo),
           "ZURVAN_MCP_READONLY": "1"}
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from scripts.mcp_tools import tool_zurvan_decision_add
            print(tool_zurvan_decision_add("T", "R", "accepted", []))
        """)],
        cwd=str(repo), env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "read-only" in result.stdout

    assert kr.mutations_in(kr.COMMITTED, store) == []
    assert kr.mutations_in(kr.ACTIVE, store) == []


def test_a_failing_mcp_write_does_not_mark_the_store_suspect(store):
    """A claim whose evidence is not in its source is a rejected argument, not
    a torn write. Nothing was planned, so nothing is orphaned."""
    (store / "wiki" / "decisions" / "s.md").write_text("something else\n")
    result = _mcp(store, """
        from scripts.mcp_tools import tool_zurvan_claim_add
        print(tool_zurvan_claim_add("t", "wiki/decisions/s.md",
                                    "not in the file", "high", []))
    """)
    assert result.returncode == 0, result.stderr

    kr.ensure_schema(store)
    assert kr.current(store).state == kr.CLEAN
