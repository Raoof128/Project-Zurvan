"""MCP decisions can name their subject, say what they stamped, and be
corrected inside the ledger.

2026-10-01: `mcp_server.zurvan_decision_add` had no `project` parameter, so an
MCP-only agent could not name the subject; a server whose cwd is outside every
registered project stamped `unknown` and said only "Decision added
successfully". Agents then fixed the field with `sed -i` in wiki/decisions —
outside the ledger, twice (rev 301/302, 317).
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    (tmp_path / "wiki" / "decisions").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "projects.json").write_text(json.dumps({"current": None, "projects": {}}))
    return tmp_path


def _mcp(store, body):
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO),
           "ZURVAN_MCP_READONLY": "0", "ZURVAN_CONFIG_DIR": str(store / "config")}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(body)],
                          cwd="/", env=env, capture_output=True, text=True)


def test_the_mcp_server_tool_passes_the_subject_through(store):
    r = _mcp(store, """
        from scripts.mcp_server import zurvan_decision_add
        print(zurvan_decision_add("T", "R", "accepted", [], project="otherproj"))
    """)
    assert r.returncode == 0, r.stderr
    page = next((store / "wiki" / "decisions").glob("*.md")).read_text()
    assert 'project: "otherproj"' in page
    assert "otherproj" in r.stdout


def test_an_unknown_stamp_is_reported_with_the_managed_fix(store):
    r = _mcp(store, """
        from scripts.mcp_server import zurvan_decision_add
        print(zurvan_decision_add("T", "R", "accepted", []))
    """)
    assert r.returncode == 0, r.stderr
    assert 'project: "unknown"' in next((store / "wiki" / "decisions").glob("*.md")).read_text()
    assert "unknown" in r.stdout and "zurvan_decision_set_project" in r.stdout


def test_the_mcp_set_project_tool_fixes_the_page_inside_the_barrier(store):
    assert _mcp(store, """
        from scripts.mcp_server import zurvan_decision_add
        zurvan_decision_add("T", "R", "accepted", [])
    """).returncode == 0
    page = next((store / "wiki" / "decisions").glob("*.md"))
    before = kr.current(store).revision
    r = _mcp(store, f"""
        from scripts.mcp_server import zurvan_decision_set_project
        print(zurvan_decision_set_project([{str(page)!r}], "otherproj"))
    """)
    assert r.returncode == 0, r.stderr
    assert 'project: "otherproj"' in page.read_text()
    state = kr.current(store)
    assert state.revision == before + 1 and state.atomicity == kr.MANAGED
    assert kr.mutations_in(kr.COMMITTED, store)[-1].store_project == "otherproj"


def test_the_mcp_set_project_tool_refuses_a_bad_slug_and_writes_nothing(store):
    assert _mcp(store, """
        from scripts.mcp_server import zurvan_decision_add
        zurvan_decision_add("T", "R", "accepted", [])
    """).returncode == 0
    page = next((store / "wiki" / "decisions").glob("*.md"))
    original = page.read_bytes()
    r = _mcp(store, f"""
        from scripts.mcp_server import zurvan_decision_set_project
        print(zurvan_decision_set_project([{str(page)!r}], "Acme"))
    """)
    assert r.stdout.startswith("Error:") and "not a valid identifier" in r.stdout
    assert page.read_bytes() == original
