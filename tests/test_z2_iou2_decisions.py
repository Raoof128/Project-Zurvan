"""IOU-Z2-2 (design 2026-11-15-z2-iou-1-4 §2): decision supersession links.

Decisions stay authority=markdown. `zurvan decision supersede <old> --by <new> --reason`
links both pages in one operation; a superseded decision leaves the recall digest but
stays in search; `decision history` walks the chain; the graph gets a `supersedes` edge.
Throwaway stores only (subprocesses with ZURVAN_ROOT: memory.py binds PROJECT_ROOT at import).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import knowledge_revision as kr

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def store(tmp_path):
    for d in ("wiki/decisions", "wiki/claims", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _env(store, **extra):
    return {**os.environ, "ZURVAN_ROOT": str(store), "PYTHONPATH": str(REPO),
            "ZURVAN_CONFIG_DIR": str(store / "config"), **extra}


def zurvan(store, *args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), *args],
                          capture_output=True, text=True, env=_env(store), cwd=str(REPO), timeout=180)


def _decide(store, title):
    r = zurvan(store, "decision", "add", "--title", title, "--reason", f"because {title}",
               "--status", "accepted", "--tags", "orrin", "--project", "orrin")
    assert r.returncode == 0, r.stderr
    pages = sorted((store / "wiki" / "decisions").glob("*.md"), key=lambda p: p.stat().st_mtime_ns)
    return pages[-1]


def _fm(page):
    text = page.read_text(encoding="utf-8")
    end = text.index("\n---", 4)
    return dict(l.split(":", 1) for l in text[4:end].splitlines() if ":" in l and not l.startswith(" ")), text


def test_supersede_links_both_pages_in_one_revision(store):
    old, new = _decide(store, "Use pypdf"), _decide(store, "Use PyMuPDF")
    before = kr.current(store).revision
    r = zurvan(store, "decision", "supersede", str(old), "--by", str(new), "--reason", "pypdf loses page boundaries")
    assert r.returncode == 0, r.stderr
    assert kr.current(store).revision == before + 1
    fo, to = _fm(old)
    fn, _ = _fm(new)
    assert fo["status"].strip().strip('"') == "superseded"
    assert fo["superseded_by"].strip().strip('"') == new.stem
    assert fn["supersedes"].strip().strip('"') == old.stem
    assert fn["status"].strip().strip('"') == "accepted"
    assert f"Superseded by [[{new.stem}]]" in to and "pypdf loses page boundaries" in to


def test_supersede_accepts_slugs_too(store):
    old, new = _decide(store, "Plan A"), _decide(store, "Plan B")
    r = zurvan(store, "decision", "supersede", old.stem, "--by", new.stem, "--reason", "B is cheaper")
    assert r.returncode == 0, r.stderr
    assert _fm(old)[0]["superseded_by"].strip().strip('"') == new.stem


@pytest.mark.parametrize("case,code", [("unknown", "DECISION_UNKNOWN"), ("self", "DECISION_CYCLE"),
                                       ("old_ended", "DECISION_NOT_CURRENT"), ("new_ended", "DECISION_NOT_CURRENT"),
                                       ("blank_reason", "REASON_REQUIRED")])
def test_refusals_write_nothing(store, case, code):
    a, b, c = _decide(store, "Alpha"), _decide(store, "Beta"), _decide(store, "Gamma")
    if case in ("old_ended", "new_ended"):
        assert zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "r").returncode == 0
    args = {"unknown": (a.stem, "no-such-decision", "r"), "self": (a.stem, a.stem, "r"),
            "old_ended": (a.stem, c.stem, "r"), "new_ended": (c.stem, a.stem, "r"),
            "blank_reason": (a.stem, b.stem, "   ")}[case]
    before = kr.current(store).revision
    snap = {p: p.read_bytes() for p in (store / "wiki").rglob("*.md")}
    r = zurvan(store, "decision", "supersede", args[0], "--by", args[1], "--reason", args[2])
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert code in r.stderr
    assert kr.current(store).revision == before
    assert {p: p.read_bytes() for p in (store / "wiki").rglob("*.md")} == snap


def test_history_walks_the_chain_both_ways(store):
    a, b, c = _decide(store, "First"), _decide(store, "Second"), _decide(store, "Third")
    assert zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "r1").returncode == 0
    assert zurvan(store, "decision", "supersede", b.stem, "--by", c.stem, "--reason", "r2").returncode == 0
    before = kr.current(store).revision
    for start in (a, b, c):
        r = zurvan(store, "decision", "history", start.stem)
        assert r.returncode == 0, r.stderr
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        assert [l.split()[0] for l in lines] == [a.stem, b.stem, c.stem]
        assert "superseded" in lines[0] and "superseded" in lines[1] and "accepted" in lines[2]
    assert kr.current(store).revision == before, "history is read-only"


def test_digest_skips_a_superseded_decision_but_search_keeps_it(store):
    from scripts.agent_workflow import project_digest
    a, b = _decide(store, "Orrin output in tonnes"), _decide(store, "Orrin output in kilotonnes")
    assert zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "unit change").returncode == 0
    digest = project_digest("orrin", root=store)
    assert "kilotonnes" in digest and "in tonnes" not in digest
    from scripts.claim_visibility import page_is_hidden
    assert not page_is_hidden(store, a.relative_to(store).as_posix())


def test_graph_has_a_supersedes_edge(store):
    from scripts.graph_build import extract_edges, generate_node_id
    a, b = _decide(store, "Old way"), _decide(store, "New way")
    assert zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "r").returncode == 0
    ra, rb = a.relative_to(store).as_posix(), b.relative_to(store).as_posix()
    nodes = {generate_node_id(p): {"path": p, "slug": Path(p).stem, "title": Path(p).stem} for p in (ra, rb)}
    edges = extract_edges(rb, b.read_text(encoding="utf-8"), nodes)
    assert any(e["edge_type"] == "supersedes" and e["to_node_id"] == generate_node_id(ra) for e in edges)


def _bridge(store, tool, args, readonly="0"):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "mcp_bridge.py")],
                          input=json.dumps({"tool": tool, "args": args}), cwd=str(REPO),
                          env=_env(store, ZURVAN_MCP_READONLY=readonly), capture_output=True, text=True, timeout=180)


def test_mcp_supersede_is_one_mcp_operation_and_respects_read_only(store):
    a, b = _decide(store, "Keep MCP read-only"), _decide(store, "Allow MCP writes")
    blocked = json.loads(_bridge(store, "decision_supersede",
                                 {"old": a.stem, "new": b.stem, "reason": "opt-in"}, readonly="1").stdout)
    assert blocked["text"].startswith("Error: MCP server is in read-only mode")
    assert "superseded_by" not in a.read_text(encoding="utf-8")
    ok = json.loads(_bridge(store, "decision_supersede", {"old": a.stem, "new": b.stem, "reason": "opt-in"}).stdout)
    assert ok["ok"] is True and not ok["text"].startswith("Error"), ok
    m = kr.mutations_in(kr.COMMITTED, store)[-1]
    assert (m.writer, m.caller) == ("mcp", "mcp:tool_zurvan_decision_supersede")
    refused = json.loads(_bridge(store, "decision_supersede", {"old": a.stem, "new": b.stem, "reason": "again"}).stdout)
    assert refused["text"].startswith("Error: DECISION_NOT_CURRENT")


def test_python_and_rust_tool_lists_include_the_tool():
    from scripts import mcp_bridge
    assert mcp_bridge.WRITES["decision_supersede"] == "tool_zurvan_decision_supersede"
    fixture = json.loads((REPO / "rust" / "zurvan-mcp" / "fixtures" / "tools_list.json").read_text())
    assert "zurvan_decision_supersede" in {t["name"] for t in fixture["tools"]}
    rs = (REPO / "rust" / "zurvan-mcp" / "src" / "bridge.rs").read_text()
    assert '"zurvan_decision_supersede" => ("decision_supersede", true)' in rs


@pytest.mark.parametrize("fail_on", ["old", "new"])
def test_a_failed_page_write_leaves_a_retryable_state(store, fail_on):   # I4
    a, b = _decide(store, "Old rule"), _decide(store, "New rule")
    target = a if fail_on == "old" else b
    code = (
        "import sys, scripts.memory as m\n"
        "real = m.write_file_safely\n"
        "def flaky(p, c):\n"
        f"    if p.endswith('{target.name}'): raise OSError('disk full')\n"
        "    return real(p, c)\n"
        "m.write_file_safely = flaky\n"
        "from scripts import knowledge_revision as kr\n"
        "try:\n"
        "    with kr.operation(writer='test'):\n"
        f"        m.supersede_decision('{a.stem}', '{b.stem}', 'r')\n"
        "except OSError: print('failed as planned')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=_env(store), cwd=str(REPO))
    assert "failed as planned" in r.stdout, r.stderr
    from scripts import knowledge_revision as kr
    if kr.current(store).state == kr.SUSPECT:
        assert subprocess.run([sys.executable, str(REPO / "scripts" / "cli.py"), "knowledge", "reconcile"],
                              capture_output=True, text=True, env=_env(store), cwd=str(REPO)).returncode == 0
    r = zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "retry")
    assert r.returncode == 0, r.stderr
    assert _fm(b)[0]["supersedes"].strip().strip('"') == a.stem          # not "a, a"
    assert _fm(a)[0]["superseded_by"].strip().strip('"') == b.stem


def test_cross_project_supersession_is_refused(store):                     # I5
    a = _decide(store, "Rule in orrin")
    r = zurvan(store, "decision", "add", "--title", "Rule in halden", "--reason", "x", "--status", "accepted",
               "--project", "halden")
    assert r.returncode == 0, r.stderr
    b = sorted((store / "wiki" / "decisions").glob("*.md"), key=lambda p: p.stat().st_mtime_ns)[-1]
    before = kr.current(store).revision
    r = zurvan(store, "decision", "supersede", a.stem, "--by", b.stem, "--reason", "r")
    assert r.returncode == 1 and "DECISION_PROJECT_MISMATCH" in r.stderr
    assert kr.current(store).revision == before
