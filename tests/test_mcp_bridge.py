"""scripts/mcp_bridge.py: one Rust-server write or eval call = one Python process.

The bridge must add NOTHING to the ledger semantics: it calls the same
`scripts.mcp_tools.tool_zurvan_*` functions the Python server calls, whose
`@mcp_operation` opens the operation (writer="mcp"). An operation opened by the
bridge itself would be joined by the inner one and silently change `writer`.
Throwaway stores only (subprocesses with ZURVAN_ROOT, as in
test_mcp_operation_boundary.py): memory.py binds PROJECT_ROOT at import.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "scripts" / "mcp_bridge.py"
READONLY_ERROR = ("Error: MCP server is in read-only mode (set ZURVAN_MCP_READONLY=0 to enable writes). "
                  "Write operation blocked.")


@pytest.fixture
def store(tmp_path):
    for d in ("wiki/decisions", "wiki/claims", "wiki/notes", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _bridge(store, tool, args, readonly="0"):
    env = {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO), "ZURVAN_MCP_READONLY": readonly}
    return subprocess.run([sys.executable, str(BRIDGE)], input=json.dumps({"tool": tool, "args": args}),
                          cwd=str(REPO), env=env, capture_output=True, text=True, timeout=180)


def _reply(p):
    assert p.returncode == 0, p.stderr[-2000:]
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"bridge stdout must be exactly one JSON line, got {len(lines)}: {p.stdout[:500]!r}"
    return json.loads(lines[0])


DECISION = {"title": "T", "reason": "R", "status": "accepted", "tags": ["t"], "project": "zurvan"}


def test_bridge_decision_add_one_mutation(store):
    reply = _reply(_bridge(store, "decision_add", DECISION))
    assert reply["ok"] is True
    assert reply["text"].startswith("Decision added successfully (project: zurvan): T")
    committed = kr.mutations_in(kr.COMMITTED, store)
    assert len(committed) == 1, [(m.writer, m.caller) for m in committed]
    m = committed[0]
    assert m.writer == "mcp"
    assert m.caller == "mcp:tool_zurvan_decision_add"
    assert m.implicit is False
    assert any(p.endswith("log.md") for p in m.completed_paths)
    assert any("decisions" in p for p in m.completed_paths)


def test_bridge_reports_revision(store):
    reply = _reply(_bridge(store, "decision_add", DECISION))
    assert reply["revision"] == kr.current(store).revision == 1


def test_bridge_stdout_is_one_json_line(store):
    p = _bridge(store, "remember", {"type": "note", "title": "N", "body": "b", "tags": ["x"]})
    reply = _reply(p)  # asserts one line
    assert reply["ok"] is True and reply["text"].startswith("Note added successfully (note): N")
    assert "Created" in p.stderr, "memory.py's prints go to stderr, not the protocol channel"


def test_bridge_readonly_refuses(store):
    reply = _reply(_bridge(store, "question_add", {"question": "q?", "reason": "r", "tags": []}, readonly="1"))
    assert reply["text"] == READONLY_ERROR
    assert kr.mutations_in(kr.COMMITTED, store) == []
    assert kr.mutations_in(kr.ACTIVE, store) == []


def test_bridge_eval_text_says_what_it_measures(store):
    reply = _reply(_bridge(store, "validate_gold", {}))
    assert reply["text"].startswith("Evaluates the Python reference retriever, not this server.\n")
    assert reply["revision"] is None


def test_bridge_unknown_tool_is_an_error_reply(store):
    reply = _reply(_bridge(store, "drop_everything", {}))
    assert reply["ok"] is False and "unknown bridge tool" in reply["error"]
